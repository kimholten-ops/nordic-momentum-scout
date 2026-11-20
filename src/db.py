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
) WITHOUT ROWID;   -- nøkkelen ER tabellen: sparer en tredjedel plass

-- Kontekst fra MFN (uten tekst): rapporter, tilbakekjøp og emisjoner per
-- selskap. Én rad per meldingsgruppe (språkversjoner deler group_id).
CREATE TABLE IF NOT EXISTS selskapshendelser (
    group_id  TEXT PRIMARY KEY,
    slug      TEXT NOT NULL,
    type      TEXT NOT NULL,      -- rapport | tilbakekjop | emisjon
    publisert TEXT NOT NULL       -- norsk lokal tid
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS idx_selskapshendelser_slug ON selskapshendelser(slug, type, publisert);

-- ISIN per selskap (fra MFN), for å koble mot shortregisteret.
CREATE TABLE IF NOT EXISTS selskap_isin (slug TEXT NOT NULL, isin TEXT NOT NULL, PRIMARY KEY (slug, isin)) WITHOUT ROWID;

-- Sektor og antall aksjer fra Yahoo (oppdateres månedlig). Børsverdi på en
-- dato = aksjer x sluttkurs (antall aksjer endrer seg sjelden mye).
CREATE TABLE IF NOT EXISTS selskapsinfo (
    yahoo     TEXT PRIMARY KEY,
    sektor    TEXT,
    bransje   TEXT,
    aksjer    REAL,
    oppdatert TEXT
);

-- Netto shortandel (% av aksjene) per ISIN og dato, fra Finanstilsynets
-- shortregister (Oslo). Bare endringsdager; verdien gjelder til neste rad.
CREATE TABLE IF NOT EXISTS short (
    isin      TEXT NOT NULL,
    dato      TEXT NOT NULL,
    prosent   REAL NOT NULL,
    PRIMARY KEY (isin, dato)
) WITHOUT ROWID;

-- Faktormodellen (src/panel/): siste score per aksje og modellens målte kvalitet.
CREATE TABLE IF NOT EXISTS panel_score (
    yahoo         TEXT PRIMARY KEY,
    dato          TEXT NOT NULL,
    region        TEXT NOT NULL,
    persentil     REAL NOT NULL,
    tidel         INTEGER NOT NULL,
    forventet_mer REAL,
    kostnad       REAL,
    bidrag        TEXT            -- JSON: {"opp": [...], "ned": [...]}
);
CREATE TABLE IF NOT EXISTS panel_status (
    region    TEXT PRIMARY KEY,
    oppdatert TEXT NOT NULL,
    data      TEXT NOT NULL       -- JSON fra panel.modell.evaluer
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

-- Fase 1: dedupliserte hendelser. Samme pressemelding havner ofte som flere
-- rader i "nyheter" (ett per språk, ett per distributør). En hendelse er
-- klyngen av slike rader som faktisk er samme nyhet. "nyheter" endres aldri;
-- dette er et lag over, bygget av src/klassifisering/dedup.py og kan bygges
-- på nytt fra bunnen av når som helst uten å miste noe.
CREATE TABLE IF NOT EXISTS hendelser (
    id                INTEGER PRIMARY KEY,
    kanonisk_nyhet_id INTEGER NOT NULL REFERENCES nyheter(id),
    selskap_slug      TEXT NOT NULL,
    ticker            TEXT,
    publisert         TEXT NOT NULL,   -- tidligste publisert blant medlemmene
    tittel            TEXT NOT NULL,   -- tittel fra kanonisk_nyhet_id
    antall_rader      INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_hendelser_slug_publisert ON hendelser(selskap_slug, publisert);

-- Kobling nyhet -> hendelse. En nyhet-rad tilhører nøyaktig én hendelse.
CREATE TABLE IF NOT EXISTS hendelse_nyheter (
    hendelse_id INTEGER NOT NULL REFERENCES hendelser(id),
    nyhet_id    INTEGER NOT NULL UNIQUE REFERENCES nyheter(id),
    PRIMARY KEY (hendelse_id, nyhet_id)
);

-- Klassifisering av hendelser: enten regelbasert "rutine"-filtrering eller
-- LLM-kall. UNIQUE(hendelse_id, prompt_versjon) er selve garantien for at en
-- hendelse aldri klassifiseres to ganger med samme prompt: bump
-- PROMPT_VERSJON for å klassifisere på nytt med en endret prompt, og
-- sammenlign versjonene mot hverandre i stedet for å overskrive historikken.
-- Fremdrift for src/backfill_arkiv.py. Jobben blir avbrutt (GitHub Actions
-- har tidsgrenser), så den må vite hvor den slapp per selskap for å kunne
-- fortsette i stedet for å starte på nytt.
CREATE TABLE IF NOT EXISTS arkiv_fremdrift (
    slug         TEXT PRIMARY KEY,
    siste_side   INTEGER NOT NULL DEFAULT 0,
    ferdig       INTEGER NOT NULL DEFAULT 0,
    sist_forsokt TEXT
);

CREATE TABLE IF NOT EXISTS klassifiseringer (
    id                      INTEGER PRIMARY KEY,
    hendelse_id             INTEGER NOT NULL REFERENCES hendelser(id),
    prompt_versjon          TEXT NOT NULL,
    modell                  TEXT NOT NULL,      -- 'regelbasert-filter' eller Haiku-modellnavn
    status                  TEXT NOT NULL,      -- 'rutine' | 'ok' | 'feilet'
    kategori                TEXT,
    retning                 TEXT,                -- 'positiv' | 'negativ' | 'nøytral'
    overraskelse            INTEGER,             -- 1-5
    sikkerhet               REAL,                -- 0-1
    begrunnelse             TEXT,
    tallverdi               REAL,
    valuta                  TEXT,
    klassifisert_tidspunkt  TEXT NOT NULL,
    UNIQUE (hendelse_id, prompt_versjon)
);

-- Innsidehandel (MAR art. 19), se src/innside/. Utledet fra "nyheter", som
-- aldri endres. innside_behandlet sier hvilke meldinger som er forsøkt
-- trukket ut (per ekstraksjonsversjon), innsidehandler er selve handlene.
CREATE TABLE IF NOT EXISTS innside_behandlet (
    nyhet_id  INTEGER NOT NULL REFERENCES nyheter(id),
    versjon   TEXT NOT NULL,
    status    TEXT NOT NULL,     -- 'ok' | 'ingen_handler' | 'trenger_llm' | 'feilet'
    detalj    TEXT,
    tidspunkt TEXT NOT NULL,
    PRIMARY KEY (nyhet_id, versjon)
);

CREATE TABLE IF NOT EXISTS innsidehandler (
    id               INTEGER PRIMARY KEY,
    nyhet_id         INTEGER NOT NULL REFERENCES nyheter(id),
    linje            INTEGER NOT NULL,
    versjon          TEXT NOT NULL,
    kilde            TEXT NOT NULL,      -- 'regel' | 'llm'
    selskap_slug     TEXT NOT NULL,
    publisert        TEXT NOT NULL,
    er_korreksjon    INTEGER NOT NULL DEFAULT 0,
    gyldig           INTEGER NOT NULL DEFAULT 1,  -- 0 = erstattet av en korreksjon
    person           TEXT,
    rolle_tekst      TEXT,
    rolle            TEXT NOT NULL,      -- ceo|cfo|styreleder|styremedlem|ledelse|naerstaaende|ukjent
    naerstaaende     INTEGER NOT NULL DEFAULT 0,
    retning          TEXT NOT NULL,      -- kjop|salg|tildeling|annet
    instrument       TEXT NOT NULL,      -- aksje|obligasjon|opsjon_derivat|annet
    antall           REAL,
    pris             REAL,
    valuta           TEXT,
    verdi            REAL,
    transaksjonsdato TEXT,
    beholdning_etter REAL,           -- aksjer eid etter handelen (når oppgitt)
    noekkel          TEXT NOT NULL,      -- samme handel via flere distributører/språk får samme nøkkel
    UNIQUE (nyhet_id, linje, versjon)
);
CREATE INDEX IF NOT EXISTS idx_innside_slug_publisert ON innsidehandler(selskap_slug, publisert);
CREATE INDEX IF NOT EXISTS idx_innside_noekkel ON innsidehandler(noekkel);

-- Signaler: regelbasert score over innsidekjøp. Ingen kursdata inngår.
-- UNIQUE(selskap_slug, utlost_av_nyhet, signal_versjon) gjør at et signal
-- aldri lages to ganger; bump SIGNAL_VERSJON for å teste andre vekter.
CREATE TABLE IF NOT EXISTS innside_signaler (
    id               INTEGER PRIMARY KEY,
    selskap_slug     TEXT NOT NULL,
    utlost_av_nyhet  INTEGER NOT NULL REFERENCES nyheter(id),
    publisert        TEXT NOT NULL,
    signal_versjon   TEXT NOT NULL,
    score            REAL NOT NULL,
    antall_personer  INTEGER NOT NULL,
    verdi_nok        REAL,
    begrunnelse      TEXT NOT NULL,
    varslet          INTEGER NOT NULL DEFAULT 0,
    UNIQUE (selskap_slug, utlost_av_nyhet, signal_versjon)
);

-- Hjernen (src/hjerne/). prediksjoner/trades/egenkapital regnes på nytt
-- ved hver kjøring (walk-forward fra rådata); hjerne_forslag er varige
-- fordi de varsles, og varslet-flagget hindrer dobbeltvarsel.
CREATE TABLE IF NOT EXISTS hjerne_prediksjoner (
    nokkel     TEXT PRIMARY KEY,
    kilde      TEXT, slug TEXT, navn TEXT, publisert TEXT, inn_dato TEXT,
    pred       REAL, sikkerhet REAL, valgt INTEGER, utfall REAL, trent_n INTEGER,
    bidrag     TEXT, beskrivelse TEXT
);
CREATE TABLE IF NOT EXISTS hjerne_trades (
    id INTEGER PRIMARY KEY, nokkel TEXT, slug TEXT, navn TEXT, yahoo TEXT,
    inn_dato TEXT, inn REAL, stopp REAL, plan_ut_dato TEXT, ut_dato TEXT, ut REAL,
    status TEXT, raa REAL, marked REAL, kost REAL, netto REAL, pnl_kr REAL, pred REAL, belop REAL
);
CREATE TABLE IF NOT EXISTS hjerne_egenkapital (dato TEXT PRIMARY KEY, verdi REAL);
CREATE TABLE IF NOT EXISTS hjerne_forslag (
    nokkel TEXT PRIMARY KEY, opprettet TEXT, kilde TEXT, slug TEXT, navn TEXT, yahoo TEXT,
    publisert TEXT, pred REAL, sikkerhet REAL, siste_kurs REAL, limit_pris REAL, stopp REAL,
    inn_dato TEXT, ut_dato TEXT, beskrivelse TEXT, bidrag TEXT, anbefalt INTEGER, varslet INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS hjerne_status (id INTEGER PRIMARY KEY CHECK (id = 1), oppdatert TEXT, data TEXT);

"""


def connect(path: Path | None = None) -> sqlite3.Connection:
    p = Path(path or DB_PATH)
    p.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(p)
    con.row_factory = sqlite3.Row
    return con


# Kolonner lagt til etter at tabellen fantes. CREATE TABLE IF NOT EXISTS
# endrer ikke eksisterende tabeller, så de legges til her.
MIGRERINGER = [("innsidehandler", "beholdning_etter", "REAL"), ("hjerne_trades", "belop", "REAL"),
               ("kurser", "utbytte", "REAL")]


def _migrer(con: sqlite3.Connection) -> None:
    # kurser som WITHOUT ROWID (eldre databaser har en egen indeks i tillegg).
    sql = (con.execute("SELECT sql FROM sqlite_master WHERE name = 'kurser'").fetchone() or [""])[0] or ""
    if sql and "WITHOUT ROWID" not in sql.upper():
        con.executescript(
            "CREATE TABLE kurser_ny (yahoo TEXT NOT NULL, dato TEXT NOT NULL, open REAL, high REAL, low REAL, "
            " close REAL, volum INTEGER, PRIMARY KEY (yahoo, dato)) WITHOUT ROWID;"
            "INSERT INTO kurser_ny SELECT yahoo, dato, open, high, low, close, volum FROM kurser;"
            "DROP TABLE kurser; ALTER TABLE kurser_ny RENAME TO kurser;"
        )
    for tabell, kolonne, typ in MIGRERINGER:
        finnes = {r[1] for r in con.execute(f"PRAGMA table_info({tabell})")}
        if finnes and kolonne not in finnes:
            con.execute(f"ALTER TABLE {tabell} ADD COLUMN {kolonne} {typ}")


def init(path: Path | None = None) -> None:
    with connect(path) as con:
        con.executescript(SCHEMA)
        _migrer(con)


@contextmanager
def session(path: Path | None = None):
    con = connect(path)
    try:
        con.executescript(SCHEMA)
        _migrer(con)
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
