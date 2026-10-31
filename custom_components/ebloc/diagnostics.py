"""
Diagnosticare pentru integrarea E-bloc România.

Exportă informații de diagnostic pentru support tickets:
- Intrarea și versiunile (integrare, Home Assistant, aplicația e-bloc trimisă serverului)
- Licență (fingerprint trunchiat, status, cheie mascată, expirare, eșecuri ale serverului)
- Contul: sesiune (doar dacă există), asociații, apartamente, carduri salvate
- Ultima actualizare: durată, solicitări API, cea mai lentă solicitare, surse indisponibile
- Pe fiecare asociație: paginile permise, perioada de citire, structura datelor comune
- Pe fiecare apartament: starea fiecărei surse, contoarele, structura datelor proprii
- Problemele active din Reparații și entitățile intrării (active, dezactivate, indisponibile, orfane)

Diagnosticul se atașează la issue-uri publice, deci nu conține sume, indexuri, nume, adrese,
numărul de persoane, sesiunea sau emailul complet. ID-ul utilizatorului e mascat peste tot,
inclusiv în entity ID-uri. Din răspunsurile e-bloc.ro apar doar numele câmpurilor și numărul de elemente.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import __version__ as HA_VERSION
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.loader import async_get_integration

from .const import APP_VERSION, DOMAIN, INDEX_NOT_SET, LICENSE_DATA_KEY, OS_VERSION, PAGE_LABELS
from .coordinator import APARTMENT_SOURCES, SHARED_SOURCES, SOURCE_LABELS
from .helpers import index_for, mascheaza_email
from .logfmt import short_id


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> dict[str, Any]:
    """Returnează datele de diagnostic pentru E-bloc România."""
    runtime = getattr(entry, "runtime_data", None)
    coordinator = getattr(runtime, "coordinator", None)
    api = getattr(coordinator, "api_client", None)
    masca = _Masca(entry, api)

    try:
        versiune_integrare = str((await async_get_integration(hass, DOMAIN)).version)
    except Exception:  # noqa: BLE001
        versiune_integrare = None

    session_data = entry.data.get("session_data") or {}
    return {
        "intrare": {
            "titlu": masca.text(entry.title),
            "versiune": entry.version,
            "versiune_integrare": versiune_integrare,
            "versiune_home_assistant": HA_VERSION,
            "domeniu": DOMAIN,
            "email": mascheaza_email(entry.data.get("email", "")),
            "update_interval": entry.data.get("update_interval"),
            "sesiune_salvata": bool(session_data.get("session_id")),
            "sesiune_salvata_la": _iso(session_data.get("timestamp")),
        },
        "licenta": _sigur(_licenta, hass),
        "cont": _sigur(_cont, coordinator, masca) if coordinator else None,
        "actualizare": _sigur(_actualizare, coordinator, masca) if coordinator else None,
        "asociatii": _sigur(_asociatii, coordinator) if coordinator else {},
        "probleme_active": _sigur(_probleme_active, hass),
        "entitati": _sigur(_entitati, hass, entry, masca),
    }


def _sigur(fn, *args) -> Any:
    """O secțiune cu date neașteptate nu strică tot diagnosticul."""
    try:
        return fn(*args)
    except Exception as err:  # noqa: BLE001
        return {"eroare_diagnostic": type(err).__name__}


# ══════════════════════════════════════════════
# Licență și cont
# ══════════════════════════════════════════════

def _licenta(hass: HomeAssistant) -> dict[str, Any]:
    """Starea licenței. Fingerprint-ul complet NU apare: diagnosticele se atașează la
    issue-uri publice, iar serverul de licențe acceptă cereri semnate cu fingerprint."""
    mgr = hass.data.get(DOMAIN, {}).get(LICENSE_DATA_KEY)
    if not mgr:
        return {}
    return {
        "fingerprint": short_id(mgr.fingerprint, 16),
        "status": mgr.status,
        "license_key": mgr.license_key_masked,
        "is_valid": mgr.is_valid,
        "license_type": mgr.license_type,
        "zile_ramase_evaluare": mgr.trial_days_remaining if mgr.status == "trial" else None,
        "activata_la": _iso(mgr.activated_at),
        "expira_la": _iso(mgr.license_expires_at),
        # câte verificări consecutive la serverul de licențe au eșuat (0 = serverul răspunde)
        "esecuri_consecutive_server": getattr(mgr, "_consecutive_failures", None),
    }


def _cont(coordinator, masca: _Masca) -> dict[str, Any]:
    """Contul e-bloc.ro: autentificare, versiuni trimise, asociații, apartamente."""
    api = coordinator.api_client
    data = coordinator.data or {}
    info = api.get_info_data or {}
    return {
        "autentificat": api.is_authenticated,
        "versiune_aplicatie_trimisa": APP_VERSION,
        "versiune_android_trimisa": OS_VERSION,
        "versiune_minima_server": info.get("min_version"),
        "luna_curenta": api.luna_curenta or None,
        "asociatii": len(api.associations),
        "apartamente": sum(len(aps) for aps in api.apartments.values()),
        "carduri_salvate": len((data.get("carduri") or {}).get("aCard") or []),
        "ultima_eroare": masca.text(api.last_error) or None,
        "probleme_cont": [
            masca.text(f"asociația {id_asoc}: {motiv}")
            for _sursa, id_asoc, motiv in getattr(api, "account_problems", [])
        ],
        "campuri_get_info": sorted(info.keys()),
    }


def _actualizare(coordinator, masca: _Masca) -> dict[str, Any]:
    """Starea coordinatorului și rezumatul ultimei actualizări."""
    interval = coordinator.update_interval
    exc = coordinator.last_exception
    report = coordinator.last_report or {}
    endpoint, durata = (coordinator.api_client.stats or {}).get("cea_mai_lenta") or ("", 0.0)
    return {
        "last_update_success": coordinator.last_update_success,
        "ultima_eroare": masca.text(f"{type(exc).__name__}: {exc}") if exc else None,
        "interval_secunde": int(interval.total_seconds()) if interval else None,
        "actualizari_efectuate": getattr(coordinator, "_update_count", None),
        "ultima_finalizata": report.get("actualizare"),
        "moment": report.get("moment"),
        "durata_s": report.get("durata_s"),
        "solicitari_api": report.get("solicitari_api"),
        "cea_mai_lenta_solicitare": {"endpoint": endpoint, "durata_s": round(durata, 2)} if endpoint else None,
        "surse_indisponibile": [masca.text(p) for p in report.get("probleme", [])],
    }


# ══════════════════════════════════════════════
# Asociații și apartamente
# ══════════════════════════════════════════════

def _asociatii(coordinator) -> dict[str, Any]:
    """Pe fiecare asociație: drepturi, perioada de citire, sursele comune și apartamentele."""
    api = coordinator.api_client
    data = coordinator.data or {}
    per_apartment = data.get("per_apartment", {})
    report = (coordinator.last_report or {}).get("asociatii", {})

    out: dict[str, Any] = {}
    for id_asoc, ap_list in data.get("apartments", {}).items():
        home = data.get("home_ap_data", {}).get(id_asoc, {})
        aps = [(ap, per_apartment.get(f"{id_asoc}:{_id_ap(ap)}", {})) for ap in ap_list if _id_ap(ap)]
        # sursele comune sunt aceleași în datele fiecărui apartament din asociație
        comune = next((d for _ap, d in aps if d), {})
        stari = report.get(str(id_asoc), {}).get("apartamente", {})
        out[f"asociația {id_asoc}"] = {
            "pagini_permise": sorted(PAGE_LABELS.get(p, p) for p in api.pages_for(id_asoc)),
            "luna": home.get("luna") or None,
            "perioada_citire": {
                "inceput": home.get("indecsi_start") or None,
                "sfarsit": home.get("indecsi_end") or None,
            },
            "poate_modifica_indexul": str(home.get("can_edit_index", "0")) == "1",
            "data_scadenta": home.get("data_scadenta") or None,
            "surse_comune": {
                SOURCE_LABELS[s]: _rezumat(comune[s]) for s in SHARED_SOURCES if s in comune
            },
            "detalii_tichete_citite": len(comune.get("ticket_details") or {}),
            "apartamente": {
                f"apartamentul {ap.get('ap', _id_ap(ap))}": _apartament(ap, d, stari.get(str(_id_ap(ap))))
                for ap, d in aps
            },
        }
    return out


def _apartament(ap: dict[str, Any], data: dict[str, Any], stare: dict[str, str] | None) -> dict[str, Any]:
    """Starea surselor la ultima actualizare, contoarele și structura datelor proprii."""
    id_ap = _id_ap(ap)
    contoare = data.get("contoare") or {}
    indexuri = [
        cv for cd in contoare.get("aInfoContoare") or []
        if (cv := index_for(contoare, cd.get("id_contor"), id_ap))
    ]
    return {
        "id_ap": id_ap,
        "stare_surse": stare,
        "contoare": {
            "definite_in_asociatie": len(contoare.get("aInfoContoare") or []),
            "cu_index_pentru_apartament": len(indexuri),
            "editabile": sum(1 for cv in indexuri if str(cv.get("right_edit_index", "0")) == "1"),
            "cu_index_nou_trimis": sum(1 for cv in indexuri if _are_index(cv.get("index_nou"))),
        } if contoare else None,
        "surse_proprii": {
            SOURCE_LABELS[s]: _rezumat(data[s]) for s in APARTMENT_SOURCES if s in data
        },
        "campuri_apartament": sorted(ap.keys()),
    }


def _rezumat(value: Any) -> Any:
    """Doar forma unui răspuns e-bloc.ro: rezultatul, listele (cu numărul de elemente și
    numele câmpurilor) și numele celorlalte câmpuri. Valorile nu apar."""
    if not isinstance(value, dict):
        return type(value).__name__
    rez: dict[str, Any] = {"rezultat": value.get("result")}
    if value.get("_anterior"):
        rez["din_actualizarea_anterioara"] = True
    liste = {
        k: {"elemente": len(v), "campuri": _campuri(v)}
        for k, v in sorted(value.items()) if isinstance(v, list)
    }
    if liste:
        rez["liste"] = liste
    rez["campuri"] = sorted(
        k for k, v in value.items() if not isinstance(v, list) and k not in ("result", "_anterior")
    )
    return rez


def _campuri(items: Any) -> list[str]:
    """Reuniunea numelor de câmpuri dintr-o listă de dict-uri."""
    return sorted({k for i in items if isinstance(i, dict) for k in i.keys()})


def _id_ap(ap: dict[str, Any]) -> Any:
    return ap.get("id_ap", ap.get("id"))


def _are_index(raw: Any) -> bool:
    try:
        return int(raw) != INDEX_NOT_SET
    except (TypeError, ValueError):
        return False


def _iso(value: Any) -> str | None:
    """Timestamp Unix → ISO (UTC); alte valori rămân text."""
    if value in (None, "", 0):
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, timezone.utc).isoformat(timespec="seconds")
    return str(value)


# ══════════════════════════════════════════════
# Reparații și entități
# ══════════════════════════════════════════════

def _probleme_active(hass: HomeAssistant) -> list[str]:
    """Problemele integrării afișate în Setări → Reparații (de ex. licența expirată)."""
    return sorted(
        issue_id for (domeniu, issue_id), issue in ir.async_get(hass).issues.items()
        if domeniu == DOMAIN and issue.active
    )


def _entitati(hass: HomeAssistant, entry: ConfigEntry, masca: _Masca) -> dict[str, Any]:
    """Entitățile acestei intrări din registru, cu starea lor.

    „orfană" = există în registru, dar integrarea nu o mai creează (de ex. contoarele unui
    apartament care primesc identificator nou); HA îi păstrează doar o stare restaurată.
    """
    registry = er.async_get(hass)
    entitati: dict[str, dict[str, str]] = {}
    numar: dict[str, int] = {}
    for reg in er.async_entries_for_config_entry(registry, entry.entry_id):
        state = hass.states.get(reg.entity_id)
        if reg.disabled_by:
            stare = "dezactivată"
        elif state is None or state.attributes.get("restored"):
            stare = "orfană"
        elif state.state == "unavailable":
            stare = "indisponibilă"
        else:
            stare = "activă"
        entitati.setdefault(reg.domain, {})[masca.text(reg.entity_id)] = stare
        numar[stare] = numar.get(stare, 0) + 1

    return {
        "numar": numar,
        **{domeniu: dict(sorted(lista.items())) for domeniu, lista in sorted(entitati.items())},
    }


# ══════════════════════════════════════════════
# Mascare
# ══════════════════════════════════════════════

class _Masca:
    """Ascunde emailul, sesiunea și ID-ul utilizatorului din texte (titlu, erori, entity ID-uri)."""

    def __init__(self, entry: ConfigEntry, api) -> None:
        session_data = entry.data.get("session_data") or {}
        email = entry.data.get("email", "")
        session_id = (api.session_id if api else None) or session_data.get("session_id")
        id_user = (api.id_user if api else None) or session_data.get("id_user")

        inlocuiri: dict[str, str] = {}
        if email:
            inlocuiri[email] = mascheaza_email(email)
        if session_id:
            inlocuiri[str(session_id)] = "***"
        # ID-ul e numeric; un ID foarte scurt s-ar confunda cu alte numere din text
        if id_user and len(str(id_user)) >= 4:
            inlocuiri[str(id_user)] = "***"
        # cele mai lungi întâi, ca o valoare să nu fie înlocuită parțial de alta
        self._inlocuiri = sorted(inlocuiri.items(), key=lambda kv: len(kv[0]), reverse=True)

    def text(self, text: str | None) -> str:
        text = text or ""
        for valoare, masca in self._inlocuiri:
            text = text.replace(valoare, masca)
        return text
