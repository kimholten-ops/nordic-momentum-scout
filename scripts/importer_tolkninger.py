"""Legger tolkninger fra data/ollama/tolkninger.jsonl inn i databasen.

Kjøres av workflowen «Importer Ollama-tolkninger» (alt) og av hver nyhetsrunde
med --siden-timer 24 (bare det vakten på PC-en har tolket nylig). Idempotent:
meldinger som allerede er ferdig behandlet, røres ikke. Selskaper som får sitt
første innsidekjøp her, legges i universet med tickeren MFN oppga.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta

from src import db
from src.config import ROOT, TZ
from src.innside.arkiv import utvid_univers
from src.innside.deteksjon import er_korreksjon
from src.innside.kjor import EKSTRAKSJON_VERSJON, _lagre_handler, _marker
from src.innside.parser import INSTRUMENTER, RETNINGER, Handel
from src.marked import suffiks
from src.universe import last_universe

FIL = ROOT / "data" / "ollama" / "tolkninger.jsonl"
FIL_KL = ROOT / "data" / "ollama" / "klassifiseringer.jsonl"
FIL_RAP = ROOT / "data" / "ollama" / "rapporter.jsonl"
FIL_MED = ROOT / "data" / "ollama" / "medier.jsonl"
GREN_FILER = {FIL: "data/ollama/tolkninger.jsonl", FIL_KL: "data/ollama/klassifiseringer.jsonl",
              FIL_RAP: "data/ollama/rapporter.jsonl", FIL_MED: "data/ollama/medier.jsonl"}
BORS = {".OL": "Oslo Børs", ".ST": "Stockholm", ".HE": "Helsinki", ".CO": "København"}
SIDEN: str | None = None  # bare rader tolket etter dette (lokal tid, ISO), se --siden-timer


def _linjer(fil) -> list[str]:
    ut = []
    for linje in fil.read_text(encoding="utf-8").splitlines():
        if not linje.strip():
            continue
        if SIDEN:
            try:
                if (json.loads(linje).get("tid") or "") < SIDEN:
                    continue
            except ValueError:
                continue
        ut.append(linje)
    return ut


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
        for linje in _linjer(fil):
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


def kjor_klassifiseringer(fil=FIL_KL) -> dict:
    """Børsmeldinger klassifisert av Ollama på PC-en -> klassifiseringer.
    Erstatter bare manglende eller feilede klassifiseringer."""
    from src.klassifisering.kategorier import PROMPT_VERSJON
    from src.klassifisering.llm import _valider

    stat = {"lest": 0, "lagt_inn": 0, "fantes": 0, "feil": 0, "ukjent_url": 0}
    if not fil.exists():
        return stat
    na = datetime.now(TZ).isoformat(timespec="seconds")
    with db.session() as con:
        for linje in _linjer(fil):
            stat["lest"] += 1
            rad = json.loads(linje)
            h = con.execute(
                "SELECT h.id FROM hendelser h JOIN nyheter n ON n.id = h.kanonisk_nyhet_id WHERE n.url = ?", (rad["url"],)
            ).fetchone()
            if not h:
                stat["ukjent_url"] += 1
                continue
            finnes = con.execute(
                "SELECT status FROM klassifiseringer WHERE hendelse_id = ? AND prompt_versjon = ?", (h[0], PROMPT_VERSJON)
            ).fetchone()
            if finnes and finnes[0] != "feilet":
                stat["fantes"] += 1
                continue
            try:
                r = _valider(rad["resultat"])
            except (KeyError, TypeError, ValueError):
                stat["feil"] += 1
                continue
            con.execute("DELETE FROM klassifiseringer WHERE hendelse_id = ? AND prompt_versjon = ?", (h[0], PROMPT_VERSJON))
            con.execute(
                "INSERT INTO klassifiseringer (hendelse_id, prompt_versjon, modell, status, kategori, retning, "
                " overraskelse, sikkerhet, begrunnelse, tallverdi, valuta, klassifisert_tidspunkt) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (h[0], PROMPT_VERSJON, f"ollama:{rad.get('modell')}", "ok", r["kategori"], r["retning"],
                 r["overraskelse"], r["sikkerhet"], r["begrunnelse"], r["tallverdi"], r["valuta"], na),
            )
            stat["lagt_inn"] += 1
        con.commit()
        db.logg(con, "importer_klassifiseringer", "ok", ", ".join(f"{k} {v}" for k, v in stat.items()))
    return stat


def kjor_rapporter(fil=FIL_RAP) -> dict:
    """Nøkkeltall fra rapporter (Ollama på PC-en) -> rapporttall."""
    from src import rapport

    stat = {"lest": 0, "lagt_inn": 0, "fantes": 0, "feil": 0}
    if not fil.exists():
        return stat
    na = datetime.now(TZ).isoformat(timespec="seconds")
    with db.session() as con:
        # Rader fra eldre promptversjoner fjernes; de tolkes på nytt.
        con.execute("DELETE FROM rapporttall WHERE modell NOT LIKE ?", (f"%|{rapport.VERSJON}",))
        for linje in _linjer(fil):
            stat["lest"] += 1
            rad = json.loads(linje)
            if "resultat" not in rad or rad.get("versjon") != rapport.VERSJON:
                stat["feil"] += 1  # feilet, eller tolket med en eldre prompt (tolkes på nytt)
                continue
            try:
                r = rapport.valider(rad["resultat"])
            except (KeyError, TypeError, ValueError):
                stat["feil"] += 1
                continue
            n = con.execute(
                "INSERT OR REPLACE INTO rapporttall (group_id, slug, publisert, periode, valuta, omsetning, omsetning_fjor, "
                " driftsresultat, driftsresultat_fjor, eps, eps_fjor, omsetningsvekst_pst, utsikter, tone, modell, tolket) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (rad["group_id"], rad["slug"], rad["publisert"], r["periode"], r["valuta"], r["omsetning"],
                 r["omsetning_fjor"], r["driftsresultat"], r["driftsresultat_fjor"], r["eps"], r["eps_fjor"],
                 r["omsetningsvekst_pst"], r["utsikter"], r["tone"], f"ollama:{rad.get('modell')}|{rapport.VERSJON}", na),
            ).rowcount
            stat["lagt_inn"] += 1
        con.commit()
        db.logg(con, "importer_rapporter", "ok", ", ".join(f"{k} {v}" for k, v in stat.items()))
    return stat


def hent_fra_gren() -> None:
    """I GitHub Actions: hent filen fra grenen PC-en pusher til."""
    import subprocess

    r = subprocess.run(["git", "fetch", "-q", "origin", "+refs/heads/ollama-tolkninger:refs/remotes/origin/ollama-tolkninger"],
                       cwd=ROOT, capture_output=True, text=True)
    if r.returncode:
        print("Grenen ollama-tolkninger finnes ikke (ennå).")
        return
    for lokal, i_gren in GREN_FILER.items():
        innhold = subprocess.run(["git", "show", f"origin/ollama-tolkninger:{i_gren}"],
                                 cwd=ROOT, capture_output=True, text=True, encoding="utf-8").stdout
        lokal.parent.mkdir(parents=True, exist_ok=True)
        lokal.write_text(innhold, encoding="utf-8")
        print(f"Hentet {len(innhold.splitlines())} linjer fra {i_gren}.")


def kjor_medier(fil=FIL_MED) -> dict:
    if not fil.exists():
        return {"lest": 0, "lagt_inn": 0}
    from src import medietone

    rader = []
    for linje in _linjer(fil):
        try:
            rader.append(json.loads(linje))
        except ValueError:
            continue
    with db.session() as con:
        return medietone.importer(con, rader)


if __name__ == "__main__":
    if "--siden-timer" in sys.argv:
        timer = float(sys.argv[sys.argv.index("--siden-timer") + 1])
        SIDEN = (datetime.now(TZ) - timedelta(hours=timer)).replace(tzinfo=None).isoformat(timespec="seconds")
    if "--fra-gren" in sys.argv:
        hent_fra_gren()
    print("Innsidehandler:", kjor())
    print("Klassifiseringer:", kjor_klassifiseringer())
    print("Rapporttall:", kjor_rapporter())
    print("Medietone:", kjor_medier())
    sys.exit(0)
