"""Orkestratoren: forfilter -> LLM, budsjettgrense, idempotens, og — det
viktigste — at ingen kursdata noensinne havner i det som sendes til LLM-en.
Ingen ekte nettverkskall: mfn.hent_brodtekst og requests.post mockes."""
from __future__ import annotations

import json

import pytest

from src import db
from src.klassifisering import dedup, kjor, llm
from src.klassifisering.kategorier import PROMPT_VERSJON
from src.sources import mfn


@pytest.fixture
def con(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(llm, "ANTHROPIC_API_KEY", "test-nokkel")
    with db.session() as c:
        yield c


def _sett_inn_nyhet(con, *, url, publisert, slug, tittel, sprak="no", yahoo=None):
    con.execute(
        "INSERT INTO nyheter (url, publisert, hentet, selskap_slug, tittel, sprak, yahoo) "
        "VALUES (?,?,?,?,?,?,?)",
        (url, publisert, publisert, slug, tittel, sprak, yahoo),
    )


class _FalskSvar:
    def __init__(self, data: dict):
        self._data = data

    def raise_for_status(self):
        pass

    def json(self):
        return {"content": [{"type": "text", "text": json.dumps(self._data)}]}


GYLDIG_RESULTAT = {
    "kategori": "kontrakt_ordre",
    "retning": "positiv",
    "overraskelse": 4,
    "sikkerhet": 0.8,
    "begrunnelse": "Ny kontrakt av betydelig størrelse.",
    "tallverdi": 500.0,
    "valuta": "NOK",
}


def test_rutine_hopper_over_llm_helt(con, monkeypatch):
    _sett_inn_nyhet(
        con, url="https://mfn.se/a/equinor/1", publisert="2024-05-01T08:00:00",
        slug="equinor", tittel="Equinor: Tilbakekjøp av egne aksjer",
    )
    dedup.bygg_hendelser(con)

    def skal_ikke_kalles(*a, **kw):
        raise AssertionError("LLM skal aldri kalles for en rutinemelding")

    monkeypatch.setattr(llm, "klassifiser", skal_ikke_kalles)

    resultat = kjor.kjor(con, frisk_timer=None)
    assert resultat["filtrert"] == 1
    assert resultat["llm_kall"] == 0

    rad = con.execute("SELECT * FROM klassifiseringer").fetchone()
    assert rad["status"] == "rutine"
    assert rad["kategori"] == "rutine"
    assert rad["modell"] == "regelbasert-filter"


def test_ekte_hendelse_klassifiseres_og_lagres(con, monkeypatch):
    _sett_inn_nyhet(
        con, url="https://mfn.se/a/kongsberg/1", publisert="2024-05-01T08:00:00",
        slug="kongsberg-gruppen", tittel="Kongsberg tildelt kontrakt verdt 500 MNOK",
    )
    dedup.bygg_hendelser(con)

    monkeypatch.setattr(mfn, "hent_brodtekst", lambda url: "Kongsberg har inngått en ny avtale.")
    monkeypatch.setattr(
        llm.requests, "post", lambda *a, **kw: _FalskSvar(GYLDIG_RESULTAT)
    )

    resultat = kjor.kjor(con, frisk_timer=None)
    assert resultat["llm_kall"] == 1
    assert resultat["feilet"] == 0

    rad = con.execute("SELECT * FROM klassifiseringer").fetchone()
    assert rad["status"] == "ok"
    assert rad["kategori"] == "kontrakt_ordre"
    assert rad["tallverdi"] == 500.0

    # Brødteksten skal være lagret på selve nyhets-raden til senere bruk.
    nyhet = con.execute("SELECT brodtekst FROM nyheter").fetchone()
    assert nyhet["brodtekst"] == "Kongsberg har inngått en ny avtale."


def test_klassifisering_feilet_stopper_ikke_jobben(con, monkeypatch):
    _sett_inn_nyhet(
        con, url="https://mfn.se/a/kongsberg/1", publisert="2024-05-01T08:00:00",
        slug="kongsberg-gruppen", tittel="Kongsberg tildelt kontrakt verdt 500 MNOK",
    )
    dedup.bygg_hendelser(con)

    monkeypatch.setattr(mfn, "hent_brodtekst", lambda url: "tekst")

    def feiler_alltid(*a, **kw):
        raise llm.KlassifiseringsFeil("simulert feil")

    monkeypatch.setattr(llm, "klassifiser", feiler_alltid)

    resultat = kjor.kjor(con, frisk_timer=None)
    assert resultat["feilet"] == 1

    rad = con.execute("SELECT * FROM klassifiseringer").fetchone()
    assert rad["status"] == "feilet"
    assert rad["kategori"] is None


def test_samme_promptversjon_klassifiseres_ikke_pa_nytt(con, monkeypatch):
    _sett_inn_nyhet(
        con, url="https://mfn.se/a/kongsberg/1", publisert="2024-05-01T08:00:00",
        slug="kongsberg-gruppen", tittel="Kongsberg tildelt kontrakt verdt 500 MNOK",
    )
    dedup.bygg_hendelser(con)
    monkeypatch.setattr(mfn, "hent_brodtekst", lambda url: "tekst")

    kall = []
    monkeypatch.setattr(
        llm.requests, "post",
        lambda *a, **kw: (kall.append(1), _FalskSvar(GYLDIG_RESULTAT))[1],
    )

    kjor.kjor(con, frisk_timer=None)
    kjor.kjor(con, frisk_timer=None)  # ny kjøring, samme PROMPT_VERSJON

    assert len(kall) == 1
    assert con.execute("SELECT COUNT(*) FROM klassifiseringer").fetchone()[0] == 1


def test_maks_kall_grense_utsetter_resten_til_neste_kjoring(con, monkeypatch):
    for i in range(3):
        _sett_inn_nyhet(
            con, url=f"https://mfn.se/a/selskap{i}/1", publisert=f"2024-05-01T08:0{i}:00",
            slug=f"selskap{i}", tittel=f"Selskap {i} tildelt kontrakt verdt {i}00 MNOK",
        )
    dedup.bygg_hendelser(con)
    monkeypatch.setattr(mfn, "hent_brodtekst", lambda url: "tekst")
    monkeypatch.setattr(llm.requests, "post", lambda *a, **kw: _FalskSvar(GYLDIG_RESULTAT))

    forste = kjor.kjor(con, maks_kall=1, frisk_timer=None)
    assert forste["llm_kall"] == 1
    assert forste["stanset_av_budsjett"] == 2
    assert con.execute("SELECT COUNT(*) FROM klassifiseringer").fetchone()[0] == 1

    andre = kjor.kjor(con, maks_kall=10, frisk_timer=None)
    assert andre["llm_kall"] == 2
    assert con.execute("SELECT COUNT(*) FROM klassifiseringer").fetchone()[0] == 3


def test_ingen_kursdata_havner_i_llm_kallet(con, monkeypatch):
    """Look-ahead-forbudet, ende-til-ende: en kjent, søkbar sluttkurs ligger
    i databasen for samme selskap og dato som hendelsen. Den skal aldri
    dukke opp i det som faktisk sendes til Anthropic sitt API."""
    SLUTTKURS_SENTINEL = "918273.45"

    _sett_inn_nyhet(
        con, url="https://mfn.se/a/kongsberg/1", publisert="2024-05-01T08:00:00",
        slug="kongsberg-gruppen", tittel="Kongsberg tildelt kontrakt verdt 500 MNOK",
        yahoo="KOG.OL",
    )
    con.execute(
        "INSERT INTO kurser (yahoo, dato, open, high, low, close, volum) "
        "VALUES ('KOG.OL', '2024-05-01', 1.0, 1.0, 1.0, ?, 100)",
        (SLUTTKURS_SENTINEL,),
    )
    dedup.bygg_hendelser(con)
    monkeypatch.setattr(mfn, "hent_brodtekst", lambda url: "Kongsberg har inngått en ny avtale.")

    sendte_payloads = []

    def fake_post(url, headers, json, timeout):
        sendte_payloads.append(json)
        return _FalskSvar(GYLDIG_RESULTAT)

    monkeypatch.setattr(llm.requests, "post", fake_post)

    kjor.kjor(con, frisk_timer=None)

    assert sendte_payloads, "forventet at LLM-en faktisk ble kalt"
    hele_kallet = json.dumps(sendte_payloads)
    assert SLUTTKURS_SENTINEL not in hele_kallet
    assert "kurser" not in hele_kallet.lower()
