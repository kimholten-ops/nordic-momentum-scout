"""Sammenligner lokale modeller på rapporttolkning (samme rapporter, samme prompt).

  python -m scripts.modelltest qwen2.5:7b qwen3:14b gemma3:12b

Rapportene står i scripts/modelltest_utvalg.json (valgt tilfeldig, ulike
børser og språk). Svarene og tiden per rapport skrives til
data/ollama/modelltest.jsonl og pushes til grenen «ollama-tolkninger», så de
kan sammenlignes med fasit uten at du gjør noe mer.
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src import rapport  # noqa: E402
from src.sources import mfn  # noqa: E402

UTVALG = ROOT / "scripts" / "modelltest_utvalg.json"
UT = ROOT / "data" / "ollama" / "modelltest.jsonl"
TEKST = ROOT / "data" / "ollama" / "modelltest_tekster.json"


def main() -> int:
    modeller = sys.argv[1:] or ["qwen2.5:7b", "qwen3:14b", "gemma3:12b"]
    saker = json.loads(UTVALG.read_text(encoding="utf-8"))
    tekster = json.loads(TEKST.read_text(encoding="utf-8")) if TEKST.exists() else {}
    for s in saker:
        if s["group_id"] not in tekster:
            tekster[s["group_id"]] = mfn.hent_brodtekst(s["url"]) or ""
            time.sleep(0.5)
    TEKST.write_text(json.dumps(tekster, ensure_ascii=False), encoding="utf-8")
    gjort = set()
    if UT.exists():
        for l in UT.read_text(encoding="utf-8").splitlines():
            r = json.loads(l)
            gjort.add((r["modell"], r["group_id"]))
    with UT.open("a", encoding="utf-8") as f:
        for m in modeller:
            print(f"\n== {m}", flush=True)
            subprocess.run(["ollama", "pull", m], check=False)
            for k, s in enumerate(saker, 1):
                if (m, s["group_id"]) in gjort:
                    continue
                t0 = time.time()
                rad = {"modell": m, "group_id": s["group_id"]}
                try:
                    rad["resultat"] = rapport.tolk(s["tittel"], tekster[s["group_id"]], modell=m)
                except Exception as e:  # noqa: BLE001
                    rad["feil"] = f"{type(e).__name__}: {e}"[:300]
                rad["sek"] = round(time.time() - t0, 1)
                f.write(json.dumps(rad, ensure_ascii=False) + "\n")
                f.flush()
                print(f"[{k}/{len(saker)}] {s['slug'][:28]:<28} {rad['sek']:>5} s  "
                      f"{'FEIL' if 'feil' in rad else rad['resultat'].get('periode')}", flush=True)
    # Push resultatet sammen med tolkningene (samme gren, samme mekanisme).
    from scripts import ollama_tolk

    ollama_tolk.push(0)
    print("\nFerdig. Resultatene er sendt til GitHub.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
