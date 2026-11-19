"""Tolker ventende innsidemeldinger med Ollama på egen PC — gratis, men tar tid.

  python -m scripts.ollama_tolk                 # alt som venter, push hver 50.
  python -m scripts.ollama_tolk --maks 200      # prøverunde

Skriver ett JSON-objekt per melding til data/ollama/tolkninger.jsonl og
pusher filen jevnlig til grenen «ollama-tolkninger» (git-plumbing: arbeids-
mappen og databasen røres aldri, så det blir ingen konflikter med GitHub-
jobbene som oppdaterer databasen). Workflowen «Importer Ollama-tolkninger»
henter filen derfra annenhver time og legger handlene inn.

Kan avbrytes (Ctrl+C) og startes igjen: meldinger som står i filen, hoppes over.
Rekkefølge: selskaper vi har kurser for først, nyeste først. Titler som
åpenbart er tildeling/spareprogram/emisjon hoppes over (gir aldri kjøpssignal),
og samme melding på flere språk tolkes én gang.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

os.environ.setdefault("INNSIDE_LLM", "ollama")

from src import db  # noqa: E402
from src.config import OLLAMA_MODELL, ROOT  # noqa: E402
from src.innside import llm  # noqa: E402
from src.klassifisering.llm import KlassifiseringsFeil  # noqa: E402
from src.sources import mfn  # noqa: E402

UT = ROOT / "data" / "ollama" / "tolkninger.jsonl"
STOY = re.compile(
    r"tildel|allocat|allot|grant|award|share\s*(?:saving|purchase|incentive|option)\s*program|aksjespar|aktiespar|"
    r"incentive|\bLTI\b|\bRSU|\bPSU|subscri|tegning|emisjon|private\s+placement|rights\s+issue|bonus|option|warrant|"
    r"buy-?back|tilbakekjøp|egne\s+aksjer|own\s+shares|lending|aksjelån|pledge|pant|savings|spareprogram|vesting|exercise",
    re.IGNORECASE,
)


def ko(con) -> list[dict]:
    rader = con.execute(
        "SELECT n.url, n.tittel, n.publisert, n.selskap_slug, n.sprak, n.yahoo, "
        "  EXISTS (SELECT 1 FROM kurser k WHERE k.yahoo = n.yahoo) AS har_kurs "
        "FROM innside_behandlet b JOIN nyheter n ON n.id = b.nyhet_id "
        "WHERE b.status IN ('trenger_llm', 'feilet')"
    ).fetchall()
    sett: dict[tuple, dict] = {}
    for r in rader:
        if STOY.search(r["tittel"] or ""):
            continue
        # Samme melding på flere språk: én per selskap og 10-minutters vindu, engelsk foretrukket.
        nokkel = (r["selskap_slug"], (r["publisert"] or "")[:15])
        if nokkel not in sett or (r["sprak"] == "en" and sett[nokkel]["sprak"] != "en"):
            sett[nokkel] = dict(r)
    return list(sett.values())


def ferdige() -> set[str]:
    if not UT.exists():
        return set()
    ut = set()
    for linje in UT.read_text(encoding="utf-8").splitlines():
        try:
            ut.add(json.loads(linje)["url"])
        except (ValueError, KeyError):
            continue
    return ut


GREN = "ollama-tolkninger"
FIL_I_GREN = "data/ollama/tolkninger.jsonl"


def git(*args: str, env: dict | None = None) -> str | None:
    r = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True,
                       env={**os.environ, **(env or {})})
    if r.returncode:
        print(f"  git {' '.join(args[:2])} feilet: {r.stderr.strip()[:200]}")
        return None
    return r.stdout.strip()


def push(antall: int) -> None:
    """Legger jsonl-filen i en commit på grenen «ollama-tolkninger» og pusher
    den — uten å røre arbeidsmappen, gjeldende gren eller databasen. Bare denne
    PC-en skriver til grenen, så det blir aldri konflikter."""
    blob = git("hash-object", "-w", str(UT))
    if not blob:
        return
    indeks = str(ROOT / ".git" / "ollama-index")
    env = {"GIT_INDEX_FILE": indeks}
    if git("read-tree", "--empty", env=env) is None:
        return
    if git("update-index", "--add", "--cacheinfo", f"100644,{blob},{FIL_I_GREN}", env=env) is None:
        return
    tre = git("write-tree", env=env)
    git("fetch", "-q", "origin", f"+refs/heads/{GREN}:refs/remotes/origin/{GREN}")  # finnes kanskje ikke ennå
    forelder = subprocess.run(["git", "rev-parse", "-q", "--verify", f"refs/remotes/origin/{GREN}"],
                              cwd=ROOT, capture_output=True, text=True).stdout.strip()
    args = ["commit-tree", tre, "-m", f"data: ollama-tolkninger ({antall} totalt)"] + (["-p", forelder] if forelder else [])
    commit = git(*args)
    if commit and git("push", "-q", "origin", f"{commit}:refs/heads/{GREN}") is not None:
        print(f"  ↑ pushet {antall} tolkninger til grenen {GREN}")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--maks", type=int, default=None)
    p.add_argument("--push-hver", type=int, default=50, help="0 = ikke push")
    a = p.parse_args()

    UT.parent.mkdir(parents=True, exist_ok=True)
    with db.session() as con:
        kø = ko(con)
    gjort = ferdige()
    kø = [r for r in kø if r["url"] not in gjort]
    # Har kurs først (de kan brukes med en gang), og innen hver gruppe nyeste først.
    kø = sorted([r for r in kø if r["har_kurs"]], key=lambda r: r["publisert"], reverse=True) + \
        sorted([r for r in kø if not r["har_kurs"]], key=lambda r: r["publisert"], reverse=True)
    if a.maks:
        kø = kø[: a.maks]
    print(f"Modell {OLLAMA_MODELL}. {len(kø)} meldinger i kø ({len(gjort)} allerede tolket).", flush=True)

    start, nye = time.time(), 0
    with UT.open("a", encoding="utf-8") as f:
        for k, r in enumerate(kø, 1):
            rad = {"url": r["url"], "modell": OLLAMA_MODELL, "tid": datetime.now().isoformat(timespec="seconds")}
            try:
                tekst = mfn.hent_brodtekst(r["url"])
                handler = llm.trekk_ut(r["tittel"], tekst or "", r["publisert"])
                rad["handler"] = [asdict(h) for h in handler]
            except KlassifiseringsFeil as e:
                rad["feil"] = str(e)[:300]
            except Exception as e:  # nett, MFN, Ollama nede: noter og fortsett
                rad["feil"] = f"{type(e).__name__}: {e}"[:300]
                if "Connection" in type(e).__name__ and "11434" in str(e):
                    print("Får ikke kontakt med Ollama på localhost:11434 — kjører «ollama serve»?")
                    return 1
            f.write(json.dumps(rad, ensure_ascii=False) + "\n")
            f.flush()
            nye += 1
            per = (time.time() - start) / k
            igjen = (len(kø) - k) * per / 3600
            kjop = sum(1 for h in rad.get("handler", []) if h["retning"] == "kjop")
            print(f"[{k}/{len(kø)}] {r['publisert'][:10]} {r['selskap_slug'][:28]:<28} "
                  f"{'FEIL' if 'feil' in rad else str(len(rad['handler'])) + ' handler'}{' (kjøp)' if kjop else ''}"
                  f"  ~{per:.0f}s/stk, {igjen:.1f} t igjen", flush=True)
            if a.push_hver and nye % a.push_hver == 0:
                f.flush()
                push(len(gjort) + nye)
    if a.push_hver and nye:
        push(len(gjort) + nye)
    print("Ferdig.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
