"""LLM-klassifisering av hendelser som har passert det regelbaserte
forfilteret. Kaller Claude Haiku direkte via Anthropic sitt Messages-API med
requests — ingen ekstra avhengighet for ett API-kall.

**Look-ahead-forbud**: bygg_prompt() tar bevisst kun imot tittel, brødtekst
og publiseringstidspunkt. Den har ingen parameter for kursdata og spør aldri
databasen om noe fra "kurser". Det er selve poenget: modellen skal aldri se
hva kursen gjorde, verken før eller etter publisering, ellers er backtesten
i fase 2 verdiløs. Se tests/test_llm.py for testen som bekrefter dette.
"""
from __future__ import annotations

import json
import re

import requests

from src.config import (
    ANTHROPIC_API_KEY,
    ANTHROPIC_API_URL,
    ANTHROPIC_TIMEOUT,
    KLASSIFISERING_LLM,
    KLASSIFISERING_MODELL,
    OLLAMA_MODELL,
    OLLAMA_NUM_CTX,
    OLLAMA_URL,
)
from src.klassifisering.kategorier import KATEGORIER, KATEGORI_BESKRIVELSE, RETNINGER

MAKS_FORSOK = 2  # ett forsøk + ett nytt forsøk, deretter klassifisering_feilet

SYSTEM_PROMPT = (
    "Du klassifiserer børsmeldinger fra nordiske selskaper for et "
    "forskningsprosjekt. Du ser KUN tittel, brødtekst og "
    "publiseringstidspunkt for én enkelt melding — aldri kursdata, aldri "
    "andre meldinger. Svar utelukkende med ett JSON-objekt, ingen "
    "markdown-blokk, ingen forklarende tekst utenfor JSON-et.\n\n"
    "Feltene er:\n"
    '  "kategori": nøyaktig én av ' + json.dumps(KATEGORIER, ensure_ascii=False) + "\n"
    '  "retning": nøyaktig én av ' + json.dumps(RETNINGER, ensure_ascii=False) + "\n"
    '  "overraskelse": heltall 1-5, hvor 5 er svært uventet\n'
    '  "sikkerhet": tall mellom 0 og 1\n'
    '  "begrunnelse": maks 25 ord, på norsk\n'
    '  "tallverdi": tall hvis meldingen inneholder en sentral størrelse '
    "(f.eks. kontraktsverdi), ellers null\n"
    '  "valuta": valutakoden for tallverdi (f.eks. "NOK"), ellers null\n\n'
    "Kategoribeskrivelser:\n"
    + "\n".join(f"  {k}: {v}" for k, v in KATEGORI_BESKRIVELSE.items())
    + "\n\nFinn aldri på en kategori som ikke står i listen over."
)


class KlassifiseringsFeil(Exception):
    """Kastes når modellen ikke gir et gyldig svar etter MAKS_FORSOK forsøk."""


class APIStengt(Exception):
    """API-et tar ikke imot kall nå (tom kreditt, ugyldig nøkkel, struping).
    Ikke en feil ved meldingen: orkestratoren skal stoppe LLM-kallene for
    resten av kjøringen og IKKE merke meldingen som feilet."""


_STENGT_ORD = ("credit balance", "billing", "insufficient", "quota", "disabled", "rate limit", "overloaded")


def sjekk_svar(r: requests.Response) -> None:
    """Som raise_for_status, men skiller «API-et er stengt for oss» fra vanlige feil."""
    kode, tekst = getattr(r, "status_code", 200), str(getattr(r, "text", "") or "")
    if kode in (401, 402, 403, 429, 529) or (kode == 400 and any(o in tekst.lower() for o in _STENGT_ORD)):
        raise APIStengt(f"HTTP {kode}: {tekst[:200]}")
    r.raise_for_status()


