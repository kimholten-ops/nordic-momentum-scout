"""universe/tickers.csv er fasiten for hvilke selskaper vi følger.

CSV-en er med vilje redigerbar for hånd: du kommer til å fylle den ut
gradvis etter hvert som scripts/map_missing.py forteller deg hvilke slugs
som dukker opp oftest.
"""
from __future__ import annotations

import csv
import sqlite3

from src.config import UNIVERSE_CSV

KOLONNER = ["slug", "navn", "ticker", "yahoo", "marked", "aktiv"]


def _aktiv(v) -> int:
    try:
        return int(str(v or "1").strip() or 1)
    except ValueError:
        return 1


def last_universe(con: sqlite3.Connection) -> int:
    if not UNIVERSE_CSV.exists():
        return 0
    n = 0
    with UNIVERSE_CSV.open(encoding="utf-8", newline="") as f:
        for rad in csv.DictReader(f):
            slug = (rad.get("slug") or "").strip()
            if not slug or slug.startswith("#"):
                continue
            con.execute(
                "INSERT INTO selskaper (slug, navn, ticker, yahoo, marked, aktiv) "
                "VALUES (?,?,?,?,?,?) ON CONFLICT(slug) DO UPDATE SET "
                "navn=excluded.navn, ticker=excluded.ticker, yahoo=excluded.yahoo, "
                "marked=excluded.marked, aktiv=excluded.aktiv",
                (
                    slug,
                    (rad.get("navn") or "").strip() or None,
                    (rad.get("ticker") or "").strip() or None,
                    (rad.get("yahoo") or "").strip() or None,
                    (rad.get("marked") or "").strip() or None,
                    _aktiv(rad.get("aktiv")),
                ),
            )
            n += 1
    # Etterfyll ticker på nyheter som kom inn før mappingen fantes.
    con.execute(
        "UPDATE nyheter SET ticker = (SELECT ticker FROM selskaper s WHERE s.slug = nyheter.selskap_slug), "
        "                   yahoo  = (SELECT yahoo  FROM selskaper s WHERE s.slug = nyheter.selskap_slug) "
        "WHERE ticker IS NULL AND selskap_slug IN (SELECT slug FROM selskaper WHERE ticker IS NOT NULL)"
    )
    con.execute("DELETE FROM umappede WHERE slug IN (SELECT slug FROM selskaper WHERE ticker IS NOT NULL)")
    return n
