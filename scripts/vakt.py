"""Vakt: tolker ferske børsmeldinger med Ollama på PC-en gjennom børsdagen.

Startes av Oppgaveplanlegging i Windows (scripts/installer_vakt.ps1) på
hverdager kl. 07:55 og når du logger inn. Hvert annet minutt:

1. Henter arbeidskøen GitHub lager etter hver nyhetsrunde (scripts/arbeidsko.py)
   — en liten fil, ikke hele databasen.
2. Tolker det som ikke er gjort: innsidemeldinger først, så børsmeldinger,
   så nye kvartalsrapporter.
   Til slutt tonen i medieomtaler (src/medietone.py).
3. Pusher svarene til grenen «ollama-tolkninger». Neste nyhetsrunde i GitHub
   (hvert 10. minutt) legger dem inn, kjører hjernen og sender varsler.

Avslutter av seg selv etter VAKT_SLUTT. Kjører bare én vakt om gangen.

  python -m scripts.vakt            # vanlig vakt
  python -m scripts.vakt --en-gang  # én runde (test)
"""
from __future__ import annotations

import faulthandler
import json
import socket
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts import ollama_tolk as ot  # noqa: E402  (setter INNSIDE_LLM/KLASSIFISERING_LLM=ollama)
from src.config import OLLAMA_MODELL, OLLAMA_URL, TZ  # noqa: E402

KO_URL = "https://github.com/kimholten-ops/nordic-momentum-scout/releases/download/data-siste/arbeidsko.json"
VAKT_START, VAKT_SLUTT = "07:45", "17:45"
PAUSE_SEK = 120
LAS_PORT = 47613  # én vakt om gangen
LOGG = ROOT / "data" / "vakt.log"
STATUS = ROOT / "data" / "vakt_status.json"
HENG_SEK = 900  # skriv stakkspor til loggen hvis én runde henger lenger enn dette


def logg(tekst: str) -> None:
    """Til vinduet og til data/vakt.log (så vi kan se hva vakten gjorde)."""
    print(tekst, flush=True)
    try:
        with LOGG.open("a", encoding="utf-8") as f:
            f.write(f"{datetime.now(TZ).strftime('%Y-%m-%d')} {tekst}\n")
    except OSError:
        pass


