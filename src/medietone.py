"""Tonen i en medieomtale for ett bestemt selskap, vurdert av Ollama.

Kjøres av vakten på PC-en (scripts/vakt.py). Ser bare overskrift og ingress,
ikke kursdata, så vurderingen ikke kan lekke fremtiden.
"""
from __future__ import annotations

import json

SYSTEM = (
    "Du vurderer korte nyhetsoverskrifter for en aksjeinvestor. Svar bare med JSON: "
    '{"relevant": true/false, "tone": -1, 0 eller 1}. '
    "relevant = omtalen handler faktisk om det oppgitte selskapet (ikke bare et navn som ligner). "
    "tone = 1 hvis nyheten trolig er god for selskapets aksje (vekst, kontrakter, oppkjøpsinteresse, "
    "oppgradering), -1 hvis trolig dårlig (tap, nedgradering, granskning, emisjon til rabatt, konflikt), "
    "0 hvis nøytral eller uklar. Vær forsiktig: uten tydelig grunn, svar 0."
)


def prompt(selskap: str, tittel: str, ingress: str | None) -> str:
    return f"Selskap: {selskap}\nOverskrift: {tittel}\nIngress: {ingress or '(ingen)'}"


def tolk(selskap: str, tittel: str, ingress: str | None) -> dict:
    from src.klassifisering.llm import _kall_ollama, _parse_json

    data = _parse_json(_kall_ollama(prompt(selskap, tittel, ingress), SYSTEM))
    tone = data.get("tone")
    tone = int(tone) if isinstance(tone, (int, float)) and int(tone) in (-1, 0, 1) else 0
    return {"relevant": bool(data.get("relevant")), "tone": tone}


def importer(con, rader: list[dict]) -> dict:
    """Rader fra data/ollama/medier.jsonl -> tabellen medietone."""
    from src.sources.medier import SKJEMA

    con.executescript(SKJEMA)
    n = 0
    for r in rader:
        res = r.get("resultat")
        if not res or not r.get("url") or not r.get("slug"):
            continue
        n += con.execute("INSERT OR REPLACE INTO medietone (url, slug, tone, relevant, modell, tid) VALUES (?,?,?,?,?,?)",
                         (r["url"], r["slug"], res.get("tone"), int(bool(res.get("relevant"))), r.get("modell"),
                          r.get("tid"))).rowcount
    con.commit()
    return {"lest": len(rader), "lagt_inn": n}


def nokkel(r: dict) -> str:
    return json.dumps([r["url"], r["slug"]])
