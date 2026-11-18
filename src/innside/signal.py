"""Regelbasert score over innsidekjøp. Ingen kursdata inngår — poenget er at
vekter og terskler kan backtestes mot kurser uten at signalet har sett dem.

Vektene under er STARTVERDIER, ikke funn: de bygger på at åpne kjøp fra
CEO/CFO og klynger av flere kjøpere er det vanlige sterke signalet, mens
salg, tildelinger og opsjonsutøvelser i all hovedsak er støy. Fase 2-
backtesten skal kalibrere dem; bump SIGNAL_VERSJON når de endres.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta

from src.config import INNSIDE_FX_NOK

SIGNAL_VERSJON = "s1"
VINDU_DAGER = 30
TERSKEL = 4.0

ROLLEVEKT = {"ceo": 3.0, "cfo": 3.0, "styreleder": 2.0, "styremedlem": 1.0, "ledelse": 1.0, "naerstaaende": 0.5, "ukjent": 0.5}


def verdi_nok(verdi: float | None, valuta: str | None) -> float | None:
    if verdi is None:
        return None
    kurs = INNSIDE_FX_NOK.get((valuta or "").upper())
    return verdi * kurs if kurs else None


def _verdipoeng(total: float) -> float:
    if total >= 2_000_000:
        return 3.0
    if total >= 500_000:
        return 2.0
    if total >= 100_000:
        return 1.0
    return 0.0


def score_vindu(kjop: list[dict]) -> tuple[float, int, float, str]:
    """kjop = dedupliserte, gyldige aksjekjøp i vinduet. Returnerer
    (score, antall personer, sum NOK, begrunnelse)."""
    per_person: dict[str, float] = {}
    total = 0.0
    for k in kjop:
        nøkkel = (k["person"] or f"ukjent-{k['nyhet_id']}").strip().lower()
        per_person[nøkkel] = max(per_person.get(nøkkel, 0.0), ROLLEVEKT.get(k["rolle"], 0.5))
        total += verdi_nok(k["verdi"], k["valuta"]) or 0.0
    n = len(per_person)
    rolle_poeng = sum(per_person.values())
    klynge = 0.0 if n < 2 else (3.0 if n >= 3 else 2.0)
    verdi_p = _verdipoeng(total)
    score = rolle_poeng + klynge + verdi_p
    deler = [f"roller {rolle_poeng:g}", f"klynge ({n} pers.) {klynge:g}", f"verdi ~{total:,.0f} NOK {verdi_p:g}".replace(",", " ")]
    return score, n, total, ", ".join(deler)


def _dedupliserte_kjop(con: sqlite3.Connection) -> list[dict]:
    rader = con.execute(
        "SELECT * FROM innsidehandler WHERE gyldig = 1 AND retning = 'kjop' AND instrument = 'aksje' "
        "ORDER BY publisert, nyhet_id, linje"
    ).fetchall()
    sett: set[str] = set()
    ut = []
    for r in rader:
        if r["noekkel"] in sett:
            continue
        sett.add(r["noekkel"])
        ut.append(dict(r))
    return ut


def bygg_signaler(con: sqlite3.Connection) -> int:
    """Lager ett signal per innsidekjøp der vindusscoren er over terskel."""
    kjop = _dedupliserte_kjop(con)
    nye = 0
    for k in kjop:
        t = datetime.fromisoformat(k["publisert"])
        start = (t - timedelta(days=VINDU_DAGER)).isoformat(timespec="seconds")
        vindu = [x for x in kjop if x["selskap_slug"] == k["selskap_slug"] and start <= x["publisert"] <= k["publisert"]]
        score, n, total, begr = score_vindu(vindu)
        if score < TERSKEL:
            continue
        cur = con.execute(
            "INSERT OR IGNORE INTO innside_signaler "
            "(selskap_slug, utlost_av_nyhet, publisert, signal_versjon, score, antall_personer, verdi_nok, begrunnelse) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (k["selskap_slug"], k["nyhet_id"], k["publisert"], SIGNAL_VERSJON, score, n, total, begr),
        )
        nye += cur.rowcount
    con.commit()
    return nye


def ikke_varslede(con: sqlite3.Connection) -> list[sqlite3.Row]:
    """Signaler som ikke er varslet, og som slår forrige varslede signal i
    samme selskap de siste VINDU_DAGER dagene (unngå ett varsel per kjøp i
    en klynge)."""
    ut = []
    for s in con.execute(
        "SELECT * FROM innside_signaler WHERE varslet = 0 AND signal_versjon = ? ORDER BY publisert",
        (SIGNAL_VERSJON,),
    ).fetchall():
        start = (datetime.fromisoformat(s["publisert"]) - timedelta(days=VINDU_DAGER)).isoformat(timespec="seconds")
        forrige = con.execute(
            "SELECT MAX(score) FROM innside_signaler WHERE selskap_slug = ? AND varslet = 1 "
            "AND signal_versjon = ? AND publisert >= ? AND publisert < ?",
            (s["selskap_slug"], SIGNAL_VERSJON, start, s["publisert"]),
        ).fetchone()[0]
        if forrige is None or s["score"] > forrige:
            ut.append(s)
        else:
            con.execute("UPDATE innside_signaler SET varslet = 2 WHERE id = ?", (s["id"],))  # 2 = undertrykt
    con.commit()
    return ut


def formater_varsel(con: sqlite3.Connection, s: sqlite3.Row) -> str:
    h = con.execute(
        "SELECT h.*, n.selskap_navn FROM innsidehandler h JOIN nyheter n ON n.id = h.nyhet_id "
        "WHERE h.nyhet_id = ? AND h.retning = 'kjop' AND h.instrument = 'aksje' AND h.gyldig = 1 ORDER BY h.linje LIMIT 1",
        (s["utlost_av_nyhet"],),
    ).fetchone()
    navn = (h["selskap_navn"] if h and h["selskap_navn"] else s["selskap_slug"])
    linje = ""
    if h:
        pris = f" à {h['pris']:g} {h['valuta']}" if h["pris"] and h["valuta"] else ""
        antall = f"{h['antall']:,.0f}".replace(",", " ") if h["antall"] else "?"
        linje = f"\n{h['person'] or 'ukjent'} ({h['rolle_tekst'] or h['rolle']}) kjøpte {antall} aksjer{pris}"
    return (
        f"🔎 <b>Innsidekjøp: {navn}</b> (score {s['score']:g}){linje}\n"
        f"{s['antall_personer']} kjøper(e) siste {VINDU_DAGER} dager, ~{s['verdi_nok'] or 0:,.0f} NOK totalt.\n"
        f"<i>{s['begrunnelse']}</i>\n"
        "Informasjon, ikke kjøpsråd — ingen inn-/utpris før backtest."
    ).replace(",", " ")
