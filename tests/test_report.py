"""Rapporten skal vise klassifiseringsstatistikk fra fase 1 i tillegg til
innsamlingstallene fra fase 0. Ingen nettverkskall."""
from __future__ import annotations

import json

import pytest

from src import db, report
from src.klassifisering import dedup, kjor, llm
from src.sources import mfn


@pytest.fixture
def con(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    with db.session() as c:
        yield c


class _FalskSvar:
    def raise_for_status(self):
        pass

    def json(self):
        return {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(
                        {
                            "kategori": "kontrakt_ordre",
                            "retning": "positiv",
                            "overraskelse": 4,
                            "sikkerhet": 0.8,
                            "begrunnelse": "Stor kontrakt.",
                            "tallverdi": 500.0,
                            "valuta": "NOK",
                        }
                    ),
                }
            ]
        }


def test_rapport_viser_kategorifordeling_og_siste_klassifiseringer(con, monkeypatch):
    con.execute(
        "INSERT INTO nyheter (url, publisert, hentet, selskap_slug, tittel, sprak) "
        "VALUES ('https://mfn.se/a/x/1', '2024-05-01T08:00:00', '2024-05-01T08:01:00', "
        "'equinor', 'Equinor: Tilbakekjøp av egne aksjer', 'no')"
    )
    con.execute(
        "INSERT INTO nyheter (url, publisert, hentet, selskap_slug, tittel, sprak) "
        "VALUES ('https://mfn.se/a/x/2', '2024-05-01T09:00:00', '2024-05-01T09:01:00', "
        "'kongsberg-gruppen', 'Kongsberg tildelt kontrakt verdt 500 MNOK', 'no')"
    )
    con.commit()
    dedup.bygg_hendelser(con)
    con.commit()

    monkeypatch.setattr(llm, "ANTHROPIC_API_KEY", "test-nokkel")
    monkeypatch.setattr(mfn, "hent_brodtekst", lambda url: "tekst")
    monkeypatch.setattr(llm.requests, "post", lambda *a, **kw: _FalskSvar())
    kjor.kjor(con)
    con.commit()

    tekst = report.bygg()

    assert "Hendelser (etter dedup): 2" in tekst
    assert "Klassifisert: 2" in tekst
    assert "Filtrert bort som rutine: 1 (50%)" in tekst
    assert "rutine: 1" in tekst
    assert "kontrakt_ordre: 1" in tekst
    assert "v1: 2" in tekst
    assert "Kongsberg tildelt kontrakt verdt 500 MNOK — Stor kontrakt." in tekst


def test_rapport_uten_data_krasjer_ikke(con):
    tekst = report.bygg()
    assert "Momentum – status" in tekst
    assert "Median forsinkelse: ukjent" in tekst
