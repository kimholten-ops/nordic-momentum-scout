"""Oppdaterer kontekstdata til hjernen (se src/sources/kontekst.py).

  python -m src.kontekst_kjor                    # daglig: MFN inkrementelt + short + 150 Yahoo-oppslag
  python -m src.kontekst_kjor --mfn-fra 2022-06-01 --info-maks 0   # arkiv: alt, ingen grense
"""
from __future__ import annotations

import argparse

from src import db
from src.sources import euronext, kontekst, nasdaq
from src.universe import last_universe


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--mfn-fra", default=None, help="eldste dato (YYYY-MM-DD); standard: inkrementelt")
    p.add_argument("--info-maks", type=int, default=150, help="Yahoo-oppslag per kjøring (0 = ingen grense)")
    p.add_argument("--uten-info", action="store_true")
    a = p.parse_args()
    feil = []
    with db.session() as con:
        last_universe(con)
        for navn, jobb in [
            ("mfn", lambda: kontekst.hent_mfn(con, fra=a.mfn_fra)),
            ("short", lambda: kontekst.hent_short(con)),
            ("euronext", lambda: euronext.kjor(con)),  # etter mfn: trenger ISIN
            ("nasdaq", lambda: nasdaq.kjor(con)),  # hovedliste/First North (ASK)
            ("info", (lambda: {"hoppet over": True}) if a.uten_info else
             (lambda: kontekst.hent_info(con, maks=a.info_maks or None))),
        ]:
            try:
                res = jobb()
                print(f"{navn}: {res}")
                db.logg(con, f"kontekst-{navn}", "ok", str(res)[:300])
            except Exception as e:  # noqa: BLE001 — én kilde skal ikke stoppe de andre
                feil.append(navn)
                print(f"{navn}: FEIL {e}")
                db.logg(con, f"kontekst-{navn}", "feil", str(e)[:300])
        dekning = con.execute(
            "SELECT (SELECT COUNT(*) FROM selskapshendelser), (SELECT COUNT(DISTINCT slug) FROM selskapshendelser), "
            "(SELECT COUNT(*) FROM short), (SELECT COUNT(*) FROM selskapsinfo WHERE sektor IS NOT NULL OR aksjer IS NOT NULL)"
        ).fetchone()
        print(f"Dekning: {dekning[0]} selskapshendelser i {dekning[1]} selskaper, {dekning[2]} shortrader, "
              f"{dekning[3]} selskaper med Yahoo-info")
    return 1 if len(feil) == 5 else 0


if __name__ == "__main__":
    raise SystemExit(main())
