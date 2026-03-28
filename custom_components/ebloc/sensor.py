"""Platforma Sensor pentru E-bloc România.

Câmpurile JSON sunt preluate STRICT din 6_ebloc_api_results_v2.json / debug_v2.py.
Pattern-ul entităților urmează EXACT modelul eonromania-main:
  - _attr_has_entity_name = False
  - custom entity_id property (getter/setter)
  - self._custom_entity_id = f"sensor.{DOMAIN}_{id_user}_xxx"
  - Atribute cu etichete românești human-readable
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util
from homeassistant.util import slugify as ha_slugify

from .const import (
    AMOUNT_DIVISOR,
    ATTRIBUTION,
    DOMAIN,
    INDEX_NOT_SET,
    LICENSE_DATA_KEY,
    NR_PERS_MULTIPLIER,
)
from .coordinator import EblocCoordinator
from .helpers import is_license_valid, luna_ro, luna_ro_scurt

_LOGGER = logging.getLogger(__name__)


# ──────────────────────────────────────────────
# Clasă de bază (pattern IDENTIC cu eonromania)
# ──────────────────────────────────────────────
class EblocEntity(CoordinatorEntity[EblocCoordinator], SensorEntity):
    """Clasă de bază pentru entitățile E-bloc România."""

    _attr_has_entity_name = False

    def __init__(self, coordinator: EblocCoordinator, id_user: int) -> None:
        """Inițializare cu coordinator."""
        super().__init__(coordinator)
        self._id_user = id_user
        self._custom_entity_id: str | None = None

    @property
    def _license_valid(self) -> bool:
        """Verifică dacă licența este validă."""
        mgr = self.hass.data.get(DOMAIN, {}).get(LICENSE_DATA_KEY)
        return mgr.is_valid if mgr else False

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

    def _get_ap_data(self, id_asoc, id_ap) -> dict[str, Any]:
        """Returnează datele per apartament din coordinator."""
        data = self.coordinator.data or {}
        key = f"{id_asoc}:{id_ap}"
        return data.get("per_apartment", {}).get(key, {})


# ──────────────────────────────────────────────
# async_setup_entry
# ──────────────────────────────────────────────
async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Configurează senzorii."""
    runtime = entry.runtime_data
    coordinator: EblocCoordinator = runtime.coordinator
    id_user = coordinator.api_client.id_user or 0

    license_valid = is_license_valid(hass)
    licenta_uid = f"{DOMAIN}_licenta_{id_user}"

    if not license_valid:
        registru = er.async_get(hass)
        for entry_reg in er.async_entries_for_config_entry(
            registru, entry.entry_id
        ):
            if (
                entry_reg.domain == "sensor"
                and entry_reg.unique_id != licenta_uid
            ):
                registru.async_remove(entry_reg.entity_id)
        async_add_entities(
            [LicentaNecesaraSensor(coordinator, id_user)],
            update_before_add=True,
        )
        return

    # Licență validă — curăță LicentaNecesaraSensor orfan
    registru = er.async_get(hass)
    entitate_licenta = registru.async_get_entity_id("sensor", DOMAIN, licenta_uid)
    if entitate_licenta is not None:
        registru.async_remove(entitate_licenta)

    entities: list[SensorEntity] = []

    # 1. Cont utilizator (global)
    entities.append(ContUtilizatorSensor(coordinator, id_user))

    # 2. Carduri salvate (global)
    entities.append(CarduriSensor(coordinator, id_user))

    # Senzori per apartament
    data = coordinator.data or {}
    apartments = data.get("apartments", {})
    get_info = data.get("get_info", {})
    associations_info = get_info.get("aInfoAsoc", [])
    assoc_by_id: dict[str, dict[str, Any]] = {}
    for a in associations_info:
        aid = str(a.get("id", ""))
        if aid:
            assoc_by_id[aid] = a

    for id_asoc, ap_list in apartments.items():
        for ap in ap_list:
            id_ap = ap.get("id_ap", ap.get("id", 0))
            if not id_ap:
                continue

            ap_key = f"{id_asoc}:{id_ap}"

            # 3. Asociație
            assoc_info = assoc_by_id.get(str(id_asoc), {})
            entities.append(
                AsociatieSensor(coordinator, id_user, id_asoc, assoc_info)
            )

            # 4. Index/Contoare — DOAR dacă id_contor are intrare în aInfoIndex
            ap_data_dict = data.get("per_apartment", {}).get(ap_key, {})
            contoare_data = ap_data_dict.get("contoare", {})
            contoare_defs = contoare_data.get("aInfoContoare", [])
            contoare_vals = contoare_data.get("aInfoIndex", [])

            idx_by_contor: dict[str, dict[str, Any]] = {}
            for cv in contoare_vals:
                cid = str(cv.get("id_contor", ""))
                if cid:
                    idx_by_contor[cid] = cv

            for contor_def in contoare_defs:
                id_contor = str(contor_def.get("id_contor", ""))
                if not id_contor:
                    continue
                contor_val = idx_by_contor.get(id_contor)
                if contor_val is None:
                    continue
                entities.append(
                    IndexContorSensor(
                        coordinator, id_user, id_asoc, id_ap,
                        int(id_contor), contor_def, contor_val,
                    )
                )

            # 5. Factură restantă
            entities.append(
                FacturaRestantaSensor(coordinator, id_user, id_asoc, id_ap, ap)
            )

            # 6. Arhivă plăți
            entities.append(
                ArhivaPlatiSensor(coordinator, id_user, id_asoc, id_ap)
            )

            # 7. Număr persoane
            entities.append(
                NrPersoaneSensor(coordinator, id_user, id_asoc, id_ap)
            )

            # 8. Tichete
            entities.append(
                TicheteSensor(coordinator, id_user, id_asoc, id_ap)
            )

            # 9. Citire permisă
            entities.append(
                CitirePermisaSensor(coordinator, id_user, id_asoc, id_ap)
            )

    _LOGGER.info(
        "[Ebloc:Sensor] Creez %d senzori pentru user %s",
        len(entities), id_user,
    )
    async_add_entities(entities, update_before_add=True)


