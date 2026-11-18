"""Gjenkjenner innsidemeldinger på tittelen alene (ingen brødtekst, ingen LLM)."""
from __future__ import annotations

import re

_INNSIDE_MONSTRE = [
    r"mandatory notification of (?:trade|transaction)",
    r"notifiable trad",
    r"primary insiders?",
    r"managers.{0,2} transactions?",
    r"\bpdmr\b",
    r"persons? discharging managerial responsibilit",
    r"close(?:ly)? (?:associates?|related|associated)",
    r"meldepliktig handel",
    r"primærinnsider",
    r"innsidehandel|insiderhandel|insynshandel|insidertransaktion",
    r"transaktion(?:er)? (?:utförd|genomförd|foretaget|foretagne)",
    r"ledande befattningshavare|ledende medarbejdere",
    r"johtotehtävissä toimivien",
]
_INNSIDE_RE = re.compile("|".join(_INNSIDE_MONSTRE), re.IGNORECASE)

# "Correction: ..." / "Korrigert: ..." — erstatter en tidligere melding.
_KORREKSJON_RE = re.compile(r"^\s*(?:correction|corrected|korrigert|korrigering|rättelse|korrektion)\b", re.IGNORECASE)


def er_innsidemelding(tittel: str) -> bool:
    return bool(_INNSIDE_RE.search(tittel or ""))


def er_korreksjon(tittel: str) -> bool:
    return bool(_KORREKSJON_RE.search(tittel or ""))
