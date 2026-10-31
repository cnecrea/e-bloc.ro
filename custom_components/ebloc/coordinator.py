"""DataUpdateCoordinator pentru integrarea E-bloc România.

La fiecare actualizare:
  1. contul (AppGetInfo + AppHomeGetAp): sesiune, asociații, drepturi, apartamente, luna curentă
  2. pentru fiecare asociație, doar paginile permise:
       index    → AppContoareGetIndexLuni + AppContoareGetIndex
       plateste → AppPlatesteGetAp (restanțe cu detalii)
       contact  → AppContactGetTickets (+ AppContactGetTicketData pentru primele 5)
       facturi  → AppFacturiGetData
  3. pentru fiecare apartament:
       AppIstoricPlatiGetPlati; cu pagina „home": AppHomeGetNrPers, AppHomeGetWalletItems
  4. AppSettingsGetCard (la nivel de cont)

O sursă care nu răspunde nu golește senzorii: se păstrează valoarea de la actualizarea
anterioară, iar problema apare în avertismentul de la finalul actualizării.

Logging (convenția din logfmt.py):
  DEBUG    începutul și rezumatul fiecărei actualizări; raportul pe apartament doar la prima
           actualizare sau când starea unei surse se schimbă
  INFO     conectarea inițială; asociații, apartamente sau drepturi apărute ori retrase
  WARNING  sursele indisponibile ale unei actualizări, într-un singur mesaj
  ERROR    actualizarea eșuată (UpdateFailed, logată de Home Assistant); erorile neprevăzute
           cu traceback
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timedelta
from functools import partial
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import EblocApiClient, describe_result, is_ok
from .const import DEFAULT_UPDATE_INTERVAL, DOMAIN, LICENSE_DATA_KEY, PAGE_LABELS
from .helpers import mascheaza_email
from .logfmt import oneline, seconds

_LOGGER = logging.getLogger(__name__)

# Numele surselor de date, așa cum apar în log și în diagnostic
SOURCE_LABELS = {
    "apartamente": "Apartamente",
    "contoare_luni": "Luna de citire",
    "contoare": "Indecși contoare",
    "plateste": "Sume de plată",
    "contact": "Tichete",
    "facturi": "Facturi",
    "plati": "Istoric plăți",
    "nr_pers": "Număr persoane",
    "wallet": "Sold",
    "carduri": "Carduri salvate",
}
# Pagina de care depinde fiecare sursă (istoricul plăților nu depinde de nicio pagină)
SOURCE_PAGES = {
    "contoare": "index",
    "plateste": "plateste",
    "contact": "contact",
    "facturi": "facturi",
    "nr_pers": "home",
    "wallet": "home",
}
SHARED_SOURCES = ("contoare", "plateste", "contact", "facturi")
APARTMENT_SOURCES = ("plati", "nr_pers", "wallet")

STATUS_OK = "OK"
TICKET_DETAILS_MAX = 5


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
        # Numele apare în mesajele standard ale Home Assistant — cu emailul mascat
        super().__init__(
            hass,
            _LOGGER,
            name=f"E-bloc România ({mascheaza_email(config_entry.data.get('email', ''))})",
            update_interval=timedelta(seconds=update_interval_seconds),
        )
        self.api_client = api_client
        self._config_entry = config_entry
        self._update_count = 0
        self._relogin_lock = asyncio.Lock()
        self._relogin_done = False
        # (sursă, unde, motiv, s-a păstrat valoarea anterioară)
        self._failures: list[tuple[str, str, str, bool]] = []
        # Raportul ultimei actualizări (pentru diagnostic)
        self.last_report: dict[str, Any] = {}
        # Drepturile și apartamentele de la actualizarea anterioară (pentru a semnala schimbările)
        self._known_layout: dict[str, tuple[list[str], list[str]]] | None = None
        # Starea surselor per apartament la actualizarea anterioară (raportul se repetă doar la schimbări)
        self._known_status: dict[str, dict[str, str]] = {}

    async def _async_update_data(self) -> dict[str, Any]:
        """Obține datele de la e-bloc.ro.

        Structura datelor:
        {
            "associations": [...], "apartments": {id_asoc: [aInfoAp]},
            "access_rights": {pagină: bool}, "access_by_asoc": {pagină: [id_asoc]},
            "home_ap_data": {id_asoc: {...}}, "luna": "2026-10",
            "per_apartment": {"id_asoc:id_ap": {"contoare", "plateste", "contact", "facturi",
                                                "plati", "nr_pers", "wallet", "ticket_details"}},
            "carduri": {...}, "get_info": {...},
        }
        """
        license_mgr = self.hass.data.get(DOMAIN, {}).get(LICENSE_DATA_KEY)
        if license_mgr and not license_mgr.is_valid:
            _LOGGER.debug("Actualizare omisă: licența nu este validă.")
            return self.data or {}

        self._update_count += 1
        nr = self._update_count
        self._relogin_done = False
        self._failures = []
        self.api_client.reset_stats()
        start = time.monotonic()
        _LOGGER.debug("Actualizarea #%d începută.", nr)

        try:
            api = self.api_client
            if not await api.async_refresh_account():
                raise UpdateFailed(f"{api.last_error or 'Datele contului e-bloc.ro nu au putut fi citite'}.")
            for _sursa, id_asoc, motiv in api.account_problems:
                self._failures.append(("apartamente", f"asociația {id_asoc}", motiv, False))
            if not api.apartments:
                raise UpdateFailed("Contul e-bloc.ro nu are niciun apartament disponibil.")

            self._log_layout_changes()

            previous = (self.data or {}).get("per_apartment", {})
            per_apartment: dict[str, dict[str, Any]] = {}
            report: dict[str, Any] = {}

            for id_asoc, ap_list in api.apartments.items():
                per_apartment.update(
                    await self._async_update_association(id_asoc, ap_list, previous, report)
                )

            carduri, _ = self._accept(
                "carduri", await self._fetch(api.async_get_carduri),
                (self.data or {}).get("carduri"), "cont",
            )

            self._persist_session()
            durata = time.monotonic() - start
            stats = api.stats
            self.last_report = {
                "actualizare": nr,
                "moment": dt_util.now().isoformat(timespec="seconds"),
                "durata_s": round(durata, 2),
                "solicitari_api": {k: stats[k] for k in ("total", "ok", "respinse", "esuate")},
                "asociatii": report,
                "probleme": [f"{SOURCE_LABELS.get(s, s)} | {w}: {m}" for s, w, m, _ in self._failures],
            }

            self._log_summary(nr, len(per_apartment), durata)
            if nr == 1:
                _LOGGER.info(oneline("Conectare reușită la e-bloc.ro", {
                    "Asociații": len(api.apartments),
                    "Apartamente": len(per_apartment),
                    "Durată actualizare": seconds(durata),
                }))

            return {
                "associations": api.associations,
                "apartments": api.apartments,
                "access_rights": api.access_rights,
                "access_by_asoc": {p: sorted(a) for p, a in api.access_by_asoc.items()},
                "home_ap_data": api.home_ap_data,
                "luna": api.luna_curenta,
                "per_apartment": per_apartment,
                "carduri": carduri,
                "get_info": api.get_info_data,
            }

        except UpdateFailed:
            raise
        except Exception as err:
            _LOGGER.exception("Actualizarea #%d a eșuat din cauza unei erori neprevăzute.", nr)
            raise UpdateFailed(f"Eroare neprevăzută: {type(err).__name__}: {err}") from err

    async def _async_update_association(
        self,
        id_asoc: Any,
        ap_list: list[dict[str, Any]],
        previous: dict[str, dict[str, Any]],
        report: dict[str, Any],
    ) -> dict[str, dict[str, Any]]:
        """Datele unei asociații: cererile comune o singură dată, apoi cele per apartament."""
        api = self.api_client
        where = f"asociația {id_asoc}"
        prev_asoc = next(
            (v for k, v in previous.items() if k.split(":", 1)[0] == str(id_asoc)), {}
        )
        skipped = {
            name: f"nu se citește (asociația nu are acces la pagina „{PAGE_LABELS[page]}”)"
            for name, page in SOURCE_PAGES.items()
            if not api.has_access(page, id_asoc)
        }

        # ── Surse comune asociației (doar paginile permise) ──
        shared_calls: dict[str, Any] = {}
        status_luna: dict[str, str] = {}
        if "contoare" not in skipped:
            luna, status_luna["contoare_luni"] = await self._async_luna_contoare(id_asoc)
            shared_calls["contoare"] = partial(api.async_get_contoare_index, id_asoc, luna)
        if "plateste" not in skipped:
            shared_calls["plateste"] = partial(api.async_get_plateste_ap, id_asoc)
        if "contact" not in skipped:
            shared_calls["contact"] = partial(api.async_get_contact_tickets, id_asoc)
        if "facturi" not in skipped:
            if api.luna_curenta:
                shared_calls["facturi"] = partial(api.async_get_facturi, id_asoc, api.luna_curenta)
            else:
                skipped["facturi"] = "nu se citește (luna curentă nu este cunoscută)"

        shared, shared_status = await self._fetch_all(shared_calls, prev_asoc, where)
        shared["ticket_details"] = await self._async_ticket_details(
            shared.get("contact", {}), prev_asoc.get("ticket_details", {})
        )

        # ── Surse per apartament ──
        result: dict[str, dict[str, Any]] = {}
        apartamente: dict[str, Any] = {}
        for ap in ap_list:
            id_ap = ap.get("id_ap", ap.get("id"))
            if not id_ap:
                continue
            key = f"{id_asoc}:{id_ap}"
            ap_where = f"asociația {id_asoc} | apartamentul {ap.get('ap', id_ap)}"
            calls: dict[str, Any] = {"plati": partial(api.async_get_istoric_plati, id_asoc, id_ap)}
            if "nr_pers" not in skipped:
                calls["nr_pers"] = partial(api.async_get_nr_pers, id_asoc, id_ap)
                calls["wallet"] = partial(api.async_get_wallet, id_asoc, id_ap)
            own, own_status = await self._fetch_all(calls, previous.get(key, {}), ap_where)
            result[key] = {**shared, **own}

            stare = {**status_luna, **shared_status, **own_status, **skipped}
            ordered = {
                SOURCE_LABELS[n]: stare[n]
                for n in ("contoare_luni", *SHARED_SOURCES, *APARTMENT_SOURCES)
                if n in stare
            }
            apartamente[str(id_ap)] = ordered
            self._log_apartment(key, ap_where, ordered)

        report[str(id_asoc)] = {
            "pagini_permise": api.pages_for(id_asoc),
            "apartamente": apartamente,
        }
        return result

    async def _async_luna_contoare(self, id_asoc: Any) -> tuple[str, str]:
        """Luna de citire a contoarelor (cea mai recentă din AppContoareGetIndexLuni) și starea ei."""
        data = await self._fetch(partial(self.api_client.async_get_contoare_luni, id_asoc))
        luni = (data.get("aLuni") or []) if is_ok(data) else []
        if luni:
            first = luni[0]
            luna = first if isinstance(first, str) else first.get("luna", "")
            if luna:
                return luna, luna
        fallback = self.api_client.luna_curenta or datetime.now().strftime("%Y-%m")
        if not is_ok(data):
            motiv = describe_result(data)
            self._failures.append(("contoare_luni", f"asociația {id_asoc}", motiv, False))
            return fallback, f"eroare ({motiv}); se folosește {fallback}"
        return fallback, f"răspunsul nu conține nicio lună; se folosește {fallback}"

    async def _async_ticket_details(
        self, contact: dict[str, Any], previous: dict[str, Any]
    ) -> dict[str, dict[str, Any]]:
        """Detaliile primelor tichete (starea deschis/închis)."""
        ids = [
            str(t.get("id_ticket", t.get("id")))
            for t in (contact.get("aTickets") or [])[:TICKET_DETAILS_MAX]
            if t.get("id_ticket", t.get("id"))
        ]
        if not ids:
            return {}
        results = await asyncio.gather(
            *(self._fetch(partial(self.api_client.async_get_ticket_detail, tid)) for tid in ids)
        )
        details = {}
        for tid, data in zip(ids, results):
            if is_ok(data):
                details[tid] = data
            elif tid in previous:
                details[tid] = previous[tid]
        return details

    async def _fetch_all(
        self, calls: dict[str, Any], previous: dict[str, Any], where: str
    ) -> tuple[dict[str, Any], dict[str, str]]:
        """Rulează solicitările în paralel; întoarce datele și starea fiecărei surse."""
        if not calls:
            return {}, {}
        results = await asyncio.gather(*(self._fetch(call) for call in calls.values()))
        data: dict[str, Any] = {}
        status: dict[str, str] = {}
        for name, response in zip(calls, results):
            data[name], status[name] = self._accept(name, response, previous.get(name), where)
        return data, status

    async def _fetch(self, call) -> dict[str, Any]:
        """O solicitare; la „nologin" (sesiune expirată în timpul actualizării) o singură
        autentificare nouă pe actualizare, apoi solicitarea se reia."""
        session_before = self.api_client.session_id
        data = await call()
        if data.get("result") != "nologin":
            return data
        async with self._relogin_lock:
            if self.api_client.session_id == session_before:
                if self._relogin_done:
                    return data
                self._relogin_done = True
                _LOGGER.debug("Sesiunea a expirat în timpul actualizării; se face o autentificare nouă.")
                if not await self.api_client.async_login():
                    return data
        return await call()

    def _accept(self, name: str, data: dict[str, Any], prev: Any, where: str) -> tuple[dict[str, Any], str]:
        """Datele valide se folosesc; altfel valoarea anterioară, iar problema se notează."""
        if is_ok(data):
            return data, STATUS_OK
        motiv = describe_result(data)
        self._failures.append((name, where, motiv, bool(prev)))
        if prev:
            return {**prev, "_anterior": True}, f"eroare ({motiv}); se păstrează valoarea anterioară"
        return {}, f"eroare ({motiv})"

    def _log_apartment(self, key: str, where: str, stare: dict[str, str]) -> None:
        """Raportul unui apartament, doar la prima actualizare sau când o sursă își schimbă starea."""
        if self._known_status.get(key) != stare:
            _LOGGER.debug(oneline(where[0].upper() + where[1:], stare))
        self._known_status[key] = stare

    def _log_summary(self, nr: int, apartamente: int, durata: float) -> None:
        """Rezumatul actualizării (DEBUG) și, dacă e cazul, sursele indisponibile (WARNING)."""
        stats = self.api_client.stats
        fields = [
            f"Actualizarea #{nr} finalizată",
            f"apartamente: {apartamente}",
            f"solicitări API: {stats['total']} (reușite {stats['ok']}, respinse {stats['respinse']}, "
            f"eșuate {stats['esuate']})",
        ]
        if stats["cea_mai_lenta"][0]:
            fields.append(f"cea mai lentă: {stats['cea_mai_lenta'][0]} ({seconds(stats['cea_mai_lenta'][1])})")
        fields.append(f"durată: {seconds(durata)}")
        _LOGGER.debug(" | ".join(fields))

        if self._failures:
            _LOGGER.warning(oneline(
                f"Actualizarea #{nr} finalizată parțial | surse indisponibile: {len(self._failures)}",
                [
                    (
                        f"{SOURCE_LABELS.get(sursa, sursa)} ({unde.replace(' | ', ', ')})",
                        f"{motiv}; se păstrează valoarea anterioară" if pastrat else motiv,
                    )
                    for sursa, unde, motiv, pastrat in self._failures
                ],
            ))

    def _log_layout_changes(self) -> None:
        """Semnalează asociațiile, apartamentele sau drepturile apărute ori retrase."""
        api = self.api_client
        layout = {
            str(id_asoc): (
                api.pages_for(id_asoc),
                sorted(str(ap.get("id_ap", ap.get("id"))) for ap in aps),
            )
            for id_asoc, aps in api.apartments.items()
        }
        if self._known_layout is None:
            for id_asoc, (pagini, aps) in layout.items():
                _LOGGER.debug(
                    "Asociația %s | apartamente: %s | pagini permise: %s",
                    id_asoc, ", ".join(aps) or "niciunul", _pages_text(pagini),
                )
        elif layout != self._known_layout:
            for id_asoc in sorted(set(layout) | set(self._known_layout)):
                old_p, old_a = self._known_layout.get(id_asoc, ([], []))
                new_p, new_a = layout.get(id_asoc, ([], []))
                schimbari: dict[str, str] = {}
                if set(new_a) != set(old_a):
                    schimbari["Apartamente"] = (
                        f"înainte {', '.join(old_a) or 'niciunul'}; acum {', '.join(new_a) or 'niciunul'}"
                    )
                if adaugate := sorted(set(new_p) - set(old_p)):
                    schimbari["Acces nou"] = _pages_text(adaugate)
                if retrase := sorted(set(old_p) - set(new_p)):
                    schimbari["Acces retras"] = _pages_text(retrase)
                if schimbari:
                    schimbari["Acțiune necesară"] = "reîncarcă integrarea pentru a actualiza entitățile"
                    _LOGGER.info(oneline(f"Asociația {id_asoc} s-a modificat", schimbari))
        self._known_layout = layout

    def _persist_session(self) -> None:
        """Salvează sesiunea în config entry, ca repornirea să nu ceară o autentificare nouă."""
        session_data = self.api_client.export_session()
        if not session_data or self._config_entry is None:
            return
        current = dict(self._config_entry.data)
        if (current.get("session_data") or {}).get("session_id") == session_data["session_id"]:
            return
        current["session_data"] = session_data
        self.hass.config_entries.async_update_entry(self._config_entry, data=current)
        _LOGGER.debug("Sesiune nouă salvată în configurația intrării.")


def _pages_text(pagini: list[str]) -> str:
    return ", ".join(sorted(PAGE_LABELS.get(p, p) for p in pagini)) or "niciuna"
