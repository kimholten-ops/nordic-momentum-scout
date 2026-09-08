"""Dedup skal slå sammen rader fra samme selskap innen tidsvinduet med lik
nok tittel, la ekte separate nyheter stå som egne hendelser, foretrekke
norsk/engelsk som kanonisk rad, og være idempotent ved gjentatt kjøring."""
from __future__ import annotations

import pytest

from src import db
from src.klassifisering.dedup import bygg_hendelser, tittel_likhet


@pytest.fixture
def con(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    with db.session() as c:
        yield c


def _sett_inn(con, *, url, publisert, slug, tittel, sprak, ticker=None):
    con.execute(
        "INSERT INTO nyheter (url, publisert, hentet, selskap_slug, tittel, sprak, ticker) "
        "VALUES (?,?,?,?,?,?,?)",
        (url, publisert, publisert, slug, tittel, sprak, ticker),
    )


def test_akobo_eksempel_samme_sprak_ulik_distributor(con):
    _sett_inn(
        con, url="https://mfn.se/cis/a/akobo/1", publisert="2024-03-01T08:15:00",
        slug="akobo-minerals", tittel="Akobo Minerals: Resultat av emisjon", sprak="no",
    )
    _sett_inn(
        con, url="https://mfn.se/ob/a/akobo/2", publisert="2024-03-01T08:15:15",
        slug="akobo-minerals", tittel="Akobo Minerals: Resultat av emisjonen", sprak="no",
    )
    resultat = bygg_hendelser(con)
    assert resultat["nye_hendelser"] == 1
    assert resultat["sammenslatt_i_eksisterende"] == 1

    hendelser = con.execute("SELECT * FROM hendelser").fetchall()
    assert len(hendelser) == 1
    assert hendelser[0]["antall_rader"] == 2
    assert hendelser[0]["publisert"] == "2024-03-01T08:15:00"


def test_martela_eksempel_ulikt_sprak_samme_sekund(con):
    _sett_inn(
        con, url="https://mfn.se/a/martela/fi", publisert="2024-04-10T09:00:00",
        slug="martela", tittel="Martela Oyj:n osavuosikatsaus Q1 2024", sprak="fi",
    )
    _sett_inn(
        con, url="https://mfn.se/a/martela/en", publisert="2024-04-10T09:00:00",
        slug="martela", tittel="Martela Corporation Interim Report Q1 2024", sprak="en",
    )
    bygg_hendelser(con)

    hendelser = con.execute("SELECT * FROM hendelser").fetchall()
    assert len(hendelser) == 1
    # Engelsk er ikke norsk, men slår finsk i språkrangeringen.
    assert hendelser[0]["tittel"] == "Martela Corporation Interim Report Q1 2024"


def test_norsk_foretrekkes_som_kanonisk_uansett_ankomstrekkefolge(con):
    _sett_inn(
        con, url="https://mfn.se/a/equinor/en", publisert="2024-05-01T10:00:00",
        slug="equinor", tittel="Equinor Q1 2024 results", sprak="en",
    )
    bygg_hendelser(con)  # engelsk blir først kanonisk

    _sett_inn(
        con, url="https://mfn.se/a/equinor/no", publisert="2024-05-01T10:00:05",
        slug="equinor", tittel="Equinor Q1 2024 resultater", sprak="no",
    )
    bygg_hendelser(con)  # norsk skal overta som kanonisk

    hendelser = con.execute("SELECT * FROM hendelser").fetchall()
    assert len(hendelser) == 1
    assert hendelser[0]["tittel"] == "Equinor Q1 2024 resultater"


def test_ulike_nyheter_samme_selskap_blir_egne_hendelser(con):
    _sett_inn(
        con, url="https://mfn.se/a/equinor/1", publisert="2024-05-01T10:00:00",
        slug="equinor", tittel="Equinor: Kvartalsrapport Q1 2024", sprak="no",
    )
    _sett_inn(
        con, url="https://mfn.se/a/equinor/2", publisert="2024-05-01T10:05:00",
        slug="equinor", tittel="Equinor tildelt ny lisens på norsk sokkel", sprak="no",
    )
    resultat = bygg_hendelser(con)
    assert resultat["nye_hendelser"] == 2
    assert resultat["sammenslatt_i_eksisterende"] == 0


def test_utenfor_tidsvindu_blir_ikke_slatt_sammen(con):
    _sett_inn(
        con, url="https://mfn.se/a/equinor/1", publisert="2024-05-01T10:00:00",
        slug="equinor", tittel="Equinor: Kvartalsrapport Q1 2024", sprak="no",
    )
    _sett_inn(
        con, url="https://mfn.se/a/equinor/2", publisert="2024-05-01T10:25:00",
        slug="equinor", tittel="Equinor: Kvartalsrapport Q1 2024", sprak="no",
    )
    resultat = bygg_hendelser(con)
    assert resultat["nye_hendelser"] == 2


def test_idempotent_ved_gjentatt_kjoring(con):
    _sett_inn(
        con, url="https://mfn.se/a/equinor/1", publisert="2024-05-01T10:00:00",
        slug="equinor", tittel="Equinor: Kvartalsrapport Q1 2024", sprak="no",
    )
    forste = bygg_hendelser(con)
    andre = bygg_hendelser(con)
    assert forste["nye_hendelser"] == 1
    assert andre["rader_vurdert"] == 0
    assert andre["nye_hendelser"] == 0


def test_tittel_likhet_fanger_opp_tall_og_navn_pa_tvers_av_sprak():
    a = "Martela Oyj:n osavuosikatsaus Q1 2024"
    b = "Martela Corporation Interim Report Q1 2024"
    assert tittel_likhet(a, b) >= 0.5
