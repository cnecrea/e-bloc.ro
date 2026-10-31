"""Constante pentru integrarea E-bloc România."""

from __future__ import annotations

from typing import Final

from homeassistant.const import Platform

DOMAIN: Final = "ebloc"
PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.BUTTON, Platform.NUMBER]

# ── API e-bloc.ro ──────────────────────────────────────────
# Valorile trimise de aplicația Android e-bloc.ro v9.12 (verificate în APK-ul decompilat).
BASE_URL: Final = "https://www.e-bloc.ro/ajax"
READ_URL: Final = "https://read.e-bloc.ro/ajax"
PUBLIC_IP_URL: Final = "https://upload.xisoft.net/XiGetRealIp.php"
API_KEY: Final = "58126855-ef70-4548-a35e-eca020c24570"
APP_VERSION: Final = "9.12"  # serverul cere minimum 8.96 (min_version din AppGetInfo)
OS_VERSION: Final = "34"  # aplicația trimite nivelul API Android (Android 14 = 34)
DEVICE_BRAND: Final = "samsung"
DEVICE_MODEL: Final = "SM-S928B"
DEVICE_TYPE: Final = 1  # 1 = telefon, 2 = tabletă

USER_AGENT: Final = (
    "Dalvik/2.1.0 (Linux; U; Android 14; SM-S928B Build/UP1A.231005.007)"
)
HEADERS: Final = {
    "User-Agent": USER_AGENT,
    "Accept": "application/json",
    "Accept-Language": "ro-RO,ro;q=0.9",
}

# ── Timeout (secunde) ────────────────────────────────────────
API_TIMEOUT: Final = 30
PUBLIC_IP_TIMEOUT: Final = 3  # aplicația așteaptă 2 s; IP-ul e opțional
PUBLIC_IP_MAX_AGE: Final = 6 * 3600  # IP-ul public se cere din nou după 6 ore

# ── Drepturi de acces (aAccesPage* din AppGetInfo) ─────────
# Dreptul e per asociație: o pagină e disponibilă doar pentru asociațiile din lista ei.
# Pentru celelalte, serverul răspunde „nologin" (nu „deny").
ACCESS_PAGES: Final = {
    "aAccesPageHome": "home",
    "aAccesPageAddChitanta": "add_chitanta",
    "aAccesPageChitante": "chitante",
    "aAccesPageAvizier": "avizier",
    "aAccesPageIndex": "index",
    "aAccesPageFacturi": "facturi",
    "aAccesPageDocumente": "documente",
    "aAccesPagePlateste": "plateste",
    "aAccesPageContact": "contact",
    "aAccesPageSolduri": "solduri",
    "aAccesPageInfo": "info",
    "aAccesPageLog": "log",
}
# Numele paginilor, așa cum apar în aplicație (pentru log)
PAGE_LABELS: Final = {
    "home": "acasă",
    "add_chitanta": "adăugare chitanță",
    "chitante": "chitanțe",
    "avizier": "avizier",
    "index": "contoare",
    "facturi": "facturi",
    "documente": "documente",
    "plateste": "plătește",
    "contact": "contact",
    "solduri": "solduri",
    "info": "informații",
    "log": "jurnal",
}

# ── Configurare ────────────────────────────────────────────
CONF_EMAIL: Final = "email"
CONF_PASSWORD: Final = "password"
CONF_LICENSE_KEY: Final = "license_key"

# ── Licențiere ─────────────────────────────────────────────
LICENSE_DATA_KEY: Final = "ebloc_license_manager"

# ── Update intervals (secunde) ─────────────────────────────
DEFAULT_UPDATE_INTERVAL: Final = 43200  # 12 ore (default)
MIN_UPDATE_INTERVAL: Final = 43200  # 12 ore (minim)
MAX_UPDATE_INTERVAL: Final = 86400  # 24 ore (maxim)
CONF_UPDATE_INTERVAL: Final = "update_interval"

# ── Luni în română ────────────────────────────────────────
LUNI_RO: Final = {
    "01": "ianuarie", "02": "februarie", "03": "martie",
    "04": "aprilie", "05": "mai", "06": "iunie",
    "07": "iulie", "08": "august", "09": "septembrie",
    "10": "octombrie", "11": "noiembrie", "12": "decembrie",
}

# ── Sentinel values ────────────────────────────────────────
INDEX_NOT_SET: Final = -999999999
NR_PERS_MULTIPLIER: Final = 1000
AMOUNT_DIVISOR: Final = 100  # total_plata e in bani (37505 = 375.05 Lei)

# ── Defaults ───────────────────────────────────────────────
DEFAULT_NR_PERS: Final = 0
DEFAULT_INDEX: Final = 0
MAX_NR_PERS: Final = 10

# ── Atribuție ─────────────────────────────────────────────
ATTRIBUTION: Final = "Date furnizate de e-bloc.ro"

LICENSE_PURCHASE_URL: Final = "https://hubinteligent.org/donate?ref=ebloc"
