"""Client asincron pentru API-ul e-bloc.ro (cel folosit de aplicația Android).

Fluxul este cel al aplicației e-bloc.ro v9.12:
  1. IP-ul public (XiGetRealIp.php), trimis ca ip_public la AppLogin și la AppGetInfo
  2. AppLogin.php → session_id + id_user
  3. AppGetInfo.php → asociații + drepturi de acces, separat pentru fiecare asociație
  4. AppHomeGetAp.php (per asociație) → apartamente, luna curentă, perioada de citire
  5. restul endpoint-urilor primesc session_id + id_asoc [+ id_ap] [+ luna]

Fiecare răspuns are câmpul „result": „ok" sau „empty" înseamnă date valide; „nologin"
înseamnă sesiune expirată — sau o pagină la care asociația nu are drept, motiv pentru
care cererile se fac doar pentru paginile permise (vezi has_access).
"""

from __future__ import annotations

import hashlib
import logging
import math
import time
from typing import Any

import aiohttp
from aiohttp import ClientSession, ClientTimeout

from .const import (
    ACCESS_PAGES,
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
    PUBLIC_IP_MAX_AGE,
    PUBLIC_IP_TIMEOUT,
    PUBLIC_IP_URL,
    READ_URL,
)
from .logfmt import seconds

_LOGGER = logging.getLogger(__name__)

# Rezultatele care înseamnă date valide
RESULT_OK = ("ok", "empty")
# Rezultatele puse de client când solicitarea nu a primit un răspuns utilizabil
RESULT_FAILED = ("HTTP_ERROR", "TIMEOUT", "NETWORK_ERROR", "INVALID")

# Motivele refuzului la autentificare (Constants.RESPONSE_STATUS_* din aplicație)
_LOGIN_FAILURES = {
    "nok_user": "contul nu există",
    "nok_pass": "parola este greșită",
    "nok_nopass": "contul nu are parolă (a fost creat prin Google, Facebook sau Apple)",
    "nok": "datele de autentificare nu sunt corecte",
    "lock": "contul este blocat temporar",
    "old": "serverul nu mai acceptă versiunea de aplicație trimisă",
    "expired": "contul a expirat",
}

# Clasele de caractere din PasswordValidator (aplicația v9.12)
_PASS_CLASSES = (
    "abcdefghijklmnopqrstuvwxyz",
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ",
    " !\"#$%&'()*+,-./:;<=>?@[\\]^_{|}~",
    "0123456789",
)


def _pass_sha512(password: str) -> str:
    """SHA-512 lowercase hex, 128 de caractere — identic cu AppUtils.getHashSHA512."""
    return hashlib.sha512(password.encode("utf-8")).hexdigest().zfill(128)


def _pass_entropy(password: str) -> float:
    """PasswordValidator.getEntropy: lungime × ln(mărimea alfabetului folosit)."""
    base, seen = 0, set()
    for ch in password:
        for chars in _PASS_CLASSES:
            if ch in chars:
                if chars not in seen:
                    base += len(chars)
                    seen.add(chars)
                break
        else:
            base += 1
    return round(len(password) * math.log(base) * 100) / 100 if base > 1 else 0.0


def _pass_complexity(password: str) -> int:
    """Complexitatea parolei, ca în AuthRepository.logIn(): 2 = puternică, 1 = slabă."""
    has_letters = any(c.isalpha() for c in password)
    has_digits = any(c.isdigit() for c in password)
    if len(password) >= 8 and has_letters and has_digits and _pass_entropy(password) >= 20.0:
        return 2
    return 1


def _to_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def is_ok(data: Any) -> bool:
    """Răspuns cu date valide."""
    return isinstance(data, dict) and data.get("result") in RESULT_OK


def describe_result(data: Any) -> str:
    """Motivul unui răspuns care nu conține date valide, pe înțelesul utilizatorului."""
    if not isinstance(data, dict):
        return "răspuns neașteptat"
    result = data.get("result")
    if result == "nologin":
        return "sesiunea a expirat"
    if result in ("deny", "deny_all", "deny_user", "deny_imported", "acces"):
        return "acces refuzat de server"
    if data.get("error"):
        return str(data["error"])
    if data.get("message"):
        return str(data["message"])
    return f"răspuns „{result}”" if result else "răspuns fără rezultat"


