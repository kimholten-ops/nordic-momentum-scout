"""Backtest av innsidekjøp mot daglige kurser.

Regler som holder testen ærlig:
- Innsiden er først kjent når meldingen er PUBLISERT, ikke på transaksjons-
  dagen. Inngang = åpningskurs første handelsdag ETTER publiseringsdagen.
- Utgang = sluttkurs h handelsdager etter inngangsdagen (h = 1, 5, 20, 60).
- Kostnader trekkes fra hver handel (INNSIDE_KOSTNAD_ROUNDTRIP, standard 2,2 %:
  2 x 29 kr på ~5 000 kr + 1 % slippage på småcap).
- Handler uten kursdata (umappet selskap, for kort historikk) telles og
  rapporteres, aldri imputeres.
Ingen markedsjustering ennå: en oppgang kan være markedet, ikke innsiden. Se
"gjenstår" i README før tallene brukes til noe annet enn å se om det finnes
noe å følge opp.
"""
from __future__ import annotations

import argparse
import json
import statistics
from bisect import bisect_right
from datetime import datetime

from src import db
from src.config import env
from src.innside.signal import SIGNAL_VERSJON

HORISONTER = (1, 5, 20, 60)
KOSTNAD = float(env("INNSIDE_KOSTNAD_ROUNDTRIP", "0.022"))


def _kurser(con) -> dict[str, tuple[list[str], dict[str, tuple[float | None, float | None]]]]:
    ut: dict[str, tuple[list[str], dict]] = {}
    for r in con.execute("SELECT yahoo, dato, open, close FROM kurser ORDER BY yahoo, dato"):
        datoer, rader = ut.setdefault(r["yahoo"], ([], {}))
        datoer.append(r["dato"])
        rader[r["dato"]] = (r["open"], r["close"])
    return ut


def avkastning(kurs: tuple[list[str], dict], publisert: str, h: int) -> float | None:
    """Netto avkastning for én handel, eller None uten dekning."""
    datoer, rader = kurs
    pub_dag = publisert[:10]
    i = bisect_right(datoer, pub_dag)           # første dato STRENGT etter publiseringsdagen
    if i >= len(datoer) or i + h >= len(datoer):
        return None
    inn = rader[datoer[i]][0]
    ut = rader[datoer[i + h]][1]
    if not inn or not ut:
        return None
    return ut / inn - 1 - KOSTNAD


def _hendelser(con) -> list[dict]:
    rader = con.execute(
        "SELECT h.*, s.yahoo, "
        "  EXISTS (SELECT 1 FROM innside_signaler g WHERE g.utlost_av_nyhet = h.nyhet_id AND g.signal_versjon = ?) AS signal "
        "FROM innsidehandler h LEFT JOIN selskaper s ON s.slug = h.selskap_slug "
        "WHERE h.gyldig = 1 AND h.retning = 'kjop' AND h.instrument = 'aksje' ORDER BY h.publisert, h.nyhet_id, h.linje",
        (SIGNAL_VERSJON,),
    ).fetchall()
    sett, ut = set(), []
    for r in rader:
        if r["noekkel"] in sett:
            continue
        sett.add(r["noekkel"])
        ut.append(dict(r))
    return ut


def _stat(vals: list[float]) -> dict:
    if not vals:
        return {"n": 0}
    return {
        "n": len(vals),
        "treff": round(sum(v > 0 for v in vals) / len(vals), 3),
        "snitt": round(statistics.mean(vals), 4),
        "median": round(statistics.median(vals), 4),
    }


def kjor(con) -> dict:
    kurser = _kurser(con)
    grupper: dict[str, dict[int, list[float]]] = {}
    uten_kurs = 0
    hendelser = _hendelser(con)

    def legg(gruppe: str, h: int, v: float) -> None:
        grupper.setdefault(gruppe, {}).setdefault(h, []).append(v)

    for e in hendelser:
        kurs = kurser.get(e["yahoo"] or "")
        if not kurs:
            uten_kurs += 1
            continue
        for h in HORISONTER:
            v = avkastning(kurs, e["publisert"], h)
            if v is None:
                continue
            legg("alle_kjop", h, v)
            if e["signal"]:
                legg("signal", h, v)
            legg(f"rolle:{e['rolle']}", h, v)
            if e["verdi"] and e["valuta"]:
                legg("verdi>=100k", h, v) if (e["verdi"] >= 100_000) else legg("verdi<100k", h, v)

    return {
        "kjop_totalt": len(hendelser),
        "uten_kursdata": uten_kurs,
        "kostnad_roundtrip": KOSTNAD,
        "grupper": {g: {f"{h}d": _stat(v) for h, v in sorted(hs.items())} for g, hs in sorted(grupper.items())},
    }


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--json", action="store_true")
    a = p.parse_args()
    with db.session() as con:
        r = kjor(con)
    if a.json:
        print(json.dumps(r, ensure_ascii=False, indent=2))
        return 0
    print(f"{r['kjop_totalt']} innsidekjøp, {r['uten_kursdata']} uten kursdata, kostnad {r['kostnad_roundtrip']:.1%} per runde\n")
    print(f"{'gruppe':<18}{'h':>4}{'n':>6}{'treff':>8}{'snitt':>9}{'median':>9}")
    for g, hs in r["grupper"].items():
        for h, s in hs.items():
            if s["n"]:
                print(f"{g:<18}{h:>4}{s['n']:>6}{s['treff']:>8.0%}{s['snitt']:>9.1%}{s['median']:>9.1%}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
