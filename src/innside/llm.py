"""Uttrekk av innsidehandler fra fritekst (Oslo Børs m.fl.) med Claude Haiku.

Som i klassifiseringen: modellen ser KUN tittel, brødtekst og
publiseringstidspunkt — aldri kursdata (look-ahead-forbud, se
tests/test_innside.py). Modellen skal trekke ut det som står, ikke tolke det:
manglende tall er null, ikke et gjett.
"""
from __future__ import annotations

import json

import requests

from src.config import (
    ANTHROPIC_API_KEY,
    ANTHROPIC_API_URL,
    ANTHROPIC_TIMEOUT,
    INNSIDE_LLM,
    KLASSIFISERING_MODELL,
    OLLAMA_MODELL,
    OLLAMA_URL,
)
from src.innside.parser import INSTRUMENTER, RETNINGER, Handel, rolle_fra_tekst
from src.klassifisering.llm import APIStengt, KlassifiseringsFeil, _parse_json, sjekk_svar  # noqa: F401

MAKS_FORSOK = 2

SYSTEM_PROMPT = (
    "Du trekker ut data fra børsmeldinger om transaksjoner utført av ledende "
    "personer (MAR art. 19 / primærinnsidere) for et forskningsprosjekt. Du "
    "ser KUN tittel, brødtekst og publiseringstidspunkt — aldri kursdata. "
    "Svar utelukkende med ett JSON-objekt, ingen markdown.\n\n"
    'Format: {"handler": [ {...}, ... ]}. Ett objekt per transaksjon med feltene:\n'
    '  "person": navn på personen eller selskapet som handlet, ellers null\n'
    '  "rolle_tekst": rolle slik den står (f.eks. "CFO", "Chair of the Board"), ellers null\n'
    '  "naerstaaende": true hvis handelen er gjort av en nærstående/nærstående selskap, ellers false\n'
    '  "retning": nøyaktig én av ' + json.dumps(RETNINGER) + " (kjop = åpent markedskjøp; "
    "tildeling = tildeling, opsjonsutøvelse, bonus-/incentivaksjer, tegning i emisjon; "
    "salg = salg; annet = utlån, gave, pantsettelse m.m.)\n"
    '  "instrument": nøyaktig én av ' + json.dumps(INSTRUMENTER) + "\n"
    '  "antall": antall enheter som tall, ellers null\n'
    '  "pris": pris per enhet som tall, ellers null\n'
    '  "valuta": valutakode (NOK, SEK, EUR ...), ellers null\n'
    '  "dato": transaksjonsdato YYYY-MM-DD, ellers null\n'
    '  "beholdning_etter": antall aksjer personen (inkl. nærstående) eier ETTER handelen, '
    'hvis det står i teksten, ellers null\n\n'
    "Står ikke tallene i teksten (f.eks. kun i et vedlegg), skal de være null. "
    "Finn aldri på tall. Handler meldingen ikke om en transaksjon, svar "
    '{"handler": []}.'
)


def bygg_prompt(tittel: str, brodtekst: str, publisert: str) -> str:
    return f"Publisert: {publisert}\nTittel: {tittel}\n\nBrødtekst:\n{brodtekst or '(ikke tilgjengelig)'}"


def _tall(v) -> float | None:
    if v in (None, ""):
        return None
    return float(v)


def _valider(data: dict) -> list[Handel]:
    handler = data.get("handler")
    if not isinstance(handler, list):
        raise ValueError("mangler 'handler'-liste")
    ut: list[Handel] = []
    for h in handler:
        if h.get("retning") not in RETNINGER:
            raise ValueError(f"ugyldig retning: {h.get('retning')!r}")
        if h.get("instrument") not in INSTRUMENTER:
            raise ValueError(f"ugyldig instrument: {h.get('instrument')!r}")
        rolle_tekst = h.get("rolle_tekst") or None
        rolle, naer_regel = rolle_fra_tekst(rolle_tekst)
        naer = bool(h.get("naerstaaende")) or naer_regel
        dato = h.get("dato")
        ut.append(
            Handel(
                person=(h.get("person") or None),
                rolle_tekst=rolle_tekst,
                rolle=rolle if rolle_tekst else ("naerstaaende" if naer else "ukjent"),
                naerstaaende=naer,
                retning=h["retning"],
                instrument=h["instrument"],
                antall=_tall(h.get("antall")),
                pris=_tall(h.get("pris")),
                valuta=(h.get("valuta") or None),
                transaksjonsdato=str(dato)[:10] if dato else None,
                beholdning_etter=_tall(h.get("beholdning_etter")),
            )
        )
    return ut


def tilgjengelig() -> bool:
    if INNSIDE_LLM == "ollama":
        return True
    return INNSIDE_LLM == "anthropic" and bool(ANTHROPIC_API_KEY)


def _kall_ollama(prompt: str) -> str:
    r = requests.post(
        f"{OLLAMA_URL.rstrip('/')}/api/chat",
        json={
            "model": OLLAMA_MODELL,
            "stream": False,
            "format": "json",
            "options": {"temperature": 0},
            "messages": [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": prompt}],
        },
        timeout=300,
    )
    r.raise_for_status()
    return r.json().get("message", {}).get("content", "")


def _kall_api(prompt: str, modell: str) -> str:
    if INNSIDE_LLM == "ollama":
        return _kall_ollama(prompt)
    if not ANTHROPIC_API_KEY:
        raise KlassifiseringsFeil("ANTHROPIC_API_KEY er ikke satt")
    r = requests.post(
        ANTHROPIC_API_URL,
        headers={
            "x-api-key": ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": modell,
            "max_tokens": 4000,  # mange transaksjoner i én melding ga avkuttet JSON ved 900
            "system": SYSTEM_PROMPT,
            "messages": [{"role": "user", "content": prompt}],
        },
        timeout=ANTHROPIC_TIMEOUT,
    )
    sjekk_svar(r)
    return "".join(b.get("text", "") for b in r.json().get("content", []) if b.get("type") == "text")


def trekk_ut(tittel: str, brodtekst: str, publisert: str, modell: str = KLASSIFISERING_MODELL) -> list[Handel]:
    prompt = bygg_prompt(tittel, brodtekst, publisert)
    siste: Exception | None = None
    for _ in range(MAKS_FORSOK):
        try:
            return _valider(_parse_json(_kall_api(prompt, modell)))
        except (ValueError, KeyError, TypeError, AttributeError, requests.RequestException) as e:
            siste = e
    raise KlassifiseringsFeil(f"ugyldig svar etter {MAKS_FORSOK} forsøk: {siste}")
