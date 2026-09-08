"""Tester paginering, stoppbetingelser og gjenopptakbarhet i
src/backfill_arkiv.py. Ingen nettverkskall og ingen ekte sleep:
src.sources.mfn.hent og time.sleep mockes."""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from src import backfill_arkiv as arkiv
from src import db
from src.config import TZ
from src.sources.mfn import Melding

FRIST_LANGT_FRAM = datetime.now(TZ) + timedelta(days=1)


@pytest.fixture
def con(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(arkiv.time, "sleep", lambda *_: None)
    with db.session() as c:
        c.execute(
            "INSERT INTO selskaper (slug, navn, ticker, yahoo, marked, aktiv) "
            "VALUES ('equinor', 'Equinor', 'EQNR', 'EQNR.OL', 'Oslo Børs', 1)"
        )
        c.commit()
        yield c


def _melding(url: str, publisert: str) -> Melding:
    return Melding(
        url=url, publisert=publisert, selskap_slug="equinor", selskap_navn="Equinor",
        tittel=f"Nyhet {url}", distributor="MFN", sprak="no",
    )


def test_stopper_pa_tom_side(con, monkeypatch):
    sider = {
        1: [_melding("https://mfn.se/a/equinor/1", "2024-01-01T08:00:00")],
        2: [],
    }
    monkeypatch.setattr(arkiv.mfn, "hent", lambda url: sider[int(url.rsplit("=", 1)[1])])

    grense = (datetime.now(TZ) - timedelta(days=3650)).isoformat(timespec="seconds")
    resultat = arkiv._importer_selskap(con, "equinor", grense, forsinkelse=0, frist=FRIST_LANGT_FRAM)

    assert resultat["sider"] == 2
    assert resultat["nye"] == 1
    rad = con.execute("SELECT * FROM arkiv_fremdrift WHERE slug='equinor'").fetchone()
    assert rad["ferdig"] == 1
    assert con.execute("SELECT COUNT(*) FROM nyheter").fetchone()[0] == 1


def test_stopper_ved_grense_dato(con, monkeypatch):
    sider = {
        1: [_melding("https://mfn.se/a/equinor/1", "2024-01-01T08:00:00")],
        2: [_melding("https://mfn.se/a/equinor/2", "2015-01-01T08:00:00")],  # eldre enn grensen
    }
    monkeypatch.setattr(arkiv.mfn, "hent", lambda url: sider[int(url.rsplit("=", 1)[1])])

    grense = "2020-01-01T00:00:00"
    resultat = arkiv._importer_selskap(con, "equinor", grense, forsinkelse=0, frist=FRIST_LANGT_FRAM)

    assert resultat["sider"] == 2
    rad = con.execute("SELECT * FROM arkiv_fremdrift WHERE slug='equinor'").fetchone()
    assert rad["ferdig"] == 1
    assert rad["siste_side"] == 2


def test_gjenopptar_fra_siste_side(con, monkeypatch):
    con.execute(
        "INSERT INTO arkiv_fremdrift (slug, siste_side, ferdig) VALUES ('equinor', 5, 0)"
    )
    con.commit()

    besokte_sider = []

    def fake_hent(url):
        side = int(url.rsplit("=", 1)[1])
        besokte_sider.append(side)
        return []

    monkeypatch.setattr(arkiv.mfn, "hent", fake_hent)

    grense = (datetime.now(TZ) - timedelta(days=3650)).isoformat(timespec="seconds")
    arkiv._importer_selskap(con, "equinor", grense, forsinkelse=0, frist=FRIST_LANGT_FRAM)

    assert besokte_sider == [6]  # fortsetter fra side 6, ikke fra 1


def test_ferdig_selskap_hoppes_over(con, monkeypatch):
    con.execute(
        "INSERT INTO arkiv_fremdrift (slug, siste_side, ferdig) VALUES ('equinor', 12, 1)"
    )
    con.commit()

    def skal_ikke_kalles(url):
        raise AssertionError("skal ikke hente flere sider for et ferdig selskap")

    monkeypatch.setattr(arkiv.mfn, "hent", skal_ikke_kalles)

    resultat = arkiv._importer_selskap(con, "equinor", "2020-01-01T00:00:00", forsinkelse=0, frist=FRIST_LANGT_FRAM)
    assert resultat == {"sider": 0, "nye": 0}


def test_dedupliserer_pa_tvers_av_sider(con, monkeypatch):
    """Samme URL på to 'sider' (f.eks. fordi arkivet overlapper) skal ikke
    telles som ny to ganger — nyheter.url er UNIQUE."""
    sider = {
        1: [_melding("https://mfn.se/a/equinor/1", "2024-01-01T08:00:00")],
        2: [_melding("https://mfn.se/a/equinor/1", "2024-01-01T08:00:00")],
        3: [],
    }
    monkeypatch.setattr(arkiv.mfn, "hent", lambda url: sider[int(url.rsplit("=", 1)[1])])

    grense = (datetime.now(TZ) - timedelta(days=3650)).isoformat(timespec="seconds")
    resultat = arkiv._importer_selskap(con, "equinor", grense, forsinkelse=0, frist=FRIST_LANGT_FRAM)

    assert resultat["nye"] == 1
    assert con.execute("SELECT COUNT(*) FROM nyheter").fetchone()[0] == 1


def test_tidsbudsjett_stopper_uten_a_markere_ferdig(con, monkeypatch):
    """Jobben blir avbrutt før arkivet er tomt: den skal stoppe seg selv
    (uten å sette ferdig=1) slik at den fortsetter derfra neste kjøring,
    i stedet for å bli drept av GitHub Actions sin tidsgrense midt i en
    side."""
    monkeypatch.setattr(arkiv.mfn, "hent", lambda url: [_melding(url, "2024-01-01T08:00:00")])

    frist_passert = datetime.now(TZ) - timedelta(seconds=1)
    grense = (datetime.now(TZ) - timedelta(days=3650)).isoformat(timespec="seconds")
    resultat = arkiv._importer_selskap(con, "equinor", grense, forsinkelse=0, frist=frist_passert)

    assert resultat["sider"] == 0
    rad = con.execute("SELECT * FROM arkiv_fremdrift WHERE slug='equinor'").fetchone()
    assert rad["ferdig"] == 0
