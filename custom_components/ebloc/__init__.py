"""Inițializarea integrării E-bloc România."""

import json
import asyncio
import logging
from dataclasses import dataclass

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import issue_registry as ir
from homeassistant.components import persistent_notification

from .const import (
    CONF_EMAIL,
    CONF_PASSWORD,
    CONF_UPDATE_INTERVAL,
    DEFAULT_UPDATE_INTERVAL,
    DOMAIN,
    LICENSE_DATA_KEY,
    LICENSE_PURCHASE_URL,
    PLATFORMS,
)
from .api import EblocApiClient
from .coordinator import EblocCoordinator
from .license import LicenseManager

_LOGGER = logging.getLogger(__name__)

# Stările în care o intrare încă folosește managerul de licențe
_STARI_ACTIVE = (
    ConfigEntryState.LOADED,
    ConfigEntryState.SETUP_IN_PROGRESS,
    ConfigEntryState.SETUP_RETRY,
    ConfigEntryState.UNLOAD_IN_PROGRESS,
)

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


@dataclass
class EblocRuntimeData:
    """Structură tipizată pentru datele runtime ale integrării."""

    coordinator: EblocCoordinator | None = None
    api_client: EblocApiClient | None = None
    # Configurația (fără sesiunea e-bloc) la momentul setup-ului — vezi _async_update_options
    config_signature: str = ""


def _config_signature(entry: ConfigEntry) -> str:
    """Amprenta configurației care contează pentru reload.

    `session_data` e excluse: coordinatorul salvează sesiunea e-bloc în
    entry.data la fiecare re-login, iar asta NU trebuie să reîncarce integrarea.
    """
    data = {k: v for k, v in entry.data.items() if k != "session_data"}
    return json.dumps([data, dict(entry.options)], sort_keys=True, default=str)


async def async_setup(hass: HomeAssistant, config: dict):
    """Configurează integrarea globală E-bloc România."""
    return True


def _update_license_notifications(hass: HomeAssistant, mgr: LicenseManager) -> None:
    """Creează sau șterge notificările de expirare licență/trial."""
    if mgr.is_valid:
        ir.async_delete_issue(hass, DOMAIN, "trial_expired")
        ir.async_delete_issue(hass, DOMAIN, "license_expired")
        persistent_notification.async_dismiss(hass, "ebloc_license_expired")
        return

    if mgr.has_license:
        issue_id = "license_expired"
        notif_title = "E-bloc România — Licența a expirat"
        notif_message = (
            "Licența pentru integrarea **E-bloc România** a expirat.\n\n"
            "Senzorii sunt dezactivați până la reînnoirea licenței.\n\n"
            f"[Reînnoiește licența]({LICENSE_PURCHASE_URL})"
        )
    else:
        issue_id = "trial_expired"
        notif_title = "E-bloc România — Licența de probă a expirat"
        notif_message = (
            "Perioada de evaluare gratuită pentru integrarea **E-bloc România** s-a încheiat.\n\n"
            "Senzorii sunt dezactivați până la obținerea unei licențe.\n\n"
            f"[Obține o licență acum]({LICENSE_PURCHASE_URL})"
        )

    other_id = "license_expired" if issue_id == "trial_expired" else "trial_expired"
    ir.async_delete_issue(hass, DOMAIN, other_id)

    ir.async_create_issue(
        hass,
        DOMAIN,
        issue_id,
        is_fixable=False,
        is_persistent=True,
        learn_more_url=LICENSE_PURCHASE_URL,
        severity=ir.IssueSeverity.WARNING,
        translation_key=issue_id,
        translation_placeholders={"learn_more_url": LICENSE_PURCHASE_URL},
    )

    persistent_notification.async_create(
        hass,
        notif_message,
        title=notif_title,
        notification_id="ebloc_license_expired",
    )

    _LOGGER.debug("[E-bloc] Notificare expirare creată: %s", issue_id)


