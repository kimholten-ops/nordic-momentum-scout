"""Regelbasert uttrekk fra innsidemeldinger med fast skjema.

Nasdaq (Helsinki/København/Stockholm) bruker et strukturert skjema med linjer
som "Nature of transaction: DISPOSAL". Oslo Børs-meldinger er derimot fritekst
med selve skjemaet som PDF-vedlegg — de går via src/innside/llm.py.
parse_strukturert() returnerer tom liste når skjemaet ikke gjenkjennes, slik
at kalleren vet at den må falle tilbake til LLM i stedet for å gjette.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

RETNINGER = ["kjop", "salg", "tildeling", "annet"]
INSTRUMENTER = ["aksje", "obligasjon", "opsjon_derivat", "annet"]
ROLLER = ["ceo", "cfo", "styreleder", "styremedlem", "ledelse", "naerstaaende", "ukjent"]


@dataclass
class Handel:
    person: str | None
    rolle_tekst: str | None
    rolle: str
    naerstaaende: bool
    retning: str
    instrument: str
    antall: float | None
    pris: float | None
    valuta: str | None
    transaksjonsdato: str | None
    beholdning_etter: float | None = None

    @property
    def verdi(self) -> float | None:
        if self.antall is not None and self.pris is not None:
            return round(self.antall * self.pris, 2)
        return None


def rolle_fra_tekst(tekst: str | None) -> tuple[str, bool]:
    """(rolle, nærstående). Nærstående er et eget flagg fordi en kone som
    kjøper er et annet signal enn at CEO selv kjøper."""
    t = (tekst or "").lower()
    naer = bool(re.search(r"close(?:ly)? (?:associate|related|associated)|nærstående|närstående|related part|closely", t))
    if re.search(r"\bceo\b|chief executive|administrerende direktør|konsernsjef|verkställande|vd\b|toimitusjohtaja|adm\.? dir|"
                 r"daglig leder|adm\. direktør", t):
        return "ceo", naer
    if re.search(r"\bcfo\b|chief financial|finansdirektør|økonomidirektør|ekonomichef|finanschef|talousjohtaja", t):
        return "cfo", naer
    if re.search(r"chair(?:man|woman|person)?\b|styreleder|styrets leder|ordförande|hallituksen puheenjohtaja|bestyrelsesformand", t):
        return "styreleder", naer
    if re.search(r"board member|member of the board|styremedlem|styrets medlem|\bdirector\b|ledamot|hallituksen jäsen|"
                 r"bestyrelsesmedlem|næstformand|nestleder i styret|deputy chair|vice chair", t):
        return "styremedlem", naer
    if naer and not t.strip():
        return "naerstaaende", True
    if t.strip():
        return ("naerstaaende" if naer else "ledelse"), naer
    return "ukjent", naer


def retning_fra_tekst(nature: str | None) -> str:
    n = (nature or "").lower()
    if re.search(r"disposal|sale|sell|sold|salg|solgt|försäljning|avyttring", n):
        return "salg"
    if re.search(r"acquisition|purchase|buy|bought|kjøp|køb|förvärv|köp", n):
        return "kjop"
    if re.search(r"subscription|allocation|allot|grant|award|exercise|vesting|bonus|conversion|receipt|tildel|tilldel", n):
        return "tildeling"
    return "annet"


def instrument_fra_tekst(tekst: str | None) -> str:
    t = (tekst or "").lower()
    if re.search(r"bond|obligasjon|obligation|note\b|convertible", t):
        return "obligasjon"
    if re.search(r"option|warrant|derivative|linked to|opsjon|swap|future", t):
        return "opsjon_derivat"
    if re.search(r"\bshare|aksje|aktie|osake", t):
        return "aksje"
    return "annet"


_TALL = r"[\d][\d\s .,]*"


def _tall(s: str | None) -> float | None:
    if not s:
        return None
    s = s.replace(" ", "").replace(" ", "")
    if "," in s and "." in s:
        s = s.replace(",", "")          # 1,234.50
    elif "," in s:
        s = s.replace(",", ".")         # 0,626
    try:
        return float(s.rstrip("."))
    except ValueError:
        return None


def _felt(blokk: str, navn: str) -> str | None:
    m = re.search(rf"^\s*{navn}\s*:\s*(.+?)\s*$", blokk, re.I | re.M)
    return m.group(1).strip() if m else None


def parse_strukturert(tekst: str) -> list[Handel]:
    """Ett Handel per transaksjonsblokk (aggregert volum/VWAP når oppgitt)."""
    if not re.search(r"nature of transaction\s*:", tekst, re.I):
        return []
    blokker = re.split(r"\n\s*_{5,}\s*\n", tekst)
    person = rolle_tekst = None
    handler: list[Handel] = []
    for b in blokker:
        navn = _felt(b, "name")
        if navn and re.search(r"position\s*:", b, re.I):
            person = navn
            rolle_tekst = _felt(b, "position")
        nature = _felt(b, "nature of transaction")
        if not nature:
            continue
        rolle, naer = rolle_fra_tekst(rolle_tekst)
        agg = re.search(
            rf"volume\s*:\s*({_TALL}?)\s*volume weighted average price\s*:\s*({_TALL}?)\s*([A-Z]{{3}})",
            b, re.I,
        )
        enkel = re.search(rf"volume\s*:\s*({_TALL}?)\s*unit price\s*:\s*({_TALL}?)\s*([A-Z]{{3}})", b, re.I)
        m = agg or enkel
        antall = _tall(m.group(1)) if m else None
        pris = _tall(m.group(2)) if m else None
        valuta = m.group(3).upper() if m else None
        dato = _felt(b, "transaction date")
        handler.append(
            Handel(
                person=person,
                rolle_tekst=rolle_tekst,
                rolle=rolle,
                naerstaaende=naer,
                retning=retning_fra_tekst(nature),
                instrument=instrument_fra_tekst(_felt(b, "instrument type")),
                antall=antall,
                pris=pris,
                valuta=valuta,
                transaksjonsdato=dato[:10] if dato else None,
            )
        )
    return handler
