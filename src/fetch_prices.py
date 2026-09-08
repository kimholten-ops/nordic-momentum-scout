"""Daglig kurshenting etter børsslutt."""
from __future__ import annotations

import sys

from src import db
from src.config import valider
from src.notify import send
from src.sources import prices
from src.universe import last_universe


def main() -> int:
    try:
        valider()
        with db.session() as con:
            last_universe(con)
            n = prices.oppdater(con)
            db.logg(con, "fetch_prices", "ok", f"{n} kursrader")
    except Exception as e:
        print(f"FEIL: {type(e).__name__}: {e}", file=sys.stderr)
        send(f"⚠️ fetch_prices feilet: {type(e).__name__}: {e}")
        return 1
    print(f"{n} kursrader oppdatert.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
