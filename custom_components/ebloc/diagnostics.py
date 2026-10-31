"""
Diagnosticare pentru integrarea E-bloc România.

Exportă informații de diagnostic pentru support tickets:
- Licență (fingerprint, status, cheie mascată)
- Apartamente active și senzori
- Starea coordinator-ului

Datele sensibile (parolă, token-uri, session_id) sunt excluse.
"""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import APP_VERSION, DOMAIN, LICENSE_DATA_KEY, OS_VERSION
from .helpers import mascheaza_email


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> dict[str, Any]:
    """Returnează datele de diagnostic pentru E-bloc România."""

    # ── Licență (fingerprint TRUNCHIAT + cheie mascată) ──
    # Fingerprint-ul complet NU apare în diagnostic: diagnosticele se atașează la
    # issue-uri publice, iar serverul de licențe acceptă cereri semnate cu fingerprint.
    license_mgr = hass.data.get(DOMAIN, {}).get(LICENSE_DATA_KEY)
    licenta_info: dict[str, Any] = {}
    if license_mgr:
        licenta_info = {
            "fingerprint": license_mgr.fingerprint[:16] + "...",
            "status": license_mgr.status,
            "license_key": license_mgr.license_key_masked,
            "is_valid": license_mgr.is_valid,
            "license_type": license_mgr.license_type,
        }

    # ── Coordinator ──
    runtime = getattr(entry, "runtime_data", None)
    coordinator_info: dict[str, Any] = {}
    if runtime and hasattr(runtime, "coordinator") and runtime.coordinator:
        coordinator = runtime.coordinator
        coordinator_info = {
            "last_update_success": coordinator.last_update_success,
            "update_interval": str(coordinator.update_interval),
            "versiune_aplicatie_trimisa": APP_VERSION,
            "versiune_android_trimisa": OS_VERSION,
        }
        data = coordinator.data or {}
        coordinator_info["associations_count"] = len(data.get("associations", []))
        coordinator_info["apartments"] = {
            str(k): len(v) for k, v in data.get("apartments", {}).items()
        }
        coordinator_info["per_apartment_keys"] = list(
            data.get("per_apartment", {}).keys()
        )
        coordinator_info["luna"] = data.get("luna", "")
        # Paginile permise pe fiecare asociație (aAccesPage* din AppGetInfo)
        coordinator_info["acces_pe_asociatie"] = data.get("access_by_asoc", {})
        # Ce s-a citit la ultima actualizare: surse ok / din actualizarea anterioară / indisponibile
        coordinator_info["ultima_actualizare"] = getattr(coordinator, "last_report", {})

    # ── Senzori activi ──
    senzori_activi = sorted(
        entitate.entity_id
        for entitate in hass.states.async_all("sensor")
        if entitate.entity_id.startswith(f"sensor.{DOMAIN}_")
    )

    butoane_active = sorted(
        entitate.entity_id
        for entitate in hass.states.async_all("button")
        if entitate.entity_id.startswith(f"button.{DOMAIN}_")
    )

    numbers_activi = sorted(
        entitate.entity_id
        for entitate in hass.states.async_all("number")
        if entitate.entity_id.startswith(f"number.{DOMAIN}_")
    )

    # ── Config entry (fără date sensibile) ──
    return {
        "intrare": {
            "titlu": entry.title.replace(
                entry.data.get("email", "") or "\0", mascheaza_email(entry.data.get("email", ""))
            ),
            "versiune": entry.version,
            "domeniu": DOMAIN,
            "email": mascheaza_email(entry.data.get("email", "")),
            "update_interval": entry.data.get("update_interval"),
        },
        "licenta": licenta_info,
        "coordinator": coordinator_info,
        "stare": {
            "senzori_activi": len(senzori_activi),
            "lista_senzori": senzori_activi,
            "butoane_active": len(butoane_active),
            "lista_butoane": butoane_active,
            "numbers_activi": len(numbers_activi),
            "lista_numbers": numbers_activi,
        },
    }