def _cleanup_domain(hass: HomeAssistant) -> None:
    """Oprește verificarea licenței și eliberează datele domeniului."""
    domain_data = hass.data.pop(DOMAIN, None) or {}
    cancel_check = domain_data.pop("_cancel_license_check", None)
    if cancel_check:
        cancel_check()


def _schedule_license_check(hass: HomeAssistant, mgr: LicenseManager) -> None:
    """Programează următoarea verificare a licenței (un singur timer, reprogramat mereu).

    Momentul vine de la server: imediat după `valid_until`, sau cu backoff
    (60s → 1h) cât timp serverul nu răspunde. La fiecare verificare: /check
    (+ /validate pentru licențe); dacă validitatea s-a schimbat → notificări
    actualizate + reload.
    """
    if hass.is_stopping:
        return  # la oprire sesiunea HTTP se închide — nu mai verificăm
    domain_data = hass.data.get(DOMAIN)
    if domain_data is None or domain_data.get(LICENSE_DATA_KEY) is not mgr:
        return
    cancel_prev = domain_data.pop("_cancel_license_check", None)
    if cancel_prev:
        cancel_prev()

    delay = mgr.check_interval_seconds + (2 if not mgr.needs_heartbeat else 0)

    async def _async_check() -> None:
        try:
            was_valid = mgr.is_valid
            if mgr.needs_heartbeat:
                await mgr.async_heartbeat()
            now_valid = mgr.is_valid
            if hass.data.get(DOMAIN, {}).get(LICENSE_DATA_KEY) is not mgr:
                return  # integrarea a fost descărcată în timpul verificării
            if was_valid != now_valid:
                if now_valid:
                    _LOGGER.info("[Ebloc] Licența a redevenit validă — reîncarc")
                else:
                    _LOGGER.warning("[Ebloc] Licența a devenit invalidă — reîncarc")
                _update_license_notifications(hass, mgr)
                await mgr._async_reload_entries()
        except Exception:  # noqa: BLE001 — timer-ul nu trebuie să se oprească
            _LOGGER.exception("[Ebloc] Eroare la verificarea periodică a licenței")
        finally:
            _schedule_license_check(hass, mgr)

    @callback
    def _on_timer(_now) -> None:
        domain_now = hass.data.get(DOMAIN, {})
        domain_now.pop("_cancel_license_check", None)
        if hass.is_stopping or domain_now.get(LICENSE_DATA_KEY) is not mgr:
            return
        if not any(
            e.state in _STARI_ACTIVE for e in hass.config_entries.async_entries(DOMAIN)
        ):
            # Ex: intrare dezactivată cât era în „setup retry" (HA nu apelează unload)
            _cleanup_domain(hass)
            return
        # Task de fundal: pornirea/oprirea Home Assistant nu așteaptă
        # niciodată serverul de licențe (issue #329).
        hass.async_create_background_task(
            _async_check(), f"{DOMAIN}_license_check"
        )

    domain_data["_cancel_license_check"] = async_call_later(hass, delay, _on_timer)


