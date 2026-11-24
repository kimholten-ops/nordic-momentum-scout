"""Kan aksjen ligge på aksjesparekonto (ASK) hos Nordnet?

Reglene (skatteloven § 10-21, Skatteetaten og Nordnet, nov. 2026):
  1. Selskapet må være hjemmehørende i EU/EØS. Bermuda, Singapore,
     Marshalløyene, Caymanøyene, Sveits, Storbritannia, USA og Færøyene
     (står utenfor EØS) er ute.
  2. Aksjen må være børsnotert (regulert marked): Oslo Børs, Euronext Expand
     og Nasdaq Stockholm/Helsinki/København. Euronext Growth, First North og
     Spotlight er multilaterale handelsfasiliteter og er foreløpig ute.
     Statsbudsjettet for 2027 foreslår å åpne for dem fra 1.1.2027; sett
     ASK_MHF=1 når det er vedtatt.

Hjemland leses av ISIN-prefikset (landet som utstedte ISIN-en er nesten
alltid selskapets hjemland). Har et selskap flere ISIN-er (gammel og ny etter
flytting, eller en amerikansk ADR), holder det at én er i EØS. Unntak legges
i universe/ask.csv (slug,status,merknad) med status ja/nei/mhf.

Segmentet (hovedliste eller First North) hentes fra Nasdaq for Stockholm,
Helsinki og København (src/sources/nasdaq.py); for Oslo står det i
markedsfeltet i universe/tickers.csv.
"""
from __future__ import annotations

import csv
from pathlib import Path

from src.config import ASK_MHF

ROOT = Path(__file__).resolve().parent.parent
OVERSTYRING = ROOT / "universe" / "ask.csv"

EOS = set(
    "AT BE BG HR CY CZ DK EE FI FR DE GR HU IE IT LV LT LU MT NL PL PT RO SK SI ES SE "  # EU
    "IS LI NO".split()  # EØS utenfor EU
)
LAND = {"BM": "Bermuda", "SG": "Singapore", "MH": "Marshalløyene", "KY": "Caymanøyene", "VG": "Jomfruøyene",
        "CH": "Sveits", "GB": "Storbritannia", "US": "USA", "FO": "Færøyene", "AU": "Australia", "CA": "Canada",
        "JE": "Jersey", "GG": "Guernsey", "IM": "Isle of Man"}
MHF_ORD = ("growth", "first north", "spotlight", "ngm")

JA = "ja"
UTENFOR = "utenfor EØS"
MHF = "Growth/First North"
UKJENT = "ukjent"


def ok(status: str | None) -> bool:
    """Kan kjøpes på ASK nå? Ukjent slippes gjennom (få selskaper, merkes i appen)."""
    return status in (JA, UKJENT, None) or (status == MHF and ASK_MHF)


def vurder(isins: set[str] | list[str], marked: str | None, segment: dict[str, str] | None = None,
           yahoo: str | None = None) -> tuple[str, str]:
    """(status, kort forklaring)."""
    if yahoo and "." not in yahoo.lstrip("^"):
        return UTENFOR, "amerikansk aksje"
    land = {i[:2].upper() for i in isins if len(i) >= 2}
    if land and not land & EOS:
        navn = ", ".join(sorted(LAND.get(x, x) for x in land))
        return UTENFOR, f"selskapet er hjemmehørende i {navn}"
    seg = {segment[i] for i in isins if segment and i in segment}
    if "mhf" in seg or (not seg and any(o in (marked or "").lower() for o in MHF_ORD)):
        return MHF, "handles på Euronext Growth/First North, som ikke er børsnotert i ASK-forstand"
    if not land:
        return UKJENT, "mangler ISIN, så hjemlandet er ukjent"
    return JA, ""


def _overstyring() -> dict[str, tuple[str, str]]:
    if not OVERSTYRING.exists():
        return {}
    kode = {"ja": JA, "nei": UTENFOR, "mhf": MHF}
    with OVERSTYRING.open(encoding="utf-8") as f:
        return {r["slug"]: (kode[r["status"].strip()], r.get("merknad") or "manuelt satt")
                for r in csv.DictReader(r for r in f if not r.startswith("#")) if r.get("status", "").strip() in kode}


def beregn(con) -> dict[str, tuple[str, str]]:
    """slug -> (status, forklaring) for alle selskaper i universet."""
    tabeller = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    isin: dict[str, set[str]] = {}
    if "selskap_isin" in tabeller:
        for slug, i in con.execute("SELECT slug, isin FROM selskap_isin"):
            isin.setdefault(slug, set()).add(i)
    segment = ({i: s for i, s in con.execute("SELECT isin, segment FROM nasdaq_segment")}
               if "nasdaq_segment" in tabeller else {})
    over = _overstyring()
    ut = {}
    for slug, marked, yahoo in con.execute("SELECT slug, marked, yahoo FROM selskaper"):
        ut[slug] = over.get(slug) or vurder(isin.get(slug, set()), marked, segment, yahoo)
    return ut


def per_yahoo(con) -> dict[str, tuple[str, str]]:
    """Yahoo-symbol -> (status, forklaring). Symboler uten børssuffiks er amerikanske."""
    s = beregn(con)
    ut: dict[str, tuple[str, str]] = {}
    for slug, yahoo in con.execute("SELECT slug, yahoo FROM selskaper WHERE yahoo IS NOT NULL AND yahoo <> ''"):
        if slug in s and (yahoo not in ut or s[slug][0] == JA):
            ut[yahoo] = s[slug]
    return ut
