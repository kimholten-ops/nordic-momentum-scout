"""Daglig hjernejobb (kjøres etter kursene i «Kurser og rapport»):

  python -m src.hjerne_kjor            # lær, simuler, lag forslag, varsle
  python -m src.hjerne_kjor --ikke-varsle

Skriver hjerne_* -tabellene som dashboardet leser, og sender nye forslag til
Telegram og web push. Forslag varsles bare én gang.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from dataclasses import asdict
from datetime import datetime

from src import db, marked
from src.config import HJERNE_HORISONT_LANG, HJERNE_VARSLE, TZ
from src.hjerne import kandidater as kand
from src.hjerne import hendelser as hh
from src.hjerne import simuler
from src.notify import esc, send


def _pst(v: float | None, d: int = 1) -> str:
    return "–" if v is None else f"{v * 100:+.{d}f} %".replace(".", ",")


def _kr(v: float) -> str:
    return f"{v:,.2f}".replace(",", " ").replace(".", ",")


def lagre(con, res: simuler.Resultat, p: simuler.Param, ekstra: dict | None = None) -> dict:
    na = datetime.now(TZ).isoformat(timespec="seconds")
    con.execute("DELETE FROM hjerne_prediksjoner")
    con.executemany(
        "INSERT OR REPLACE INTO hjerne_prediksjoner (nokkel, kilde, slug, navn, publisert, inn_dato, pred, sikkerhet, "
        "valgt, utfall, trent_n, bidrag, beskrivelse) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [(r["nokkel"], r["kilde"], r["slug"], r["navn"], r["publisert"], r["inn_dato"], r["pred"], r["sikkerhet"],
          r["valgt"], r["utfall"], r["trent_n"], json.dumps(r["bidrag"], ensure_ascii=False), r["beskrivelse"])
         for r in res.prediksjoner],
    )
    con.execute("DELETE FROM hjerne_trades")
    kol = ["nokkel", "slug", "navn", "yahoo", "inn_dato", "inn", "stopp", "plan_ut_dato", "ut_dato", "ut",
           "status", "raa", "marked", "kost", "netto", "pnl_kr", "pred", "belop"]
    con.executemany(
        f"INSERT INTO hjerne_trades ({','.join(kol)}) VALUES ({','.join('?' * len(kol))})",
        [tuple(asdict(t)[k] for k in kol) for t in res.trades],
    )
    con.execute("DELETE FROM hjerne_egenkapital")
    con.executemany("INSERT INTO hjerne_egenkapital (dato, verdi) VALUES (?,?)", res.egenkapital)
    for f in res.forslag:
        con.execute(
            "INSERT INTO hjerne_forslag (nokkel, opprettet, kilde, slug, navn, yahoo, publisert, pred, sikkerhet, "
            " siste_kurs, limit_pris, stopp, inn_dato, ut_dato, beskrivelse, bidrag, anbefalt) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(nokkel) DO UPDATE SET "
            " pred=excluded.pred, sikkerhet=excluded.sikkerhet, siste_kurs=excluded.siste_kurs, "
            " limit_pris=excluded.limit_pris, stopp=excluded.stopp, bidrag=excluded.bidrag, "
            " anbefalt=MAX(hjerne_forslag.anbefalt, excluded.anbefalt)",
            (f["nokkel"], na, f["kilde"], f["slug"], f["navn"], f["yahoo"], f["publisert"], f["pred"], f["sikkerhet"],
             f["siste_kurs"], f["limit"], f["stopp"], f["inn_dato"], f["ut_dato"], f["beskrivelse"],
             json.dumps(f["bidrag"], ensure_ascii=False), f["anbefalt"]),
        )

    lukket = [t for t in res.trades if t.status != "åpen" and t.netto is not None]
    alle_utfall = [r["utfall"] for r in res.prediksjoner if r["utfall"] is not None]
    status = {
        "param": {k: getattr(p, k) for k in ("horisont", "halveringstid", "prior", "min_edge", "min_sikkerhet",
                                              "maks_apne", "kapital", "stopp_atr", "maks_andel", "risiko",
                                              "likviditet_tak", "min_posisjon")} | {"posisjon": round(p.posisjon)},
        "modell": res.modell.som_dict() if res.modell else None,
        "kalibrering": simuler.kalibrering(res.prediksjoner),
        "hendelser_vurdert": len(res.prediksjoner),
        "hendelser_uten_kurs": res.hoppet_over,
        "trades": {
            "lukket": len(lukket),
            "apne": sum(t.status == "åpen" for t in res.trades),
            "treff": round(sum(t.netto > 0 for t in lukket) / len(lukket), 3) if lukket else None,
            "snitt_netto": round(statistics.mean(t.netto for t in lukket), 4) if lukket else None,
            "snitt_mer": round(statistics.mean(t.netto - (t.marked or 0) for t in lukket), 4) if lukket else None,
            "pnl_kr": round(sum(t.pnl_kr or 0 for t in lukket), 2),
            "stoppet": sum(t.status == "stoppet" for t in lukket),
        },
        "referanse_alle_hendelser": round(statistics.mean(alle_utfall), 4) if alle_utfall else None,
        "egenkapital_slutt": res.egenkapital[-1][1] if res.egenkapital else p.kapital,
    } | (ekstra or {})
    con.execute(
        "INSERT INTO hjerne_status (id, oppdatert, data) VALUES (1, ?, ?) "
        "ON CONFLICT(id) DO UPDATE SET oppdatert=excluded.oppdatert, data=excluded.data",
        (na, json.dumps(status, ensure_ascii=False)),
    )
    con.commit()
    return status


def formater_forslag(f, forklaring: str | None = None) -> str:
    grunner = "; ".join(f"{esc(b[0])} {_pst(b[1])}" for b in json.loads(f["bidrag"])[:3])
    ticker = (f["yahoo"] or "").split(".")[0]
    if forklaring:
        return (
            f"📈 <b>Forslag: kjøp {esc(f['navn'])}</b> ({esc(ticker)})\n"
            f"{esc(forklaring)}\n"
            "<i>Papirhandel/læring — ikke kjøpsråd.</i>"
        )
    return (
        f"📈 <b>Forslag: kjøp {esc(f['navn'])}</b> ({esc(ticker)})\n"
        f"Inngang: åpning {f['inn_dato']}, maks {_kr(f['limit_pris'])}\n"
        f"Stopp: {_kr(f['stopp'])}  ·  Utgang: sluttkurs {f['ut_dato']}\n"
        f"Forventet netto meravkastning {_pst(f['pred'])} (sikkerhet {f['sikkerhet'] * 100:.0f} %)\n"
        f"{esc(f['beskrivelse'])}\n"
        f"<i>Hvorfor: {grunner}</i>\n"
        "Papirhandel/læring — ikke kjøpsråd."
    )


def varsle(con, kandidater: list[dict] | None = None) -> int:
    """Sender nye forslag. Forklaringen i vanlig språk hentes fra kandidaten
    for samme selskap når den finnes."""
    forklar = {c["slug"]: c.get("forklaring") for c in (kandidater or [])}
    n = 0
    for f in con.execute(
        "SELECT * FROM hjerne_forslag WHERE anbefalt = 1 AND varslet = 0 ORDER BY pred DESC"
    ).fetchall():
        if send(formater_forslag(f, forklar.get(f["slug"])), push=True, tittel=f"Kjøpsforslag: {f['navn']}"):
            con.execute("UPDATE hjerne_forslag SET varslet = 1 WHERE nokkel = ?", (f["nokkel"],))
            n += 1
    con.commit()
    return n


def kjor(varsle_nye: bool = HJERNE_VARSLE) -> dict:
    with db.session() as con:
        kurser = marked.last_kurser(con)
        hendelser = hh.alle(con, kurser)
        p = simuler.Param()
        res = simuler.kjor(hendelser, kurser, p)
        # Lang horisont: egen walk-forward (for å måle om den lærer) og egen modell.
        p_lang = simuler.Param(horisont=HJERNE_HORISONT_LANG)
        res_lang = simuler.kjor(hendelser, kurser, p_lang)
        regel = {
            (r["selskap_slug"], r["publisert"][:10]): r["score"]
            for r in con.execute("SELECT selskap_slug, publisert, MAX(score) AS score FROM innside_signaler "
                                 "GROUP BY selskap_slug, substr(publisert, 1, 10)")
        }
        panel = {}
        if con.execute("SELECT name FROM sqlite_master WHERE name = 'panel_score'").fetchone():
            panel = {r["yahoo"]: {"persentil": r["persentil"], "tidel": r["tidel"], "region": r["region"],
                                  "forventet_mer": r["forventet_mer"], "bidrag": json.loads(r["bidrag"] or "{}")}
                     for r in con.execute("SELECT * FROM panel_score")}
        from src import intradag

        kandidater = (kand.ranger(hendelser, kurser, res.modell, res_lang.modell, p, p_lang, regel, panel,
                                  intradag.last(con))
                      if res.modell and res_lang.modell else [])
        ekstra = {
            "kandidater": kandidater,
            "kvalitet": {
                "kort": {"dager": p.horisont, **simuler.kalibrering(res.prediksjoner)},
                "lang": {"dager": p_lang.horisont, **simuler.kalibrering(res_lang.prediksjoner)},
            },
        }
        status = lagre(con, res, p, ekstra)
        varslet = varsle(con, kandidater) if varsle_nye else 0
        t = status["trades"]
        db.logg(
            con, "hjerne", "ok",
            f"{len(hendelser)} hendelser, {status['hendelser_vurdert']} vurdert, {t['lukket']} lukkede papirhandler "
            f"(treff {t['treff']}), {t['apne']} åpne, {len(res.forslag)} nye kandidater, {varslet} varslet",
        )
    return status | {"varslet": varslet, "kandidater": len(res.forslag)}


def _komprimer() -> None:
    """Hjernetabellene skrives på nytt hver kjøring og etterlater tomme sider.
    VACUUM holder databasefilen (som committes til git) under GitHubs grense."""
    import sqlite3

    from src.config import DB_PATH
    try:
        c = sqlite3.connect(DB_PATH)
        c.execute("VACUUM")
        c.close()
    except sqlite3.Error as e:
        print(f"VACUUM feilet: {e}")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--ikke-varsle", action="store_true")
    a = p.parse_args()
    try:
        s = kjor(varsle_nye=HJERNE_VARSLE and not a.ikke_varsle)
    except Exception as e:
        print(f"FEIL: {type(e).__name__}: {e}", file=sys.stderr)
        send(f"⚠️ hjerne feilet: {esc(type(e).__name__)}: {esc(e)}")
        return 1
    _komprimer()
    t, k = s["trades"], s["kalibrering"]
    print(f"Vurdert {s['hendelser_vurdert']} hendelser ({s['hendelser_uten_kurs']} uten kurs).")
    print(f"Papirhandler: {t['lukket']} lukket, {t['apne']} åpne, treff {t['treff']}, snitt netto {t['snitt_netto']}, "
          f"meravkastning {t['snitt_mer']}, resultat {t['pnl_kr']} kr. Referanse (alle hendelser): {s['referanse_alle_hendelser']}")
    print(f"Kalibrering: n={k['n']}, rangkorrelasjon {k['rangkorrelasjon']}")
    for g in k["grupper"]:
        print(f"  forventet {g['forventet']:+.2%}  faktisk {g['faktisk']:+.2%}  treff {g['treff']:.0%}  (n={g['n']})")
    print(f"{s['kandidater']} kandidater uten inngang ennå, {s['varslet']} varslet.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
