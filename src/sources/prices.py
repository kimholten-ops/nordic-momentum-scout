"""Daglige kurser via yfinance.

Gratis og forsinket. Det holder i fase 0-3: alt vi trenger er sluttkurser for
å måle hva som faktisk skjedde etter en nyhet. Byttes ut hvis du en dag
betaler for sanntidsdata.
"""
from __future__ import annotations

import sqlite3
from datetime import date, timedelta

from src.config import INDEKSER, KURSHISTORIKK_DAGER, PRICE_BATCH_SIZE, PRICE_LOOKBACK_DAYS


def _batcher(seq: list[str], n: int):
    for i in range(0, len(seq), n):
        yield seq[i : i + n]


def _symboler(con: sqlite3.Connection) -> list[str]:
    aksjer = [
        r["yahoo"]
        for r in con.execute(
            "SELECT DISTINCT yahoo FROM selskaper WHERE aktiv=1 AND yahoo IS NOT NULL AND yahoo<>''"
        )
    ]
    usa = [u for u in usa_symboler() if u not in aksjer]
    return aksjer + usa + [i for i in INDEKSER.values() if i not in aksjer]


def usa_symboler() -> list[str]:
    """Amerikanske aksjer til faktormodellen (universe/usa.csv)."""
    import csv

    from src.config import ROOT

    fil = ROOT / "universe" / "usa.csv"
    if not fil.exists():
        return []
    with fil.open(encoding="utf-8") as f:
        return [r["symbol"] for r in csv.DictReader(f) if r.get("symbol")]


def _last_ned(con: sqlite3.Connection, symboler: list[str], start: str) -> int:
    import yfinance as yf

    rader = 0
    for gruppe in _batcher(symboler, PRICE_BATCH_SIZE):
        df = yf.download(
            gruppe,
            start=start,
            interval="1d",
            group_by="ticker",
            auto_adjust=False,
            actions=True,  # utbytte (Dividends) per eksdato
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
            if hasattr(sub.columns, "nlevels") and sub.columns.nlevels > 1:
                sub = sub.droplevel(0, axis=1) if sym in sub.columns.get_level_values(0) else sub
            sub = sub.dropna(how="all")
            for idx, rad in sub.iterrows():
                # Yahoo leverer av og til en dag uten sluttkurs (ufullstendig
                # bar, eller feil). Den hoppes over: en rad uten close er
                # verdiløs for oss og ville ellers skjule en god rad.
                if _f(rad.get("Close")) is None:
                    continue
                # Upsert som aldri overskriver en kjent verdi med NULL.
                con.execute(
                    "INSERT INTO kurser (yahoo, dato, open, high, low, close, volum, utbytte) VALUES (?,?,?,?,?,?,?,?) "
                    "ON CONFLICT(yahoo, dato) DO UPDATE SET "
                    " open = COALESCE(excluded.open, kurser.open), high = COALESCE(excluded.high, kurser.high), "
                    " low = COALESCE(excluded.low, kurser.low), close = COALESCE(excluded.close, kurser.close), "
                    " volum = COALESCE(excluded.volum, kurser.volum), "
                    " utbytte = COALESCE(excluded.utbytte, kurser.utbytte)",
                    (
                        sym,
                        idx.date().isoformat(),
                        _f(rad.get("Open")),
                        _f(rad.get("High")),
                        _f(rad.get("Low")),
                        _f(rad.get("Close")),
                        _i(rad.get("Volume")),
                        _f(rad.get("Dividends")) or None,  # 0 lagres som NULL (sparer plass)
                    ),
                )
                rader += 1
    return rader


def oppdater(con: sqlite3.Connection, dager: int = PRICE_LOOKBACK_DAYS) -> int:
    """Henter siste N dager for hele det aktive universet + markedsindeksene.
    Symboler med under 60 kursdager (nylig mappet) får full historikk, slik at
    backtest og hjerne kan bruke dem med en gang. Idempotent."""
    symboler = _symboler(con)
    if not symboler:
        return 0
    antall = {
        r["yahoo"]: r["n"] for r in con.execute("SELECT yahoo, COUNT(*) n FROM kurser GROUP BY yahoo")
    }
    korte = [s for s in symboler if antall.get(s, 0) < 60]
    lange = [s for s in symboler if s not in korte]
    rader = 0
    if lange:
        rader += _last_ned(con, lange, (date.today() - timedelta(days=dager)).isoformat())
    if korte:
        rader += _last_ned(con, korte, (date.today() - timedelta(days=max(dager, KURSHISTORIKK_DAGER))).isoformat())
    return rader


def _f(v):
    try:
        return None if v is None or v != v else float(v)
    except (TypeError, ValueError):
        return None


def _i(v):
    f = _f(v)
    return None if f is None else int(f)