async def _async_wait_license_loaded(hass: HomeAssistant) -> None:
    """Așteaptă încărcarea managerului de licențe pornită de altă intrare.

    La pornirea HA intrările aceluiași domeniu se configurează în paralel. Fără
    această așteptare, a doua intrare găsea managerul încă neîncărcat (licență
    „invalidă") → fără date, doar „Licență necesară" și senzorii reali scoși din
    registry, până la un reload manual (issue reteleelectrice#15).
    """
    load = hass.data.get(DOMAIN, {}).get("_license_load")
    if load is not None and not load.done():
        try:
            await asyncio.shield(load)
        except Exception as err:
            raise ConfigEntryNotReady("Managerul de licențe nu s-a putut încărca") from err
    if LICENSE_DATA_KEY not in hass.data.get(DOMAIN, {}):
        raise ConfigEntryNotReady("Managerul de licențe nu s-a putut încărca")


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry):
    """Configurează integrarea pentru o anumită intrare (config entry)."""
    _LOGGER.info(
        "[Ebloc] Se configurează integrarea (entry_id=%s).",
        entry.entry_id,
    )

    hass.data.setdefault(DOMAIN, {})

    # ── Inițializare License Manager (o singură instanță per domeniu) ──
    if LICENSE_DATA_KEY not in hass.data[DOMAIN]:
        license_mgr = LicenseManager(hass)
        # Referința se setează ÎNAINTE de async_load(): async_load() face await HTTP,
        # iar intrările configurate în paralel nu trebuie să creeze manageri duplicați.
        hass.data[DOMAIN][LICENSE_DATA_KEY] = license_mgr
        # Încărcarea e un task comun: intrările configurate în paralel o așteaptă
        # (vezi _async_wait_license_loaded) în loc să folosească managerul neîncărcat.
        load = hass.async_create_task(license_mgr.async_load(), f"{DOMAIN} license load")
        hass.data[DOMAIN]["_license_load"] = load
        try:
            await load
        except Exception:
            hass.data[DOMAIN].pop(LICENSE_DATA_KEY, None)
            hass.data[DOMAIN].pop("_license_load", None)
            raise
        _LOGGER.debug(
            "[Ebloc] LicenseManager: status=%s, valid=%s, fingerprint=%s...",
            license_mgr.status,
            license_mgr.is_valid,
            license_mgr.fingerprint[:16],
        )
        _schedule_license_check(hass, license_mgr)

        # Notificare re-enable
        if hass.data.pop(f"{DOMAIN}_was_disabled", False):
            await license_mgr.async_notify_event("integration_enabled")
    else:
        await _async_wait_license_loaded(hass)

    license_mgr = hass.data[DOMAIN][LICENSE_DATA_KEY]
    if not license_mgr.is_valid:
        _LOGGER.warning(
            "[Ebloc] Integrarea nu are licență validă. "
            "Senzorii vor afișa 'Licență necesară'."
        )
    # La FIECARE setup — după activare, reload-ul intrărilor șterge notificările
    # chiar dacă managerul de licențe rămâne același (mai multe conturi).
    _update_license_notifications(hass, license_mgr)

    # ── Client API ──
    session = async_get_clientsession(hass)
    email = entry.data[CONF_EMAIL]
    password = entry.data[CONF_PASSWORD]

    api_client = EblocApiClient(session, email, password)

    # Injectăm sesiunea salvată (dacă există)
    saved_session = entry.data.get("session_data")
    if saved_session:
        api_client.inject_session(saved_session)
        _LOGGER.debug("[Ebloc] Sesiune injectată din config_entry")

    # ── Coordinator (cu interval configurabil) ──
    update_interval = entry.data.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL)
    coordinator = EblocCoordinator(hass, api_client, entry, update_interval)

    # Dacă e-bloc.ro nu răspunde, ridică ConfigEntryNotReady → HA reîncearcă
    # automat (înainte: return False = integrare picată până la reload manual).
    await coordinator.async_config_entry_first_refresh()

    # Salvăm datele runtime
    entry.runtime_data = EblocRuntimeData(
        coordinator=coordinator,
        api_client=api_client,
        config_signature=_config_signature(entry),
    )

    # Încărcăm platformele (NECONDIȚIONAT — gating-ul e în sensor.py)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Listener pentru modificarea opțiunilor
    entry.async_on_unload(entry.add_update_listener(_async_update_options))

    _LOGGER.info(
        "[Ebloc] Integrarea configurată (entry_id=%s).",
        entry.entry_id,
    )
    return True


