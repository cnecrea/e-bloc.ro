"""Client API asincron pentru comunicarea cu e-bloc.ro.

Toate endpoint-urile sunt preluate direct din debug_v2.py și 6_ebloc_api_results_v2.json.

Fluxul de autentificare:
  1. Login (GET AppLogin.php) → session_id + id_user
  2. GetInfo (GET AppGetInfo.php) → asociații + drepturi de acces
  3. GetAp (GET AppHomeGetAp.php) → apartamente per asociație (aInfoAp cu id_ap)
  4. Restul endpoint-urilor necesită session_id + id_asoc [+ id_ap] [+ luna]

Dual server:
  - main (www.e-bloc.ro): login + write + read
  - read (read.e-bloc.ro): cache read-only (mai rapid)
"""

from __future__ import annotations

import hashlib
import logging
import time
from typing import Any

import aiohttp
from aiohttp import ClientSession, ClientTimeout

from .const import (
    API_KEY,
    API_TIMEOUT,
    APP_VERSION,
    BASE_URL,
    DEVICE_BRAND,
    DEVICE_MODEL,
    DEVICE_TYPE,
    HEADERS,
    NR_PERS_MULTIPLIER,
    OS_VERSION,
    READ_URL,
)

_LOGGER = logging.getLogger(__name__)


def _pass_sha512(password: str) -> str:
    """SHA-512 lowercase hex, zero-padded la 128 chars — identic cu AppUtils.getHashSHA512."""
    return hashlib.sha512(password.encode("utf-8")).hexdigest().zfill(128)


def _pass_complexity(password: str) -> int:
    """Calculează complexitatea parolei exact ca în AuthRepository.logIn()."""
    has_letters = any(c.isalpha() for c in password)
    has_digits = any(c.isdigit() for c in password)
    if len(password) >= 8 and has_letters and has_digits:
        return 2
    return 1


