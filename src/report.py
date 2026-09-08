"""Ukesrapport for fase 0: går innsamlingen som den skal?

Den viktigste linjen er medianforsinkelsen. Er den over ~10 minutter, er
polleintervallet ditt for tregt til at post-nyhet-strategier gir mening.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from src import db
from src.config import TZ
from src.notify import send


def bygg() -> str:
    with db.session() as con:
        c = con.cursor()
        totalt = c.execute("SELECT COUNT(*) FROM nyheter").fetchone()[0]
        uke = c.execute(
            "SELECT COUNT(*) FROM nyheter WHERE publisert >= ?",
            ((datetime.now(TZ) - timedelta(days=7)).isoformat(timespec="seconds"),),
        ).fetchone()[0]
        mappet = c.execute("SELECT COUNT(*) FROM nyheter WHERE ticker IS NOT NULL").fetchone()[0]
        kurser = c.execute("SELECT COUNT(*) FROM kurser").fetchone()[0]
        selskaper = c.execute("SELECT COUNT(*) FROM selskaper WHERE aktiv=1").fetchone()[0]

        forsinkelser = []
        for r in c.execute(
            "SELECT publisert, hentet FROM nyheter ORDER BY id DESC LIMIT 300"
        ):
            try:
                p = datetime.fromisoformat(r["publisert"])
                h = datetime.fromisoformat(r["hentet"]).replace(tzinfo=None)
                forsinkelser.append((h - p.replace(tzinfo=None)).total_seconds() / 60)
            except ValueError:
                continue
        forsinkelser = sorted(x for x in forsinkelser if 0 <= x < 240)
        median = forsinkelser[len(forsinkelser) // 2] if forsinkelser else None

        topp = c.execute(
            "SELECT slug, navn, antall FROM umappede ORDER BY antall DESC LIMIT 8"
        ).fetchall()
        siste = c.execute(
            "SELECT tidspunkt, jobb, status, detalj FROM kjorelogg ORDER BY id DESC LIMIT 3"
        ).fetchall()

    linjer = [
        "<b>Momentum – status fase 0</b>",
        f"Nyheter totalt: {totalt}  (siste 7 dager: {uke})",
        f"Med ticker: {mappet} av {totalt}",
        f"Selskaper i universet: {selskaper}   Kursrader: {kurser}",
        f"Median forsinkelse publisering→henting: "
        f"{median:.1f} min" if median is not None else "Median forsinkelse: ukjent",
        "",
    ]
    if topp:
        linjer.append("<b>Umappede selskaper (flest nyheter først)</b>")
        linjer += [f"  {r['slug']} – {r['antall']}" for r in topp]
        linjer.append("")
    linjer.append("<b>Siste kjøringer</b>")
    linjer += [f"  {r['tidspunkt']} {r['jobb']} {r['status']} – {r['detalj']}" for r in siste]
    return "\n".join(linjer)


if __name__ == "__main__":
    tekst = bygg()
    print(tekst.replace("<b>", "").replace("</b>", ""))
    send(tekst, stille=True)
