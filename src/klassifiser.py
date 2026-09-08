"""Fase 1-jobben: dedupliser nye nyheter til hendelser, kjør regelbasert
forfilter, og send det som gjenstår til Claude Haiku.

Kjøres etter src.poll_news i samme workflow. Ingen signaler, ingen
inn-/utpriser — bare klassifisering, slik briefen for fase 1 ber om.
"""
from __future__ import annotations

import argparse
import sys

from src import db
from src.config import KLASSIFISERING_MAKS_KALL
from src.klassifisering import kjor as kjor_klassifisering
from src.klassifisering.dedup import bygg_hendelser
from src.notify import send


def kjor(maks_kall: int = KLASSIFISERING_MAKS_KALL) -> dict:
    with db.session() as con:
        dedup_resultat = bygg_hendelser(con)
        klass_resultat = kjor_klassifisering.kjor(con, maks_kall=maks_kall)

        detalj = (
            f"dedup: {dedup_resultat['nye_hendelser']} nye hendelser, "
            f"{dedup_resultat['sammenslatt_i_eksisterende']} slått sammen. "
            f"klassifisering: {klass_resultat['hendelser_vurdert']} vurdert, "
            f"{klass_resultat['filtrert']} filtrert, "
            f"{klass_resultat['llm_kall']} LLM-kall "
            f"(~{klass_resultat['tokens_estimat']} tokens), "
            f"{klass_resultat['feilet']} feilet, "
            f"{klass_resultat['stanset_av_budsjett']} stanset av budsjett"
        )
        db.logg(con, "klassifiser", "ok", detalj)

    return {**dedup_resultat, **klass_resultat}


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument(
        "--maks-kall", type=int, default=KLASSIFISERING_MAKS_KALL,
        help="maks antall LLM-kall i denne kjøringen",
    )
    a = p.parse_args()
    try:
        resultat = kjor(maks_kall=a.maks_kall)
    except Exception as e:  # cronen skal ikke dø stille
        print(f"FEIL: {type(e).__name__}: {e}", file=sys.stderr)
        send(f"⚠️ klassifiser feilet: {type(e).__name__}: {e}")
        return 1
    print(
        f"{resultat['llm_kall']} LLM-kall, {resultat['filtrert']} filtrert, "
        f"{resultat['feilet']} feilet."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
