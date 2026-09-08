"""Skriver ut de umappede selskapene med flest nyheter, som ferdige CSV-linjer.

Bruk:  python -m scripts.map_missing >> universe/tickers.csv
Deretter fyller du inn ticker/yahoo/marked for hånd og sletter linjene du
ikke bryr deg om. Å mappe de 100 mest aktive slugene dekker det meste.
"""
from __future__ import annotations

import argparse
import sys

from src import db


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("-n", type=int, default=50)
    p.add_argument("--min", type=int, default=1, help="minst så mange nyheter")
    a = p.parse_args()

    with db.session() as con:
        rader = con.execute(
            "SELECT slug, navn, antall FROM umappede WHERE antall >= ? "
            "ORDER BY antall DESC LIMIT ?",
            (a.min, a.n),
        ).fetchall()

    for r in rader:
        navn = (r["navn"] or "").replace(",", " ")
        print(f"{r['slug']},{navn},,,,1")
        print(f"  {r['slug']:<28} {r['antall']} nyheter", file=sys.stderr)


if __name__ == "__main__":
    main()
