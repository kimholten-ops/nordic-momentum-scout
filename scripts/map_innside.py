"""Foreslår Yahoo-tickere for selskaper som har innsidekjøp, via Yahoos søk.

Bruk:
  python -m scripts.map_innside              # skriv forslag (trygg tørrkjøring)
  python -m scripts.map_innside --skriv      # legg SIKRE forslag til i universe/tickers.csv

Rekkefølge: (1) ISIN funnet i meldingsteksten søkes opp direkte — det er
entydig og regnes som sikkert. (2) Navnesøk med æøå/äö brettet til ASCII
(Yahoos søk finner ofte ikke "Østlandet", men finner "Ostlandet"). Et
navnetreff er "sikkert" bare når børssuffikset er nordisk (.OL .ST .CO .HE)
og første ord i selskapsnavnet også står først i Yahoo-navnet. Alt annet
skrives til stderr for manuell vurdering — en feil ticker ødelegger
backtesten stille, så her er det bedre å hoppe over enn å gjette.
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
import time

from src import db
from src.config import UNIVERSE_CSV
from src.universe import KOLONNER, last_universe

NORDISKE = {".OL": "Oslo Børs", ".ST": "Stockholm", ".CO": "København", ".HE": "Helsinki"}
STOPPORD = {"asa", "ab", "abp", "as", "oyj", "plc", "group", "holding", "holdings", "the", "a/s", "publ", "ltd", "corporation", "corp", "nv", "se"}


ISIN_RE = re.compile(r"\b(?:NO|SE|FI|DK)[0-9A-Z]{9}[0-9]\b")
_BRETT = str.maketrans({"æ": "ae", "ø": "o", "å": "a", "ä": "a", "ö": "o", "ü": "u", "é": "e", "ú": "u", "Æ": "AE", "Ø": "O", "Å": "A", "Ä": "A", "Ö": "O"})


def brett(tekst: str) -> str:
    return (tekst or "").translate(_BRETT)


def _ord(navn: str) -> list[str]:
    return [o for o in re.findall(r"[a-z0-9]+", brett(navn).lower()) if o not in STOPPORD]


def isiner(tekster: list[str]) -> list[str]:
    sett: list[str] = []
    for t in tekster:
        for m in ISIN_RE.findall(t or ""):
            if m not in sett:
                sett.append(m)
    return sett


def velg(navn: str, kandidater: list[dict]) -> tuple[dict | None, bool]:
    """(beste nordiske kandidat, sikker?). Dropper Yahoos obligasjons-/lån-
    symboler og tar første treff, som er mest likvide noteringen."""
    mine = _ord(navn)
    for k in kandidater:
        sym = k.get("symbol") or ""
        suff = next((s for s in NORDISKE if sym.upper().endswith(s)), None)
        if not suff or re.search(r"\d{3,}", sym):
            continue
        ya = _ord(k.get("shortname") or k.get("longname") or "")
        sikker = bool(mine and ya and mine[0] == ya[0])
        return k, sikker
    return None, False


def main() -> int:
    import yfinance as yf

    p = argparse.ArgumentParser()
    p.add_argument("--skriv", action="store_true")
    p.add_argument("-n", type=int, default=200)
    a = p.parse_args()

    with db.session() as con:
        last_universe(con)
        rader = con.execute(
            "SELECT h.selskap_slug AS slug, MAX(n.selskap_navn) AS navn, COUNT(*) AS antall "
            "FROM innsidehandler h JOIN nyheter n ON n.id = h.nyhet_id "
            "WHERE h.gyldig = 1 AND h.retning = 'kjop' AND h.instrument = 'aksje' "
            "AND h.selskap_slug NOT IN (SELECT slug FROM selskaper WHERE ticker IS NOT NULL) "
            "GROUP BY h.selskap_slug ORDER BY antall DESC LIMIT ?",
            (a.n,),
        ).fetchall()

    sikre, usikre = [], []
    for r in rader:
        navn = r["navn"] or r["slug"].replace("-", " ")
        with db.session() as con:
            tekster = [
                x[0] for x in con.execute(
                    "SELECT brodtekst FROM nyheter WHERE selskap_slug = ? AND brodtekst IS NOT NULL LIMIT 20",
                    (r["slug"],),
                )
            ]
        k, sikker = None, False
        try:
            for isin in isiner(tekster)[:3]:
                k, _ = velg("", yf.Search(isin, max_results=5).quotes)
                if k:
                    sikker = True
                    break
            if not k:
                k, sikker = velg(navn, yf.Search(brett(navn), max_results=8).quotes)
        except Exception as e:
            print(f"søk feilet for {navn}: {type(e).__name__}", file=sys.stderr)
            continue
        time.sleep(0.5)
        if not k:
            usikre.append((r["slug"], navn, None))
            continue
        sym = k["symbol"]
        suff = next(s for s in NORDISKE if sym.upper().endswith(s))
        rad = {"slug": r["slug"], "navn": navn.replace(",", " "), "ticker": sym[: -len(suff)], "yahoo": sym, "marked": NORDISKE[suff], "aktiv": 1}
        (sikre if sikker else usikre).append(rad if sikker else (r["slug"], navn, sym))

    for s in sikre:
        print(f"OK     {s['slug']:<32} {s['yahoo']}")
    for slug, navn, sym in usikre:
        print(f"SJEKK  {slug:<32} {sym or '(ingen nordisk treff)'}  [{navn}]", file=sys.stderr)

    if a.skriv and sikre:
        ny_fil = not UNIVERSE_CSV.exists()
        with UNIVERSE_CSV.open("a", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=KOLONNER)
            if ny_fil:
                w.writeheader()
            w.writerows(sikre)
        print(f"{len(sikre)} selskaper lagt til i {UNIVERSE_CSV.name}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
