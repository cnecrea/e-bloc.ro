"""DataUpdateCoordinator pentru integrarea E-bloc România.

Toate endpoint-urile sunt cele din debug_v2.py:
  - AppHomeGetAp.php → aInfoAp (cu id_ap, total_plata, nr_pers, cod_client)
  - AppContoareGetIndex.php → aInfoAp, aInfoContoare, aInfoIndex
  - AppFacturiGetData.php → aFactChelt, aFactFond
  - AppHomeGetNrPers.php → aLuna, aDeclaratii
  - AppContactGetTickets.php → aTickets
  - AppIstoricPlatiGetPlati.php → aChitante
  - AppSettingsGetCard.php → aCard
  - AppPlatesteGetAp.php → aInfoAp (cu aDatAp breakdown restanță)
  - AppHomeGetWalletItems.php → sold, aItems
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    UpdateFailed,
)

from .api import EblocApiClient
from .const import DEFAULT_UPDATE_INTERVAL

_LOGGER = logging.getLogger(__name__)


class EblocCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Coordinator care se ocupă de toate datele e-bloc.ro."""

    def __init__(
        self,
        hass: HomeAssistant,
        api_client: EblocApiClient,
        config_entry: ConfigEntry,
        update_interval_seconds: int = DEFAULT_UPDATE_INTERVAL,
    ) -> None:
        """Inițializează coordinatorul."""
        super().__init__(
            hass,
            _LOGGER,
            name=f"EblocCoordinator_{config_entry.entry_id[:8]}",
            update_interval=timedelta(seconds=update_interval_seconds),
        )
        self.api_client = api_client
        self._config_entry = config_entry
        self._refresh_counter: int = 0

    async def _async_update_data(self) -> dict[str, Any]:
        """Obține date de la API-ul e-bloc.ro.

        Structura datelor returnate:
        {
            "associations": [...],
            "apartments": {id_asoc: [{"id_ap": .., "ap": .., "total_plata": ..}]},
            "access_rights": {...},
            "home_ap_data": {id_asoc: {...}},
            "luna": "2026-02",
            "per_apartment": {
                "id_asoc:id_ap": {
                    "contoare": {aInfoContoare, aInfoIndex, ...},
                    "plateste": {aCard, aInfoAp (cu aDatAp), ...},
                    "contact": {aTickets, ...},
                    "plati": {aChitante, ...},
                    "nr_pers": {aLuna, aDeclaratii, ...},
                    "wallet": {sold, aItems, ...},
                    "facturi": {aFactChelt, ...},
                }
            },
            "carduri": {aCard, ...},
            "get_info": {...},
        }
        """
        _LOGGER.debug(
            "[Ebloc:Coordinator] Actualizare #%d", self._refresh_counter
        )

        try:
            # Asigurăm sesiune validă
            if not await self.api_client.async_ensure_authenticated():
                raise UpdateFailed("Nu s-a putut autentifica la e-bloc.ro.")

            # Re-autodiscovery dacă nu avem date
            if not self.api_client.associations:
                if not await self.api_client.async_autodiscover():
                    raise UpdateFailed("Autodiscovery eșuat.")

            associations = self.api_client.associations
            apartments = self.api_client.apartments
            access_rights = self.api_client.access_rights
            home_ap_data = self.api_client.home_ap_data
            luna = self.api_client.luna_curenta

            # Colectăm date per apartament
            per_apartment: dict[str, dict[str, Any]] = {}

            # Pre-fetch: luna corectă de contoare per asociație
            # (AppContoareGetIndexLuni.php → aLuni, prima = cea mai recentă)
            luna_contoare_per_asoc: dict[Any, str] = {}
            luna_fallback = luna or datetime.now().strftime("%Y-%m")

            luni_tasks = {
                id_asoc: self.api_client.async_get_contoare_luni(id_asoc)
                for id_asoc in apartments
            }
            if luni_tasks:
                luni_keys = list(luni_tasks.keys())
                luni_results = await asyncio.gather(
                    *luni_tasks.values(), return_exceptions=True
                )

                # Asociații care au nevoie de retry (nologin → sesiune expirată)
                retry_asoc: list[Any] = []

                for id_asoc_key, luni_res in zip(luni_keys, luni_results):
                    if isinstance(luni_res, Exception):
                        _LOGGER.warning(
                            "[Ebloc:Coordinator] ContoareGetIndexLuni eșuat "
                            "pentru asoc %s: %s", id_asoc_key, luni_res,
                        )
                        continue

                    result = luni_res.get("result", "")
                    if result == "nologin":
                        _LOGGER.info(
                            "[Ebloc:Coordinator] ContoareGetIndexLuni nologin "
                            "pentru asoc %s — programăm retry", id_asoc_key,
                        )
                        retry_asoc.append(id_asoc_key)
                        continue

                    a_luni = luni_res.get("aLuni", [])
                    if a_luni:
                        # aLuni poate conține stringuri sau obiecte {"luna": "..."}
                        first = a_luni[0]
                        if isinstance(first, str):
                            luna_contoare_per_asoc[id_asoc_key] = first
                        elif isinstance(first, dict):
                            luna_contoare_per_asoc[id_asoc_key] = first.get(
                                "luna", ""
                            )

                # Retry pentru asociațiile cu nologin (re-autentificare)
                if retry_asoc:
                    _LOGGER.info(
                        "[Ebloc:Coordinator] Re-autentificare pentru retry "
                        "ContoareGetIndexLuni: %s", retry_asoc,
                    )
                    await self.api_client.async_ensure_authenticated()
                    retry_tasks = {
                        asoc_id: self.api_client.async_get_contoare_luni(asoc_id)
                        for asoc_id in retry_asoc
                    }
                    retry_keys = list(retry_tasks.keys())
                    retry_results = await asyncio.gather(
                        *retry_tasks.values(), return_exceptions=True
                    )
                    for id_asoc_key, luni_res in zip(retry_keys, retry_results):
                        if isinstance(luni_res, Exception):
                            _LOGGER.warning(
                                "[Ebloc:Coordinator] ContoareGetIndexLuni retry "
                                "eșuat pentru asoc %s: %s",
                                id_asoc_key, luni_res,
                            )
                            continue
                        a_luni = luni_res.get("aLuni", [])
                        if a_luni:
                            first = a_luni[0]
                            if isinstance(first, str):
                                luna_contoare_per_asoc[id_asoc_key] = first
                            elif isinstance(first, dict):
                                luna_contoare_per_asoc[id_asoc_key] = first.get(
                                    "luna", ""
                                )

            for id_asoc, ap_list in apartments.items():
                # Luna corectă de contoare pentru această asociație
                # Fallback: luna din AppHomeGetAp sau luna curentă (datetime)
                luna_contoare = luna_contoare_per_asoc.get(
                    id_asoc, luna_fallback
                )
                if id_asoc not in luna_contoare_per_asoc:
                    _LOGGER.info(
                        "[Ebloc:Coordinator] Asoc %s: luna_contoare lipsă din "
                        "GetIndexLuni, fallback → %s", id_asoc, luna_contoare,
                    )
                else:
                    _LOGGER.info(
                        "[Ebloc:Coordinator] Asoc %s: luna_contoare=%s "
                        "(din GetIndexLuni)", id_asoc, luna_contoare,
                    )

                for ap in ap_list:
                    # Câmpul corect este id_ap (NU id)
                    id_ap = ap.get("id_ap", ap.get("id", 0))
                    if not id_ap:
                        continue

                    key = f"{id_asoc}:{id_ap}"
                    ap_data: dict[str, Any] = {}

                    # Fetch paralel
                    tasks: dict[str, Any] = {}

                    # Contoare: AppContoareGetIndex.php (params: id_asoc, luna)
                    # Folosim luna_contoare (din AppContoareGetIndexLuni),
                    # NU luna din AppHomeGetAp (care poate fi luna anterioară)
                    if luna_contoare:
                        tasks["contoare"] = self.api_client.async_get_contoare_index(
                            id_asoc, luna_contoare
                        )

                    # Plătește: AppPlatesteGetAp.php (include restanță + breakdown)
                    if access_rights.get("plateste"):
                        tasks["plateste"] = self.api_client.async_get_plateste_ap(
                            id_asoc, id_ap
                        )

                    # Contact: AppContactGetTickets.php (this_user=1 = doar ale mele)
                    if access_rights.get("contact"):
                        tasks["contact"] = self.api_client.async_get_contact_tickets(
                            id_asoc, id_ap, this_user=1
                        )

                    # Plăți: AppIstoricPlatiGetPlati.php
                    tasks["plati"] = self.api_client.async_get_istoric_plati(
                        id_asoc, id_ap
                    )

                    # Nr persoane: AppHomeGetNrPers.php (fără luna!)
                    tasks["nr_pers"] = self.api_client.async_get_nr_pers(
                        id_asoc, id_ap
                    )

                    # Wallet: AppHomeGetWalletItems.php
                    tasks["wallet"] = self.api_client.async_get_wallet(
                        id_asoc, id_ap
                    )

                    # Facturi: AppFacturiGetData.php
                    if access_rights.get("facturi") and luna:
                        tasks["facturi"] = self.api_client.async_get_facturi(
                            id_asoc, id_ap, luna
                        )

                    # Executăm toate task-urile paralel
                    task_keys = list(tasks.keys())
                    task_coros = list(tasks.values())
                    results = await asyncio.gather(
                        *task_coros, return_exceptions=True
                    )

                    for task_key, result in zip(task_keys, results):
                        if isinstance(result, Exception):
                            _LOGGER.warning(
                                "[Ebloc:Coordinator] Task %s eșuat pentru %s: %s",
                                task_key, key, result,
                            )
                            ap_data[task_key] = {}
                        else:
                            ap_data[task_key] = result

                    # Fetch detalii tickete (primele 5) — AppContactGetTicketData.php
                    contact_data = ap_data.get("contact", {})
                    tickets_list = contact_data.get("aTickets", [])
                    ticket_details: dict[str, dict[str, Any]] = {}
                    if tickets_list:
                        detail_tasks = []
                        detail_ids = []
                        for t in tickets_list[:5]:
                            tid = t.get("id_ticket", t.get("id", ""))
                            if tid:
                                detail_ids.append(str(tid))
                                detail_tasks.append(
                                    self.api_client.async_get_ticket_detail(tid)
                                )
                        if detail_tasks:
                            detail_results = await asyncio.gather(
                                *detail_tasks, return_exceptions=True
                            )
                            for tid, res in zip(detail_ids, detail_results):
                                if isinstance(res, Exception):
                                    _LOGGER.debug(
                                        "[Ebloc:Coordinator] Ticket %s detail eșuat: %s",
                                        tid, res,
                                    )
                                else:
                                    ticket_details[tid] = res
                    ap_data["ticket_details"] = ticket_details

                    per_apartment[key] = ap_data

            # Carduri (globale): AppSettingsGetCard.php
            carduri: dict[str, Any] = {}
            try:
                carduri = await self.api_client.async_get_carduri()
            except Exception as err:  # noqa: BLE001
                _LOGGER.warning("[Ebloc:Coordinator] Carduri eșuat: %s", err)

            # Persistăm sesiunea
            self._persist_session()

            self._refresh_counter += 1

            _LOGGER.debug(
                "[Ebloc:Coordinator] Actualizare #%d completă: "
                "%d asociații, %d apartamente",
                self._refresh_counter - 1,
                len(associations),
                len(per_apartment),
            )

            return {
                "associations": associations,
                "apartments": apartments,
                "access_rights": access_rights,
                "home_ap_data": home_ap_data,
                "luna": luna,
                "per_apartment": per_apartment,
                "carduri": carduri,
                "get_info": self.api_client.get_info_data,
            }

        except UpdateFailed:
            raise
        except Exception as err:
            _LOGGER.exception(
                "[Ebloc:Coordinator] Eroare neașteptată: %s", err
            )
            raise UpdateFailed(
                "Eroare neașteptată la actualizarea datelor e-bloc.ro."
            ) from err

    def _persist_session(self) -> None:
        """Persistă sesiunea în config_entry.data."""
        if self._config_entry is None:
            return

        session_data = self.api_client.export_session()
        if not session_data:
            return

        current_data = dict(self._config_entry.data)
        old_session = current_data.get("session_data", {})

        if (
            old_session
            and old_session.get("session_id") == session_data.get("session_id")
        ):
            return

        current_data["session_data"] = session_data
        self.hass.config_entries.async_update_entry(
            self._config_entry, data=current_data
        )
        _LOGGER.debug(
            "[Ebloc:Coordinator] Sesiune persistată: session_id=%s...",
            (session_data.get("session_id") or "")[:20],
        )