async def _async_update_options(hass: HomeAssistant, entry: ConfigEntry):
    """Reîncarcă integrarea când configurația s-a schimbat efectiv.

    Listener-ul e apelat la ORICE actualizare a intrării — inclusiv când
    coordinatorul salvează sesiunea e-bloc după un re-login. Acelea sunt ignorate.
    """
    runtime = getattr(entry, "runtime_data", None)
    if runtime is not None and runtime.config_signature == _config_signature(entry):
        return
    _LOGGER.info("[Ebloc] Configurația s-a schimbat. Se reîncarcă...")
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry):
    """Descărcarea intrării din config_entries."""
    _LOGGER.info(
        "[Ebloc] async_unload_entry entry_id=%s",
        entry.entry_id,
    )

    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if unload_ok:
        # Doar intrările ACTIVE contează (cele dezactivate / descărcate nu folosesc
        # managerul de licențe — altfel acesta și timer-ul nu s-ar opri niciodată).
        entry_ids_ramase = {
            e.entry_id
            for e in hass.config_entries.async_entries(DOMAIN)
            if e.entry_id != entry.entry_id and e.state in _STARI_ACTIVE
        }

        if not entry_ids_ramase:
            _LOGGER.info("[Ebloc] Ultima entry descărcată — curăț domeniul complet")

            # Notificare lifecycle
            mgr = hass.data.get(DOMAIN, {}).get(LICENSE_DATA_KEY)
            if mgr and not hass.is_stopping:
                if entry.disabled_by:
                    await mgr.async_notify_event("integration_disabled")
                    hass.data[f"{DOMAIN}_was_disabled"] = True
                else:
                    hass.data.setdefault(f"{DOMAIN}_notify", {}).update(
                        {
                            "fingerprint": mgr.fingerprint,
                            "license_key": mgr._data.get("license_key", ""),
                        }
                    )

            _cleanup_domain(hass)

            _LOGGER.info("[Ebloc] Cleanup complet")
    else:
        _LOGGER.error("[Ebloc] Unload EȘUAT pentru entry_id=%s", entry.entry_id)

    return unload_ok


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Notifică serverul când integrarea e complet eliminată."""
    _LOGGER.debug("[Ebloc] async_remove_entry entry_id=%s", entry.entry_id)

    remaining = hass.config_entries.async_entries(DOMAIN)
    if not remaining:
        # O intrare ștearsă cât era în „setup retry" nu trece prin async_unload_entry
        if DOMAIN in hass.data:
            mgr = hass.data[DOMAIN].get(LICENSE_DATA_KEY)
            if mgr is not None and f"{DOMAIN}_notify" not in hass.data:
                hass.data[f"{DOMAIN}_notify"] = {
                    "fingerprint": mgr.fingerprint,
                    "license_key": mgr._data.get("license_key", ""),
                }
            _cleanup_domain(hass)
        notify_data = hass.data.pop(f"{DOMAIN}_notify", None)
        if notify_data and notify_data.get("fingerprint"):
            await _send_lifecycle_event(
                hass,
                notify_data["fingerprint"],
                notify_data.get("license_key", ""),
                "integration_removed",
            )


async def _send_lifecycle_event(
    hass: HomeAssistant, fingerprint: str, license_key: str, action: str
) -> None:
    """Trimite un eveniment lifecycle direct (fără LicenseManager)."""
    import hashlib
    import hmac as hmac_lib
    import json
    import time

    import aiohttp

    from .license import INTEGRATION, LICENSE_API_URL

    timestamp = int(time.time())
    payload = {
        "fingerprint": fingerprint,
        "timestamp": timestamp,
        "action": action,
        "license_key": license_key,
        "integration": INTEGRATION,
    }
    data = {k: v for k, v in payload.items() if k != "hmac"}
    msg = json.dumps(data, sort_keys=True).encode()
    payload["hmac"] = hmac_lib.new(
        fingerprint.encode(), msg, hashlib.sha256
    ).hexdigest()

    try:
        session = async_get_clientsession(hass)
        async with session.post(
            f"{LICENSE_API_URL}/notify",
            json=payload,
            timeout=aiohttp.ClientTimeout(total=10),
            headers={
                "Content-Type": "application/json",
                "User-Agent": "Ebloc-HA-Integration/3.0",
            },
        ) as resp:
            if resp.status == 200:
                result = await resp.json()
                if not result.get("success"):
                    _LOGGER.warning(
                        "[Ebloc] Server a refuzat '%s': %s",
                        action,
                        result.get("error"),
                    )
    except Exception as err:  # noqa: BLE001
        _LOGGER.debug("[Ebloc] Nu s-a putut raporta '%s': %s", action, err)
