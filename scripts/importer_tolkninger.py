"""Legger tolkninger fra data/ollama/tolkninger.jsonl inn i databasen.

Kjøres av workflowen «Importer Ollama-tolkninger» når filen pushes. Idempotent:
meldinger som allerede er ferdig behandlet, røres ikke. Selskaper som får sitt
første innsidekjøp her, legges i universet med tickeren MFN oppga.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime

from src import db
from src.config import ROOT, TZ
from src.innside.arkiv import utvid_univers
from src.innside.deteksjon import er_korreksjon
from src.innside.kjor import EKSTRAKSJON_VERSJON, _lagre_handler, _marker
from src.innside.parser import INSTRUMENTER, RETNINGER, Handel
from src.marked import suffiks
from src.universe import last_universe

FIL = ROOT / "data" / "ollama" / "tolkninger.jsonl"
BORS = {".OL": "Oslo Børs", ".ST": "Stockholm", ".HE": "Helsinki", ".CO": "København"}


def _handel(d: dict) -> Handel | None:
    if d.get("retning") not in RETNINGER or d.get("instrument") not in INSTRUMENTER:
        return None
    felt = {k: d.get(k) for k in Handel.__dataclass_fields__}
    return Handel(**felt)


def kjor(fil=FIL) -> dict:
    stat = {"lest": 0, "lagt_inn": 0, "ingen_handler": 0, "feil": 0, "fantes": 0, "ukjent_url": 0, "nye_selskaper": 0}
    if not fil.exists():
        return stat
    na = datetime.now(TZ).isoformat(timespec="seconds")
    nye_selskaper: dict[str, dict] = {}
    with db.session() as con:
        last_universe(con)
        for linje in fil.read_text(encoding="utf-8").splitlines():
            if not linje.strip():
                continue
            stat["lest"] += 1
            rad = json.loads(linje)
            n = con.execute(
                "SELECT id, url, publisert, selskap_slug, selskap_navn, tittel, ticker, yahoo FROM nyheter WHERE url = ?",
                (rad["url"],),
            ).fetchone()
            if not n:
                stat["ukjent_url"] += 1
                continue
            status = con.execute(
                "SELECT status FROM innside_behandlet WHERE nyhet_id = ? AND versjon = ?", (n["id"], EKSTRAKSJON_VERSJON)
            ).fetchone()
            if status and status[0] in ("ok", "ingen_handler"):
                stat["fantes"] += 1
                continue
            if "feil" in rad:
                stat["feil"] += 1  # blir stående som «trenger_llm»/«feilet» og kan prøves igjen
                continue
            handler = [h for h in (_handel(d) for d in rad.get("handler", [])) if h]
            if handler:
                _lagre_handler(con, n, handler, "ollama", er_korreksjon(n["tittel"]))
                _marker(con, n["id"], "ok", f"ollama ({rad.get('modell')}): {len(handler)} handler", na)
                stat["lagt_inn"] += 1
                if n["yahoo"] and any(h.retning == "kjop" and h.instrument == "aksje" for h in handler):
                    nye_selskaper.setdefault(n["selskap_slug"], {
                        "slug": n["selskap_slug"], "navn": (n["selskap_navn"] or n["selskap_slug"]).replace(",", " "),
                        "ticker": n["ticker"], "yahoo": n["yahoo"], "marked": BORS.get(suffiks(n["yahoo"]) or "", ""),
                        "aktiv": 1,
                    })
            else:
                con.execute("DELETE FROM innsidehandler WHERE nyhet_id = ?", (n["id"],))
                _marker(con, n["id"], "ingen_handler", f"ollama ({rad.get('modell')})", na)
                stat["ingen_handler"] += 1
        con.commit()
        stat["nye_selskaper"] = utvid_univers(con, nye_selskaper)
        db.logg(con, "importer_tolkninger", "ok", ", ".join(f"{k} {v}" for k, v in stat.items()))
    return stat


if __name__ == "__main__":
    print(kjor())
    sys.exit(0)
