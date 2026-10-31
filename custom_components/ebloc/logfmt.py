"""Convenția de logging a integrării E-bloc România (aplicată în toate modulele).

Format
  Formatul standard Home Assistant. Mesajele nu au prefix (numele loggerului arată modulul)
  și nu conțin emoji sau simboluri decorative.

Niveluri
  DEBUG    diagnostic: solicitări API, rezultatele pe apartament, stări interne
  INFO     evenimente importante și rare: conectarea inițială, schimbări de cont sau de licență,
           acțiuni reușite ale utilizatorului, revenirea unui serviciu după eșecuri
  WARNING  probleme recuperabile: surse de date indisponibile, acțiuni refuzate, licență
           nevalidă, server de licențe inaccesibil (o singură dată, nu la fiecare reîncercare)
  ERROR    operațiuni eșuate fără recuperare. O actualizare eșuată ridică UpdateFailed, iar
           Home Assistant o loghează o singură dată (și anunță singur revenirea). Excepțiile
           neprevăzute se loghează cu traceback (_LOGGER.exception).

Forma mesajelor
  Eveniment:    o propoziție, cu punct final.
                „Autentificare reușită."
  Operațiune:   „Operațiune | câmp | câmp", fără punct final.
                „GET AppHomeGetAp.php | asociația 107879 | HTTP 200 | rezultat: ok | 0,21 s"
  Raport:       antet de operațiune + câmpuri „etichetă: valoare", pe un singur rând
                (vezi oneline()).
                „Asociația 107879 | apartamentul 71 | indecși contoare: OK | sold: OK"

Identificare și terminologie
  „asociația 107879", „apartamentul 75"; durate „1,90 s"; termeni: actualizare, solicitare API,
  autentificare, sesiune, sursă de date.

Nu apar niciodată în log
  Parole, hash-uri, sesiuni, cookie-uri, ID-ul utilizatorului, IP-ul public, emailul complet
  (doar mascat), cheia de licență (doar mascată), fingerprint-ul complet (doar primele caractere).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any


def seconds(value: float) -> str:
    """Durată în secunde, cu două zecimale și virgulă: „1,90 s"."""
    return f"{value:.2f}".replace(".", ",") + " s"


def oneline(header: str, items: Mapping[str, Any] | Iterable[tuple[str, Any]]) -> str:
    """Antet + câmpuri „etichetă: valoare", separate cu „ | " (eticheta începe cu literă mică)."""
    pairs = items.items() if isinstance(items, Mapping) else items
    return " | ".join([header, *(f"{label[:1].lower()}{label[1:]}: {value}" for label, value in pairs)])


def short_id(value: str | None, keep: int = 8) -> str:
    """Doar începutul unui identificator lung (fingerprint): „a1b2c3d4..."."""
    return f"{value[:keep]}..." if value else "-"
