"""Deduplisering: mange rader i "nyheter" er samme pressemelding.

MFN publiserer ofte samme sak flere ganger — én rad per språk og én rad per
distributør. En "hendelse" er klyngen av slike rader som faktisk er samme
nyhet. Grupperingen er bevisst enkel og deterministisk (ingen LLM): samme
selskap, publisert innen ti minutter, og en tittellikhet over terskelen.

"nyheter" endres aldri av dette. Denne modulen bygger kun laget over, og kan
kjøres på nytt fra bunnen av (mot en tom hendelser-tabell) uten å miste noe,
eller inkrementelt hver gang poll_news har hentet noe nytt.
"""
from __future__ import annotations

import difflib
import re
import sqlite3
from datetime import datetime, timedelta

VINDU_MINUTTER = 10
TERSKEL = 0.5

# Foretrekk norsk, deretter engelsk, som kanonisk rad. Alt annet er likestilt.
SPRAK_RANG = {"no": 0, "en": 1}

# Svært vanlige ord på tvers av no/sv/da/en/fi. Filtreres bort før
# token-overlapp beregnes, ellers gir de falsk likhet mellom to helt
# forskjellige nyheter fra samme selskap.
STOPPORD = {
    "og", "i", "en", "et", "til", "for", "av", "med", "på", "om", "er", "the",
    "and", "of", "to", "in", "a", "for", "on", "is", "och", "för", "med",
    "att", "på", "i", "en", "ett", "og", "for", "i", "på", "af", "med",
}

_TOKEN_RE = re.compile(r"\w+", re.UNICODE)


def normaliser_tittel(tittel: str) -> set[str]:
    tokens = {t.lower() for t in _TOKEN_RE.findall(tittel)}
    return {t for t in tokens if t not in STOPPORD and len(t) > 1}


def tittel_likhet(a: str, b: str) -> float:
    """Kombinerer token-overlapp (bra på tvers av språk: tall, ticker,
    selskapsnavn overlever ofte oversettelse) og SequenceMatcher (bra for
    nesten identisk tekst i samme språk, f.eks. to distributører)."""
    ta, tb = normaliser_tittel(a), normaliser_tittel(b)
    overlapp = len(ta & tb) / min(len(ta), len(tb)) if ta and tb else 0.0
    sekvens = difflib.SequenceMatcher(None, a.lower(), b.lower()).ratio()
    return max(overlapp, sekvens)


def _vindu(publisert: str, minutter: int = VINDU_MINUTTER) -> tuple[str, str]:
    t = datetime.fromisoformat(publisert)
    delta = timedelta(minutes=minutter)
    return (t - delta).isoformat(timespec="seconds"), (t + delta).isoformat(timespec="seconds")


def _rang(sprak: str | None) -> int:
    return SPRAK_RANG.get(sprak or "", 2)


def _finn_kandidat(con: sqlite3.Connection, slug: str, publisert: str, tittel: str):
    start, slutt = _vindu(publisert)
    kandidater = con.execute(
        "SELECT id, kanonisk_nyhet_id, publisert, tittel FROM hendelser "
        "WHERE selskap_slug = ? AND publisert BETWEEN ? AND ?",
        (slug, start, slutt),
    ).fetchall()
    best = None
    best_score = 0.0
    for k in kandidater:
        score = tittel_likhet(tittel, k["tittel"])
        if score >= TERSKEL and score > best_score:
            best, best_score = k, score
    return best


def bygg_hendelser(con: sqlite3.Connection) -> dict:
    """Kobler alle nyheter som ennå ikke tilhører en hendelse. Idempotent:
    et gjentatt kall uten nye nyheter kobler null rader."""
    ukoblet = con.execute(
        "SELECT n.id, n.selskap_slug, n.ticker, n.publisert, n.tittel, n.sprak "
        "FROM nyheter n LEFT JOIN hendelse_nyheter hn ON hn.nyhet_id = n.id "
        "WHERE hn.nyhet_id IS NULL "
        "ORDER BY n.publisert, n.id"
    ).fetchall()

    nye_hendelser = 0
    sammenslatt = 0

    for rad in ukoblet:
        kandidat = _finn_kandidat(con, rad["selskap_slug"], rad["publisert"], rad["tittel"])

        if kandidat is None:
            cur = con.execute(
                "INSERT INTO hendelser (kanonisk_nyhet_id, selskap_slug, ticker, "
                "publisert, tittel, antall_rader) VALUES (?,?,?,?,?,1)",
                (rad["id"], rad["selskap_slug"], rad["ticker"], rad["publisert"], rad["tittel"]),
            )
            hendelse_id = cur.lastrowid
            con.execute(
                "INSERT INTO hendelse_nyheter (hendelse_id, nyhet_id) VALUES (?,?)",
                (hendelse_id, rad["id"]),
            )
            nye_hendelser += 1
            continue

        con.execute(
            "INSERT INTO hendelse_nyheter (hendelse_id, nyhet_id) VALUES (?,?)",
            (kandidat["id"], rad["id"]),
        )

        kanon_sprak = con.execute(
            "SELECT sprak FROM nyheter WHERE id = ?", (kandidat["kanonisk_nyhet_id"],)
        ).fetchone()["sprak"]

        ny_kanonisk_id = kandidat["kanonisk_nyhet_id"]
        ny_tittel = kandidat["tittel"]
        if _rang(rad["sprak"]) < _rang(kanon_sprak):
            ny_kanonisk_id = rad["id"]
            ny_tittel = rad["tittel"]

        ny_publisert = min(kandidat["publisert"], rad["publisert"])

        con.execute(
            "UPDATE hendelser SET kanonisk_nyhet_id = ?, tittel = ?, publisert = ?, "
            "antall_rader = antall_rader + 1, "
            "ticker = COALESCE(ticker, ?) "
            "WHERE id = ?",
            (ny_kanonisk_id, ny_tittel, ny_publisert, rad["ticker"], kandidat["id"]),
        )
        sammenslatt += 1

    return {
        "rader_vurdert": len(ukoblet),
        "nye_hendelser": nye_hendelser,
        "sammenslatt_i_eksisterende": sammenslatt,
    }
