"""Engangs: hent lang kurshistorikk for hele universet (til backtest).

fetch_prices henter bare siste 10 dager; backtesten trenger tilbake til
første innsidehandel. Bruk:  python -m scripts.hent_kurshistorikk --dager 800
"""
from __future__ import annotations

import argparse

from src import db
from src.sources import prices
from src.universe import last_universe


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--dager", type=int, default=1600)
    a = p.parse_args()
    with db.session() as con:
        last_universe(con)
        n = prices.oppdater(con, dager=a.dager)
        db.logg(con, "kurshistorikk", "ok", f"{n} kursrader, {a.dager} dager")
    print(f"{n} kursrader.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