class EblocApiClient:
    """Client API asincron pentru e-bloc.ro."""

    def __init__(self, session: ClientSession, email: str, password: str) -> None:
        """Inițializează clientul API."""
        self._session = session
        self._email = email
        self._pass_sha = _pass_sha512(password)
        self._pass_complexity = _pass_complexity(password)
        self._timeout = ClientTimeout(total=API_TIMEOUT)

        self._session_id: str | None = None
        self._id_user: int | None = None
        # Motivul ultimului eșec de autentificare / citire a contului (pentru UpdateFailed)
        self.last_error = ""
        # Probleme la citirea apartamentelor (le preia raportul actualizării)
        self.account_problems: list[tuple[str, str, str]] = []
        # Statisticile solicitărilor API, pe actualizare (vezi reset_stats)
        self.stats: dict[str, Any] = {}
        self.reset_stats()
        self._public_ip = ""
        self._public_ip_time = 0.0

        # Date descoperite din AppGetInfo + AppHomeGetAp (reîmprospătate la fiecare actualizare)
        self._get_info_data: dict[str, Any] = {}
        self._associations: list[dict[str, Any]] = []
        # pagină → {id_asoc (str) → intrarea din aAccesPage*}
        self._access_by_asoc: dict[str, dict[str, dict[str, Any]]] = {}
        # id_asoc → aInfoAp (id_ap, ap, nr_pers, total_plata, cod_client, ...)
        self._apartments: dict[Any, list[dict[str, Any]]] = {}
        self._home_ap_data: dict[Any, dict[str, Any]] = {}
        self._luna_curenta = ""

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
    def access_by_asoc(self) -> dict[str, dict[str, dict[str, Any]]]:
        return self._access_by_asoc

    @property
    def access_rights(self) -> dict[str, bool]:
        """Paginile la care contul are acces în cel puțin o asociație."""
        return {page: bool(self._access_by_asoc.get(page)) for page in ACCESS_PAGES.values()}

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

    def has_access(self, page: str, id_asoc: Any) -> bool:
        """Dreptul pe pagină pentru asociația dată (ca AppUtils.get*Page din aplicație)."""
        return str(id_asoc) in self._access_by_asoc.get(page, {})

    def pages_for(self, id_asoc: Any) -> list[str]:
        """Paginile permise pentru o asociație."""
        return sorted(p for p, asocs in self._access_by_asoc.items() if str(id_asoc) in asocs)

    # ─── HTTP ───

    def reset_stats(self) -> dict[str, Any]:
        """Începe o nouă numărătoare a solicitărilor API; o întoarce pe cea anterioară."""
        previous = self.stats
        self.stats = {"total": 0, "ok": 0, "respinse": 0, "esuate": 0, "durata": 0.0,
                      "cea_mai_lenta": ("", 0.0)}
        return previous

    @staticmethod
    def _context(params: dict[str, Any], json_body: dict[str, Any] | None) -> list[str]:
        """Parametrii neconfidențiali care identifică solicitarea (fără sesiune, cont, parolă, IP)."""
        parts = []
        if params.get("id_asoc"):
            parts.append(f"asociația {params['id_asoc']}")
        if params.get("id_ap"):
            parts.append(f"apartamentul {params['id_ap']}")
        if params.get("luna"):
            parts.append(f"luna {params['luna']}")
        if params.get("id_ticket"):
            parts.append(f"tichetul {params['id_ticket']}")
        if json_body and isinstance(json_body.get("aIndex"), list):
            parts.append(f"indecși trimiși: {len(json_body['aIndex'])}")
        return parts

    async def _request(
        self,
        method: str,
        endpoint: str,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        use_read_server: bool = False,
    ) -> dict[str, Any]:
        """O solicitare API. Nu ridică excepții de rețea: le întoarce ca rezultat.

        Log: metoda, endpoint-ul, contextul (asociație, apartament, lună), codul HTTP,
        rezultatul, durata și, la eșec, motivul. Niciodată sesiunea, contul sau parola.
        """
        url = f"{READ_URL if use_read_server else BASE_URL}/{endpoint}"
        query = {**(params or {}), "debug": 0}
        headers = {**HEADERS, "Content-Type": "application/json"} if json_body is not None else HEADERS
        start = time.monotonic()
        http_status: int | None = None
        try:
            async with self._session.request(
                method, url, params=query, json=json_body, timeout=self._timeout, headers=headers
            ) as resp:
                http_status = resp.status
                if resp.status >= 400:
                    data: Any = {"result": "HTTP_ERROR", "error": f"serverul a răspuns cu HTTP {resp.status}"}
                else:
                    data = await resp.json(content_type=None)
        except TimeoutError:
            data = {"result": "TIMEOUT", "error": f"serverul nu a răspuns în {API_TIMEOUT} s"}
        except aiohttp.ClientError as err:
            data = {"result": "NETWORK_ERROR", "error": f"eroare de rețea ({type(err).__name__})"}
        except ValueError:
            data = {"result": "INVALID", "error": "serverul a trimis un răspuns care nu este JSON"}
        if not isinstance(data, dict):
            data = {"result": "INVALID", "error": "serverul a trimis un răspuns neașteptat"}

        elapsed = time.monotonic() - start
        result = data.get("result", "fără rezultat")
        stats = self.stats
        stats["total"] += 1
        stats["durata"] += elapsed
        if result in RESULT_OK:
            stats["ok"] += 1
        elif result in RESULT_FAILED:
            stats["esuate"] += 1
        else:
            stats["respinse"] += 1
        if elapsed > stats["cea_mai_lenta"][1]:
            stats["cea_mai_lenta"] = (endpoint, elapsed)

        fields = [
            f"{method} {endpoint}" + (" (server read)" if use_read_server else ""),
            *self._context(params or {}, json_body),
            f"HTTP {http_status}" if http_status is not None else "fără răspuns HTTP",
            f"rezultat: {result}",
            seconds(elapsed),
        ]
        if result not in RESULT_OK and result != "HTTP_ERROR":
            fields.append(describe_result(data))
        _LOGGER.debug(" | ".join(fields))
        return data

    async def _get(self, endpoint: str, **params: Any) -> dict[str, Any]:
        return await self._request("GET", endpoint, {"session_id": self._session_id, **params})

    async def _async_public_ip(self) -> str:
        """IP-ul public, ca IpRealService din aplicație (opțional: la eroare se trimite gol)."""
        if self._public_ip and time.monotonic() - self._public_ip_time < PUBLIC_IP_MAX_AGE:
            return self._public_ip
        try:
            async with self._session.get(
                PUBLIC_IP_URL, timeout=ClientTimeout(total=PUBLIC_IP_TIMEOUT)
            ) as resp:
                text = (await resp.text()).strip() if resp.status == 200 else ""
        except (TimeoutError, aiohttp.ClientError):
            text = ""
        if text and len(text) < 150:
            self._public_ip, self._public_ip_time = text, time.monotonic()
            _LOGGER.debug("IP public obținut.")
        else:
            _LOGGER.debug("IP public indisponibil; se trimite gol, ca în aplicația oficială.")
        return self._public_ip

    # ─── Autentificare ───

    async def async_login(self) -> bool:
        """Autentificare nouă. Returnează True dacă a reușit."""
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
            "ip_public": await self._async_public_ip(),
            "facebook_id": "",
            "google_id": "",
            "apple_id": "",
            "nume_user": "",
            "prenume_user": "",
            "facebook_access_token": "",
            "google_id_token": "",
        }
        data = await self._request("GET", "AppLogin.php", params)

        if data.get("result") == "ok" and data.get("session_id"):
            self._session_id = str(data["session_id"])
            self._id_user = _to_int(data.get("id_user"))
            _LOGGER.debug("Autentificare reușită.")
            return True

        self._session_id = None
        motiv = _LOGIN_FAILURES.get(str(data.get("result")), describe_result(data))
        # Debug, nu warning: motivul ajunge în eroarea afișată de Home Assistant (o singură dată)
        self.last_error = f"Autentificarea la e-bloc.ro a eșuat: {motiv}"
        _LOGGER.debug("%s.", self.last_error)
        return False

    async def _async_get_info(self) -> dict[str, Any]:
        return await self._get("AppGetInfo.php", ip_public=await self._async_public_ip())

    async def async_refresh_account(self) -> bool:
        """Reîmprospătează contul: sesiunea, asociațiile, drepturile și apartamentele.

        Ca aplicația: AppGetInfo cu sesiunea existentă; dacă a expirat, autentificare nouă.
        """
        if not self._session_id and not await self.async_login():
            return False
        info = await self._async_get_info()
        if info.get("result") == "nologin":
            _LOGGER.debug("Sesiunea a expirat la citirea contului; se face o autentificare nouă.")
            if not await self.async_login():
                return False
            info = await self._async_get_info()
        if info.get("result") != "ok":
            self.last_error = f"Datele contului nu au putut fi citite: {describe_result(info)}"
            _LOGGER.debug("%s.", self.last_error)
            return False
        self.last_error = ""
        self._apply_info(info)
        await self._async_load_apartments()
        return True

    async def async_autodiscover(self) -> bool:
        """Asociații, drepturi și apartamente (după async_login; folosit de config flow)."""
        if not self._session_id:
            return False
        info = await self._async_get_info()
        if info.get("result") != "ok":
            self.last_error = f"Datele contului nu au putut fi citite: {describe_result(info)}"
            _LOGGER.warning("%s.", self.last_error)
            return False
        self._apply_info(info)
        await self._async_load_apartments()
        # Aici nu există un raport de actualizare care să preia problemele
        for _sursa, id_asoc, motiv in self.account_problems:
            _LOGGER.warning("Apartamentele asociației %s nu au putut fi citite: %s.", id_asoc, motiv)
        return True

    def _apply_info(self, info: dict[str, Any]) -> None:
        """Asociațiile și drepturile din AppGetInfo."""
        self._get_info_data = info
        self._associations = info.get("aInfoAsoc") or []
        self._access_by_asoc = {
            page: {
                str(entry["id_asoc"]): entry
                for entry in (info.get(api_key) or [])
                if isinstance(entry, dict) and entry.get("id_asoc")
            }
            for api_key, page in ACCESS_PAGES.items()
        }

    async def _async_load_apartments(self) -> None:
        """AppHomeGetAp pentru fiecare asociație. La eroare se păstrează datele anterioare."""
        apartments: dict[Any, list[dict[str, Any]]] = {}
        home: dict[Any, dict[str, Any]] = {}
        luna = ""
        # (sursă, asociație, motiv) — raportate în blocul de avertisment al actualizării
        self.account_problems = []

        for assoc in self._associations:
            id_asoc = assoc.get("id")
            if not id_asoc:
                continue
            data = await self._get("AppHomeGetAp.php", id_asoc=id_asoc)
            if data.get("result") == "ok":
                apartments[id_asoc] = data.get("aInfoAp") or []
                # Câmpurile reale din HomeApartmentInfoResponse
                home[id_asoc] = {
                    "luna": data.get("luna", ""),
                    "indecsi_start": data.get("indecsi_start", ""),
                    "indecsi_end": data.get("indecsi_end", ""),
                    "can_edit_index": data.get("can_edit_index", "0"),
                    "data_scadenta": data.get("data_scadenta", ""),
                    "right_email": data.get("right_email", "0"),
                    "right_global_edit_users": data.get("right_global_edit_users", "0"),
                    "right_telefon": data.get("right_telefon", "0"),
                }
                luna = luna or data.get("luna", "")
            elif id_asoc in self._apartments:
                apartments[id_asoc] = self._apartments[id_asoc]
                home[id_asoc] = self._home_ap_data.get(id_asoc, {})
                self.account_problems.append(
                    ("apartamente", str(id_asoc), f"{describe_result(data)}; se folosesc datele anterioare")
                )
            elif not self.has_access("home", id_asoc):
                _LOGGER.debug(
                    "Asociația %s nu are acces la pagina „acasă”; apartamentele ei nu sunt disponibile.",
                    id_asoc,
                )
            else:
                self.account_problems.append(
                    ("apartamente", str(id_asoc), f"{describe_result(data)}; asociația este omisă")
                )

        self._apartments = apartments
        self._home_ap_data = home
        self._luna_curenta = luna or self._luna_curenta

    # ─── Endpoint-uri de citire ───

    async def async_get_contoare_luni(self, id_asoc: Any) -> dict[str, Any]:
        """AppContoareGetIndexLuni.php → aLuni (cea mai recentă prima). Pagina „index"."""
        return await self._get("AppContoareGetIndexLuni.php", id_asoc=id_asoc)

    async def async_get_contoare_index(self, id_asoc: Any, luna: str) -> dict[str, Any]:
        """AppContoareGetIndex.php → aInfoContoare (definiții), aInfoIndex (per apartament)."""
        return await self._get("AppContoareGetIndex.php", id_asoc=id_asoc, luna=luna)

    async def async_get_facturi(self, id_asoc: Any, luna: str) -> dict[str, Any]:
        """AppFacturiGetData.php → aFactChelt, aFactFond, aFactVenit, aLuni. Pagina „facturi"."""
        return await self._get("AppFacturiGetData.php", id_asoc=id_asoc, luna=luna)

    async def async_get_nr_pers(self, id_asoc: Any, id_ap: Any) -> dict[str, Any]:
        """AppHomeGetNrPers.php → nr_pers_def, aLuna, aDeclaratii (fără parametru de lună)."""
        return await self._get("AppHomeGetNrPers.php", id_asoc=id_asoc, id_ap=id_ap)

    async def async_get_contact_tickets(self, id_asoc: Any, all_tickets: bool = False) -> dict[str, Any]:
        """AppContactGetTickets.php → aInfoAp, aTickets. all=0: doar tichetele contului."""
        return await self._get("AppContactGetTickets.php", id_asoc=id_asoc, all=int(all_tickets))

    async def async_get_ticket_detail(self, id_ticket: Any) -> dict[str, Any]:
        """AppContactGetTicketData.php → id, open, aReply."""
        return await self._get("AppContactGetTicketData.php", id_ticket=id_ticket)

    async def async_get_istoric_plati(self, id_asoc: Any, id_ap: Any) -> dict[str, Any]:
        """AppIstoricPlatiGetPlati.php → aChitante."""
        return await self._get("AppIstoricPlatiGetPlati.php", id_asoc=id_asoc, id_ap=id_ap)

    async def async_get_carduri(self) -> dict[str, Any]:
        """AppSettingsGetCard.php → aCard (id_card, titlu, tip, card_nr, data_exp)."""
        return await self._get("AppSettingsGetCard.php")

    async def async_get_wallet(self, id_asoc: Any, id_ap: Any) -> dict[str, Any]:
        """AppHomeGetWalletItems.php → sold, aItems.

        Aplicația v9.12 nu mai folosește acest endpoint, dar serverul încă îl servește.
        """
        return await self._get("AppHomeGetWalletItems.php", id_asoc=id_asoc, id_ap=id_ap)

    async def async_get_plateste_ap(self, id_asoc: Any) -> dict[str, Any]:
        """AppPlatesteGetAp.php → aCard, aInfoAp (total_plata + aDatAp). Pagina „plateste"."""
        return await self._get("AppPlatesteGetAp.php", id_asoc=id_asoc)

    # ─── Endpoint-uri de scriere ───

    async def async_set_nr_persoane(
        self, id_asoc: Any, id_ap: Any, luna: str, nr_pers: int
    ) -> dict[str, Any]:
        """AppHomeSetNrPers.php. nr_pers e numărul real (0-10); API-ul primește ×1000."""
        if not 0 <= nr_pers <= 10:
            return {"result": "INVALID", "error": f"numărul de persoane {nr_pers} nu este între 0 și 10"}
        return await self._with_relogin(
            lambda: self._get(
                "AppHomeSetNrPers.php",
                id_asoc=id_asoc, id_ap=id_ap, luna=luna, nr_pers=nr_pers * NR_PERS_MULTIPLIER,
            )
        )

    async def async_set_index_contoare(self, indexes: list[dict[str, Any]]) -> dict[str, Any]:
        """AppContoareSetIndexes.php (POST JSON). index_nou = valoarea × 1000."""
        a_index = [
            {
                "id_asoc": idx["id_asoc"],
                "id_ap": idx["id_ap"],
                "id_contor": idx["id_contor"],
                "index_nou": idx["index_nou"],
                "img_data": "",
                "img_guid": "",
            }
            for idx in indexes
        ]
        return await self._with_relogin(
            lambda: self._request(
                "POST", "AppContoareSetIndexes.php",
                json_body={"session_id": self._session_id, "aIndex": a_index},
            )
        )

    async def _with_relogin(self, call) -> dict[str, Any]:
        """O scriere; dacă sesiunea a expirat, autentificare nouă și o singură reîncercare.

        `call` construiește cererea la fiecare apel, deci reîncercarea folosește sesiunea nouă.
        """
        result = await call()
        if result.get("result") == "nologin":
            _LOGGER.debug("Sesiunea a expirat la trimiterea datelor; se face o autentificare nouă.")
            if await self.async_login():
                result = await call()
        return result

    # ─── Utilitare ───

    def association_name(self, id_asoc: Any) -> str:
        for assoc in self._associations:
            if str(assoc.get("id")) == str(id_asoc):
                return assoc.get("nume") or str(id_asoc)
        return str(id_asoc)

    def get_all_apartments(self) -> list[tuple[Any, Any, str]]:
        """Toate perechile (id_asoc, id_ap, etichetă)."""
        result = []
        for id_asoc, aps in self._apartments.items():
            assoc_name = self.association_name(id_asoc)
            for ap in aps:
                id_ap = ap.get("id_ap", ap.get("id", 0))
                result.append((id_asoc, id_ap, f"{assoc_name} - Ap. {ap.get('ap', '?')}"))
        return result

    def export_session(self) -> dict[str, Any] | None:
        """Sesiunea curentă, pentru salvare în config entry."""
        if not self._session_id:
            return None
        return {"session_id": self._session_id, "id_user": self._id_user, "timestamp": time.time()}

    def inject_session(self, session_data: dict[str, Any]) -> None:
        """Folosește o sesiune salvată anterior (se verifică la prima actualizare)."""
        self._session_id = session_data.get("session_id") or None
        self._id_user = _to_int(session_data.get("id_user"))
