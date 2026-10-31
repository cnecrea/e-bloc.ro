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

Rezultatul fiecărei trimiteri (reușită sau nu, cu motivul) apare ca notificare permanentă
în bara laterală; o trimitere nouă pentru același contor / apartament o înlocuiește.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from homeassistant.components import persistent_notification
from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util
from homeassistant.util import slugify as ha_slugify

from .const import (
    ATTRIBUTION,
    DOMAIN,
    LICENSE_DATA_KEY,
    NR_PERS_MULTIPLIER,
)
from .api import describe_result
from .coordinator import EblocCoordinator
from .helpers import association_device, contor_suffix, index_for, is_license_valid, luna_ro

_LOGGER = logging.getLogger(__name__)


class EblocActionError(HomeAssistantError):
    """Trimitere eșuată; motivul e păstrat separat pentru notificare."""

    def __init__(self, message: str, reason: str) -> None:
        super().__init__(message)
        self.reason = reason


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
        """Dispozitivul asociației."""
        return association_device(self._id_user, getattr(self, "_id_asoc", self._id_user))

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

    def _read_number_value(self, number_unique_id: str, label: str) -> float:
        """Valoarea curentă a selectorului (number) asociat butonului.

        Ridică HomeAssistantError — utilizatorul vede eroarea în interfață,
        în loc ca apăsarea să pară reușită.
        """
        entity_id = self._find_number_entity_id(number_unique_id)
        state = self.hass.states.get(entity_id) if entity_id else None
        if state is None or state.state in ("unknown", "unavailable"):
            # Numele afișat, nu entity_id-ul (acesta conține ID-ul utilizatorului)
            motiv = f"selectorul „{label}” nu are o valoare"
        else:
            try:
                return float(state.state)
            except (ValueError, TypeError):
                motiv = f"valoarea „{state.state}” din selectorul „{label}” nu este un număr"
        _LOGGER.warning("Trimitere oprită | %s | %s", self._where(), motiv)
        raise EblocActionError(f"Trimiterea nu a pornit: {motiv}.", motiv)

    def _raise_if_failed(self, result: dict[str, Any], ce: str) -> None:
        """Orice răspuns în afară de result=ok devine o eroare vizibilă în interfață și în log."""
        if result.get("result") == "ok":
            return
        if result.get("result") == "readonly":
            motiv = "perioada pentru această lună este închisă"
        else:
            motiv = describe_result(result)
        _LOGGER.warning(
            "Trimitere refuzată | %s | %s | rezultat: %s | %s",
            self._where(), ce, result.get("result", "fără rezultat"), motiv,
        )
        raise EblocActionError(f"e-bloc.ro nu a acceptat {ce}: {motiv}.", motiv)

    def _ap_number(self) -> Any:
        """Numărul apartamentului (câmpul „ap"), altfel id_ap."""
        id_asoc = getattr(self, "_id_asoc", "")
        id_ap = getattr(self, "_id_ap", "")
        apartments = (self.coordinator.data or {}).get("apartments", {})
        return next(
            (
                a.get("ap") for a in apartments.get(id_asoc, apartments.get(str(id_asoc), []))
                if str(a.get("id_ap", a.get("id"))) == str(id_ap)
            ),
            None,
        ) or id_ap

    def _where(self) -> str:
        return f"asociația {getattr(self, '_id_asoc', '')} | apartamentul {self._ap_number()}"

    def _notify(self, notification_id: str, titlu: str, text: str, motiv: str | None = None) -> None:
        """Notificare permanentă cu rezultatul trimiterii (înlocuiește rezultatul anterior)."""
        api = self.coordinator.api_client
        id_asoc = getattr(self, "_id_asoc", "")
        nr_ap = self._ap_number()
        mesaj = [text]
        if motiv:
            mesaj.append(f"**Motiv:** {motiv[0].upper() + motiv[1:]}.")
        mesaj.append(
            f"- Asociația: {api.association_name(id_asoc)} ({id_asoc})\n"
            f"- Apartamentul: {nr_ap}\n"
            f"- {'Încercare' if motiv else 'Trimis'}: {dt_util.now().strftime('%d.%m.%Y, %H:%M')}"
        )
        persistent_notification.async_create(
            self.hass, "\n\n".join(mesaj), title=f"E-bloc România — {titlu}",
            notification_id=notification_id,
        )


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
        # Fără licență nu există date → entitățile nu pot fi create. Le scoatem din
        # registru (ca la senzori), altfel HA le afișează „nu mai sunt furnizate".
        registru = er.async_get(hass)
        for entry_reg in er.async_entries_for_config_entry(registru, entry.entry_id):
            if entry_reg.domain == "button":
                registru.async_remove(entry_reg.entity_id)
        _LOGGER.debug("Platformă neîncărcată | butoane | licența nu este validă")
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

            # Butoane trimite index — doar contoarele editabile ale acestui apartament
            ap_data = data.get("per_apartment", {}).get(ap_key, {})
            contoare_data = ap_data.get("contoare", {})
            suffix = contor_suffix(data, id_asoc, id_ap)

            for cd in contoare_data.get("aInfoContoare", []):
                id_contor = str(cd.get("id_contor", ""))
                if not id_contor:
                    continue
                contor_val = index_for(contoare_data, id_contor, id_ap) or {}
                if str(contor_val.get("right_edit_index", "0")) == "1":
                    entities.append(
                        TrimiteIndexButton(
                            coordinator, id_user, id_asoc_int, id_ap,
                            int(id_contor), cd, suffix,
                        )
                    )

    _LOGGER.debug("Platformă încărcată | butoane: %d", len(entities))
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
        id_contor: int, contor_def: dict[str, Any], suffix: str = "",
    ) -> None:
        super().__init__(coordinator, id_user)
        self._id_asoc = id_asoc
        self._id_ap = id_ap
        self._id_contor = id_contor

        titlu = contor_def.get("titlu", f"Contor {id_contor}")
        titlu_slug = ha_slugify(titlu)
        self._titlu = titlu
        self._attr_name = f"Trimite index {titlu}"
        self._attr_unique_id = f"{DOMAIN}_{id_user}_{id_asoc}_{id_contor}{suffix}_trimite_index"
        self._custom_entity_id = f"button.{DOMAIN}_{id_user}_{id_asoc}_trimite_index_{titlu_slug}{suffix}"

        # unique_id al number entity corespunzător (același sufix ca în number.py)
        self._number_uid = f"{DOMAIN}_{id_user}_{id_asoc}_{id_contor}{suffix}_index"

    async def async_press(self) -> None:
        """Trimite indexul din number helper; rezultatul apare ca notificare."""
        notification_id = f"{DOMAIN}_index_{self._id_asoc}_{self._id_ap}_{self._id_contor}"
        valoare = ""
        try:
            index_value = self._read_number_value(self._number_uid, f"Index {self._titlu} (selector)")
            valoare = f"{_ro(index_value)} m³"
            # round(): float("1.005") * 1000 = 1004.999… → int() ar trimite 1004 (index greșit)
            index_api = int(round(index_value * NR_PERS_MULTIPLIER))
            _LOGGER.debug(
                "Trimitere index | %s | contorul „%s” | %s", self._where(), self._titlu, valoare
            )
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
            self._raise_if_failed(result, f"indexul pentru contorul „{self._titlu}”")
        except EblocActionError as err:
            self._notify(
                notification_id, "Index netrimis",
                f"Indexul{f' **{valoare}**' if valoare else ''} pentru contorul "
                f"**{self._titlu}** nu a fost trimis.",
                err.reason,
            )
            raise

        self._notify(
            notification_id, "Index trimis",
            f"Indexul **{valoare}** pentru contorul **{self._titlu}** a fost trimis cu succes.",
        )
        _LOGGER.info("Index trimis | %s | contorul „%s” | %s", self._where(), self._titlu, valoare)
        await self.coordinator.async_request_refresh()


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
        self._custom_entity_id = f"button.{DOMAIN}_{id_user}_{id_asoc}_trimite_nr_persoane"

        # unique_id al number entity corespunzător
        self._number_uid = f"{DOMAIN}_{id_user}_{id_asoc}_{id_ap}_nr_pers"

    async def async_press(self) -> None:
        """Trimite nr. persoane din number helper; rezultatul apare ca notificare."""
        notification_id = f"{DOMAIN}_nr_persoane_{self._id_asoc}_{self._id_ap}"

        # Luna curentă — calculată la apăsare; API-ul refuză lunile închise
        luna_curenta = datetime.now().strftime("%Y-%m")
        luna = (self.coordinator.data or {}).get("luna", "") or luna_curenta
        if luna < luna_curenta:
            luna = luna_curenta
        luna_text = luna_ro(luna)

        nr_pers: int | None = None
        try:
            nr_pers = int(round(self._read_number_value(self._number_uid, "Nr. persoane")))
            _LOGGER.debug(
                "Trimitere număr persoane | %s | luna %s | persoane: %d", self._where(), luna_text, nr_pers
            )
            result = await self.coordinator.api_client.async_set_nr_persoane(
                self._id_asoc, self._id_ap, luna, nr_pers
            )
            self._raise_if_failed(result, f"numărul de persoane pentru {luna_text}")
        except EblocActionError as err:
            self._notify(
                notification_id, "Număr de persoane netrimis",
                f"Numărul de persoane{f' (**{nr_pers}**)' if nr_pers is not None else ''} "
                f"pentru **{luna_text}** nu a fost trimis.",
                err.reason,
            )
            raise

        self._notify(
            notification_id, "Număr de persoane trimis",
            f"Numărul de persoane (**{nr_pers}**) pentru **{luna_text}** a fost trimis cu succes.",
        )
        _LOGGER.info(
            "Număr persoane trimis | %s | luna %s | persoane: %d", self._where(), luna_text, nr_pers
        )
        await self.coordinator.async_request_refresh()


def _ro(value: float) -> str:
    """Număr cu 3 zecimale și virgulă, ca în română: 409,000."""
    return f"{value:.3f}".replace(".", ",")
