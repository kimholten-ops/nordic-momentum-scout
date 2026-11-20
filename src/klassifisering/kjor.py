"""Orkestrerer klassifisering av hendelser: regelbasert forfilter -> (evt.)
LLM. Forutsetter at src.klassifisering.dedup.bygg_hendelser() allerede har
kjørt i samme jobb, slik at "hendelser" er oppdatert.

En hendelse klassifiseres én gang per PROMPT_VERSJON (UNIQUE-constraint i
src/db.py), så denne modulen henter kun hendelser som ennå ikke har en rad i
klassifiseringer for gjeldende versjon — bump PROMPT_VERSJON i kategorier.py
for å klassifisere alt på nytt med en endret prompt.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta

from src.config import KLASSIFISERING_MAKS_KALL, KLASSIFISERING_MODELL, LLM_FRISK_TIMER, TZ
from src.klassifisering import llm
from src.klassifisering.forfilter import sjekk_rutine
from src.klassifisering.kategorier import PROMPT_VERSJON
from src.sources import mfn


def _uklassifiserte(con: sqlite3.Connection, prompt_versjon: str) -> list[sqlite3.Row]:
    return con.execute(
        "SELECT h.id, h.tittel, h.publisert, h.kanonisk_nyhet_id, n.url, n.brodtekst "
        "FROM hendelser h "
        "JOIN nyheter n ON n.id = h.kanonisk_nyhet_id "
        "LEFT JOIN klassifiseringer k ON k.hendelse_id = h.id AND k.prompt_versjon = ? "
        "WHERE k.id IS NULL "
        "ORDER BY h.publisert",
        (prompt_versjon,),
    ).fetchall()


def _lagre(
    con: sqlite3.Connection,
    hendelse_id: int,
    modell: str,
    status: str,
    felter: dict | None,
    tidspunkt: str,
) -> None:
    felter = felter or {}
    con.execute(
        "INSERT OR IGNORE INTO klassifiseringer "
        "(hendelse_id, prompt_versjon, modell, status, kategori, retning, "
        " overraskelse, sikkerhet, begrunnelse, tallverdi, valuta, klassifisert_tidspunkt) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            hendelse_id,
            PROMPT_VERSJON,
            modell,
            status,
            felter.get("kategori"),
            felter.get("retning"),
            felter.get("overraskelse"),
            felter.get("sikkerhet"),
            felter.get("begrunnelse"),
            felter.get("tallverdi"),
            felter.get("valuta"),
            tidspunkt,
        ),
    )


def kjor(con: sqlite3.Connection, maks_kall: int = KLASSIFISERING_MAKS_KALL,
         frisk_timer: float | None = LLM_FRISK_TIMER) -> dict:
    """frisk_timer: bare hendelser publisert de siste N timene går til LLM
    (None = alle). Eldre etterslep tas av Ollama på PC-en. Nyeste først, så
    budsjettet går til det som kan gi varsel."""
    hendelser = list(reversed(_uklassifiserte(con, PROMPT_VERSJON)))
    na_dt = datetime.now(TZ)
    na = na_dt.isoformat(timespec="seconds")
    grense = (na_dt - timedelta(hours=frisk_timer)).replace(tzinfo=None).isoformat() if frisk_timer else ""
    ki = llm.tilgjengelig()
    api_stengt = None
    for_gamle = 0

    filtrert = 0
    llm_kall = 0
    feilet = 0
    brodtekst_feilet = 0
    stanset_av_budsjett = 0
    tokens_estimat = 0

    for h in hendelser:
        regel = sjekk_rutine(h["tittel"])
        if regel is not None:
            _lagre(
                con, h["id"], "regelbasert-filter", "rutine",
                {"kategori": "rutine", "begrunnelse": f"forfilter: {regel}"}, na,
            )
            con.commit()  # jobben (særlig backfill) kan bli avbrutt når som helst
            filtrert += 1
            continue

        if (h["publisert"] or "") < grense:
            for_gamle += 1
            continue  # etterslep: Ollama på PC-en
        if llm_kall >= maks_kall or api_stengt or not ki:
            stanset_av_budsjett += 1
            continue  # ingenting lagres - tas opp igjen neste kjøring

        brodtekst = h["brodtekst"]
        if not brodtekst:
            try:
                brodtekst = mfn.hent_brodtekst(h["url"])
            except Exception:
                # Ikke lagre noe: hendelsen tas opp igjen neste kjøring i
                # stedet for å låse fast en tittel-bare klassifisering
                # fordi ett HTTP-kall feilet forbigående.
                brodtekst_feilet += 1
                continue
            if brodtekst:
                con.execute(
                    "UPDATE nyheter SET brodtekst = ? "
                    "WHERE id = ? AND (brodtekst IS NULL OR brodtekst = '')",
                    (brodtekst, h["kanonisk_nyhet_id"]),
                )

        prompt = llm.bygg_prompt(h["tittel"], brodtekst, h["publisert"])
        tokens_estimat += llm.estimer_tokens(prompt)
        llm_kall += 1

        try:
            resultat = llm.klassifiser(h["tittel"], brodtekst, h["publisert"])
            _lagre(con, h["id"], KLASSIFISERING_MODELL, "ok", resultat, na)
        except llm.APIStengt as e:
            api_stengt = str(e)  # ikke meldingens feil: ingenting lagres, stopp kallene
            continue
        except llm.KlassifiseringsFeil:
            _lagre(con, h["id"], KLASSIFISERING_MODELL, "feilet", {}, na)
            feilet += 1
        con.commit()  # ikke la et avbrutt kall koste et nytt LLM-kall neste gang

    return {
        "hendelser_vurdert": len(hendelser),
        "filtrert": filtrert,
        "llm_kall": llm_kall,
        "feilet": feilet,
        "brodtekst_feilet": brodtekst_feilet,
        "stanset_av_budsjett": stanset_av_budsjett,
        "tokens_estimat": tokens_estimat,
        "for_gamle": for_gamle,
        "api_stengt": api_stengt,
    }
