"""Fase 1-jobben: dedupliser nye nyheter til hendelser, kjør regelbasert
forfilter, og send det som gjenstår til Claude Haiku.

Kjøres etter src.poll_news i samme workflow. Ingen signaler, ingen
inn-/utpriser — bare klassifisering, slik briefen for fase 1 ber om.
"""
from __future__ import annotations

import argparse
import sys

from src import db
from src.config import KLASSIFISERING_MAKS_KALL, LLM_FRISK_TIMER
from src.klassifisering import kjor as kjor_klassifisering
from src.klassifisering.dedup import bygg_hendelser
from src.notify import send


def _varsle_api_stengt(con, feil: str) -> None:
    """Høyst én Telegram-melding per dag om at API-et ikke tar imot kall."""
    from datetime import datetime

    from src.config import TZ

    idag = datetime.now(TZ).date().isoformat()
    if con.execute("SELECT 1 FROM kjorelogg WHERE jobb = 'api_stengt_varsel' AND tidspunkt >= ?", (idag,)).fetchone():
        return
    send("⚠️ Claude-API-et tar ikke imot kall (tom kreditt eller ugyldig nøkkel). Ferske meldinger venter "
         "og tolkes når API-et er tilbake, eller av Ollama på PC-en. Detalj: " + feil[:150])
    db.logg(con, "api_stengt_varsel", "ok", feil[:300])


def kjor(maks_kall: int = KLASSIFISERING_MAKS_KALL, frisk_timer: float | None = LLM_FRISK_TIMER) -> dict:
    with db.session() as con:
        dedup_resultat = bygg_hendelser(con)
        klass_resultat = kjor_klassifisering.kjor(con, maks_kall=maks_kall, frisk_timer=frisk_timer)

        detalj = (
            f"dedup: {dedup_resultat['nye_hendelser']} nye hendelser, "
            f"{dedup_resultat['sammenslatt_i_eksisterende']} slått sammen. "
            f"klassifisering: {klass_resultat['hendelser_vurdert']} vurdert, "
            f"{klass_resultat['filtrert']} filtrert, "
            f"{klass_resultat['llm_kall']} LLM-kall "
            f"(~{klass_resultat['tokens_estimat']} tokens), "
            f"{klass_resultat['feilet']} feilet, "
            f"{klass_resultat['stanset_av_budsjett']} stanset av budsjett, "
            f"{klass_resultat['for_gamle']} eldre enn fristen (til Ollama)"
            + (f". API stengt: {klass_resultat['api_stengt'][:120]}" if klass_resultat["api_stengt"] else "")
        )
        db.logg(con, "klassifiser", "ok", detalj)
        if klass_resultat["api_stengt"]:
            _varsle_api_stengt(con, klass_resultat["api_stengt"])

    return {**dedup_resultat, **klass_resultat}


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument(
        "--maks-kall", type=int, default=KLASSIFISERING_MAKS_KALL,
        help="maks antall LLM-kall i denne kjøringen",
    )
    p.add_argument("--alle", action="store_true", help="send også gammelt etterslep til LLM (koster)")
    a = p.parse_args()
    try:
        resultat = kjor(maks_kall=a.maks_kall, frisk_timer=None if a.alle else LLM_FRISK_TIMER)
    except Exception as e:  # cronen skal ikke dø stille
        print(f"FEIL: {type(e).__name__}: {e}", file=sys.stderr)
        send(f"⚠️ klassifiser feilet: {type(e).__name__}: {e}")
        return 1
    print(
        f"{resultat['llm_kall']} LLM-kall, {resultat['filtrert']} filtrert, "
        f"{resultat['feilet']} feilet, {resultat['for_gamle']} eldre enn fristen (til Ollama)."
        + (f" API stengt: {resultat['api_stengt'][:160]}" if resultat["api_stengt"] else "")
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