class EblocApiClient:
    """Client API asincron pentru e-bloc.ro."""

    def __init__(
        self,
        session: ClientSession,
        email: str,
        password: str,
    ) -> None:
        """Inițializează clientul API."""
        self._session = session
        self._email = email
        self._password = password
        self._pass_sha = _pass_sha512(password)
        self._pass_complexity = _pass_complexity(password)

        self._session_id: str | None = None
        self._id_user: int | None = None
        self._timeout = ClientTimeout(total=API_TIMEOUT)

        # Date descoperite la login/autodiscovery
        self._associations: list[dict[str, Any]] = []
        # apartments: dict cu chei id_asoc (int/str), valori = lista din aInfoAp
        # Fiecare element din aInfoAp are: id_ap, ap, nume, total_plata, cod_client, etc.
        self._apartments: dict[Any, list[dict[str, Any]]] = {}
        self._access_rights: dict[str, bool] = {}
        self._home_ap_data: dict[Any, dict[str, Any]] = {}
        self._get_info_data: dict[str, Any] = {}
        self._luna_curenta: str = ""

    # ─── Proprietăți publice ───

    @property
    def session_id(self) -> str | None:
        return self._session_id

    @property
    def id_user(self) -> int | None:
        return self._id_user

    @property
    def associations(self) -> list[dict[str, Any]]:
        return self._associations

    @property
    def apartments(self) -> dict[Any, list[dict[str, Any]]]:
        return self._apartments

    @property
    def access_rights(self) -> dict[str, bool]:
        return self._access_rights

    @property
    def home_ap_data(self) -> dict[Any, dict[str, Any]]:
        return self._home_ap_data

    @property
    def get_info_data(self) -> dict[str, Any]:
        return self._get_info_data

    @property
    def luna_curenta(self) -> str:
        return self._luna_curenta

    @property
    def is_authenticated(self) -> bool:
        return self._session_id is not None

    # ─── HTTP Helpers ───

    async def _api_get(
        self,
        endpoint: str,
        params: dict[str, Any],
        use_read_server: bool = False,
    ) -> dict[str, Any]:
        """Face un GET request la API și returnează JSON-ul."""
        base = READ_URL if use_read_server else BASE_URL
        url = f"{base}/{endpoint}"
        params["debug"] = 0

        try:
            async with self._session.get(
                url,
                params=params,
                timeout=self._timeout,
                headers=HEADERS,
            ) as resp:
                resp.raise_for_status()
                return await resp.json(content_type=None)
        except aiohttp.ClientError as err:
            _LOGGER.error("[Ebloc:API] GET %s eșuat: %s", endpoint, err)
            return {"result": "ERROR", "error": str(err)}
        except Exception as err:  # noqa: BLE001
            _LOGGER.error("[Ebloc:API] GET %s excepție: %s", endpoint, err)
            return {"result": "ERROR", "error": str(err)}

    async def _api_post_json(
        self,
        endpoint: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Face un POST request cu JSON body și returnează JSON-ul."""
        url = f"{BASE_URL}/{endpoint}"

        try:
            async with self._session.post(
                url,
                json=payload,
                timeout=self._timeout,
                headers={**HEADERS, "Content-Type": "application/json"},
            ) as resp:
                resp.raise_for_status()
                return await resp.json(content_type=None)
        except aiohttp.ClientError as err:
            _LOGGER.error("[Ebloc:API] POST %s eșuat: %s", endpoint, err)
            return {"result": "ERROR", "error": str(err)}
        except Exception as err:  # noqa: BLE001
            _LOGGER.error("[Ebloc:API] POST %s excepție: %s", endpoint, err)
            return {"result": "ERROR", "error": str(err)}

    # ─── Autentificare ───

    async def async_login(self) -> bool:
        """Login la e-bloc.ro. Returnează True dacă reușit."""
        params = {
            "key": API_KEY,
            "user": self._email,
            "pass_sha": self._pass_sha,
            "pass_complexity": self._pass_complexity,
            "app_version": APP_VERSION,
            "os_version": OS_VERSION,
            "device_brand": DEVICE_BRAND,
            "device_model": DEVICE_MODEL,
            "device_type": DEVICE_TYPE,
            "facebook_id": "",
            "google_id": "",
            "apple_id": "",
            "nume_user": "",
            "prenume_user": "",
            "facebook_access_token": "",
            "google_id_token": "",
        }

        data = await self._api_get("AppLogin.php", params)

        if data.get("result") == "ok":
            self._session_id = data.get("session_id", "")
            self._id_user = data.get("id_user", 0)
            # Conversie la int dacă e string
            if isinstance(self._id_user, str):
                self._id_user = int(self._id_user)
            _LOGGER.info("[Ebloc:API] Login reușit: id_user=%s", self._id_user)
            return True

        _LOGGER.warning(
            "[Ebloc:API] Login eșuat: result=%s, message=%s",
            data.get("result"),
            data.get("message", ""),
        )
        return False

    async def async_validate_session(self) -> bool:
        """Verifică dacă sesiunea curentă e validă pe server."""
        if not self._session_id:
            return False
        data = await self._api_get(
            "AppGetInfo.php", {"session_id": self._session_id}
        )
        return data.get("result") == "ok"

    async def async_ensure_authenticated(self) -> bool:
        """Asigură o sesiune validă — verifică apoi re-login dacă e cazul."""
        if self._session_id:
            if await self.async_validate_session():
                return True
            _LOGGER.debug("[Ebloc:API] Sesiunea a expirat, re-login...")
        return await self.async_login()

    # ─── Autodiscovery (GetInfo + GetAp) ───

    async def async_autodiscover(self) -> bool:
        """Descoperă asociații, apartamente, drepturi. Se apelează după login."""
        if not self._session_id:
            return False

        # 1. AppGetInfo.php → asociații + drepturi de acces per pagină
        data = await self._api_get(
            "AppGetInfo.php", {"session_id": self._session_id}
        )
        if data.get("result") != "ok":
            _LOGGER.error("[Ebloc:API] GetInfo eșuat: %s", data.get("result"))
            return False

        self._get_info_data = data
        self._associations = data.get("aInfoAsoc", [])

        # Parsăm drepturile de acces per pagină
        rights_mapping = {
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
        self._access_rights = {
            internal: bool(data.get(api_key))
            for api_key, internal in rights_mapping.items()
        }

        _LOGGER.debug(
            "[Ebloc:API] Descoperite %d asociații, drepturi: %s",
            len(self._associations),
            {k: v for k, v in self._access_rights.items() if v},
        )

        # 2. AppHomeGetAp.php per asociație → aInfoAp (cu id_ap, ap, total_plata, etc.)
        self._apartments = {}
        self._home_ap_data = {}
        self._luna_curenta = ""

        for assoc in self._associations:
            id_asoc = assoc.get("id", 0)
            ap_data = await self._api_get(
                "AppHomeGetAp.php",
                {"session_id": self._session_id, "id_asoc": id_asoc},
            )

            if ap_data.get("result") == "ok":
                # aInfoAp conține: id_ap, ap, nume, total_plata, cod_client,
                # right_edit_nr_pers, nr_pers, nr_fonduri, etc.
                self._apartments[id_asoc] = ap_data.get("aInfoAp", [])

                if not self._luna_curenta:
                    self._luna_curenta = ap_data.get("luna", "")

                # Sub-drepturi la nivel de asociație
                self._home_ap_data[id_asoc] = {
                    "right_email": ap_data.get("right_email", "0"),
                    "right_global_edit_users": ap_data.get(
                        "right_global_edit_users", "0"
                    ),
                    "right_edit_users": ap_data.get("right_edit_users", "0"),
                    "can_edit_index": ap_data.get("can_edit_index", "0"),
                    "indecsi_start": ap_data.get("indecsi_start", ""),
                    "indecsi_end": ap_data.get("indecsi_end", ""),
                    "luna": ap_data.get("luna", ""),
                    "luna_start": ap_data.get("luna_start", ""),
                    "luna_end": ap_data.get("luna_end", ""),
                    "data_scadenta": ap_data.get("data_scadenta", ""),
                    "plata_card": ap_data.get("plata_card", "0"),
                }

        _LOGGER.info(
            "[Ebloc:API] Autodiscovery complet: %d asociații, %d total apartamente",
            len(self._associations),
            sum(len(aps) for aps in self._apartments.values()),
        )
        return True

    # ─── Endpoint-uri READ ───
    # Toate endpoint-urile de mai jos sunt verificate în debug_v2.py

    async def async_get_contoare_index(
        self, id_asoc: int | str, luna: str
    ) -> dict[str, Any]:
        """AppContoareGetIndex.php → aInfoAp, aInfoContoare, aInfoIndex."""
        return await self._api_get(
            "AppContoareGetIndex.php",
            {
                "session_id": self._session_id,
                "id_asoc": id_asoc,
                "luna": luna,
            },
        )

    async def async_get_contoare_luni(
        self, id_asoc: int | str
    ) -> dict[str, Any]:
        """AppContoareGetIndexLuni.php → aLuni (obiecte CounterMonth)."""
        return await self._api_get(
            "AppContoareGetIndexLuni.php",
            {
                "session_id": self._session_id,
                "id_asoc": id_asoc,
            },
        )

    async def async_get_facturi(
        self, id_asoc: int | str, id_ap: int | str, luna: str
    ) -> dict[str, Any]:
        """AppFacturiGetData.php → aFactChelt, aFactFond, aFactVenit, aLuni."""
        return await self._api_get(
            "AppFacturiGetData.php",
            {
                "session_id": self._session_id,
                "id_asoc": id_asoc,
                "id_ap": id_ap,
                "luna": luna,
            },
        )

    async def async_get_nr_pers(
        self, id_asoc: int | str, id_ap: int | str
    ) -> dict[str, Any]:
        """AppHomeGetNrPersoane.php → nr_pers_def, aLuna, aDeclaratii.

        NOTĂ: acest endpoint NU primește luna ca parametru!
        """
        return await self._api_get(
            "AppHomeGetNrPers.php",
            {
                "session_id": self._session_id,
                "id_asoc": id_asoc,
                "id_ap": id_ap,
            },
        )

    async def async_get_contact_tickets(
        self, id_asoc: int | str, id_ap: int | str, this_user: int = 1
    ) -> dict[str, Any]:
        """AppContactGetTickets.php → aInfoAp, aTickets.

        this_user=1: doar tichetele utilizatorului curent
        this_user=0: toate tichetele
        """
        return await self._api_get(
            "AppContactGetTickets.php",
            {
                "session_id": self._session_id,
                "id_asoc": id_asoc,
                "id_ap": id_ap,
                "this_user": this_user,
            },
        )

    async def async_get_ticket_detail(
        self, id_ticket: int | str
    ) -> dict[str, Any]:
        """AppContactGetTicketData.php → id, open, aReply.

        Returnează detaliile unui tichet: status (open), replies cu mesaj + time.
        """
        return await self._api_get(
            "AppContactGetTicketData.php",
            {
                "session_id": self._session_id,
                "id_ticket": id_ticket,
            },
        )

    async def async_get_istoric_plati(
        self, id_asoc: int | str, id_ap: int | str
    ) -> dict[str, Any]:
        """AppIstoricPlatiGetPlati.php → aChitante."""
        return await self._api_get(
            "AppIstoricPlatiGetPlati.php",
            {
                "session_id": self._session_id,
                "id_asoc": id_asoc,
                "id_ap": id_ap,
            },
        )

    async def async_get_carduri(self) -> dict[str, Any]:
        """AppSettingsGetCard.php → aCard.

        Câmpuri per card: id_card, titlu, tip, card_nr (ultimi 4), data_exp.
        """
        return await self._api_get(
            "AppSettingsGetCard.php",
            {"session_id": self._session_id},
        )

    async def async_get_wallet(
        self, id_asoc: int | str, id_ap: int | str
    ) -> dict[str, Any]:
        """AppHomeGetWalletItems.php → sold, aItems."""
        return await self._api_get(
            "AppHomeGetWalletItems.php",
            {
                "session_id": self._session_id,
                "id_asoc": id_asoc,
                "id_ap": id_ap,
            },
        )

    async def async_get_home_stat(
        self, id_asoc: int | str, id_ap: int | str, luna: str
    ) -> dict[str, Any]:
        """AppHomeGetStat.php → aLocatari, aStat."""
        return await self._api_get(
            "AppHomeGetStat.php",
            {
                "session_id": self._session_id,
                "id_asoc": id_asoc,
                "id_ap": id_ap,
                "luna": luna,
            },
        )

    async def async_get_solduri(
        self, id_asoc: int | str, id_ap: int | str
    ) -> dict[str, Any]:
        """AppSolduriGetData.php → sold_global, aDatorii, aConturi, aFonduri."""
        return await self._api_get(
            "AppSolduriGetData.php",
            {
                "session_id": self._session_id,
                "id_asoc": id_asoc,
                "id_ap": id_ap,
            },
        )

    async def async_get_plateste_ap(
        self, id_asoc: int | str, id_ap: int | str
    ) -> dict[str, Any]:
        """AppPlatesteGetAp.php → aCard, aInfoAp (cu total_plata + aDatAp breakdown)."""
        return await self._api_get(
            "AppPlatesteGetAp.php",
            {
                "session_id": self._session_id,
                "id_asoc": id_asoc,
                "id_ap": id_ap,
            },
        )

    # ─── Endpoint-uri WRITE ───

    async def async_set_nr_persoane(
        self,
        id_asoc: int | str,
        id_ap: int | str,
        luna: str,
        nr_pers: int,
    ) -> dict[str, Any]:
        """AppHomeSetNrPers.php — setează numărul de persoane.

        nr_pers e în format UI (0-10). Se multiplică ×1000 la trimitere.
        """
        if nr_pers < 0 or nr_pers > 10:
            return {"result": "ERROR", "error": f"nr_pers={nr_pers} invalid (0-10)"}

        return await self._with_relogin(
            lambda: self._api_get(
                "AppHomeSetNrPers.php",
                {
                    "session_id": self._session_id,
                    "id_asoc": id_asoc,
                    "id_ap": id_ap,
                    "luna": luna,
                    "nr_pers": nr_pers * NR_PERS_MULTIPLIER,
                },
            )
        )

    async def async_set_index_contoare(
        self,
        indexes: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """AppContoareSetIndexes.php — trimite indexuri contoare.

        `indexes` e o listă de dict-uri cu:
          - id_asoc, id_ap, id_contor (int)
          - index_nou (int): valoarea noului index × 1000

        POST JSON: {"session_id": "...", "aIndex": [...]}
        """
        a_index = []
        for idx in indexes:
            a_index.append({
                "id_asoc": idx["id_asoc"],
                "id_ap": idx["id_ap"],
                "id_contor": idx["id_contor"],
                "index_nou": idx["index_nou"],
                "img_data": "",
                "img_guid": "",
            })

        return await self._with_relogin(
            lambda: self._api_post_json(
                "AppContoareSetIndexes.php",
                {"session_id": self._session_id, "aIndex": a_index},
            )
        )

    async def _with_relogin(self, call) -> dict[str, Any]:
        """Execută o scriere; dacă sesiunea a expirat (nologin), re-login și o reia o dată.

        `call` construiește cererea la fiecare apel, deci reluarea folosește
        session_id-ul nou.
        """
        result = await call()
        if result.get("result") == "nologin":
            _LOGGER.info("[Ebloc:API] Sesiune expirată la scriere — re-login și reîncerc")
            if await self.async_login():
                result = await call()
        return result

    # ─── Metode helper ───

    def get_all_apartments(self) -> list[tuple[Any, Any, str]]:
        """Returnează toate (id_asoc, id_ap, label) disponibile."""
        result = []
        for id_asoc, aps in self._apartments.items():
            assoc_name = ""
            for a in self._associations:
                if str(a.get("id")) == str(id_asoc):
                    assoc_name = a.get("nume", str(id_asoc))
                    break

            for ap in aps:
                id_ap = ap.get("id_ap", ap.get("id", 0))
                ap_name = ap.get("ap", ap.get("nr_ap", "?"))
                label = f"{assoc_name} - Ap. {ap_name}"
                result.append((id_asoc, id_ap, label))

        return result

    def export_session(self) -> dict[str, Any] | None:
        """Exportă sesiunea curentă (pentru salvare în config_entry)."""
        if not self._session_id:
            return None
        return {
            "session_id": self._session_id,
            "id_user": self._id_user,
            "timestamp": time.time(),
        }

    def inject_session(self, session_data: dict[str, Any]) -> None:
        """Injectează o sesiune salvată anterior."""
        self._session_id = session_data.get("session_id")
        self._id_user = session_data.get("id_user")
        if isinstance(self._id_user, str):
            self._id_user = int(self._id_user)
        _LOGGER.debug(
            "[Ebloc:API] Sesiune injectată: session_id=%s..., id_user=%s",
            (self._session_id or "")[:20],
            self._id_user,
        )
