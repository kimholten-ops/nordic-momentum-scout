"""Daglige kurser via yfinance.

Gratis og forsinket. Det holder i fase 0-3: alt vi trenger er sluttkurser for
å måle hva som faktisk skjedde etter en nyhet. Byttes ut hvis du en dag
betaler for sanntidsdata.
"""
from __future__ import annotations

import sqlite3
from datetime import date, timedelta

from src.config import PRICE_BATCH_SIZE, PRICE_LOOKBACK_DAYS


def _batcher(seq: list[str], n: int):
    for i in range(0, len(seq), n):
        yield seq[i : i + n]


def oppdater(con: sqlite3.Connection, dager: int = PRICE_LOOKBACK_DAYS) -> int:
    """Henter siste N dager for hele det aktive universet. Idempotent."""
    import yfinance as yf

    symboler = [
        r["yahoo"]
        for r in con.execute(
            "SELECT DISTINCT yahoo FROM selskaper WHERE aktiv=1 AND yahoo IS NOT NULL AND yahoo<>''"
        )
    ]
    if not symboler:
        return 0

    start = (date.today() - timedelta(days=dager)).isoformat()
    rader = 0

    for gruppe in _batcher(symboler, PRICE_BATCH_SIZE):
        df = yf.download(
            gruppe,
            start=start,
            interval="1d",
            group_by="ticker",
            auto_adjust=False,
            progress=False,
            threads=True,
        )
        if df is None or df.empty:
            continue
        for sym in gruppe:
            try:
                sub = df[sym] if len(gruppe) > 1 else df
            except KeyError:
                continue
            sub = sub.dropna(how="all")
            for idx, rad in sub.iterrows():
                if rad.isna().all():
                    continue
                con.execute(
                    "INSERT OR REPLACE INTO kurser "
                    "(yahoo, dato, open, high, low, close, volum) VALUES (?,?,?,?,?,?,?)",
                    (
                        sym,
                        idx.date().isoformat(),
                        _f(rad.get("Open")),
                        _f(rad.get("High")),
                        _f(rad.get("Low")),
                        _f(rad.get("Close")),
                        _i(rad.get("Volume")),
                    ),
                )
                rader += 1
    return rader


def _f(v):
    try:
        return None if v is None or v != v else float(v)
    except (TypeError, ValueError):
        return None


def _i(v):
    f = _f(v)
    return None if f is None else int(f)
