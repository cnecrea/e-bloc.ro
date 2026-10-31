"""Platforma Number pentru E-bloc România.

Pattern IDENTIC cu eonromania-main:
  - _attr_has_entity_name = False
  - custom entity_id property (getter/setter)
  - self._custom_entity_id = f"number.{DOMAIN}_{id_user}_xxx"

Number entities sunt LOCALE (selectori):
  - Schimbarea valorii NU afectează senzorul corespunzător
  - Senzorul citește din API (coordinator)
  - Number stochează inputul utilizatorului pentru trimitere via buton

Helpers:
  - Index contor (per id_contor cu right_edit_index=1)
  - Nr. persoane (per apartament)
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.number import (
    NumberEntity,
    NumberMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import slugify as ha_slugify

from .const import (
    ATTRIBUTION,
    DOMAIN,
    INDEX_NOT_SET,
    LICENSE_DATA_KEY,
    MAX_NR_PERS,
    NR_PERS_MULTIPLIER,
)
from .coordinator import EblocCoordinator
from .helpers import association_device, contor_suffix, index_for, is_license_valid

_LOGGER = logging.getLogger(__name__)


# ──────────────────────────────────────────────
# Clasă de bază (pattern IDENTIC cu eonromania)
# ──────────────────────────────────────────────
class EblocNumber(CoordinatorEntity[EblocCoordinator], NumberEntity):
    """Clasă de bază pentru number entities E-bloc România."""

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


# ──────────────────────────────────────────────
# async_setup_entry
# ──────────────────────────────────────────────
async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Configurează number entities."""
    if not is_license_valid(hass):
        # Fără licență nu există date → entitățile nu pot fi create. Le scoatem din
        # registru (ca la senzori), altfel HA le afișează „nu mai sunt furnizate".
        registru = er.async_get(hass)
        for entry_reg in er.async_entries_for_config_entry(registru, entry.entry_id):
            if entry_reg.domain == "number":
                registru.async_remove(entry_reg.entity_id)
        _LOGGER.debug("Platformă neîncărcată | selectoare | licența nu este validă")
        return

    runtime = entry.runtime_data
    coordinator: EblocCoordinator = runtime.coordinator
    id_user = coordinator.api_client.id_user or 0

    entities: list[NumberEntity] = []

    data = coordinator.data or {}
    apartments = data.get("apartments", {})

    for id_asoc, ap_list in apartments.items():
        id_asoc_int = int(id_asoc) if isinstance(id_asoc, str) else id_asoc
        for ap in ap_list:
            id_ap = ap.get("id_ap", ap.get("id", 0))
            if not id_ap:
                continue

            ap_key = f"{id_asoc_int}:{id_ap}"

            # Number helper pentru nr. persoane
            entities.append(
                NrPersoaneNumber(coordinator, id_user, id_asoc_int, id_ap, ap)
            )

            # Number helpers per contor — doar contoarele editabile ale acestui apartament
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
                        IndexContorNumber(
                            coordinator, id_user, id_asoc_int, id_ap,
                            int(id_contor), cd, contor_val, suffix,
                        )
                    )

    _LOGGER.debug("Platformă încărcată | selectoare: %d", len(entities))
    async_add_entities(entities, update_before_add=True)


# ──────────────────────────────────────────────
# Nr. persoane (selector LOCAL)
# ──────────────────────────────────────────────
class NrPersoaneNumber(EblocNumber):
    """Selector local pentru nr. persoane — NU afectează senzorul."""

    _attr_icon = "mdi:account-group"
    _attr_mode = NumberMode.BOX
    _attr_native_min_value = 0
    _attr_native_max_value = MAX_NR_PERS
    _attr_native_step = 1

    def __init__(
        self, coordinator: EblocCoordinator, id_user: int,
        id_asoc: int, id_ap: int | str,
        ap_data: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(coordinator, id_user)
        self._id_asoc = id_asoc
        self._id_ap = id_ap
        self._attr_name = "Nr. persoane"
        self._attr_unique_id = f"{DOMAIN}_{id_user}_{id_asoc}_{id_ap}_nr_pers"
        self._custom_entity_id = f"number.{DOMAIN}_{id_user}_{id_asoc}_nr_persoane_selector"

        # Fallback: ultimul nr. persoane din API (nr_pers / 1000)
        self._value: float = 0
        if ap_data:
            try:
                nr_raw = int(ap_data.get("nr_pers", 0))
                self._value = nr_raw // NR_PERS_MULTIPLIER
            except (ValueError, TypeError):
                self._value = 0

    @property
    def native_value(self) -> float:
        """Valoare locală — NU din API."""
        return self._value

    async def async_set_native_value(self, value: float) -> None:
        """Setează valoarea locală — NU trimite la API."""
        self._value = value
        self.async_write_ha_state()


# ──────────────────────────────────────────────
# Index contor (selector LOCAL)
# ──────────────────────────────────────────────
class IndexContorNumber(EblocNumber):
    """Selector local pentru index contor — NU afectează senzorul."""

    _attr_icon = "mdi:counter"
    _attr_mode = NumberMode.BOX
    _attr_native_min_value = 0
    _attr_native_max_value = 999999.999
    _attr_native_step = 0.001
    _attr_native_unit_of_measurement = "m³"

    def __init__(
        self, coordinator: EblocCoordinator, id_user: int,
        id_asoc: int, id_ap: int | str,
        id_contor: int, contor_def: dict[str, Any],
        contor_val: dict[str, Any], suffix: str = "",
    ) -> None:
        super().__init__(coordinator, id_user)
        self._id_asoc = id_asoc
        self._id_ap = id_ap
        self._id_contor = id_contor

        titlu = contor_def.get("titlu", f"Contor {id_contor}")
        titlu_slug = ha_slugify(titlu)

        self._attr_name = f"Index {titlu} (selector)"
        # Același sufix ca în button.py, care caută acest selector după unique_id
        self._attr_unique_id = f"{DOMAIN}_{id_user}_{id_asoc}_{id_contor}{suffix}_index"
        self._custom_entity_id = f"number.{DOMAIN}_{id_user}_{id_asoc}_index_{titlu_slug}{suffix}_selector"

        # Fallback: ultimul index disponibil (index_nou > index_vechi > 0)
        # Dacă se apasă butonul din greșeală, retrimite ultimul index valid
        self._value: float = 0
        raw_nou = contor_val.get("index_nou")
        raw_vechi = contor_val.get("index_vechi", INDEX_NOT_SET)

        fallback = INDEX_NOT_SET
        if raw_nou is not None:
            try:
                fallback = int(raw_nou)
            except (ValueError, TypeError):
                fallback = INDEX_NOT_SET
        if fallback == INDEX_NOT_SET:
            try:
                fallback = int(raw_vechi)
            except (ValueError, TypeError):
                fallback = INDEX_NOT_SET

        if fallback != INDEX_NOT_SET:
            self._value = round(fallback / NR_PERS_MULTIPLIER, 3)

    @property
    def native_value(self) -> float:
        """Valoare locală — NU din API."""
        return self._value

    async def async_set_native_value(self, value: float) -> None:
        """Setează valoarea locală — NU trimite la API."""
        self._value = value
        self.async_write_ha_state()

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Informații context pentru utilizator."""
        return {
            "ID contor": self._id_contor,
            "ID asociație": self._id_asoc,
            "ID apartament": self._id_ap,
        }
