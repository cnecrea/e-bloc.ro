"""Platforma Button pentru E-bloc România.

Pattern IDENTIC cu eonromania-main:
  - _attr_has_entity_name = False
  - custom entity_id property (getter/setter)
  - self._custom_entity_id = f"button.{DOMAIN}_{id_user}_xxx"

Butoane:
  - Trimite index (per contor cu right_edit_index=1)
  - Trimite nr. persoane (per apartament)

Lookup-ul number entity se face prin entity_registry (unique_id),
NU prin construirea manuală a entity_id-ului.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import slugify as ha_slugify

from .const import (
    ATTRIBUTION,
    DOMAIN,
    LICENSE_DATA_KEY,
    NR_PERS_MULTIPLIER,
)
from .coordinator import EblocCoordinator
from .helpers import is_license_valid

_LOGGER = logging.getLogger(__name__)


# ──────────────────────────────────────────────
# Clasă de bază (pattern IDENTIC cu eonromania)
# ──────────────────────────────────────────────
class EblocButton(CoordinatorEntity[EblocCoordinator], ButtonEntity):
    """Clasă de bază pentru butoanele E-bloc România."""

    _attr_has_entity_name = False

    def __init__(self, coordinator: EblocCoordinator, id_user: int) -> None:
        """Inițializare cu coordinator."""
        super().__init__(coordinator)
        self._id_user = id_user
        self._custom_entity_id: str | None = None

    @property
    def entity_id(self) -> str | None:
        """Returnează entity_id custom (pattern eonromania)."""
        return self._custom_entity_id

    @entity_id.setter
    def entity_id(self, value: str) -> None:
        """Setează entity_id (necesar pentru HA internals)."""
        self._custom_entity_id = value

    @property
    def device_info(self) -> DeviceInfo:
        """Informații dispozitiv."""
        return DeviceInfo(
            identifiers={(DOMAIN, str(self._id_user))},
            name=f"E-bloc România ({self._id_user})",
            manufacturer="Ciprian Nicolae (cnecrea)",
            model="E-bloc România",
            entry_type=DeviceEntryType.SERVICE,
        )

    @property
    def available(self) -> bool:
        """Disponibil doar cu licență validă."""
        mgr = self.hass.data.get(DOMAIN, {}).get(LICENSE_DATA_KEY)
        if mgr is None or not mgr.is_valid:
            return False
        return super().available

    def _find_number_entity_id(self, number_unique_id: str) -> str | None:
        """Caută entity_id al unui number entity prin unique_id în registry."""
        registry = er.async_get(self.hass)
        return registry.async_get_entity_id("number", DOMAIN, number_unique_id)


# ──────────────────────────────────────────────
# async_setup_entry
# ──────────────────────────────────────────────
async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Configurează butoanele."""
    if not is_license_valid(hass):
        _LOGGER.debug("[Ebloc:Button] Licență invalidă — skip platform button")
        return

    runtime = entry.runtime_data
    coordinator: EblocCoordinator = runtime.coordinator
    id_user = coordinator.api_client.id_user or 0

    entities: list[ButtonEntity] = []

    data = coordinator.data or {}
    apartments = data.get("apartments", {})
    luna = data.get("luna", "")

    for id_asoc, ap_list in apartments.items():
        id_asoc_int = int(id_asoc) if isinstance(id_asoc, str) else id_asoc
        for ap in ap_list:
            id_ap = ap.get("id_ap", ap.get("id", 0))
            if not id_ap:
                continue

            ap_key = f"{id_asoc_int}:{id_ap}"

            # Buton trimite nr. persoane
            entities.append(
                TrimiteNrPersButton(
                    coordinator, id_user, id_asoc_int, id_ap, luna
                )
            )

            # Butoane trimite index per contor
            ap_data = data.get("per_apartment", {}).get(ap_key, {})
            contoare_data = ap_data.get("contoare", {})
            contoare_def = contoare_data.get("aInfoContoare", [])
            contoare_idx = contoare_data.get("aInfoIndex", [])

            idx_by_contor: dict[str, dict[str, Any]] = {}
            for idx_item in contoare_idx:
                cid = str(idx_item.get("id_contor", ""))
                if cid:
                    idx_by_contor[cid] = idx_item

            for cd in contoare_def:
                id_contor = str(cd.get("id_contor", ""))
                if not id_contor:
                    continue
                contor_val = idx_by_contor.get(id_contor, {})
                right_edit = str(contor_val.get("right_edit_index", "0"))
                if right_edit == "1":
                    entities.append(
                        TrimiteIndexButton(
                            coordinator, id_user, id_asoc_int, id_ap,
                            int(id_contor), cd,
                        )
                    )

    _LOGGER.info(
        "[Ebloc:Button] Creez %d butoane pentru user %s",
        len(entities), id_user,
    )
    async_add_entities(entities, update_before_add=True)