# ──────────────────────────────────────────────
# LicentaNecesaraSensor
# ──────────────────────────────────────────────
class LicentaNecesaraSensor(EblocEntity):
    """Senzor care afișează 'Licență necesară' când nu există licență validă."""

    _attr_icon = "mdi:license"

    def __init__(self, coordinator: EblocCoordinator, id_user: int) -> None:
        super().__init__(coordinator, id_user)
        self._attr_name = "Licență necesară"
        self._attr_unique_id = f"{DOMAIN}_licenta_{id_user}"
        self._custom_entity_id = f"sensor.{DOMAIN}_{id_user}_licenta_necesara"

    @property
    def native_value(self) -> str:
        mgr = self.hass.data.get(DOMAIN, {}).get(LICENSE_DATA_KEY)
        if mgr is None:
            return "Licență necesară"
        status = mgr.status
        if status == "trial":
            return f"Evaluare ({mgr.trial_days_remaining} zile)"
        if status == "expired":
            return "Licență expirată"
        return "Licență necesară"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        mgr = self.hass.data.get(DOMAIN, {}).get(LICENSE_DATA_KEY)
        if mgr is None:
            return {}
        return mgr.as_dict()


# ──────────────────────────────────────────────
# 1. Cont utilizator
# Date din AppGetInfo.php
# ──────────────────────────────────────────────
class ContUtilizatorSensor(EblocEntity):
    """Cont utilizator — date complete din AppGetInfo.php."""

    _attr_icon = "mdi:account"

    def __init__(self, coordinator: EblocCoordinator, id_user: int) -> None:
        super().__init__(coordinator, id_user)
        self._attr_name = "Cont utilizator"
        self._attr_unique_id = f"{DOMAIN}_{id_user}_cont"
        self._custom_entity_id = f"sensor.{DOMAIN}_{id_user}_cont_utilizator"

    @property
    def native_value(self) -> str:
        if not self._license_valid:
            return "Licență necesară"
        data = self.coordinator.data or {}
        gi = data.get("get_info", {})
        return gi.get("email", "Necunoscut")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        if not self._license_valid:
            return {"Licență": "necesară"}
        data = self.coordinator.data or {}
        gi = data.get("get_info", {})

        attributes: dict[str, Any] = {}

        if gi.get("id_user"):
            attributes["ID utilizator"] = gi["id_user"]

        if gi.get("titlu_user"):
            attributes["Titlu"] = gi["titlu_user"]

        if gi.get("nume_user"):
            attributes["Nume"] = gi["nume_user"]

        if gi.get("prenume_user"):
            attributes["Prenume"] = gi["prenume_user"]

        if gi.get("email"):
            attributes["Email"] = gi["email"]

        if gi.get("ip_country"):
            attributes["Țară"] = gi["ip_country"]

        if gi.get("ip_state"):
            attributes["Regiune"] = gi["ip_state"]

        # Separator vizual
        attributes["--- Conexiune actuală"] = ""

        if gi.get("ip_city"):
            attributes["Oraș"] = gi["ip_city"]

        if gi.get("ip_lat"):
            attributes["Latitudine"] = gi["ip_lat"]

        if gi.get("ip_long"):
            attributes["Longitudine"] = gi["ip_long"]

        return attributes


