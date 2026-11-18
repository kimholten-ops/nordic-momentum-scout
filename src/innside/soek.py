"""Fyller "nyheter" med innsidemeldinger via MFNs søk (?query=...&limit=...).

Hovedfeeden viser bare de siste ~50 meldingene, og poll_news går hvert 10.
minutt i børstid, så den fanger det som skjer fremover. Dette scriptet henter
bakover så langt søket rekker (testet 2026-11-18: ca. to måneder, ~200
treff per søkeord) — nok til å få i gang en første backtest uten å vente.
Kjøres manuelt; trygt å kjøre flere ganger (INSERT OR IGNORE på URL).
"""
from __future__ import annotations

import argparse
import time
from urllib.parse import quote

import requests

from src import db
from src.config import HTTP_TIMEOUT, MFN_FEED_URL, USER_AGENT
from src.poll_news import lagre_meldinger
from src.sources import mfn

SOKEORD = [
    "primary insider", "mandatory notification", "managers' transactions", "PDMR",
    "persons discharging managerial", "notifiable trading", "meldepliktig handel",
    "insynshandel", "insiderhandel",
]
GRENSE = 500


def hent_sok(ord_: str, grense: int = GRENSE) -> list[mfn.Melding]:
    url = f"{MFN_FEED_URL}?query={quote(ord_)}&limit={grense}"
    r = requests.get(url, headers={"User-Agent": USER_AGENT, "Accept-Language": "nb,no,en;q=0.8"}, timeout=HTTP_TIMEOUT)
    r.raise_for_status()
    return mfn.parse(r.text)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--forsinkelse", type=float, default=2.0, help="sekunder mellom søk")
    a = p.parse_args()
    totalt = 0
    with db.session() as con:
        for ord_ in SOKEORD:
            ms = hent_sok(ord_)
            nye = lagre_meldinger(con, ms)
            con.commit()
            totalt += nye
            eldste = min((m.publisert for m in ms), default="-")
            print(f"{ord_!r}: {len(ms)} treff, {nye} nye, eldste {eldste}")
            time.sleep(a.forsinkelse)
        db.logg(con, "innside_soek", "ok", f"{totalt} nye")
    print(f"Totalt {totalt} nye meldinger.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
