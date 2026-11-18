"""Innsidejobben: trekk ut handler fra nye nyheter, lag signaler, varsle.

Kjøres etter src.poll_news (og src.klassifiser) i samme workflow. Varsling
til Telegram er av med mindre INNSIDE_VARSLE=1 — i papirhandelsfasen skal
signalene bare logges og måles.
"""
from __future__ import annotations

import argparse
import sys

from src import db
from src.config import INNSIDE_MAKS_LLM_KALL, INNSIDE_VARSLE
from src.innside import kjor as ekstraksjon
from src.innside import signal
from src.notify import send


def kjor(maks_llm_kall: int = INNSIDE_MAKS_LLM_KALL, varsle: bool = INNSIDE_VARSLE) -> dict:
    with db.session() as con:
        e = ekstraksjon.kjor(con, maks_llm_kall=maks_llm_kall)
        nye = signal.bygg_signaler(con)
        varslet = 0
        if varsle:
            for s in signal.ikke_varslede(con):
                if send(signal.formater_varsel(con, s)):
                    con.execute("UPDATE innside_signaler SET varslet = 1 WHERE id = ?", (s["id"],))
                    varslet += 1
            con.commit()
        db.logg(
            con, "innside", "ok",
            f"vurdert {e['vurdert']}, regel {e['regel']}, llm {e['llm']}, trenger_llm {e['trenger_llm']}, "
            f"feilet {e['feilet']}, nye signaler {nye}, varslet {varslet}",
        )
    return {**e, "nye_signaler": nye, "varslet": varslet}


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--maks-llm-kall", type=int, default=INNSIDE_MAKS_LLM_KALL)
    p.add_argument("--varsle", action="store_true", help="send Telegram (overstyrer INNSIDE_VARSLE=0)")
    a = p.parse_args()
    try:
        r = kjor(maks_llm_kall=a.maks_llm_kall, varsle=a.varsle or INNSIDE_VARSLE)
    except Exception as e:  # cronen skal ikke dø stille
        print(f"FEIL: {type(e).__name__}: {e}", file=sys.stderr)
        send(f"⚠️ innside feilet: {type(e).__name__}: {e}")
        return 1
    print(
        f"{r['vurdert']} meldinger vurdert ({r['regel']} regel, {r['llm']} LLM, "
        f"{r['trenger_llm']} venter på LLM), {r['nye_signaler']} nye signaler, {r['varslet']} varslet."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