# ──────────────────────────────────────────────
# 2. Asociație
# native_value = id_asoc (ID-ul asociației)
# Atribute din aInfoAsoc cu etichete românești
# ──────────────────────────────────────────────
class AsociatieSensor(EblocEntity):
    """Asociație — stare principală = ID asociație, atribute = date asociație."""

    _attr_icon = "mdi:office-building"

    def __init__(
        self, coordinator: EblocCoordinator, id_user: int,
        id_asoc, assoc_info: dict,
    ) -> None:
        super().__init__(coordinator, id_user)
        self._id_asoc = id_asoc
        self._assoc_info = assoc_info
        self._attr_name = f"Asociație"
        self._attr_unique_id = f"{DOMAIN}_{id_user}_{id_asoc}_asociatie"
        self._custom_entity_id = f"sensor.{DOMAIN}_{id_user}_asociatie_{id_asoc}"

    def _get_assoc_info(self) -> dict[str, Any]:
        """Caută datele actualizate din coordinator."""
        data = self.coordinator.data or {}
        gi = data.get("get_info", {})
        for a in gi.get("aInfoAsoc", []):
            if str(a.get("id", "")) == str(self._id_asoc):
                return a
        return self._assoc_info

    @property
    def native_value(self) -> str:
        """Stare principală = ID asociație."""
        if not self._license_valid:
            return "Licență necesară"
        return str(self._id_asoc)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        if not self._license_valid:
            return {"Licență": "necesară"}

        info = self._get_assoc_info()
        attributes: dict[str, Any] = {}

        if info.get("id"):
            attributes["ID asociație"] = info["id"]

        if info.get("nume"):
            attributes["Nume asociație"] = info["nume"]

        if info.get("cui"):
            cui_val = info["cui"]
            if info.get("cui_ro"):
                cui_val = f"RO{cui_val}"
            attributes["CUI"] = cui_val

        # Construim adresa completă
        parti_adresa = []
        if info.get("adr_strada"):
            parti_adresa.append(info["adr_strada"])
        if info.get("adr_nr"):
            parti_adresa.append(f"nr. {info['adr_nr']}")
        if info.get("adr_bloc"):
            parti_adresa.append(f"bl. {info['adr_bloc']}")
        if info.get("adr_scara"):
            parti_adresa.append(f"sc. {info['adr_scara']}")
        if info.get("adr_etaj"):
            parti_adresa.append(f"et. {info['adr_etaj']}")
        if info.get("adr_ap"):
            parti_adresa.append(f"ap. {info['adr_ap']}")

        adresa = ", ".join(parti_adresa)
        if info.get("adr_oras"):
            adresa += f", {info['adr_oras']}"
        if info.get("adr_judet"):
            adresa += f", jud. {info['adr_judet']}"
        if info.get("adr_cod_postal"):
            adresa += f", cod {info['adr_cod_postal']}"

        if adresa:
            attributes["Adresă"] = adresa

        if info.get("adr_oras"):
            attributes["Oraș"] = info["adr_oras"]

        if info.get("adr_sector"):
            attributes["Sector"] = info["adr_sector"]

        if info.get("adr_judet"):
            attributes["Județ"] = info["adr_judet"]

        # Separator — conducere
        has_conducere = any(info.get(k) for k in (
            "administrator", "presedinte", "cenzor", "contabil",
        ))
        if has_conducere:
            # Folosim titlul președintelui ca separator (dacă există)
            pres_label = info.get("titlu_presedinte", "Conducere")
            attributes[f"--- {pres_label}"] = ""

        if info.get("administrator"):
            label_admin = info.get("titlu_administrator", "Administrator")
            attributes[label_admin] = info["administrator"]

        if info.get("presedinte"):
            label_pres = info.get("titlu_presedinte", "Președinte")
            attributes[label_pres] = info["presedinte"]

        if info.get("cenzor"):
            label_cenz = info.get("titlu_cenzor", "Cenzor")
            attributes[label_cenz] = info["cenzor"]

        if info.get("contabil"):
            label_cont = info.get("titlu_contabil", "Contabil")
            attributes[label_cont] = info["contabil"]

        # Separator — încasări
        if info.get("locul_incasarilor"):
            attributes["--- Încasări"] = ""
            attributes["Locul încasărilor"] = info["locul_incasarilor"]

        if info.get("adr_telefon"):
            attributes["Telefon"] = info["adr_telefon"]

        if info.get("email"):
            attributes["Email asociație"] = info["email"]

        if info.get("msg_warning"):
            attributes["Avertisment"] = info["msg_warning"]

        return attributes


