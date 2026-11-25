"""Råvarer, frakt, renter og valuta som driver nordiske aksjer.

Daglige sluttkurser fra Yahoo og ukentlig eksportpris for fersk laks fra SSB
(tabell 03024). Lagres i tabellen `faktorserier` og brukes av faktorpanelet
(src/panel/bygg.py) til å måle hvor følsom hver aksje er for hver faktor, og
om aksjen har fulgt etter når faktorene har beveget seg.

Ingen fremtidsinformasjon: lakseprisen for uke U publiseres onsdagen uken
etter, og lagres med den datoen.
"""
from __future__ import annotations

from datetime import date, timedelta

import requests

from src.config import HTTP_TIMEOUT

YAHOO = {
    "brent": ("BZ=F", "Oljepris (Brent)"),
    "gass_eu": ("TTF=F", "Gasspris Europa (TTF)"),
    "gass_us": ("NG=F", "Gasspris USA"),
    "aluminium": ("ALI=F", "Aluminium"),
    "kobber": ("HG=F", "Kobber"),
    "gull": ("GC=F", "Gull"),
    "torrbulk": ("BDRY", "Fraktrater tørrbulk"),
    "usdnok": ("USDNOK=X", "Dollarkurs (USD/NOK)"),
    "eurnok": ("EURNOK=X", "Eurokurs (EUR/NOK)"),
    "usdsek": ("USDSEK=X", "Dollarkurs (USD/SEK)"),
    "rente_us10": ("^TNX", "Amerikansk 10-årsrente"),
}
NAVN = {k: v[1] for k, v in YAHOO.items()} | {"laks": "Laksepris"}
SSB_LAKS = "https://data.ssb.no/api/v0/no/table/03024"

SKJEMA = """
CREATE TABLE IF NOT EXISTS faktorserier (
    kode   TEXT NOT NULL,
    dato   TEXT NOT NULL,
    verdi  REAL NOT NULL,
    PRIMARY KEY (kode, dato)
) WITHOUT ROWID;
"""


def _lagre(con, kode: str, rader: list[tuple[str, float]]) -> int:
    con.executemany("INSERT OR REPLACE INTO faktorserier (kode, dato, verdi) VALUES (?,?,?)",
                    [(kode, d, v) for d, v in rader if v is not None and v == v and v > 0])
    return len(rader)


def hent_yahoo(con, periode: str) -> dict:
    import yfinance as yf

    syms = [s for s, _ in YAHOO.values()]
    df = yf.download(syms, period=periode, interval="1d", progress=False, auto_adjust=False)["Close"]
    ut = {}
    for kode, (sym, _) in YAHOO.items():
        if sym not in df:
            continue
        x = df[sym].dropna()
        ut[kode] = _lagre(con, kode, [(i.date().isoformat(), float(v)) for i, v in x.items()])
    return ut


def _uke_til_publisering(kode: str) -> str:
    """'2026U43' -> onsdag uken etter (da SSB publiserer)."""
    aar, uke = int(kode[:4]), int(kode[5:])
    return (date.fromisocalendar(aar, uke, 3) + timedelta(days=7)).isoformat()


def hent_laks(con, uker: int | None = None) -> int:
    tid = {"filter": "top", "values": [str(uker)]} if uker else {"filter": "all", "values": ["*"]}
    q = {"query": [{"code": "VareGrupper2", "selection": {"filter": "item", "values": ["01"]}},
                   {"code": "ContentsCode", "selection": {"filter": "item", "values": ["Kilopris"]}},
                   {"code": "Tid", "selection": tid}],
         "response": {"format": "json-stat2"}}
    r = requests.post(SSB_LAKS, json=q, timeout=HTTP_TIMEOUT * 3)
    r.raise_for_status()
    js = r.json()
    idx = js["dimension"]["Tid"]["category"]["index"]
    verdier = js["value"]
    rader = [(_uke_til_publisering(k), verdier[i]) for k, i in idx.items() if verdier[i] is not None]
    return _lagre(con, "laks", rader)


def kjor(con) -> dict:
    con.executescript(SKJEMA)
    tom = con.execute("SELECT COUNT(*) FROM faktorserier").fetchone()[0] == 0
    ut = hent_yahoo(con, "max" if tom else "3mo")
    ut["laks"] = hent_laks(con, None if tom else 12)
    con.commit()
    return ut


def last(con) -> dict[str, tuple[list[str], list[float]]]:
    """kode -> (datoer, verdier), sortert."""
    if not con.execute("SELECT name FROM sqlite_master WHERE name = 'faktorserier'").fetchone():
        return {}
    ut: dict[str, tuple[list, list]] = {}
    for kode, d, v in con.execute("SELECT kode, dato, verdi FROM faktorserier ORDER BY kode, dato"):
        ds, vs = ut.setdefault(kode, ([], []))
        ds.append(d)
        vs.append(v)
    return ut