# ──────────────────────────────────────────────
# Trimite index per contor
# ──────────────────────────────────────────────
class TrimiteIndexButton(EblocButton):
    """Buton care trimite indexul din number helper la API."""

    _attr_icon = "mdi:upload"

    def __init__(
        self, coordinator: EblocCoordinator, id_user: int,
        id_asoc: int, id_ap: int | str,
        id_contor: int, contor_def: dict[str, Any],
    ) -> None:
        super().__init__(coordinator, id_user)
        self._id_asoc = id_asoc
        self._id_ap = id_ap
        self._id_contor = id_contor

        titlu = contor_def.get("titlu", f"Contor {id_contor}")
        titlu_slug = ha_slugify(titlu)
        self._attr_name = f"Trimite index {titlu}"
        self._attr_unique_id = f"{DOMAIN}_{id_user}_{id_contor}_trimite_index"
        self._custom_entity_id = f"button.{DOMAIN}_{id_user}_trimite_index_{titlu_slug}"

        # unique_id al number entity corespunzător
        self._number_uid = f"{DOMAIN}_{id_user}_{id_contor}_index"

    async def async_press(self) -> None:
        """Trimite indexul din number helper."""
        _LOGGER.info(
            "[Ebloc:Button] === TRIMITE INDEX APĂSAT === "
            "contor=%s, asoc=%s, ap=%s, number_uid=%s",
            self._id_contor, self._id_asoc, self._id_ap, self._number_uid,
        )

        number_entity_id = self._find_number_entity_id(self._number_uid)
        _LOGGER.info(
            "[Ebloc:Button] Lookup number entity: uid=%s → entity_id=%s",
            self._number_uid, number_entity_id,
        )
        if number_entity_id is None:
            _LOGGER.warning(
                "[Ebloc:Button] STOP: Nu găsesc number entity cu uid=%s",
                self._number_uid,
            )
            return

        state = self.hass.states.get(number_entity_id)
        _LOGGER.info(
            "[Ebloc:Button] State pentru %s: state=%s",
            number_entity_id,
            state.state if state else "None",
        )
        if state is None or state.state in ("unknown", "unavailable"):
            _LOGGER.warning(
                "[Ebloc:Button] STOP: Number entity %s indisponibil (state=%s)",
                number_entity_id, state.state if state else "None",
            )
            return

        try:
            index_value = float(state.state)
        except (ValueError, TypeError):
            _LOGGER.warning(
                "[Ebloc:Button] STOP: Valoare invalidă în %s: '%s'",
                number_entity_id, state.state,
            )
            return

        index_api = int(index_value * NR_PERS_MULTIPLIER)

        _LOGGER.info(
            "[Ebloc:Button] Trimit index la API: contor=%s, "
            "val=%.3f m³, API_val=%d, asoc=%s, ap=%s",
            self._id_contor, index_value, index_api,
            self._id_asoc, self._id_ap,
        )

        try:
            result = await self.coordinator.api_client.async_set_index_contoare(
                [
                    {
                        "id_asoc": self._id_asoc,
                        "id_ap": self._id_ap,
                        "id_contor": self._id_contor,
                        "index_nou": index_api,
                    }
                ]
            )
        except Exception as err:
            _LOGGER.warning(
                "[Ebloc:Button] EXCEPȚIE la trimitere index: %s", err,
                exc_info=True,
            )
            return

        _LOGGER.info(
            "[Ebloc:Button] Răspuns API trimitere index: %s", result,
        )

        if result.get("result") == "ok":
            _LOGGER.info(
                "[Ebloc:Button] Index trimis cu SUCCES: contor=%s, val=%d",
                self._id_contor, index_api,
            )
            await self.coordinator.async_request_refresh()
        else:
            _LOGGER.warning(
                "[Ebloc:Button] EȘEC trimitere index: contor=%s, result=%s",
                self._id_contor, result,
            )


