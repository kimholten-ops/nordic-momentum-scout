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
from src.config import INDEKSER, ROOT, TZ, env
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
        "siste_kurs": con.execute("SELECT MAX(dato) FROM kurser WHERE close IS NOT NULL").fetchone()[0],
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
    st = con.execute("SELECT oppdatert, data FROM hjerne_status WHERE id = 1").fetchone()
    hjerne = {
        "oppdatert": st["oppdatert"] if st else None,
        "status": json.loads(st["data"]) if st else None,
        "forslag": _rader(con, "SELECT * FROM hjerne_forslag ORDER BY publisert DESC LIMIT 30"),
        "trades": _rader(con, "SELECT * FROM hjerne_trades ORDER BY inn_dato DESC LIMIT 200"),
        "egenkapital": _rader(con, "SELECT dato, verdi FROM hjerne_egenkapital ORDER BY dato"),
        "vurderinger": _rader(
            con, "SELECT nokkel, kilde, navn, inn_dato, pred, sikkerhet, valgt, utfall, beskrivelse "
            "FROM hjerne_prediksjoner ORDER BY inn_dato DESC LIMIT 60"),
    }
    for f in hjerne["forslag"]:
        f["bidrag"] = json.loads(f["bidrag"] or "[]")
    # Selskaper med innsidekjøp som mangler ticker: kan ikke backtestes eller handles.
    umappede = _rader(
        con,
        "SELECT h.selskap_slug AS slug, MAX(n.selskap_navn) AS navn, COUNT(*) AS kjop, MAX(h.publisert) AS siste "
        "FROM innsidehandler h JOIN nyheter n ON n.id = h.nyhet_id "
        "WHERE h.gyldig = 1 AND h.retning = 'kjop' AND h.instrument = 'aksje' "
        "AND h.selskap_slug NOT IN (SELECT slug FROM selskaper WHERE yahoo IS NOT NULL AND yahoo <> '') "
        "GROUP BY h.selskap_slug ORDER BY kjop DESC LIMIT 50",
    )
    # Kurser for kandidater, papirhandler og indekser (siste 120 handelsdager),
    # slik at simuleringsfanen kan følge egne papirkjøp mot ekte kurser.
    symboler = {c["yahoo"] for c in (hjerne["status"] or {}).get("kandidater", [])} | {t["yahoo"] for t in hjerne["trades"]}
    symboler |= set(INDEKSER.values())
    kurser = {}
    for sym in sorted(x for x in symboler if x):
        rader = con.execute(
            "SELECT dato, open, high, low, close FROM (SELECT * FROM kurser WHERE yahoo = ? AND close IS NOT NULL ORDER BY dato DESC LIMIT 120) ORDER BY dato",
            (sym,),
        ).fetchall()
        if rader:
            r4 = lambda v: round(v, 4) if v is not None else None
            kurser[sym] = {k: [r4(r[k]) if k != "dato" else r[k] for r in rader] for k in ("dato", "open", "high", "low", "close")}
    panel = {"status": {}, "score": []}
    if con.execute("SELECT name FROM sqlite_master WHERE name = 'panel_score'").fetchone():
        import csv

        usa = {}
        fil = ROOT / "universe" / "usa.csv"
        if fil.exists():
            with fil.open(encoding="utf-8") as fh:
                usa = {r["symbol"]: r for r in csv.DictReader(fh)}
        navn = {r[0]: r[1] for r in con.execute("SELECT yahoo, MIN(navn) FROM selskaper GROUP BY yahoo")}
        panel["status"] = {r[0]: {"oppdatert": r[1], **json.loads(r[2])} for r in con.execute("SELECT * FROM panel_status")}
        for st in panel["status"].values():
            st.pop("egenskaper", None)
        for r in con.execute("SELECT * FROM panel_score ORDER BY persentil DESC"):
            u = usa.get(r["yahoo"], {})
            panel["score"].append({
                "yahoo": r["yahoo"], "navn": navn.get(r["yahoo"]) or u.get("navn") or r["yahoo"], "region": r["region"],
                "mine": u.get("mine") == "1", "persentil": round(r["persentil"], 3), "tidel": r["tidel"],
                "forventet_mer": r["forventet_mer"], "kostnad": r["kostnad"], **json.loads(r["bidrag"] or "{}"),
            })
    siste_poll = con.execute(
        "SELECT MAX(tidspunkt) FROM kjorelogg WHERE jobb = 'poll_news' AND status = 'ok'"
    ).fetchone()[0]
    return {
        "generert": datetime.now(TZ).isoformat(timespec="seconds"),
        "signal": {"versjon": SIGNAL_VERSJON, "terskel": TERSKEL, "vindu_dager": VINDU_DAGER},
        "tall": tall,
        "signaler": signaler,
        "handler": handler,
        "nyheter": nyheter,
        "backtest": innside_backtest.kjor(con),
        "logg": logg,
        "hjerne": hjerne,
        "umappede": umappede,
        "siste_poll": siste_poll,
        "webpush_public": env("WEBPUSH_VAPID_PUBLIC", ""),
        "kurser": kurser,
        "panel": panel,
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
