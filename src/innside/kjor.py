"""Trekker ut innsidehandler fra nyheter som ikke er behandlet ennå."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta

from src.config import INNSIDE_MAKS_LLM_KALL, LLM_FRISK_TIMER, TZ
from src.innside import llm
from src.innside.deteksjon import STOY, er_innsidemelding, er_korreksjon
from src.innside.fritekst import parse_fritekst
from src.innside.parser import Handel, parse_strukturert
from src.klassifisering.llm import KlassifiseringsFeil
from src.sources import mfn

# Bump når regler/prompt endres; ny versjon behandler alt på nytt og
# gamle rader blir stående for sammenligning.
EKSTRAKSJON_VERSJON = "e2"  # e2: beholdning_etter, større svarbudsjett

_FERDIG = ("ok", "ingen_handler")


def noekkel(slug: str, h: Handel) -> str:
    return "|".join(
        str(x) for x in (
            slug, (h.person or "").strip().lower(), h.transaksjonsdato or "",
            h.retning, h.instrument, h.antall, h.pris,
        )
    )


def _ubehandlede(con: sqlite3.Connection, med_venter: bool = True, venter_fra: str = "") -> list[sqlite3.Row]:
    """Innsidemeldinger som ikke er ferdig behandlet. Uten KI tilgjengelig tas
    de som venter på KI ikke med — ellers ville hver kjøring hentet tusenvis av
    arkivmeldinger fra MFN på nytt bare for å gi opp igjen."""
    ferdige = {
        r[0] for r in con.execute(
            f"SELECT nyhet_id FROM innside_behandlet WHERE versjon = ? AND status IN ({','.join('?' * len(_FERDIG))})",
            (EKSTRAKSJON_VERSJON, *_FERDIG),
        )
    }
    venter = {
        r[0] for r in con.execute(
            "SELECT nyhet_id FROM innside_behandlet WHERE versjon = ? AND status = 'trenger_llm'", (EKSTRAKSJON_VERSJON,)
        )
    }
    rader = con.execute(
        "SELECT id, url, publisert, selskap_slug, tittel, brodtekst FROM nyheter ORDER BY publisert, id"
    ).fetchall()
    # Ventende tas med bare når KI er tilgjengelig, og bare de ferske (eldre: Ollama på PC-en).
    return [r for r in rader if r["id"] not in ferdige and er_innsidemelding(r["tittel"])
            and (r["id"] not in venter or (med_venter and (r["publisert"] or "") >= venter_fra))]


def _marker(con, nyhet_id: int, status: str, detalj: str, na: str) -> None:
    con.execute(
        "INSERT INTO innside_behandlet (nyhet_id, versjon, status, detalj, tidspunkt) VALUES (?,?,?,?,?) "
        "ON CONFLICT(nyhet_id, versjon) DO UPDATE SET status=excluded.status, "
        "detalj=excluded.detalj, tidspunkt=excluded.tidspunkt",
        (nyhet_id, EKSTRAKSJON_VERSJON, status, detalj, na),
    )


def _lagre_handler(con, n, handler: list[Handel], kilde: str, korreksjon: bool) -> None:
    # Tabellen holder siste uttrekk per melding: eldre versjoner erstattes.
    con.execute("DELETE FROM innsidehandler WHERE nyhet_id = ?", (n["id"],))
    for i, h in enumerate(handler):
        con.execute(
            "INSERT INTO innsidehandler (nyhet_id, linje, versjon, kilde, selskap_slug, publisert, "
            " er_korreksjon, person, rolle_tekst, rolle, naerstaaende, retning, instrument, antall, "
            " pris, valuta, verdi, transaksjonsdato, noekkel, beholdning_etter) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                n["id"], i, EKSTRAKSJON_VERSJON, kilde, n["selskap_slug"], n["publisert"],
                int(korreksjon), h.person, h.rolle_tekst, h.rolle, int(h.naerstaaende),
                h.retning, h.instrument, h.antall, h.pris, h.valuta, h.verdi,
                h.transaksjonsdato, noekkel(n["selskap_slug"], h), h.beholdning_etter,
            ),
        )
    if korreksjon and handler:
        # En korreksjon erstatter tidligere meldinger fra samme selskap om
        # samme person og transaksjonsdato (eller bare dato om navn mangler).
        for h in handler:
            if h.person:
                con.execute(
                    "UPDATE innsidehandler SET gyldig = 0 WHERE selskap_slug = ? AND nyhet_id != ? "
                    "AND lower(person) = lower(?) AND IFNULL(transaksjonsdato,'') = ? AND er_korreksjon = 0",
                    (n["selskap_slug"], n["id"], h.person, h.transaksjonsdato or ""),
                )
            else:
                con.execute(
                    "UPDATE innsidehandler SET gyldig = 0 WHERE selskap_slug = ? AND nyhet_id != ? "
                    "AND IFNULL(transaksjonsdato,'') = ? AND er_korreksjon = 0",
                    (n["selskap_slug"], n["id"], h.transaksjonsdato or ""),
                )


def tolk_med_regler(tekst: str, publisert: str | None) -> tuple[list[Handel] | None, str]:
    """(handler, kilde) fra regelparserne, eller (None, '') når de gir opp."""
    handler = parse_strukturert(tekst)
    if handler:
        return handler, "regel"
    handler = parse_fritekst(tekst, publisert)
    if handler:
        return handler, "fritekst"
    return None, ""


def kjor(con: sqlite3.Connection, maks_llm_kall: int = INNSIDE_MAKS_LLM_KALL, hent_tekst=mfn.hent_brodtekst,
         frisk_timer: float | None = LLM_FRISK_TIMER) -> dict:
    """Regler først; LLM bare for meldinger yngre enn frisk_timer (None = alle). Eldre meldinger blir «trenger_llm» og tolkes av Ollama på PC-en."""
    na_dt = datetime.now(TZ)
    na = na_dt.isoformat(timespec="seconds")
    grense = (na_dt - timedelta(hours=frisk_timer)).replace(tzinfo=None).isoformat() if frisk_timer else ""
    res = {"vurdert": 0, "regel": 0, "llm": 0, "ingen_handler": 0, "trenger_llm": 0, "feilet": 0,
           "brodtekst_feilet": 0, "stoy": 0, "api_stengt": None}

    ki = llm.tilgjengelig() and maks_llm_kall > 0
    for n in _ubehandlede(con, med_venter=ki, venter_fra=grense):  # kronologisk: korreksjoner etter originalen
        res["vurdert"] += 1
        tekst = n["brodtekst"]
        if not tekst:
            try:
                tekst = hent_tekst(n["url"])
            except Exception:
                res["brodtekst_feilet"] += 1
                continue  # ikke marker noe: tas opp igjen neste kjøring
            if tekst:
                con.execute(
                    "UPDATE nyheter SET brodtekst = ? WHERE id = ? AND (brodtekst IS NULL OR brodtekst = '')",
                    (tekst, n["id"]),
                )

        korr = er_korreksjon(n["tittel"])
        handler, kilde = tolk_med_regler(tekst or "", n["publisert"])
        if handler is None and STOY.search(n["tittel"] or ""):
            # Tildeling, spareprogram, emisjon, opsjoner o.l.: gir aldri kjøpssignal.
            con.execute("DELETE FROM innsidehandler WHERE nyhet_id = ?", (n["id"],))
            _marker(con, n["id"], "ingen_handler", "støytittel (ikke tolket)", na)
            res["ingen_handler"] += 1
            res["stoy"] += 1
            con.commit()
            continue
        if handler is None:
            if res["llm"] >= maks_llm_kall or not ki or res["api_stengt"] or (n["publisert"] or "") < grense:
                _marker(con, n["id"], "trenger_llm", "venter på LLM (Ollama tar eldre)", na)
                res["trenger_llm"] += 1
                con.commit()
                continue
            res["llm"] += 1
            try:
                handler = llm.trekk_ut(n["tittel"], tekst or "", n["publisert"])
                kilde = "llm"
            except llm.APIStengt as e:
                res["api_stengt"] = str(e)[:200]
                res["llm"] -= 1
                _marker(con, n["id"], "trenger_llm", "API stengt", na)
                res["trenger_llm"] += 1
                con.commit()
                continue
            except KlassifiseringsFeil as e:
                _marker(con, n["id"], "feilet", str(e)[:300], na)
                res["feilet"] += 1
                con.commit()
                continue
        else:
            res["regel"] += 1

        if handler:
            _lagre_handler(con, n, handler, kilde, korr)
            _marker(con, n["id"], "ok", f"{kilde}: {len(handler)} handler", na)
        else:
            con.execute("DELETE FROM innsidehandler WHERE nyhet_id = ?", (n["id"],))
            _marker(con, n["id"], "ingen_handler", kilde, na)
            res["ingen_handler"] += 1
        con.commit()
    # Ferdig tolkede innsidemeldinger trenger ikke brødteksten lenger (den kan
    # hentes fra MFN igjen). Holder databasen, som committes til git, liten.
    con.execute(
        "UPDATE nyheter SET brodtekst = NULL WHERE brodtekst IS NOT NULL AND id IN "
        "(SELECT nyhet_id FROM innside_behandlet WHERE status IN ('ok', 'ingen_handler'))"
    )
    con.commit()
    return res
