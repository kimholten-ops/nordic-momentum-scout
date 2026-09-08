"""Regelbasert forfilter: merk åpenbar rutine uten LLM-kall.

Kjøres på tittelen alene, før brødtekst hentes eller noen modell kalles.
Dette sparer det meste av kostnaden i fase 1, og reglene er langt lettere å
revidere enn en modell når MFN endrer ordlyd eller en ny rutinetype dukker
opp. Se src/klassifisering/regler.py for selve mønstrene.
"""
from __future__ import annotations

import re

from src.klassifisering.regler import RUTINE_MONSTRE

_KOMPILERT = [(navn, re.compile(monster, re.IGNORECASE)) for navn, monster in RUTINE_MONSTRE]


def sjekk_rutine(tittel: str) -> str | None:
    """Returnerer navnet på regelen som matchet først, eller None om
    tittelen ikke ser rutinemessig ut og bør sendes videre til LLM."""
    for navn, monster in _KOMPILERT:
        if monster.search(tittel):
            return navn
    return None