# ──────────────────────────────────────────────
# 3. Index/Contoare
# CONDIȚIE: senzor creat DOAR dacă id_contor are intrare în aInfoIndex
# ──────────────────────────────────────────────
class IndexContorSensor(EblocEntity):
    """Index contor — valoare curentă + detalii contor."""

    _attr_icon = "mdi:counter"
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = "m³"

    def __init__(
        self, coordinator: EblocCoordinator, id_user: int,
        id_asoc, id_ap, id_contor: int,
        contor_def: dict, contor_val: dict,
    ) -> None:
        super().__init__(coordinator, id_user)
        self._id_asoc = id_asoc
        self._id_ap = id_ap
        self._id_contor = id_contor
        self._contor_def = contor_def
        self._contor_val_initial = contor_val

        titlu = contor_def.get("titlu", f"Contor {id_contor}")
        titlu_slug = ha_slugify(titlu)
        self._titlu = titlu
        self._attr_name = f"Index {titlu}"
        self._attr_unique_id = f"{DOMAIN}_{id_user}_{id_contor}"
        self._custom_entity_id = f"sensor.{DOMAIN}_{id_user}_index_{titlu_slug}"

    def _find_contor_val(self) -> dict[str, Any] | None:
        """Caută datele contorului în aInfoIndex actualizat."""
        ap_data = self._get_ap_data(self._id_asoc, self._id_ap)
        contoare = ap_data.get("contoare", {})
        for cv in contoare.get("aInfoIndex", []):
            if str(cv.get("id_contor")) == str(self._id_contor):
                return cv
        if self._contor_val_initial:
            return self._contor_val_initial
        return None

    @property
    def native_value(self) -> float | str:
        if not self._license_valid:
            return "Licență necesară"
        cv = self._find_contor_val()
        if not cv:
            return 0.0

        # Prioritate: index_nou (dacă există și nu e null), altfel index_vechi
        raw_nou = cv.get("index_nou")
        raw_vechi = cv.get("index_vechi", INDEX_NOT_SET)

        # Încearcă index_nou mai întâi
        index_val = INDEX_NOT_SET
        if raw_nou is not None:
            try:
                index_val = int(raw_nou)
            except (ValueError, TypeError):
                index_val = INDEX_NOT_SET

        # Fallback la index_vechi dacă index_nou nu e valid
        if index_val == INDEX_NOT_SET:
            try:
                index_val = int(raw_vechi)
            except (ValueError, TypeError):
                return 0.0

        if index_val == INDEX_NOT_SET:
            return 0.0
        return round(index_val / NR_PERS_MULTIPLIER, 3)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        if not self._license_valid:
            return {"Licență": "necesară"}

        cv = self._find_contor_val()
        cd = self._contor_def
        attributes: dict[str, Any] = {}

        attributes["ID contor"] = self._id_contor
        attributes["Denumire contor"] = cd.get("titlu", "")

        tip_raw = cd.get("tip", "")
        tip_labels = {"1": "Apă rece", "2": "Apă caldă", "3": "Gaz", "4": "Energie"}
        attributes["Tip contor"] = tip_labels.get(str(tip_raw), f"Tip {tip_raw}")

        if cv:
            iv = cv.get("index_vechi", INDEX_NOT_SET)
            raw_nou = cv.get("index_nou")
            try:
                iv = int(iv)
            except (ValueError, TypeError):
                iv = INDEX_NOT_SET

            inu = INDEX_NOT_SET
            if raw_nou is not None:
                try:
                    inu = int(raw_nou)
                except (ValueError, TypeError):
                    inu = INDEX_NOT_SET

            # Index actual = index_nou dacă există, altfel index_vechi
            if inu != INDEX_NOT_SET:
                index_actual = inu
            elif iv != INDEX_NOT_SET:
                index_actual = iv
            else:
                index_actual = INDEX_NOT_SET

            attributes["Index actual"] = (
                f"{round(index_actual / NR_PERS_MULTIPLIER, 3)} m³"
                if index_actual != INDEX_NOT_SET else "Nesetat"
            )
            attributes["Index nou"] = (
                f"{round(inu / NR_PERS_MULTIPLIER, 3)} m³" if inu != INDEX_NOT_SET else "Nesetat"
            )

            can_edit = str(cv.get("can_edit_index", "0"))
            attributes["Editare permisă"] = "Da" if can_edit == "1" else "Nu"

            right_edit = str(cv.get("right_edit_index", "0"))
            attributes["Drept editare"] = "Da" if right_edit == "1" else "Nu"

            if cv.get("seria"):
                attributes["Serie contor"] = cv["seria"]

            if cv.get("eticheta"):
                attributes["Etichetă contor"] = cv["eticheta"]

            estimat = str(cv.get("estimat", "0"))
            attributes["Estimat"] = "Da" if estimat == "1" else "Nu"

        return attributes


