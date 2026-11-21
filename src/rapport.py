"""Nøkkeltall fra kvartals- og årsrapporter, tolket av Ollama (gratis, lokalt).

Rapportmeldingene finnes allerede i MFN (selskapshendelser, type «rapport»).
Ollama leser starten av meldingen (sammendraget med nøkkeltall står nesten
alltid først) og returnerer tall for perioden og samme periode i fjor, om
selskapet endrer utsiktene, og tonen. Faktormodellen bruker:

- omsetningsvekst mot samme periode i fjor,
- endring i driftsmargin (prosentpoeng),
- vekst i resultat per aksje,
- utsikter hevet / uendret / senket,
- tone (-2 til +2).

Tall som ikke står i teksten skal være null — modellen må aldri gjette.
"""
from __future__ import annotations

import json
import math

import requests

from src.config import OLLAMA_MODELL, OLLAMA_NUM_CTX, OLLAMA_URL

MAKS_TEGN = 9000
# r1 hadde et eksempel med ekte tall i prompten; qwen2.5:7b kopierte dem
# (EPS 1,12/0,85, vekst 16 %, «uendret», tone 1). r2 har bare et skjema.
VERSJON = "r2"
_R1_EKSEMPEL = {"eps": 1.12, "eps_fjor": 0.85, "omsetning": 1520.0, "omsetning_fjor": 1310.0,
                "driftsresultat": 182.0, "driftsresultat_fjor": 141.0, "omsetningsvekst_pst": 16.0}
UTSIKTER = {"hevet": 1, "uendret": 0, "senket": -1, "ingen": None}

SYSTEM_PROMPT = """Du leser kvartals- og årsrapporter fra børsnoterte selskaper (norsk, svensk, dansk, finsk eller engelsk) og henter ut nøkkeltall. Svar KUN med JSON.

Felter:
- "periode": f.eks. "Q3 2026", "H1 2026" eller "FY 2025".
- "valuta": valutakode tallene er oppgitt i (NOK, SEK, DKK, EUR, USD ...).
- "omsetning", "omsetning_fjor": driftsinntekter / net sales / omsättning / liikevaihto for perioden og samme periode året før.
- "driftsresultat", "driftsresultat_fjor": EBIT / driftsresultat / rörelseresultat / liiketulos. Bruk justert EBIT bare hvis vanlig EBIT mangler. Er bare EBITDA oppgitt, bruk EBITDA.
- "eps", "eps_fjor": resultat per aksje.
- "omsetningsvekst_pst": omsetningsveksten mot samme periode i fjor i prosent, BARE hvis teksten oppgir den direkte (f.eks. «revenue increased by 28%» gir 28). Ellers null.
- "utsikter": "hevet" hvis selskapet øker prognose/guiding, "senket" hvis det reduserer den eller varsler svakere marked, "uendret" hvis det bekrefter tidligere prognose, ellers "ingen".
- "tone": heltall fra -2 (svært negativ) til 2 (svært positiv) for hvordan ledelsen beskriver kvartalet og fremtiden.

Regler:
- Alle beløp i samme enhet som teksten bruker for perioden (f.eks. millioner). Bare tall, ingen tekst. Negative tall med minus.
- Bruk tallene for KVARTALET (ikke hittil i år) når begge finnes.
- Står et tall ikke i teksten, sett null. Ikke regn ut, ikke gjett og ikke bruk 0 for «mangler».
- «utsikter» er "ingen" med mindre teksten tydelig sier at prognosen heves, senkes eller gjentas.
- «tone» er 0 med mindre teksten er tydelig positiv eller negativ.
"""

SKJEMA = ('{"periode": tekst, "valuta": tekst, "omsetning": tall|null, "omsetning_fjor": tall|null, '
          '"driftsresultat": tall|null, "driftsresultat_fjor": tall|null, "eps": tall|null, "eps_fjor": tall|null, '
          '"omsetningsvekst_pst": tall|null, "utsikter": "hevet"|"senket"|"uendret"|"ingen", "tone": -2..2}')