def status(**felt) -> None:
    try:
        STATUS.write_text(json.dumps({"tid": datetime.now(TZ).isoformat(timespec="seconds"), **felt},
                                     ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def _tid() -> str:
    return datetime.now(TZ).strftime("%H:%M")


def i_vakttid(na: datetime | None = None) -> bool:
    na = na or datetime.now(TZ)
    return na.weekday() < 5 and VAKT_START <= na.strftime("%H:%M") < VAKT_SLUTT


def hent_ko() -> dict | None:
    try:
        r = requests.get(KO_URL, timeout=60, headers={"Cache-Control": "no-cache"})
        r.raise_for_status()
        return r.json()
    except Exception as e:  # noqa: BLE001
        logg(f"{_tid()} Fikk ikke hentet arbeidskøen: {e}")
        return None


def ollama_klar() -> bool:
    try:
        requests.get(f"{OLLAMA_URL}/api/tags", timeout=5).raise_for_status()
        return True
    except Exception:  # noqa: BLE001
        pass
    logg(f"{_tid()} Ollama svarer ikke — starter «ollama serve».")
    try:
        flagg = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        subprocess.Popen(["ollama", "serve"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flagg)
    except OSError as e:
        logg(f"{_tid()} Fant ikke ollama: {e}")
        return False
    for _ in range(30):
        time.sleep(2)
        try:
            requests.get(f"{OLLAMA_URL}/api/tags", timeout=5).raise_for_status()
            return True
        except Exception:  # noqa: BLE001
            continue
    return False


KOER = [  # (navn i arbeidskøen, fil, nøkkel, tolkefunksjon, kort tekst)
    ("innside", ot.UT, "url", ot.tolk_innside_rad, "innsidemelding"),
    ("nyheter", ot.UT_KL, "url", ot.tolk_nyhet_rad, "børsmelding"),
    ("rapporter", ot.UT_RAP, "group_id", ot.tolk_rapport_rad, "rapport"),
    ("medier", ot.UT_MED, "nokkel", ot.tolk_medie_rad, "medieomtale"),
]


def _oppsummer(navn: str, rad: dict) -> str:
    if "feil" in rad:
        return "FEIL " + rad["feil"][:80]
    if navn == "innside":
        h = rad.get("handler") or []
        return f"{len(h)} handler" + (" (kjøp)" if any(x.get("retning") == "kjop" for x in h) else "")
    res = rad.get("resultat") or {}
    if navn == "nyheter":
        return f"{res.get('kategori')}/{res.get('retning')}"
    if navn == "medier":
        return ("ikke relevant" if not res.get("relevant") else
                {1: "positiv", -1: "negativ"}.get(res.get("tone"), "nøytral"))
    return f"{res.get('periode') or '?'}, utsikter {res.get('utsikter', '?')}"


def runde(ko: dict) -> int:
    """Tolker det som ikke er gjort. Returnerer antall nye svar."""
    from src import rapport

    nye = 0
    for navn, fil, nokkel, tolk, tekst in KOER:
        gjort = ot.ferdige(fil, nokkel, versjon=rapport.VERSJON if navn == "rapporter" else None)
        for r in ko.get(navn) or []:
            if r[nokkel] in gjort:
                continue
            t0 = time.time()
            status(steg=f"tolker {navn}", nokkel=r[nokkel])
            rad = tolk(r)
            with fil.open("a", encoding="utf-8") as f:
                f.write(json.dumps(rad, ensure_ascii=False) + "\n")
            gjort.add(r[nokkel])
            nye += 1
            hvem = r.get("selskap_slug") or r.get("slug") or ""
            logg(f"{_tid()} {tekst} {hvem[:28]:<28} {_oppsummer(navn, rad)}  ({time.time() - t0:.0f} s)")
    return nye


def main() -> int:
    en_gang = "--en-gang" in sys.argv
    las = socket.socket()
    try:
        las.bind(("127.0.0.1", LAS_PORT))
    except OSError:
        print("En vakt kjører allerede. Lukker dette vinduet.")
        time.sleep(5)
        return 0
    if not en_gang and not i_vakttid():
        print(f"Utenfor vakttid (hverdager {VAKT_START}–{VAKT_SLUTT}). Avslutter.")
        time.sleep(5)
        return 0
    ot.UT.parent.mkdir(parents=True, exist_ok=True)
    logg(f"Vakt startet {_tid()} med {OLLAMA_MODELL}. Sjekker hvert {PAUSE_SEK // 60}. minutt til {VAKT_SLUTT}. "
         "La vinduet stå (det kan minimeres).")
    heng = LOGG.open("a", encoding="utf-8")
    sist_laget = None
    runder = 0
    while True:
        faulthandler.cancel_dump_traceback_later()
        faulthandler.dump_traceback_later(HENG_SEK, exit=False, file=heng)
        runder += 1
        status(steg="henter kø", runde=runder)
        ko = hent_ko()
        if ko and ko.get("laget") != sist_laget:
            if not ollama_klar():
                logg(f"{_tid()} Ollama er ikke tilgjengelig. Prøver igjen om litt.")
            else:
                try:
                    nye = runde(ko)
                    sist_laget = ko.get("laget")
                    if nye:
                        status(steg="pusher", nye=nye)
                        ot.push(nye)
                except ot.OllamaNede:
                    logg(f"{_tid()} Mistet kontakten med Ollama. Prøver igjen om litt.")
                except Exception as e:  # noqa: BLE001 — vakten skal ikke dø av én feil
                    logg(f"{_tid()} Feil i runden: {type(e).__name__}: {e}")
        if runder % 15 == 0:
            logg(f"{_tid()} Vakten går (runde {runder}).")
        status(steg="venter", runde=runder, laget=(ko or {}).get("laget"))
        if en_gang or not i_vakttid():
            break
        time.sleep(PAUSE_SEK)
    faulthandler.cancel_dump_traceback_later()
    logg(f"Vakten er ferdig for i dag ({_tid()}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
