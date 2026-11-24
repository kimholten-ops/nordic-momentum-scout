"""Hvilke Nasdaq Nordic-aksjer er på hovedlisten og hvilke på First North?

Nasdaqs egen screener (samme kilde som nasdaq.com/european-market-activity)
gir ISIN per aksje for hver liste. Brukes av src/ask.py: First North-aksjer
kan foreløpig ikke ligge på aksjesparekonto.
"""
from __future__ import annotations

from datetime import datetime

import requests

from src.config import TZ

URL = "https://api.nasdaq.com/api/nordic/screener/shares"
MARKEDER = {"STO": "Stockholm", "HEL": "Helsinki", "CPH": "København"}
KATEGORIER = {"MAIN_MARKET": "hoved", "FIRST_NORTH": "mhf"}


def hent_liste(kategori: str, marked: str) -> list[dict]:
    r = requests.get(URL, params={"category": kategori, "tableonly": "false", "market": marked},
                     headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"}, timeout=30)
    r.raise_for_status()
    return ((r.json().get("data") or {}).get("instrumentListing") or {}).get("rows") or []


def kjor(con) -> dict:
    con.execute("CREATE TABLE IF NOT EXISTS nasdaq_segment (isin TEXT PRIMARY KEY, segment TEXT NOT NULL, "
                "symbol TEXT, marked TEXT, oppdatert TEXT)")
    na = datetime.now(TZ).isoformat(timespec="seconds")
    teller = {}
    for kat, seg in KATEGORIER.items():
        for kode, navn in MARKEDER.items():
            rader = [r for r in hent_liste(kat, kode) if r.get("isin")]
            con.executemany(
                "INSERT OR REPLACE INTO nasdaq_segment (isin, segment, symbol, marked, oppdatert) VALUES (?,?,?,?,?)",
                [(r["isin"], seg, r.get("symbol"), navn, na) for r in rader],
            )
            teller[f"{seg}-{kode}"] = len(rader)
    con.commit()
    return teller