# ──────────────────────────────────────────────
# 4. Factură restantă
# Stare: DOAR "Da" sau "Nu"
# ──────────────────────────────────────────────
class FacturaRestantaSensor(EblocEntity):
    """Factură restantă — Da/Nu + breakdown datorii."""

    _attr_icon = "mdi:file-document-alert"

    def __init__(
        self, coordinator: EblocCoordinator, id_user: int,
        id_asoc, id_ap, ap_info: dict,
    ) -> None:
        super().__init__(coordinator, id_user)
        self._id_asoc = id_asoc
        self._id_ap = id_ap
        self._ap_info = ap_info
        self._attr_name = "Factură restantă"
        self._attr_unique_id = f"{DOMAIN}_{id_user}_{id_asoc}_{id_ap}_factura_restanta"
        self._custom_entity_id = f"sensor.{DOMAIN}_{id_user}_factura_restanta"

    def _get_total_plata(self) -> int:
        """Returnează total_plata din API (în bani)."""
        ap_data = self._get_ap_data(self._id_asoc, self._id_ap)
        plateste = ap_data.get("plateste", {})
        if plateste.get("result") == "ok":
            for ap in plateste.get("aInfoAp", []):
                if str(ap.get("id_ap")) == str(self._id_ap):
                    tp = ap.get("total_plata", 0)
                    try:
                        return int(tp)
                    except (ValueError, TypeError):
                        return 0
        tp = self._ap_info.get("total_plata", 0)
        try:
            return int(tp)
        except (ValueError, TypeError):
            return 0

    @property
    def native_value(self) -> str:
        if not self._license_valid:
            return "Licență necesară"
        total_plata = self._get_total_plata()
        return "Da" if total_plata > 0 else "Nu"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        if not self._license_valid:
            return {"Licență": "necesară"}

        total = self._get_total_plata()
        total_lei = round(total / AMOUNT_DIVISOR, 2) if total else 0.0
        attributes: dict[str, Any] = {}

        # Breakdown din AppPlatesteGetAp → aDatAp
        ap_data = self._get_ap_data(self._id_asoc, self._id_ap)
        plateste = ap_data.get("plateste", {})
        if plateste.get("result") == "ok":
            for ap in plateste.get("aInfoAp", []):
                if str(ap.get("id_ap")) == str(self._id_ap):
                    dat_ap = ap.get("aDatAp", [])

                    # Grupăm per lună
                    luni_set = set()
                    for d in dat_ap:
                        l = d.get("luna", "")
                        if l:
                            luni_set.add(l)

                    # Header principal per lună
                    for luna_val in sorted(luni_set):
                        luna_name = luna_ro_scurt(luna_val)
                        luna_items = [
                            d for d in dat_ap if d.get("luna") == luna_val
                        ]
                        luna_total = 0
                        for d in luna_items:
                            try:
                                luna_total += int(d.get("suma", 0))
                            except (ValueError, TypeError):
                                pass
                        luna_total_lei = round(luna_total / AMOUNT_DIVISOR, 2)
                        attributes[f"Restanță luna {luna_name}"] = f"{luna_total_lei} lei"

                    # Separator + detalii
                    attributes["--- Detalii factură"] = ""
                    for d in dat_ap:
                        titlu = d.get("titlu", "")
                        luna = d.get("luna", "")
                        luna_name = luna_ro_scurt(luna)
                        suma = d.get("suma", 0)
                        try:
                            suma_lei = round(int(suma) / AMOUNT_DIVISOR, 2)
                        except (ValueError, TypeError):
                            suma_lei = 0.0
                        attributes[f"{titlu} {luna_name}"] = f"{suma_lei} lei"

                    # Total
                    attributes["--- Total restanțe"] = ""
                    attributes["Total"] = f"{total_lei} lei"
                    break
        else:
            attributes["Total"] = f"{total_lei} lei"

        return attributes