def _kall_ollama(prompt: str, system: str) -> str:
    r = requests.post(
        f"{OLLAMA_URL.rstrip('/')}/api/chat",
        json={"model": OLLAMA_MODELL, "stream": False, "format": "json", "options": {"temperature": 0, "num_ctx": OLLAMA_NUM_CTX},
              "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}]},
        timeout=300,
    )
    r.raise_for_status()
    return r.json().get("message", {}).get("content", "")


def tilgjengelig() -> bool:
    if KLASSIFISERING_LLM == "ollama":
        return True
    return KLASSIFISERING_LLM == "anthropic" and bool(ANTHROPIC_API_KEY)


def bygg_prompt(tittel: str, brodtekst: str, publisert: str) -> str:
    """Kun tittel, brødtekst og publiseringstidspunkt — se look-ahead-
    forbudet i modul-docstringen. Signaturen har med vilje ingen plass til
    kursdata."""
    return (
        f"Publisert: {publisert}\n"
        f"Tittel: {tittel}\n\n"
        f"Brødtekst:\n{brodtekst or '(ikke tilgjengelig)'}"
    )


def estimer_tokens(tekst: str) -> int:
    """Grov overslag: ~4 tegn per token. Godt nok til å holde et øye med
    kostnaden, ikke ment som et eksakt tall."""
    return max(1, len(tekst) // 4)


_JSON_BLOKK_RE = re.compile(r"\{.*\}", re.S)


def _parse_json(tekst: str) -> dict:
    try:
        return json.loads(tekst)
    except json.JSONDecodeError:
        pass
    m = _JSON_BLOKK_RE.search(tekst)
    if not m:
        raise ValueError("fant ikke JSON i svaret")
    return json.loads(m.group(0))


def _valider(data: dict) -> dict:
    if data.get("kategori") not in KATEGORIER:
        raise ValueError(f"ugyldig kategori: {data.get('kategori')!r}")
    if data.get("retning") not in RETNINGER:
        raise ValueError(f"ugyldig retning: {data.get('retning')!r}")

    overraskelse = int(data.get("overraskelse"))
    if not 1 <= overraskelse <= 5:
        raise ValueError(f"overraskelse utenfor 1-5: {overraskelse}")

    sikkerhet = float(data.get("sikkerhet"))
    if not 0 <= sikkerhet <= 1:
        raise ValueError(f"sikkerhet utenfor 0-1: {sikkerhet}")

    tallverdi = data.get("tallverdi")
    tallverdi = float(tallverdi) if tallverdi not in (None, "") else None

    return {
        "kategori": data["kategori"],
        "retning": data["retning"],
        "overraskelse": overraskelse,
        "sikkerhet": sikkerhet,
        "begrunnelse": str(data.get("begrunnelse") or "")[:300],
        "tallverdi": tallverdi,
        "valuta": (data.get("valuta") or None),
    }


def _kall_api(prompt: str, modell: str) -> str:
    if KLASSIFISERING_LLM == "ollama":
        return _kall_ollama(prompt, SYSTEM_PROMPT)
    if not ANTHROPIC_API_KEY:
        raise KlassifiseringsFeil("ANTHROPIC_API_KEY er ikke satt")

    r = requests.post(
        ANTHROPIC_API_URL,
        headers={
            "x-api-key": ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": modell,
            "max_tokens": 400,
            "system": SYSTEM_PROMPT,
            "messages": [{"role": "user", "content": prompt}],
        },
        timeout=ANTHROPIC_TIMEOUT,
    )
    sjekk_svar(r)
    data = r.json()
    return "".join(
        blokk.get("text", "") for blokk in data.get("content", []) if blokk.get("type") == "text"
    )


def klassifiser(
    tittel: str,
    brodtekst: str,
    publisert: str,
    modell: str = KLASSIFISERING_MODELL,
) -> dict:
    """Kaller LLM-en med ett nytt forsøk ved ugyldig output. Returnerer et
    validert dict, eller kaster KlassifiseringsFeil etter MAKS_FORSOK
    mislykkede forsøk — den skal ALDRI la unntak fra dette stoppe hele
    jobben, det er orkestratorens (kjor.py) ansvar å fange den."""
    prompt = bygg_prompt(tittel, brodtekst, publisert)
    siste_feil: Exception | None = None

    for forsok in range(1, MAKS_FORSOK + 1):
        try:
            svar = _kall_api(prompt, modell)
            return _valider(_parse_json(svar))
        except (ValueError, KeyError, TypeError) as e:
            siste_feil = e
            continue
        except requests.RequestException as e:
            siste_feil = e
            continue

    raise KlassifiseringsFeil(
        f"ugyldig eller manglende svar etter {MAKS_FORSOK} forsøk: {siste_feil}"
    )
