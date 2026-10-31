"""Funcții helper comune pentru integrarea E-bloc România.

Centralizează utilitarele folosite de sensor.py, button.py, number.py, diagnostics.py:
  - is_license_valid: verificare licență
  - luna_ro: conversie dată '2026-02-26' → '26 februarie 2026'
  - luna_ro_scurt: conversie lună '2026-02' → 'februarie'
  - mascheaza_email: maschează email-ul pentru diagnostic și log
  - association_device: dispozitivul unei asociații (același în toate platformele)
  - contor_suffix: sufixul entităților de contor când contul are mai multe apartamente
    în aceeași asociație
"""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo

from .const import (
    DOMAIN,
    LICENSE_DATA_KEY,
    LUNI_RO,
)


def association_device(id_user: Any, id_asoc: Any) -> DeviceInfo:
    """Un dispozitiv per asociație."""
    return DeviceInfo(
        identifiers={(DOMAIN, f"{id_user}_{id_asoc}")},
        name=f"E-bloc România ({id_asoc})",
        manufacturer="Ciprian Nicolae (cnecrea)",
        model="E-bloc România",
        entry_type=DeviceEntryType.SERVICE,
    )


def contor_suffix(data: dict[str, Any], id_asoc: Any, id_ap: Any) -> str:
    """„" când contul are un singur apartament în asociație, altfel „_<id_ap>".

    Contoarele erau identificate doar prin asociație + contor; cu două apartamente în
    aceeași asociație, identificatorii se suprapuneau. Pentru un singur apartament
    (cazul obișnuit) identificatorii rămân cei vechi, deci entitățile existente nu se schimbă.
    """
    apartments = (data or {}).get("apartments", {})
    aps = apartments.get(id_asoc, apartments.get(str(id_asoc), []))
    return f"_{id_ap}" if len(aps) > 1 else ""


def index_for(contoare: dict[str, Any], id_contor: Any, id_ap: Any) -> dict[str, Any] | None:
    """Intrarea din aInfoIndex pentru contorul și apartamentul dat.

    aInfoContoare descrie contoarele asociației; aInfoIndex are câte o intrare pe
    fiecare (apartament, contor).
    """
    fallback = None
    for cv in (contoare or {}).get("aInfoIndex") or []:
        if str(cv.get("id_contor")) != str(id_contor):
            continue
        if str(cv.get("id_ap", "")) == str(id_ap):
            return cv
        if not cv.get("id_ap"):
            fallback = cv
    return fallback


def is_license_valid(hass: HomeAssistant) -> bool:
    """Verifică dacă licența este validă (folosit în async_setup_entry)."""
    mgr = hass.data.get(DOMAIN, {}).get(LICENSE_DATA_KEY)
    if mgr is None:
        return False
    return mgr.is_valid


def luna_ro(luna_str: str) -> str:
    """Convertește dată sau lună în format românesc.

    '2026-02-26'          → '26 februarie 2026'
    '2026-02-26 14:30:00' → '26 februarie 2026'
    '2026-02'             → 'februarie 2026'
    """
    if not luna_str:
        return ""
    parts = str(luna_str).split(" ", 1)[0].split("-")
    if len(parts) == 3:
        an, luna, zi = parts
        luna_name = LUNI_RO.get(luna, luna)
        return f"{int(zi)} {luna_name} {an}" if zi.isdigit() else str(luna_str)
    if len(parts) == 2:
        an, luna = parts
        luna_name = LUNI_RO.get(luna, luna)
        return f"{luna_name} {an}"
    return luna_str


def luna_ro_scurt(luna_str: str) -> str:
    """Convertește '2026-02' → 'februarie' (doar numele lunii)."""
    if not luna_str:
        return ""
    parts = luna_str.split("-")
    if len(parts) >= 2:
        return LUNI_RO.get(parts[1], parts[1])
    return luna_str


def mascheaza_email(email: str) -> str:
    """Maschează email-ul păstrând prima literă și domeniul.

    'cnecrea@gmail.com' → 'c******@gmail.com'
    """
    if not email or "@" not in email:
        return "***"
    local, domain = email.split("@", 1)
    if len(local) <= 1:
        return f"*@{domain}"
    return f"{local[0]}{'*' * (len(local) - 1)}@{domain}"