# ──────────────────────────────────────────────
# 5. Arhivă plăți
# Ultimele 12 plăți din aChitante, sume în Lei
# ──────────────────────────────────────────────
class ArhivaPlatiSensor(EblocEntity):
    """Arhivă plăți — ultimele 12 plăți."""

    _attr_icon = "mdi:credit-card-check"

    def __init__(
        self, coordinator: EblocCoordinator, id_user: int,
        id_asoc, id_ap,
    ) -> None:
        super().__init__(coordinator, id_user)
        self._id_asoc = id_asoc
        self._id_ap = id_ap
        self._attr_name = "Arhivă plăți"
        self._attr_unique_id = f"{DOMAIN}_{id_user}_{id_asoc}_{id_ap}_plati"
        self._custom_entity_id = f"sensor.{DOMAIN}_{id_user}_arhiva_plati"

    def _get_chitante(self) -> list[dict[str, Any]]:
        """Returnează lista de chitanțe din coordinator."""
        ap_data = self._get_ap_data(self._id_asoc, self._id_ap)
        plati = ap_data.get("plati", {})
        return plati.get("aChitante", [])

    @property
    def native_value(self) -> str:
        if not self._license_valid:
            return "Licență necesară"
        chitante = self._get_chitante()
        # Afișăm câte extragem efectiv (max 12), nu totalul din API
        return str(min(len(chitante), 12))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        if not self._license_valid:
            return {"Licență": "necesară"}

        chitante = self._get_chitante()
        afisate = chitante[:12]
        attributes: dict[str, Any] = {}

        attributes["Total plăți"] = len(afisate)

        for i, c in enumerate(afisate):
            # Format: "Plătită pe 26 februarie 2026 - 278.31 lei"
            data_raw = c.get("data", "")
            data_ro = luna_ro(data_raw) if data_raw else ""

            suma_raw = c.get("suma", 0)
            try:
                suma_lei = round(int(suma_raw) / AMOUNT_DIVISOR, 2)
            except (ValueError, TypeError):
                suma_lei = 0.0

            if data_ro:
                label = f"Plătită pe {data_ro}"
            else:
                label = f"Plata {i + 1}"

            attributes[label] = f"{suma_lei} lei"

        return attributes


