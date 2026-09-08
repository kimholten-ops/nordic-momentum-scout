"""Tester for LLM-klassifisering. Ingen ekte nettverkskall: requests.post
mockes til å returnere ferdige svar i samme form som Anthropic sitt API."""
from __future__ import annotations

import inspect
import json

import pytest

from src.klassifisering import llm
from src.klassifisering.llm import KlassifiseringsFeil, bygg_prompt, klassifiser


class _FalskSvar:
    def __init__(self, tekst: str):
        self._tekst = tekst

    def raise_for_status(self):
        pass

    def json(self):
        return {"content": [{"type": "text", "text": self._tekst}]}


def test_bygg_prompt_tar_bare_tittel_brodtekst_publisert():
    """Selve look-ahead-forbudet: funksjonen har strukturelt ingen plass til
    kursdata, den kan bare ta imot disse tre feltene."""
    assert set(inspect.signature(bygg_prompt).parameters) == {
        "tittel",
        "brodtekst",
        "publisert",
    }

    prompt = bygg_prompt("Equinor: Kvartalsrapport", "Resultatet var sterkt.", "2024-05-01T08:00:00")
    assert "Equinor: Kvartalsrapport" in prompt
    assert "Resultatet var sterkt." in prompt
    assert "2024-05-01T08:00:00" in prompt


GYLDIG_JSON = json.dumps(
    {
        "kategori": "resultat",
        "retning": "positiv",
        "overraskelse": 3,
        "sikkerhet": 0.7,
        "begrunnelse": "Bedre resultat enn ventet.",
        "tallverdi": 5.2,
        "valuta": "USD",
    }
)


def test_klassifiser_gyldig_svar_forste_forsok(monkeypatch):
    kall = []

    def fake_post(url, headers, json, timeout):
        kall.append(json)
        return _FalskSvar(GYLDIG_JSON)

    monkeypatch.setattr(llm, "ANTHROPIC_API_KEY", "test-nokkel")
    monkeypatch.setattr(llm.requests, "post", fake_post)

    resultat = klassifiser("Equinor: Kvartalsrapport", "Resultatet var sterkt.", "2024-05-01T08:00:00")

    assert resultat["kategori"] == "resultat"
    assert resultat["retning"] == "positiv"
    assert resultat["overraskelse"] == 3
    assert resultat["sikkerhet"] == 0.7
    assert resultat["tallverdi"] == 5.2
    assert resultat["valuta"] == "USD"
    assert len(kall) == 1


def test_klassifiser_ugyldig_kategori_gir_feil_etter_retry(monkeypatch):
    ugyldig = json.dumps({**json.loads(GYLDIG_JSON), "kategori": "noe_som_ikke_finnes"})
    kall = []

    def fake_post(url, headers, json, timeout):
        kall.append(1)
        return _FalskSvar(ugyldig)

    monkeypatch.setattr(llm, "ANTHROPIC_API_KEY", "test-nokkel")
    monkeypatch.setattr(llm.requests, "post", fake_post)

    with pytest.raises(KlassifiseringsFeil):
        klassifiser("tittel", "tekst", "2024-05-01T08:00:00")

    assert len(kall) == llm.MAKS_FORSOK


def test_klassifiser_lykkes_pa_andre_forsok(monkeypatch):
    svar_i_rekkefolge = iter(["ikke json i det hele tatt", GYLDIG_JSON])

    def fake_post(url, headers, json, timeout):
        return _FalskSvar(next(svar_i_rekkefolge))

    monkeypatch.setattr(llm, "ANTHROPIC_API_KEY", "test-nokkel")
    monkeypatch.setattr(llm.requests, "post", fake_post)

    resultat = klassifiser("tittel", "tekst", "2024-05-01T08:00:00")
    assert resultat["kategori"] == "resultat"


def test_manglende_api_nokkel_gir_klassifiseringsfeil(monkeypatch):
    monkeypatch.setattr(llm, "ANTHROPIC_API_KEY", "")
    with pytest.raises(KlassifiseringsFeil):
        klassifiser("tittel", "tekst", "2024-05-01T08:00:00")
