"""SQLite-lag. Databasen er én fil som committes tilbake til repoet."""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path

from src.config import DB_PATH

SCHEMA = """
PRAGMA journal_mode = DELETE;   -- ingen -wal-filer i git
PRAGMA foreign_keys = ON;

-- Rå nyheter. Denne tabellen skal ALDRI endres i ettertid,
-- den er fasiten når du senere backtester.
CREATE TABLE IF NOT EXISTS nyheter (
    id              INTEGER PRIMARY KEY,
    url             TEXT NOT NULL UNIQUE,      -- naturlig nøkkel fra MFN
    publisert       TEXT NOT NULL,             -- ISO 8601, Europe/Stockholm fra MFN
    hentet          TEXT NOT NULL,             -- når VI så den (måler forsinkelsen din)
    selskap_slug    TEXT NOT NULL,             -- mfn.se/all/a/<slug>
    selskap_navn    TEXT,
    tittel          TEXT NOT NULL,
    sprak           TEXT,                      -- gjettet: no/sv/en/fi/da
    distributor     TEXT,
    ticker          TEXT,                      -- fylt inn fra universe/tickers.csv
    yahoo           TEXT,
    brodtekst       TEXT                       -- hentes først i fase 1
);
CREATE INDEX IF NOT EXISTS idx_nyheter_publisert ON nyheter(publisert);
CREATE INDEX IF NOT EXISTS idx_nyheter_slug ON nyheter(selskap_slug);
CREATE INDEX IF NOT EXISTS idx_nyheter_ticker ON nyheter(ticker);

-- Daglige kurser for hele universet.
CREATE TABLE IF NOT EXISTS kurser (
    yahoo     TEXT NOT NULL,
    dato      TEXT NOT NULL,
    open      REAL, high REAL, low REAL, close REAL,
    volum     INTEGER,
    PRIMARY KEY (yahoo, dato)
);

-- Selskapsregister, speiler universe/tickers.csv.
CREATE TABLE IF NOT EXISTS selskaper (
    slug      TEXT PRIMARY KEY,
    navn      TEXT,
    ticker    TEXT,
    yahoo     TEXT,
    marked    TEXT,
    aktiv     INTEGER DEFAULT 1
);

-- Slugs vi har sett nyheter fra, men ikke har mappet til en ticker.
-- scripts/map_missing.py leser denne og prioriterer etter frekvens.
CREATE TABLE IF NOT EXISTS umappede (
    slug      TEXT PRIMARY KEY,
    navn      TEXT,
    antall    INTEGER DEFAULT 0,
    sist_sett TEXT
);

-- Enkel kjørelogg, så du ser om cronen faktisk går.
CREATE TABLE IF NOT EXISTS kjorelogg (
    id        INTEGER PRIMARY KEY,
    tidspunkt TEXT NOT NULL,
    jobb      TEXT NOT NULL,
    status    TEXT NOT NULL,
    detalj    TEXT
);
"""


def connect(path: Path | None = None) -> sqlite3.Connection:
    p = Path(path or DB_PATH)
    p.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(p)
    con.row_factory = sqlite3.Row
    return con


def init(path: Path | None = None) -> None:
    with connect(path) as con:
        con.executescript(SCHEMA)


@contextmanager
def session(path: Path | None = None):
    con = connect(path)
    try:
        con.executescript(SCHEMA)
        yield con
        con.commit()
    finally:
        con.close()


def logg(con: sqlite3.Connection, jobb: str, status: str, detalj: str = "") -> None:
    from datetime import datetime

    from src.config import TZ

    con.execute(
        "INSERT INTO kjorelogg (tidspunkt, jobb, status, detalj) VALUES (?,?,?,?)",
        (datetime.now(TZ).isoformat(timespec="seconds"), jobb, status, detalj),
    )
    # Hold loggen kort så repoet ikke vokser.
    con.execute(
        "DELETE FROM kjorelogg WHERE id NOT IN "
        "(SELECT id FROM kjorelogg ORDER BY id DESC LIMIT 500)"
    )


if __name__ == "__main__":
    init()
    print(f"Database initialisert: {DB_PATH}")
