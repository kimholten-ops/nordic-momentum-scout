"""Ukesrapport: går innsamlingen (fase 0) og klassifiseringen (fase 1) som
de skal?

Den viktigste linjen fra fase 0 er medianforsinkelsen. Er den over ~10
minutter, er polleintervallet ditt for tregt til at post-nyhet-strategier
gir mening. Fra fase 1 er de ti siste klassifiserte hendelsene den
viktigste seksjonen — det er slik du oppdager at modellen har begynt å
tulle, siden det aldri sendes varsel om enkeltklassifiseringer.
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

        # --- Fase 1: klassifisering -----------------------------------------
        hendelser_totalt = c.execute("SELECT COUNT(*) FROM hendelser").fetchone()[0]
        klass_totalt = c.execute("SELECT COUNT(*) FROM klassifiseringer").fetchone()[0]
        rutine_antall = c.execute(
            "SELECT COUNT(*) FROM klassifiseringer WHERE status = 'rutine'"
        ).fetchone()[0]
        feilet_antall = c.execute(
            "SELECT COUNT(*) FROM klassifiseringer WHERE status = 'feilet'"
        ).fetchone()[0]
        andel_filtrert = (rutine_antall / klass_totalt) if klass_totalt else None

        fordeling = c.execute(
            "SELECT kategori, COUNT(*) AS antall FROM klassifiseringer "
            "GROUP BY kategori ORDER BY antall DESC"
        ).fetchall()
        per_versjon = c.execute(
            "SELECT prompt_versjon, COUNT(*) AS antall FROM klassifiseringer "
            "GROUP BY prompt_versjon ORDER BY prompt_versjon"
        ).fetchall()
        siste_klassifisert = c.execute(
            "SELECT h.tittel, k.kategori, k.retning, k.begrunnelse, k.klassifisert_tidspunkt "
            "FROM klassifiseringer k JOIN hendelser h ON h.id = k.hendelse_id "
            "WHERE k.status = 'ok' ORDER BY k.id DESC LIMIT 10"
        ).fetchall()

    linjer = [
        "<b>Momentum – status</b>",
        "",
        "<b>Fase 0 – innsamling</b>",
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

    linjer.append("<b>Fase 1 – klassifisering</b>")
    linjer.append(f"Hendelser (etter dedup): {hendelser_totalt}   Klassifisert: {klass_totalt}")
    linjer.append(
        f"Filtrert bort som rutine: {rutine_antall}"
        + (f" ({andel_filtrert:.0%})" if andel_filtrert is not None else "")
        + f"   Feilet: {feilet_antall}"
    )
    if fordeling:
        linjer.append("Fordeling per kategori:")
        linjer += [f"  {r['kategori']}: {r['antall']}" for r in fordeling]
    if per_versjon:
        linjer.append("Klassifiseringer per promptversjon:")
        linjer += [f"  {r['prompt_versjon']}: {r['antall']}" for r in per_versjon]
    linjer.append("")

    if siste_klassifisert:
        linjer.append("<b>Siste 10 klassifiserte hendelser</b>")
        for r in siste_klassifisert:
            linjer.append(
                f"  [{r['kategori']}/{r['retning']}] {r['tittel'][:60]} — {r['begrunnelse']}"
            )
        linjer.append("")

    linjer.append("<b>Siste kjøringer</b>")
    linjer += [f"  {r['tidspunkt']} {r['jobb']} {r['status']} – {r['detalj']}" for r in siste]
    return "\n".join(linjer)


if __name__ == "__main__":
    tekst = bygg()
    print(tekst.replace("<b>", "").replace("</b>", ""))
    send(tekst, stille=True)
