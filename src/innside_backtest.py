"""Backtest av innsidekjøp mot daglige kurser.

Inngang/utgang, meravkastning og kostnader er definert i src/marked.py:
inngang er åpningskursen første handelsdag ETTER publisering, utgang er
sluttkurs h handelsdager senere, markedet (børsens indeks) trekkes fra, og
kostnaden avhenger av kurtasje på posisjonsstørrelsen og selskapets
likviditet. Handler uten kursdata telles, aldri imputeres.

Tabellen viser både netto avkastning (det du faktisk ville tjent) og netto
MERavkastning (det innsiden tilførte utover markedet). Det er den siste som
sier om signalet har verdi.
"""
from __future__ import annotations

import argparse
import json
import statistics

from src import db, marked
from src.innside.signal import SIGNAL_VERSJON

HORISONTER = (1, 5, 20, 60)


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


def _stat(netto: list[float], mer: list[float], kost: list[float]) -> dict:
    if not netto:
        return {"n": 0}
    return {
        "n": len(netto),
        "treff": round(sum(v > 0 for v in mer) / len(mer), 3),
        "snitt": round(statistics.mean(netto), 4),
        "median": round(statistics.median(netto), 4),
        "mer_snitt": round(statistics.mean(mer), 4),
        "mer_median": round(statistics.median(mer), 4),
        "kost_snitt": round(statistics.mean(kost), 4),
    }


def kjor(con) -> dict:
    kurser = marked.last_kurser(con)
    grupper: dict[str, dict[int, tuple[list, list, list]]] = {}
    uten_kurs = 0
    hendelser = _hendelser(con)

    def legg(gruppe: str, h: int, u: marked.Utfall) -> None:
        a, b, c = grupper.setdefault(gruppe, {}).setdefault(h, ([], [], []))
        a.append(u.raa - u.kost)
        b.append(u.netto_mer)
        c.append(u.kost)

    for e in hendelser:
        if not e["yahoo"] or e["yahoo"] not in kurser:
            uten_kurs += 1
            continue
        verdi_nok = marked.til_nok(e["verdi"], e["yahoo"])
        for h in HORISONTER:
            u = marked.utfall(kurser, e["yahoo"], e["publisert"], h)
            if u is None:
                continue
            legg("alle_kjop", h, u)
            if e["signal"]:
                legg("signal", h, u)
            legg(f"rolle:{e['rolle']}", h, u)
            if verdi_nok is not None:
                legg("verdi>=100k" if verdi_nok >= 100_000 else "verdi<100k", h, u)

    return {
        "kjop_totalt": len(hendelser),
        "uten_kursdata": uten_kurs,
        "grupper": {g: {f"{h}d": _stat(*v) for h, v in sorted(hs.items())} for g, hs in sorted(grupper.items())},
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
    print(f"{r['kjop_totalt']} innsidekjøp, {r['uten_kursdata']} uten kursdata. "
          "netto = etter kostnad; mer = netto meravkastning mot børsindeks.\n")
    print(f"{'gruppe':<18}{'h':>4}{'n':>6}{'treff':>8}{'netto':>9}{'mer':>9}{'mer med.':>10}{'kost':>8}")
    for g, hs in r["grupper"].items():
        for h, s in hs.items():
            if s["n"]:
                print(f"{g:<18}{h:>4}{s['n']:>6}{s['treff']:>8.0%}{s['snitt']:>9.1%}{s['mer_snitt']:>9.1%}"
                      f"{s['mer_median']:>10.1%}{s['kost_snitt']:>8.1%}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