# ──────────────────────────────────────────────
# Trimite nr. persoane
# ──────────────────────────────────────────────
class TrimiteNrPersButton(EblocButton):
    """Buton care trimite nr. persoane din number helper la API."""

    _attr_icon = "mdi:account-check"

    def __init__(
        self, coordinator: EblocCoordinator, id_user: int,
        id_asoc: int, id_ap: int | str, luna: str,
    ) -> None:
        super().__init__(coordinator, id_user)
        self._id_asoc = id_asoc
        self._id_ap = id_ap
        self._luna = luna
        self._attr_name = "Trimite nr. persoane"
        self._attr_unique_id = f"{DOMAIN}_{id_user}_{id_asoc}_{id_ap}_trimite_nr_pers"
        self._custom_entity_id = f"button.{DOMAIN}_{id_user}_trimite_nr_persoane"

        # unique_id al number entity corespunzător
        self._number_uid = f"{DOMAIN}_{id_user}_{id_asoc}_{id_ap}_nr_pers"

    async def async_press(self) -> None:
        """Trimite nr. persoane din number helper."""
        _LOGGER.info(
            "[Ebloc:Button] === TRIMITE NR. PERSOANE APĂSAT === "
            "asoc=%s, ap=%s, number_uid=%s",
            self._id_asoc, self._id_ap, self._number_uid,
        )

        number_entity_id = self._find_number_entity_id(self._number_uid)
        _LOGGER.info(
            "[Ebloc:Button] Lookup number entity: uid=%s → entity_id=%s",
            self._number_uid, number_entity_id,
        )
        if number_entity_id is None:
            _LOGGER.warning(
                "[Ebloc:Button] STOP: Nu găsesc number entity cu uid=%s",
                self._number_uid,
            )
            return

        state = self.hass.states.get(number_entity_id)
        _LOGGER.info(
            "[Ebloc:Button] State pentru %s: state=%s",
            number_entity_id,
            state.state if state else "None",
        )
        if state is None or state.state in ("unknown", "unavailable"):
            _LOGGER.warning(
                "[Ebloc:Button] STOP: Number entity %s indisponibil (state=%s)",
                number_entity_id, state.state if state else "None",
            )
            return

        try:
            nr_pers = int(float(state.state))
        except (ValueError, TypeError):
            _LOGGER.warning(
                "[Ebloc:Button] STOP: Valoare invalidă în %s: '%s'",
                number_entity_id, state.state,
            )
            return

        # Luna curentă — MEREU calculată dinamic (nu stale din init)
        from datetime import datetime as _dt  # noqa: C0415
        luna = self.coordinator.data.get("luna", "") or _dt.now().strftime("%Y-%m")
        # Safety: dacă luna din coordinator e mai veche decât luna curentă,
        # folosim luna curentă (API refuză luni închise/readonly)
        luna_curenta = _dt.now().strftime("%Y-%m")
        if luna < luna_curenta:
            _LOGGER.info(
                "[Ebloc:Button] Luna coordinator (%s) < luna curentă (%s), "
                "folosesc luna curentă",
                luna, luna_curenta,
            )
            luna = luna_curenta

        _LOGGER.info(
            "[Ebloc:Button] Trimit nr. persoane la API: "
            "asoc=%s, ap=%s, luna=%s, nr_pers=%d",
            self._id_asoc, self._id_ap, luna, nr_pers,
        )

        try:
            result = await self.coordinator.api_client.async_set_nr_persoane(
                self._id_asoc, self._id_ap, luna, nr_pers
            )
        except Exception as err:
            _LOGGER.warning(
                "[Ebloc:Button] EXCEPȚIE la trimitere nr. persoane: %s", err,
                exc_info=True,
            )
            return

        _LOGGER.info(
            "[Ebloc:Button] Răspuns API trimitere nr. persoane: %s", result,
        )

        if result.get("result") == "ok":
            _LOGGER.info(
                "[Ebloc:Button] Nr. persoane trimis cu SUCCES: "
                "asoc=%s, ap=%s, luna=%s, nr=%d",
                self._id_asoc, self._id_ap, luna, nr_pers,
            )
            await self.coordinator.async_request_refresh()
        else:
            _LOGGER.warning(
                "[Ebloc:Button] EȘEC trimitere nr. persoane: result=%s", result,
            )
