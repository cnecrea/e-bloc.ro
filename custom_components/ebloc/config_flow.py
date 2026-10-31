"""
ConfigFlow și OptionsFlow pentru integrarea E-bloc România.

Utilizatorul introduce email + parolă, apoi se face autodiscovery automat.
După login + autodiscovery, se creează entry-ul cu datele descoperite.
OptionsFlow permite activarea licenței.
"""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntryState, ConfigFlowResult
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers import selector

from .const import (
    CONF_EMAIL,
    CONF_LICENSE_KEY,
    CONF_PASSWORD,
    CONF_UPDATE_INTERVAL,
    DEFAULT_UPDATE_INTERVAL,
    DOMAIN,
    LICENSE_DATA_KEY,
    LICENSE_PURCHASE_URL,
    MAX_UPDATE_INTERVAL,
    MIN_UPDATE_INTERVAL,
)
from .api import EblocApiClient

_LOGGER = logging.getLogger(__name__)


# ------------------------------------------------------------------
# ConfigFlow
# ------------------------------------------------------------------


# Tipurile de licență emise de WebAPI (LICENSE_DURATION)
_LICENSE_TYPE_RO = {
    "perpetual": "perpetuă",
    "annual": "anuală",
    "semi_annual": "semestrială",
    "monthly": "lunară",
}


class EblocConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """ConfigFlow — autentificare + autodiscovery."""

    VERSION = 1

    def __init__(self) -> None:
        """Inițializează flow-ul."""
        self._email: str = ""
        self._password: str = ""
        self._api: EblocApiClient | None = None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pasul 1: Autentificare (email + parolă)."""
        errors: dict[str, str] = {}

        if user_input is not None:
            self._email = user_input[CONF_EMAIL]
            self._password = user_input[CONF_PASSWORD]

            await self.async_set_unique_id(self._email.lower())
            self._abort_if_unique_id_configured()

            session = async_get_clientsession(self.hass)
            self._api = EblocApiClient(session, self._email, self._password)

            # Login
            if await self._api.async_login():
                # Autodiscovery
                if await self._api.async_autodiscover():
                    apartments = self._api.get_all_apartments()
                    if apartments:
                        # Salvăm session-ul și datele descoperite
                        session_data = self._api.export_session()
                        update_interval = user_input.get(
                            CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL
                        )
                        return self.async_create_entry(
                            title=f"E-bloc ({self._email})",
                            data={
                                CONF_EMAIL: self._email,
                                CONF_PASSWORD: self._password,
                                CONF_UPDATE_INTERVAL: update_interval,
                                "session_data": session_data,
                                "associations": [
                                    {"id": a.get("id"), "nume": a.get("nume", "")}
                                    for a in self._api.associations
                                ],
                                "apartments": {
                                    str(k): v
                                    for k, v in self._api.apartments.items()
                                },
                                "access_rights": self._api.access_rights,
                                "home_ap_data": {
                                    str(k): v
                                    for k, v in self._api.home_ap_data.items()
                                },
                            },
                        )
                    else:
                        errors["base"] = "no_apartments"
                        _LOGGER.warning(
                            "Autentificarea a reușit, dar contul nu are niciun apartament disponibil."
                        )
                else:
                    # Motivul e deja în log (api.async_autodiscover)
                    errors["base"] = "autodiscover_failed"
            else:
                errors["base"] = "auth_failed"

        schema = vol.Schema(
            {
                vol.Required(CONF_EMAIL): str,
                vol.Required(CONF_PASSWORD): str,
                vol.Optional(
                    CONF_UPDATE_INTERVAL,
                    default=DEFAULT_UPDATE_INTERVAL,
                ): vol.All(int, vol.Range(min=MIN_UPDATE_INTERVAL, max=MAX_UPDATE_INTERVAL)),
            }
        )

        return self.async_show_form(
            step_id="user", data_schema=schema, errors=errors
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> EblocOptionsFlow:
        """Returnează options flow-ul."""
        return EblocOptionsFlow()


# ------------------------------------------------------------------
# OptionsFlow
# ------------------------------------------------------------------


class EblocOptionsFlow(config_entries.OptionsFlow):
    """OptionsFlow — licență + reconfigurare."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Meniu principal."""
        return self.async_show_menu(
            step_id="init",
            menu_options=["reconfigure", "licenta"],
        )

    async def async_step_licenta(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Formular activare licență."""
        from .license import LicenseManager

        errors: dict[str, str] = {}
        description_placeholders: dict[str, str] = {}
        is_ro = self.hass.config.language == "ro"

        mgr: LicenseManager | None = self.hass.data.get(DOMAIN, {}).get(
            LICENSE_DATA_KEY
        )
        if mgr is None:
            mgr = LicenseManager(self.hass)
            await mgr.async_load()

        server_status = mgr.status

        if server_status == "licensed":
            from datetime import datetime

            tip = _LICENSE_TYPE_RO.get(mgr.license_type or "", mgr.license_type or "necunoscut")
            status_lines = [f"✅ Licență activă ({tip})"]

            if mgr.license_key_masked:
                status_lines[0] += f" — {mgr.license_key_masked}"

            if mgr.activated_at:
                act_date = datetime.fromtimestamp(
                    mgr.activated_at
                ).strftime("%d.%m.%Y %H:%M")
                status_lines.append(f"Activată la: {act_date}")

            if mgr.license_expires_at:
                exp_date = datetime.fromtimestamp(
                    mgr.license_expires_at
                ).strftime("%d.%m.%Y %H:%M")
                status_lines.append(f"📅 Expiră la: {exp_date}")
            elif tip == "perpetual":
                status_lines.append("Valabilitate: nelimitată (perpetuă)")

            description_placeholders["license_status"] = "\n".join(status_lines)

        elif server_status == "trial":
            days = mgr.trial_days_remaining
            if is_ro:
                status_lines = [
                    f"⏳ Evaluare — {days} zile rămase",
                    "",
                    f"🛒 Obține licență: {LICENSE_PURCHASE_URL}",
                ]
            else:
                status_lines = [
                    f"⏳ Trial — {days} days remaining",
                    "",
                    f"🛒 Get a license: {LICENSE_PURCHASE_URL}",
                ]
            description_placeholders["license_status"] = "\n".join(status_lines)
        elif server_status == "expired":
            if is_ro:
                status_lines = [
                    "❌ Licență expirată",
                    "",
                    f"🛒 Obține licență: {LICENSE_PURCHASE_URL}",
                ]
            else:
                status_lines = [
                    "❌ License expired",
                    "",
                    f"🛒 Get a license: {LICENSE_PURCHASE_URL}",
                ]
            description_placeholders["license_status"] = "\n".join(status_lines)
        else:
            if is_ro:
                status_lines = [
                    "❌ Fără licență — funcționalitate blocată",
                    "",
                    f"🛒 Obține licență: {LICENSE_PURCHASE_URL}",
                ]
            else:
                status_lines = [
                    "❌ No license — functionality blocked",
                    "",
                    f"🛒 Get a license: {LICENSE_PURCHASE_URL}",
                ]
            description_placeholders["license_status"] = "\n".join(status_lines)

        if user_input is not None:
            cheie = user_input.get(CONF_LICENSE_KEY, "").strip()

            if not cheie:
                errors["base"] = "license_key_empty"
            elif len(cheie) < 10:
                errors["base"] = "license_key_invalid"
            else:
                result = await mgr.async_activate(cheie)

                if result.get("success"):
                    from homeassistant.components import persistent_notification

                    tip_ro = _LICENSE_TYPE_RO.get(
                        mgr.license_type or "", mgr.license_type or "necunoscut"
                    )

                    persistent_notification.async_create(
                        self.hass,
                        f"Licența E-bloc România a fost activată cu succes! "
                        f"Tip: {tip_ro}.",
                        title="Licență activată",
                        notification_id="ebloc_license_activated",
                    )
                    return self.async_create_entry(
                        data=self.config_entry.options
                    )

                api_error = result.get("error", "unknown_error")
                # Codurile WebAPI /activate + erorile locale ale clientului
                error_map = {
                    "invalid_key": "license_key_invalid",
                    "already_used": "license_already_used",
                    "expired_key": "license_key_expired",
                    "fingerprint_mismatch": "license_fingerprint_mismatch",
                    "network_error": "license_network_error",
                    "http_502": "license_network_error",
                    "http_503": "license_network_error",
                    "http_504": "license_network_error",
                }
                errors["base"] = error_map.get(api_error, "license_server_error")

        schema = vol.Schema(
            {
                vol.Optional(CONF_LICENSE_KEY): selector.TextSelector(
                    selector.TextSelectorConfig(
                        type=selector.TextSelectorType.TEXT,
                        suffix="EBLO-XXXX-XXXX-XXXX-XXXX",
                    )
                ),
            }
        )

        return self.async_show_form(
            step_id="licenta",
            data_schema=schema,
            errors=errors,
            description_placeholders=description_placeholders,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Reconfigurare credențiale."""
        errors: dict[str, str] = {}

        if user_input is not None:
            email = user_input[CONF_EMAIL]
            password = user_input[CONF_PASSWORD]

            session = async_get_clientsession(self.hass)
            api = EblocApiClient(session, email, password)

            conflict = any(
                e.entry_id != self.config_entry.entry_id and e.unique_id == email.lower()
                for e in self.hass.config_entries.async_entries(DOMAIN)
            )
            if conflict:
                errors["base"] = "already_configured"
            elif await api.async_login():
                if await api.async_autodiscover():
                    update_interval = user_input.get(
                        CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL
                    )
                    new_data = dict(self.config_entry.data)
                    new_data[CONF_EMAIL] = email
                    new_data[CONF_PASSWORD] = password
                    new_data[CONF_UPDATE_INTERVAL] = update_interval
                    new_data["session_data"] = api.export_session()
                    new_data["associations"] = [
                        {"id": a.get("id"), "nume": a.get("nume", "")}
                        for a in api.associations
                    ]
                    new_data["apartments"] = {
                        str(k): v for k, v in api.apartments.items()
                    }
                    new_data["access_rights"] = api.access_rights
                    new_data["home_ap_data"] = {
                        str(k): v for k, v in api.home_ap_data.items()
                    }

                    # Listener-ul de update reîncarcă intrarea o singură dată
                    # (înainte: update + reload explicit + create_entry = 3 reload-uri).
                    self.hass.config_entries.async_update_entry(
                        self.config_entry,
                        data=new_data,
                        title=f"E-bloc ({email})",
                        unique_id=email.lower(),
                    )
                    if self.config_entry.state in (
                        ConfigEntryState.SETUP_RETRY,
                        ConfigEntryState.SETUP_ERROR,
                    ):
                        # Intrarea nu rulează → listener-ul de update nu e activ;
                        # reîncercăm imediat cu noile date.
                        self.hass.config_entries.async_schedule_reload(
                            self.config_entry.entry_id
                        )
                    return self.async_create_entry(data=dict(self.config_entry.options))
                else:
                    errors["base"] = "autodiscover_failed"
            else:
                errors["base"] = "auth_failed"

        current = self.config_entry.data

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_EMAIL, default=current.get(CONF_EMAIL, "")
                ): str,
                vol.Required(
                    CONF_PASSWORD, default=current.get(CONF_PASSWORD, "")
                ): str,
                vol.Optional(
                    CONF_UPDATE_INTERVAL,
                    default=current.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL),
                ): vol.All(int, vol.Range(min=MIN_UPDATE_INTERVAL, max=MAX_UPDATE_INTERVAL)),
            }
        )

        return self.async_show_form(
            step_id="reconfigure", data_schema=schema, errors=errors
        )
