"""Sammenligner lokale modeller på rapporttolkning (samme rapporter, samme prompt).

  python -m scripts.modelltest qwen2.5:7b qwen3:14b gemma3:12b

Testen venter til grafikkortet er ledig: så lenge ComfyUI (port 8188) har
jobber i kø, står den og venter, og den starter først når køen har vært tom i
10 minutter. Da blir ikke nattfabrikken forstyrret, og tidsmålingene blir
riktige. Bruk --nå (eller --na) for å starte med en gang: da tømmes
ComfyUI-køen, pågående bilde avbrytes og andre Ollama-modeller lastes ut,
slik at testen har grafikkortet for seg selv.

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


COMFY = "http://127.0.0.1:8188/queue"
ROLIG_MIN = 10


def _comfy_jobber() -> int:
    """Antall jobber som kjører eller venter i ComfyUI (0 hvis ComfyUI ikke kjører)."""
    import requests

    try:
        q = requests.get(COMFY, timeout=5).json()
    except Exception:  # noqa: BLE001 - ingen ComfyUI = ledig
        return 0
    return len(q.get("queue_running") or []) + len(q.get("queue_pending") or [])


def frigjor_gpu(beholdes: list[str]) -> None:
    """Tøm ComfyUI-køen, avbryt pågående jobb og last ut andre Ollama-modeller."""
    import requests

    from src.config import OLLAMA_URL

    try:
        requests.post(COMFY, json={"clear": True}, timeout=5)
        requests.post(COMFY.replace("/queue", "/interrupt"), timeout=5)
        requests.post(COMFY.replace("/queue", "/free"), json={"unload_models": True, "free_memory": True}, timeout=10)
        print("ComfyUI: køen er tømt og modellene lastet ut av grafikkortet.", flush=True)
    except Exception:  # noqa: BLE001 - ComfyUI kjører ikke
        pass
    try:
        for m in requests.get(f"{OLLAMA_URL}/api/ps", timeout=5).json().get("models") or []:
            if m["name"] not in beholdes:
                requests.post(f"{OLLAMA_URL}/api/generate", json={"model": m["name"], "keep_alive": 0}, timeout=30)
                print(f"Ollama: lastet ut {m['name']}.", flush=True)
    except Exception as e:  # noqa: BLE001
        print(f"Ollama: fikk ikke sjekket lastede modeller ({e}).", flush=True)


def vent_paa_ledig_gpu(rolig_min: int = ROLIG_MIN, sov=time.sleep, jobber=_comfy_jobber) -> None:
    rolig = 0
    meldt = False
    while rolig < rolig_min:
        n = jobber()
        if n:
            if not meldt or rolig:
                print(f"{time.strftime('%H:%M')} Grafikkortet er opptatt (ComfyUI har {n} jobb(er)). "
                      "Venter … (la vinduet stå åpent)", flush=True)
                meldt = True
            rolig = 0
        else:
            if rolig == 0 and meldt:
                print(f"{time.strftime('%H:%M')} ComfyUI er ledig. Starter når det har vært rolig i "
                      f"{rolig_min} minutter.", flush=True)
            rolig += 1
        if rolig < rolig_min:
            sov(60)


def main() -> int:
    args = [a for a in sys.argv[1:] if a not in ("--nå", "--na")]
    modeller = args or ["qwen2.5:7b", "qwen3:14b", "gemma3:12b"]
    if len(args) == len(sys.argv[1:]):
        vent_paa_ledig_gpu()
    else:
        frigjor_gpu(modeller)
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
