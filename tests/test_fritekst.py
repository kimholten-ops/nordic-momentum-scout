"""Regelbasert uttrekk av fritekst, testet på ekte meldinger (fasit fra KI-uttrekket).

Målt på 635 meldinger der KI hadde svar (19. nov 2026): regelen tolket 42 %,
og var enig med KI i 97,8 % av dem. Resten går videre til KI/Ollama.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from src.innside.fritekst import parse_fritekst

FIX = Path(__file__).parent / "fixtures" / "fritekst"


def les(navn):
    return (FIX / f"{navn}.txt").read_text()


def test_engelsk_ceo_kjop_med_beholdning():
    h, = parse_fritekst(les("goodtech"), "2026-11-02T08:00:00")
    assert (h.retning, h.antall, h.pris, h.valuta, h.rolle) == ("kjop", 10000, 14.40, "NOK", "ceo")
    assert h.beholdning_etter == 380343 and h.transaksjonsdato == "2026-10-30" and h.person == "Margrethe Hauge"


def test_norsk_kjop_gjennom_heleid_selskap_i_dag():
    h, = parse_fritekst(les("nofin"), "2026-10-06T19:45:00")
    assert (h.retning, h.antall, h.pris, h.beholdning_etter) == ("kjop", 4072, 1.27, 2748139)
    assert h.naerstaaende and h.transaksjonsdato == "2026-10-06"


def test_to_handler_i_hver_sin_setning():
    hs = parse_fritekst(les("komplett_to"), "2026-10-29T20:03:44")
    assert sorted((h.antall, h.pris) for h in hs) == [(8000, 5.90), (16000, 5.90)]
    assert all(h.retning == "kjop" for h in hs)


def test_dansk_kurs_og_ikke_totalbelop():
    h, = parse_fritekst(les("spgroup_dk"), "2018-05-18T15:53:41")
    assert (h.retning, h.antall, h.pris) == ("kjop", 1016, 246.91)  # ikke «svarende til DKK 250.858,80»
    assert h.rolle == "styremedlem"


def test_norsk_salg_naerstaaende_pris_per_aksje():
    h, = parse_fritekst(les("equinor_no"), "2026-03-27T15:27:02")
    assert (h.retning, h.antall, h.pris, h.valuta) == ("salg", 25, 399.0, "NOK")
    assert h.naerstaaende


def test_gjennomsnittskurs_og_heleid():
    h, = parse_fritekst(les("skand_heleid"), "2026-07-01T21:21:58")
    assert (h.antall, h.pris, h.beholdning_etter) == (52100, 1.48, 54688385) and h.naerstaaende


@pytest.mark.parametrize("navn", ["gjensidige_egne", "storebrand_liste"])
def test_gir_opp_paa_egne_aksjer_og_lister(navn):
    assert parse_fritekst(les(navn), "2026-05-12T21:45:57") is None


@pytest.mark.parametrize("tekst", [
    "Kari Nordmann, CFO, has been allocated 10,000 shares under the LTI programme at a price of NOK 1.00.",
    "Ola Nordmann, CEO, has subscribed for 50,000 shares in the private placement at NOK 2.50 per share.",
    "Per Hansen, board member, has exercised 20,000 options and sold 20,000 shares at NOK 10.00.",
    "Kari, CEO, sold 44,687 and acquired 44,687 shares in the company.",
])
def test_gir_opp_paa_tildeling_emisjon_opsjoner(tekst):
    assert parse_fritekst(tekst) is None
