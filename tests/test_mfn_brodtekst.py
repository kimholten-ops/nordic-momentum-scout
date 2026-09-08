"""Tester brødtekst-uttrekket mot en lokal artikkel-fixture. Ingen
nettverkskall."""
from __future__ import annotations

from pathlib import Path

from src.sources import mfn

FIXTURE = Path(__file__).parent / "fixtures" / "mfn_artikkel.html"


def test_brodtekst_inneholder_selve_teksten():
    tekst = mfn.parse_brodtekst(FIXTURE.read_text(encoding="utf-8"))
    assert "Equinor rapporterer et justert resultat på 5,2 milliarder dollar" in tekst
    assert "Styret foreslår et utbytte på 0,35 dollar per aksje" in tekst


def test_brodtekst_dropper_stoy():
    tekst = mfn.parse_brodtekst(FIXTURE.read_text(encoding="utf-8"))
    assert "skal ikke med" not in tekst
    assert "color: red" not in tekst
    assert "Alle nyheter" not in tekst
    assert "Kontakt oss" not in tekst


def test_brodtekst_dekoder_entiteter():
    tekst = mfn.parse_brodtekst(FIXTURE.read_text(encoding="utf-8"))
    assert "Kvartalsrapport Q1 2024 & utbytte" in tekst