# ──────────────────────────────────────────────
# 6. Număr persoane
# DOAR valoarea — FĂRĂ atribute secundare
# Citește din coordinator (nu din snapshot inițial)
# ──────────────────────────────────────────────
class NrPersoaneSensor(EblocEntity):
    """Număr persoane — doar valoarea, fără atribute."""

    _attr_icon = "mdi:account-group"

    def __init__(
        self, coordinator: EblocCoordinator, id_user: int,
        id_asoc, id_ap,
    ) -> None:
        super().__init__(coordinator, id_user)
        self._id_asoc = id_asoc
        self._id_ap = id_ap
        self._attr_name = "Număr persoane"
        self._attr_unique_id = f"{DOMAIN}_{id_user}_{id_asoc}_{id_ap}_nr_pers"
        self._custom_entity_id = f"sensor.{DOMAIN}_{id_user}_numar_persoane"

    @property
    def native_value(self) -> int | str:
        if not self._license_valid:
            return "Licență necesară"
        # Citim din datele live ale coordinator-ului (apartments)
        data = self.coordinator.data or {}
        apartments = data.get("apartments", {})
        ap_list = apartments.get(str(self._id_asoc), apartments.get(self._id_asoc, []))
        for ap in ap_list:
            if str(ap.get("id_ap", ap.get("id", ""))) == str(self._id_ap):
                nr_pers_raw = ap.get("nr_pers", 0)
                try:
                    return int(nr_pers_raw) // NR_PERS_MULTIPLIER
                except (ValueError, TypeError):
                    return 0
        return 0

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Fără atribute secundare — conform cerință."""
        return {}


# ──────────────────────────────────────────────
# 7. Tichete/Contact
# Detalii din AppContactGetTicketData.php
# ──────────────────────────────────────────────
class TicheteSensor(EblocEntity):
    """Tichete — număr total + detalii primele 5."""

    _attr_icon = "mdi:ticket-account"

    def __init__(
        self, coordinator: EblocCoordinator, id_user: int,
        id_asoc, id_ap,
    ) -> None:
        super().__init__(coordinator, id_user)
        self._id_asoc = id_asoc
        self._id_ap = id_ap
        self._attr_name = "Tichete"
        self._attr_unique_id = f"{DOMAIN}_{id_user}_{id_asoc}_{id_ap}_tichete"
        self._custom_entity_id = f"sensor.{DOMAIN}_{id_user}_tichete"

    @property
    def native_value(self) -> str:
        if not self._license_valid:
            return "Licență necesară"
        ap_data = self._get_ap_data(self._id_asoc, self._id_ap)
        contact = ap_data.get("contact", {})
        tichete = contact.get("aTickets", [])
        return str(len(tichete))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        if not self._license_valid:
            return {"Licență": "necesară"}

        ap_data = self._get_ap_data(self._id_asoc, self._id_ap)
        contact = ap_data.get("contact", {})
        tichete = contact.get("aTickets", [])
        ticket_details = ap_data.get("ticket_details", {})

        attributes: dict[str, Any] = {}
        attributes["Total tichete"] = len(tichete)

        for t in tichete[:5]:
            tid = str(t.get("id_ticket", t.get("id", "")))

            # Format compact: "Tichet ID 305715" → "Închis" / "Deschis"
            detail = ticket_details.get(tid, {})
            if detail.get("result") == "ok":
                open_val = detail.get("open", "0")
                status = "Deschis" if str(open_val) == "1" else "Închis"
            else:
                status = "Necunoscut"

            attributes[f"Tichet ID {tid}"] = status

        return attributes


# ──────────────────────────────────────────────
# 8. Carduri salvate
# AppSettingsGetCard.php → aCard
# ──────────────────────────────────────────────
class CarduriSensor(EblocEntity):
    """Carduri salvate — număr total + detalii per card."""

    _attr_icon = "mdi:credit-card-multiple"

    def __init__(self, coordinator: EblocCoordinator, id_user: int) -> None:
        super().__init__(coordinator, id_user)
        self._attr_name = "Carduri salvate"
        self._attr_unique_id = f"{DOMAIN}_{id_user}_carduri"
        self._custom_entity_id = f"sensor.{DOMAIN}_{id_user}_carduri_salvate"

    @property
    def native_value(self) -> str:
        if not self._license_valid:
            return "Licență necesară"
        data = self.coordinator.data or {}
        carduri = data.get("carduri", {})
        carduri_list = carduri.get("aCard", [])
        return str(len(carduri_list))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        if not self._license_valid:
            return {"Licență": "necesară"}

        data = self.coordinator.data or {}
        carduri = data.get("carduri", {})
        carduri_list = carduri.get("aCard", [])

        attributes: dict[str, Any] = {}
        attributes["Total carduri"] = len(carduri_list)

        for i, c in enumerate(carduri_list):
            nr = i + 1

            if c.get("titlu"):
                attributes[f"Card {nr} — Bancă"] = c["titlu"]

            if c.get("card_nr"):
                attributes[f"Card {nr} — Ultimele 4 cifre"] = c["card_nr"]

            if c.get("data_exp"):
                attributes[f"Card {nr} — Data expirare"] = c["data_exp"]

            if c.get("id_card"):
                attributes[f"Card {nr} — ID"] = c["id_card"]

            # Separator între carduri (nu după ultimul)
            if i < len(carduri_list) - 1:
                attributes[f"---{'_' * (i + 1)}"] = ""

        return attributes


# ──────────────────────────────────────────────
# 9. Citire permisă
# Logică de DATE (pattern eonromania):
#   indecsi_start <= today <= indecsi_end → "Da"
#   altfel → "Nu"
# ──────────────────────────────────────────────
class CitirePermisaSensor(EblocEntity):
    """Citire permisă — verificare pe bază de date (nu flag can_edit_index)."""

    _attr_icon = "mdi:calendar-check"

    def __init__(
        self, coordinator: EblocCoordinator, id_user: int,
        id_asoc, id_ap,
    ) -> None:
        super().__init__(coordinator, id_user)
        self._id_asoc = id_asoc
        self._id_ap = id_ap
        self._attr_name = "Citire permisă"
        self._attr_unique_id = f"{DOMAIN}_{id_user}_{id_asoc}_{id_ap}_citire_permisa"
        self._custom_entity_id = f"sensor.{DOMAIN}_{id_user}_citire_permisa"

    def _get_home_ap(self) -> dict[str, Any]:
        """Date din home_ap_data per id_asoc."""
        data = self.coordinator.data or {}
        home_ap = data.get("home_ap_data", {})
        return home_ap.get(self._id_asoc, home_ap.get(str(self._id_asoc), {}))

    @property
    def native_value(self) -> str:
        """Stare principală: Da/Nu pe bază de comparare date.

        Logică identică cu CitirePermisaSensor din eonromania:
        1. Verificare manuală pe date: indecsi_start <= today <= indecsi_end
        2. Fallback pe can_edit_index dacă datele lipsesc
        """
        if not self._license_valid:
            return "Licență necesară"

        hap = self._get_home_ap()
        indecsi_start = hap.get("indecsi_start", "")
        indecsi_end = hap.get("indecsi_end", "")

        # Verificare pe date (metoda principală)
        if indecsi_start and indecsi_end:
            try:
                today = dt_util.now().replace(tzinfo=None)
                start_date = None
                end_date = None

                for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
                    try:
                        start_date = datetime.strptime(indecsi_start, fmt)
                        break
                    except ValueError:
                        continue

                for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
                    try:
                        end_date = datetime.strptime(indecsi_end, fmt)
                        break
                    except ValueError:
                        continue

                if start_date and end_date:
                    if start_date <= today <= end_date:
                        return "Da"
                    return "Nu"
                if start_date and today >= start_date:
                    return "Da"
                return "Nu"
            except Exception as err:
                _LOGGER.exception(
                    "Eroare la determinarea stării CitirePermisa (asoc=%s): %s",
                    self._id_asoc, err,
                )
                return "Eroare"

        # Fallback: can_edit_index (doar dacă datele lipsesc complet)
        can_edit = str(hap.get("can_edit_index", "0"))
        return "Da" if can_edit == "1" else "Nu"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        if not self._license_valid:
            return {"Licență": "necesară"}

        hap = self._get_home_ap()
        attributes: dict[str, Any] = {}

        if hap.get("indecsi_start"):
            attributes["Perioadă start"] = hap["indecsi_start"]

        if hap.get("indecsi_end"):
            attributes["Perioadă sfârșit"] = hap["indecsi_end"]

        if hap.get("data_scadenta"):
            attributes["Data scadență"] = hap["data_scadenta"]

        can_edit = str(hap.get("can_edit_index", "0"))
        attributes["Flag API (can_edit_index)"] = "Da" if can_edit == "1" else "Nu"

        return attributes