def bygg_prompt(tittel: str, tekst: str) -> str:
    return (f"Tittel: {tittel}\n\nTekst (starten av rapporten):\n{(tekst or '')[:MAKS_TEGN]}\n\n"
            f"Svar med JSON med disse feltene (bare tall fra teksten over): {SKJEMA}")


def _tall(x) -> float | None:
    if x in (None, "", "null"):
        return None
    if isinstance(x, str):
        x = x.replace("−", "-").replace(" ", "").replace(",", ".")
    v = float(x)
    if not math.isfinite(v):
        raise ValueError("ikke et tall")
    return v


def valider(d: dict) -> dict:
    ut = {"periode": str(d.get("periode") or "")[:20] or None, "valuta": (str(d.get("valuta") or "")[:5] or None)}
    for k in ("omsetning", "omsetning_fjor", "driftsresultat", "driftsresultat_fjor", "eps", "eps_fjor",
              "omsetningsvekst_pst"):
        ut[k] = _tall(d.get(k))
    # Lekkasje fra r1-eksemplet og 0 brukt som «mangler» blir null.
    for k, v in _R1_EKSEMPEL.items():
        if ut[k] is not None and abs(ut[k] - v) < 1e-9:
            ut[k] = None
    for a, b in (("omsetning", "omsetning_fjor"), ("driftsresultat", "driftsresultat_fjor"), ("eps", "eps_fjor")):
        if ut[a] == 0 and ut[b] in (0, None):
            ut[a] = ut[b] = None
    u = str(d.get("utsikter") or "ingen").lower()
    ut["utsikter"] = u if u in UTSIKTER else "ingen"
    t = d.get("tone")
    ut["tone"] = max(-2, min(2, int(float(t)))) if t not in (None, "") else None
    if all(ut[k] is None for k in ("omsetning", "driftsresultat", "eps", "omsetningsvekst_pst")) and ut["tone"] is None:
        raise ValueError("ingen nøkkeltall funnet")
    return ut


def tolk(tittel: str, tekst: str) -> dict:
    r = requests.post(
        f"{OLLAMA_URL.rstrip('/')}/api/chat",
        json={"model": OLLAMA_MODELL, "stream": False, "format": "json",
              "options": {"temperature": 0, "num_ctx": OLLAMA_NUM_CTX},
              "messages": [{"role": "system", "content": SYSTEM_PROMPT},
                           {"role": "user", "content": bygg_prompt(tittel, tekst)}]},
        timeout=600,
    )
    r.raise_for_status()
    return valider(json.loads(r.json().get("message", {}).get("content", "") or "{}"))


def _foerste(*verdier):
    return next((v for v in verdier if v is not None), None)


def _pst(p) -> float | None:
    return p / 100 if p is not None and -90 <= p <= 500 else None


def avledet(r: dict) -> dict:
    """Egenskaper til faktormodellen fra én tolket rapport. Urimelige verdier
    (feil enhet, fortegnsfeil) blir None heller enn å forvirre modellen."""
    def vekst(a, b):
        if a is None or b is None or b <= 0:
            return None
        v = a / b - 1
        return v if -0.9 <= v <= 5 else None

    def margin(a, s):
        return a / s if a is not None and s and s > 0 and abs(a / s) < 2 else None

    m, mf = margin(r.get("driftsresultat"), r.get("omsetning")), margin(r.get("driftsresultat_fjor"), r.get("omsetning_fjor"))
    eps, epsf = r.get("eps"), r.get("eps_fjor")
    epsv = None
    if eps is not None and epsf is not None and abs(epsf) > 1e-9:
        epsv = max(-3.0, min(3.0, (eps - epsf) / abs(epsf)))
    return {
        "rapport_omsvekst": _foerste(vekst(r.get("omsetning"), r.get("omsetning_fjor")), _pst(r.get("omsetningsvekst_pst"))),
        "rapport_marginendring": (m - mf) if m is not None and mf is not None else None,
        "rapport_epsvekst": epsv,
        "rapport_utsikter": UTSIKTER.get(r.get("utsikter") or "ingen"),
        "rapport_tone": r.get("tone"),
    }
