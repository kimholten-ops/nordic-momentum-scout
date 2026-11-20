"""Historisk import: hent pressemeldinger fra MFN for hvert selskap i
universet, så langt tilbake som --ar-tilbake år tillater.

Kjøres manuelt (workflow_dispatch), ikke på cron — den er tung og skal ikke
kjøre av seg selv. Et universe av noen størrelse gjør at denne jobben
garantert blir avbrutt før den er ferdig (GitHub Actions har tidsgrenser).
"arkiv_fremdrift" holder styr på hvor langt hvert selskap er kommet, og
alt committes fortløpende (per side, per klassifisering) i stedet for i én
stor transaksjon til slutt, slik at et avbrutt kall ikke mister arbeidet
som faktisk ble gjort.

MFN dokumenterer ikke arkiv-/pagineringsformatet sitt — se merknaden ved
MFN_ARKIV_URL_MAL i src/config.py. Sjekk mot ekte sider og juster om
nødvendig før du lar denne kjøre lenge.
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timedelta

from src import db
from src.config import ARKIV_FORSINKELSE_SEK, KLASSIFISERING_MAKS_KALL, MFN_ARKIV_URL_MAL, TZ
from src.klassifisering import kjor as kjor_klassifisering
from src.klassifisering.dedup import bygg_hendelser
from src.notify import send
from src.poll_news import lagre_meldinger
from src.sources import mfn
from src.universe import last_universe

MAKS_SIDER_PER_SELSKAP_PER_KJORING = 500  # sikkerhetsnett mot uendelig paginering


def _selskaper(con) -> list[str]:
    last_universe(con)
    return [r["slug"] for r in con.execute("SELECT slug FROM selskaper WHERE aktiv = 1")]


def _fremdrift(con, slug: str):
    con.execute(
        "INSERT OR IGNORE INTO arkiv_fremdrift (slug, siste_side, ferdig) VALUES (?, 0, 0)",
        (slug,),
    )
    return con.execute("SELECT * FROM arkiv_fremdrift WHERE slug = ?", (slug,)).fetchone()


def _importer_selskap(con, slug: str, grense_dato: str, forsinkelse: float, frist: datetime) -> dict:
    rad = _fremdrift(con, slug)
    if rad["ferdig"]:
        return {"sider": 0, "nye": 0}

    side = rad["siste_side"]
    nye_totalt = 0
    sider_denne_kjoringen = 0

    while sider_denne_kjoringen < MAKS_SIDER_PER_SELSKAP_PER_KJORING:
        if datetime.now(TZ) >= frist:
            break
        side += 1
        url = MFN_ARKIV_URL_MAL.format(slug=slug, side=side)
        try:
            meldinger = mfn.hent(url)
        except Exception as e:
            print(f"  {slug} side {side}: FEIL {type(e).__name__}: {e}", file=sys.stderr)
            break

        sider_denne_kjoringen += 1
        na = datetime.now(TZ).isoformat(timespec="seconds")

        if not meldinger:
            con.execute(
                "UPDATE arkiv_fremdrift SET siste_side=?, ferdig=1, sist_forsokt=? WHERE slug=?",
                (side, na, slug),
            )
            con.commit()
            break

        nye_totalt += lagre_meldinger(con, meldinger)
        eldste = min(m.publisert for m in meldinger)
        ferdig = 1 if eldste < grense_dato else 0
        con.execute(
            "UPDATE arkiv_fremdrift SET siste_side=?, ferdig=?, sist_forsokt=? WHERE slug=?",
            (side, ferdig, na, slug),
        )
        con.commit()  # avbrytes jobben nå, er denne siden likevel trygt lagret

        if ferdig:
            break
        time.sleep(forsinkelse)

    return {"sider": sider_denne_kjoringen, "nye": nye_totalt}


def kjor(
    ar_tilbake: int = 4,
    maks_kall: int = KLASSIFISERING_MAKS_KALL,
    forsinkelse: float = ARKIV_FORSINKELSE_SEK,
    maks_minutter: float = 45,
) -> dict:
    """maks_minutter er et tidsbudsjett for selve arkivimporten (ikke
    klassifiseringen etterpå, den er allerede begrenset av maks_kall). Uten
    dette ville jobben bli drept av GitHub Actions sin egen tidsgrense midt i
    en side, og workflow-steget som committer databasen ville aldri kjøre —
    da hjelper ikke fortløpende commits til SQLite-filen i det hele tatt."""
    grense_dato = (datetime.now(TZ) - timedelta(days=365 * ar_tilbake)).isoformat(timespec="seconds")
    frist = datetime.now(TZ) + timedelta(minutes=maks_minutter)

    con = db.connect()
    try:
        con.executescript(db.SCHEMA)
        slugs = _selskaper(con)
        con.commit()

        sider_totalt = 0
        nye_totalt = 0
        for slug in slugs:
            if datetime.now(TZ) >= frist:
                print("Tidsbudsjett brukt opp for denne kjøringen, fortsetter neste gang.")
                break
            resultat = _importer_selskap(con, slug, grense_dato, forsinkelse, frist)
            sider_totalt += resultat["sider"]
            nye_totalt += resultat["nye"]
            if resultat["sider"]:
                print(f"{slug}: {resultat['sider']} sider, {resultat['nye']} nye")

        dedup_resultat = bygg_hendelser(con)
        con.commit()
        klass_resultat = kjor_klassifisering.kjor(con, maks_kall=maks_kall, frisk_timer=None)

        detalj = (
            f"arkiv: {sider_totalt} sider, {nye_totalt} nye nyheter. "
            f"dedup: {dedup_resultat['nye_hendelser']} nye hendelser, "
            f"{dedup_resultat['sammenslatt_i_eksisterende']} slått sammen. "
            f"klassifisering: {klass_resultat['llm_kall']} LLM-kall "
            f"(~{klass_resultat['tokens_estimat']} tokens), "
            f"{klass_resultat['stanset_av_budsjett']} stanset av budsjett"
        )
        db.logg(con, "backfill_arkiv", "ok", detalj)
        con.commit()
    finally:
        con.close()

    return {"sider": sider_totalt, "nye": nye_totalt, **dedup_resultat, **klass_resultat}


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument(
        "--ar-tilbake", type=int, default=4,
        help="hvor mange år tilbake arkivet skal dekke (3-5 anbefalt)",
    )
    p.add_argument(
        "--maks-kall-grense", type=int, default=KLASSIFISERING_MAKS_KALL,
        help="maks antall LLM-kall i denne kjøringen, så en tabbe ikke koster hundrevis av kroner",
    )
    p.add_argument(
        "--forsinkelse", type=float, default=ARKIV_FORSINKELSE_SEK,
        help="sekunder mellom hver forespørsel til MFN",
    )
    p.add_argument(
        "--maks-minutter", type=float, default=45,
        help="tidsbudsjett for arkivimporten, så jobben stopper seg selv i tide "
        "til at workflowen rekker å committe databasen før Actions dreper den",
    )
    a = p.parse_args()
    try:
        resultat = kjor(
            ar_tilbake=a.ar_tilbake, maks_kall=a.maks_kall_grense,
            forsinkelse=a.forsinkelse, maks_minutter=a.maks_minutter,
        )
    except Exception as e:  # cronen skal ikke dø stille
        print(f"FEIL: {type(e).__name__}: {e}", file=sys.stderr)
        send(f"⚠️ backfill_arkiv feilet: {type(e).__name__}: {e}")
        return 1
    print(
        f"{resultat['sider']} sider, {resultat['nye']} nye nyheter, "
        f"{resultat['llm_kall']} LLM-kall."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
