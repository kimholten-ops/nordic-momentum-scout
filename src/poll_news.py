"""Fase 0-jobben: hent nyheter, lagre, ikke gjør noe annet.

Med vilje analysefri. I fire til seks uker skal dette bare bygge datasettet
du senere trenger for å teste om ideen holder vann.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime

from src import db
from src.config import TZ
from src.notify import send
from src.sources import mfn
from src.universe import last_universe


def kjor(dry_run: bool = False, feed: str | None = None) -> int:
    meldinger = mfn.hent(feed)
    if not meldinger:
        print("ADVARSEL: null meldinger parset. Feeden eller markupen kan ha endret seg.")
        return 0

    with db.session() as con:
        last_universe(con)
        kart = {
            r["slug"]: (r["ticker"], r["yahoo"], r["navn"])
            for r in con.execute("SELECT slug, ticker, yahoo, navn FROM selskaper")
        }
        na = datetime.now(TZ).isoformat(timespec="seconds")
        nye = 0

        for m in meldinger:
            ticker, yahoo, navn = kart.get(m.selskap_slug, (None, None, None))
            if dry_run:
                print(f"{m.publisert}  {m.selskap_slug:<22} {ticker or '-':<8} {m.tittel[:60]}")
                continue
            cur = con.execute(
                "INSERT OR IGNORE INTO nyheter "
                "(url, publisert, hentet, selskap_slug, selskap_navn, tittel, "
                " sprak, distributor, ticker, yahoo) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    m.url, m.publisert, na, m.selskap_slug,
                    m.selskap_navn or navn, m.tittel, m.sprak,
                    m.distributor, ticker, yahoo,
                ),
            )
            if cur.rowcount:
                nye += 1
                if ticker is None:
                    con.execute(
                        "INSERT INTO umappede (slug, navn, antall, sist_sett) VALUES (?,?,1,?) "
                        "ON CONFLICT(slug) DO UPDATE SET antall=antall+1, sist_sett=excluded.sist_sett",
                        (m.selskap_slug, m.selskap_navn, na),
                    )

        if not dry_run:
            db.logg(con, "poll_news", "ok", f"{len(meldinger)} sett, {nye} nye")
    return nye


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true", help="skriv ut, ikke lagre")
    p.add_argument("--feed", help="overstyr feed-URL")
    a = p.parse_args()
    try:
        nye = kjor(dry_run=a.dry_run, feed=a.feed)
    except Exception as e:  # cronen skal ikke dø stille
        print(f"FEIL: {type(e).__name__}: {e}", file=sys.stderr)
        if not a.dry_run:
            send(f"⚠️ poll_news feilet: {type(e).__name__}: {e}")
        return 1
    print(f"{nye} nye meldinger lagret.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
