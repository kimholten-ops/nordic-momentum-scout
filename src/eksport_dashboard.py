"""Eksporterer data til site/data.json for dashboardet (GitHub Pages).

Dashboardet er en ren statisk side (site/index.html) som leser denne
filen — ingen server, ingen hemmeligheter. Alt i filen er utledet av
offentlige børsmeldinger og kurser. Kjøres av pages.yml etter hver
dataoppdatering.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

from src import db, innside_backtest
from src.config import ROOT, TZ
from src.innside.kjor import EKSTRAKSJON_VERSJON
from src.innside.signal import SIGNAL_VERSJON, TERSKEL, VINDU_DAGER

UT = ROOT / "site" / "data.json"


def _rader(con, sql: str, args: tuple = ()) -> list[dict]:
    return [dict(r) for r in con.execute(sql, args).fetchall()]


def bygg(con) -> dict:
    tall = {
        "nyheter": con.execute("SELECT COUNT(*) FROM nyheter").fetchone()[0],
        "siste_nyhet": con.execute("SELECT MAX(publisert) FROM nyheter").fetchone()[0],
        "innsidehandler": con.execute("SELECT COUNT(*) FROM innsidehandler WHERE gyldig = 1").fetchone()[0],
        "innsidekjop": con.execute(
            "SELECT COUNT(*) FROM innsidehandler WHERE gyldig = 1 AND retning = 'kjop' AND instrument = 'aksje'"
        ).fetchone()[0],
        "signaler": con.execute("SELECT COUNT(*) FROM innside_signaler").fetchone()[0],
        "venter_pa_llm": con.execute(
            "SELECT COUNT(*) FROM innside_behandlet WHERE versjon = ? AND status IN ('trenger_llm','feilet')",
            (EKSTRAKSJON_VERSJON,),
        ).fetchone()[0],
        "selskaper_i_univers": con.execute("SELECT COUNT(*) FROM selskaper WHERE aktiv = 1 AND ticker IS NOT NULL").fetchone()[0],
        "siste_kurs": con.execute("SELECT MAX(dato) FROM kurser").fetchone()[0],
    }
    signaler = _rader(
        con,
        "SELECT g.publisert, g.selskap_slug AS slug, COALESCE(n.selskap_navn, g.selskap_slug) AS selskap, "
        " g.score, g.antall_personer, g.verdi_nok, g.begrunnelse "
        "FROM innside_signaler g JOIN nyheter n ON n.id = g.utlost_av_nyhet "
        "WHERE g.signal_versjon = ? ORDER BY g.publisert DESC",
        (SIGNAL_VERSJON,),
    )
    # Én rad per selskap og dag (høyeste score): en klynge på sju kjøpere
    # gir ellers sju nesten like signaler.
    beste: dict[tuple[str, str], dict] = {}
    for g in signaler:
        nokkel = (g["slug"], g["publisert"][:10])
        if nokkel not in beste or g["score"] > beste[nokkel]["score"]:
            beste[nokkel] = g
    signaler = sorted(beste.values(), key=lambda g: g["publisert"], reverse=True)[:100]
    # Dedupliser på noekkel: samme handel kan ligge der flere ganger (distributører).
    handler = _rader(
        con,
        "SELECT h.publisert, h.selskap_slug AS slug, COALESCE(n.selskap_navn, h.selskap_slug) AS selskap, "
        " h.person, h.rolle, h.rolle_tekst, h.naerstaaende, h.retning, h.instrument, h.antall, h.pris, h.valuta, "
        " h.verdi, h.transaksjonsdato, h.kilde, n.url "
        "FROM innsidehandler h JOIN nyheter n ON n.id = h.nyhet_id "
        "WHERE h.gyldig = 1 AND h.id IN (SELECT MIN(id) FROM innsidehandler WHERE gyldig = 1 GROUP BY noekkel) "
        "ORDER BY h.publisert DESC LIMIT 500",
    )
    nyheter = _rader(
        con,
        "SELECT h.publisert, h.selskap_slug AS slug, h.tittel, k.kategori, k.retning, k.overraskelse, k.begrunnelse, n.url "
        "FROM klassifiseringer k JOIN hendelser h ON h.id = k.hendelse_id "
        "JOIN nyheter n ON n.id = h.kanonisk_nyhet_id "
        "WHERE k.status = 'ok' AND k.kategori != 'rutine' ORDER BY h.publisert DESC LIMIT 100",
    )
    logg = _rader(con, "SELECT tidspunkt, jobb, status, detalj FROM kjorelogg ORDER BY id DESC LIMIT 40")
    return {
        "generert": datetime.now(TZ).isoformat(timespec="seconds"),
        "signal": {"versjon": SIGNAL_VERSJON, "terskel": TERSKEL, "vindu_dager": VINDU_DAGER},
        "tall": tall,
        "signaler": signaler,
        "handler": handler,
        "nyheter": nyheter,
        "backtest": innside_backtest.kjor(con),
        "logg": logg,
    }


def main() -> int:
    with db.session() as con:
        data = bygg(con)
    UT.parent.mkdir(exist_ok=True)
    UT.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"Skrev {UT} ({len(data['handler'])} handler, {len(data['signaler'])} signaler).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
