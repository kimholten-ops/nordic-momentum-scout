"""Tester for innsidemodulen. Ingen nettverk, ingen ekte LLM-kall."""
from __future__ import annotations

import inspect
import json
from pathlib import Path

import pytest

from src import db
from src.innside import kjor as ekstraksjon
from src.innside import llm, signal
from src.innside.deteksjon import er_innsidemelding, er_korreksjon
from src.innside.parser import parse_strukturert, rolle_fra_tekst, retning_fra_tekst

FIX = Path(__file__).parent / "fixtures"


@pytest.mark.parametrize("tittel", [
    "Kesko Oyj - Managers' Transactions (Ari Akseli)",
    "Multiconsult - Mandatory notification of trade - primary insider",
    "Lytix Biopharma ASA - Mandatory notification of trade by close associates to primary insiders",
    "Equinor ASA: Notifiable trading",
    "PDMR Transaction",
    "Trifork Group: Reporting of transactions made by persons discharging managerial responsibilities",
    "Meldepliktig handel for primærinnsider",
])
def test_deteksjon_treffer(tittel):
    assert er_innsidemelding(tittel)


@pytest.mark.parametrize("tittel", [
    "Equinor: Kvartalsrapport 3. kvartal", "Flaggningsmeddelande i Nordisk Bergteknik AB", "Innkalling til generalforsamling",
])
def test_deteksjon_ikke_treff(tittel):
    assert not er_innsidemelding(tittel)


def test_korreksjon():
    assert er_korreksjon("Correction: Mandatory notification of trade - primary insider")
    assert not er_korreksjon("Mandatory notification of trade - primary insider")


def test_parser_nasdaq_skjema():
    hs = parse_strukturert((FIX / "innside_kesko.txt").read_text())
    assert len(hs) == 3
    a, b, c = hs
    assert (a.person, a.rolle) == ("Ari Akseli", "ledelse")
    assert (a.retning, a.instrument, a.antall, a.pris, a.valuta) == ("tildeling", "aksje", 7000, 18.0, "EUR")
    assert (b.retning, b.instrument, b.antall) == ("salg", "opsjon_derivat", 5000)
    assert c.antall == 46958 and c.pris == 0.6375 and c.transaksjonsdato == "2026-11-16"


def test_parser_fritekst_gir_tom_liste():
    assert parse_strukturert((FIX / "innside_elopak.txt").read_text()) == []


@pytest.mark.parametrize("tekst,rolle,naer", [
    ("CEO", "ceo", False), ("Chief Financial Officer", "cfo", False), ("Chair of the Board", "styreleder", False),
    ("Member of the Board", "styremedlem", False), ("Other senior manager", "ledelse", False),
    ("Closely associated person to CEO", "ceo", True), ("administrerende direktør", "ceo", False),
])
def test_rolle(tekst, rolle, naer):
    assert rolle_fra_tekst(tekst) == (rolle, naer)


def test_retning():
    assert retning_fra_tekst("ACQUISITION") == "kjop"
    assert retning_fra_tekst("DISPOSAL") == "salg"
    assert retning_fra_tekst("SUBSCRIPTION") == "tildeling"
    assert retning_fra_tekst("LENDING") == "annet"


# --- LLM -----------------------------------------------------------------

def test_llm_prompt_har_ingen_plass_til_kursdata():
    assert set(inspect.signature(llm.bygg_prompt).parameters) == {"tittel", "brodtekst", "publisert"}


class _Svar:
    def __init__(self, tekst): self._t = tekst
    def raise_for_status(self): pass
    def json(self): return {"content": [{"type": "text", "text": self._t}]}


ELOPAK_SVAR = json.dumps({"handler": [{
    "person": "Bright Future AS", "rolle_tekst": "CEO Håkon Volldal (nærstående selskap)", "naerstaaende": True,
    "retning": "kjop", "instrument": "aksje", "antall": 27500, "pris": 35.8155, "valuta": "NOK", "dato": "2026-11-10",
}]})


def test_llm_trekk_ut_validerer(monkeypatch):
    monkeypatch.setattr(llm, "ANTHROPIC_API_KEY", "x")
    monkeypatch.setattr(llm.requests, "post", lambda *a, **k: _Svar(ELOPAK_SVAR))
    h = llm.trekk_ut("Elopak", "tekst", "2026-11-10T16:47:00")[0]
    assert (h.retning, h.rolle, h.naerstaaende, h.antall) == ("kjop", "ceo", True, 27500)
    assert h.verdi == pytest.approx(27500 * 35.8155, rel=1e-6)


def test_llm_ugyldig_retning_gir_feil_etter_to_forsok(monkeypatch):
    monkeypatch.setattr(llm, "ANTHROPIC_API_KEY", "x")
    kall = []
    def post(*a, **k):
        kall.append(1)
        return _Svar(json.dumps({"handler": [{"retning": "buy", "instrument": "aksje"}]}))
    monkeypatch.setattr(llm.requests, "post", post)
    with pytest.raises(llm.KlassifiseringsFeil):
        llm.trekk_ut("t", "b", "2026-11-10T00:00:00")
    assert len(kall) == 2


# --- Pipeline ------------------------------------------------------------

@pytest.fixture
def con(tmp_path, monkeypatch):
    p = tmp_path / "t.db"
    db.init(p)
    c = db.connect(p)
    yield c
    c.close()


def _nyhet(con, slug, tittel, publisert, brodtekst, url=None):
    cur = con.execute(
        "INSERT INTO nyheter (url, publisert, hentet, selskap_slug, selskap_navn, tittel, brodtekst) VALUES (?,?,?,?,?,?,?)",
        (url or f"https://mfn.se/x/{slug}/{publisert}{tittel}", publisert, "nå", slug, slug.title(), tittel, brodtekst),
    )
    con.commit()
    return cur.lastrowid


def _kjop_tekst(person, rolle, antall, pris, dato):
    return (
        f"Person subject to the notification requirement\nName: {person}\nPosition: {rolle}\n"
        f"____________________________________________\nTransaction date: {dato}\n"
        f"Venue: OSLO BORS (XOSL)\nInstrument type: SHARE\nNature of transaction: ACQUISITION\n"
        f"Aggregated transactions (1):\nVolume: {antall} Volume weighted average price: {pris} NOK\n"
    )


def test_kjor_regel_og_llm_og_ingen_dobbeltbehandling(con, monkeypatch):
    _nyhet(con, "kesko", "Kesko Oyj - Managers' Transactions", "2026-11-18T14:20:00", (FIX / "innside_kesko.txt").read_text())
    _nyhet(con, "elopak", "Elopak: Mandatory notification of trade by primary insider", "2026-11-10T16:47:00", (FIX / "innside_elopak.txt").read_text())
    _nyhet(con, "equinor", "Equinor: Kvartalsrapport", "2026-11-10T08:00:00", "ikke innside")
    monkeypatch.setattr(ekstraksjon, "ANTHROPIC_API_KEY", "x")
    monkeypatch.setattr(llm, "ANTHROPIC_API_KEY", "x")
    monkeypatch.setattr(llm.requests, "post", lambda *a, **k: _Svar(ELOPAK_SVAR))

    r = ekstraksjon.kjor(con)
    assert (r["vurdert"], r["regel"], r["llm"]) == (2, 1, 1)
    assert con.execute("SELECT COUNT(*) FROM innsidehandler").fetchone()[0] == 4
    # andre kjøring gjør ingenting
    assert ekstraksjon.kjor(con)["vurdert"] == 0


def test_uten_api_noekkel_markeres_trenger_llm_og_tas_opp_igjen(con, monkeypatch):
    _nyhet(con, "elopak", "Elopak: Mandatory notification of trade", "2026-11-10T16:47:00", (FIX / "innside_elopak.txt").read_text())
    monkeypatch.setattr(ekstraksjon, "ANTHROPIC_API_KEY", "")
    assert ekstraksjon.kjor(con)["trenger_llm"] == 1
    assert ekstraksjon.kjor(con)["vurdert"] == 1  # ikke ferdig, vurderes på nytt


def test_korreksjon_erstatter_original_og_duplikat_telles_en_gang(con, monkeypatch):
    monkeypatch.setattr(ekstraksjon, "ANTHROPIC_API_KEY", "")
    t1 = _kjop_tekst("Anna Berg", "CFO", 1000, 50, "2026-11-17")
    t2 = _kjop_tekst("Anna Berg", "CFO", 2000, 50, "2026-11-17")
    _nyhet(con, "multiconsult", "Mandatory notification of trade - primary insider", "2026-11-18T13:14:42", t1, "u1")
    _nyhet(con, "multiconsult", "Mandatory notification of trade - primary insider", "2026-11-18T13:14:59", t1, "u2")  # annen distributør
    _nyhet(con, "multiconsult", "Correction: Mandatory notification of trade - primary insider", "2026-11-18T13:26:44", t2, "u3")
    ekstraksjon.kjor(con)
    gyldige = con.execute("SELECT antall FROM innsidehandler WHERE gyldig = 1").fetchall()
    assert [r["antall"] for r in gyldige] == [2000]


# --- Signal --------------------------------------------------------------

def test_signal_ceo_kjop_over_terskel_og_salg_ignoreres(con, monkeypatch):
    monkeypatch.setattr(ekstraksjon, "ANTHROPIC_API_KEY", "")
    _nyhet(con, "abc", "Mandatory notification of trade", "2026-11-10T09:00:00", _kjop_tekst("Per Hansen", "CEO", 20000, 50, "2026-11-09"))
    salg = _kjop_tekst("Kari Nord", "CFO", 90000, 50, "2026-11-09").replace("ACQUISITION", "DISPOSAL")
    _nyhet(con, "xyz", "Mandatory notification of trade", "2026-11-10T09:05:00", salg)
    ekstraksjon.kjor(con)
    assert signal.bygg_signaler(con) == 1
    s = con.execute("SELECT * FROM innside_signaler").fetchone()
    assert s["selskap_slug"] == "abc" and s["score"] >= signal.TERSKEL and s["antall_personer"] == 1


def test_signal_klynge_og_ikke_varsle_samme_score_to_ganger(con, monkeypatch):
    monkeypatch.setattr(ekstraksjon, "ANTHROPIC_API_KEY", "")
    _nyhet(con, "abc", "Mandatory notification of trade", "2026-11-10T09:00:00", _kjop_tekst("A", "Member of the Board", 2000, 50, "2026-11-09"))
    _nyhet(con, "abc", "Mandatory notification of trade", "2026-11-12T09:00:00", _kjop_tekst("B", "Member of the Board", 3000, 50, "2026-11-11"))
    _nyhet(con, "abc", "Mandatory notification of trade", "2026-11-13T09:00:00", _kjop_tekst("C", "Member of the Board", 2000, 50, "2026-11-12"))
    ekstraksjon.kjor(con)
    signal.bygg_signaler(con)
    ny = signal.ikke_varslede(con)
    assert ny, "klynge på tre styremedlemmer skal gi minst ett signal"
    for s in ny:
        con.execute("UPDATE innside_signaler SET varslet = 1 WHERE id = ?", (s["id"],))
    con.commit()
    signal.bygg_signaler(con)  # idempotent
    assert signal.ikke_varslede(con) == []
    assert "Innsidekjøp" in signal.formater_varsel(con, ny[-1])


def test_signal_bruker_ikke_kursdata():
    import re
    kilde = inspect.getsource(signal)
    assert not re.search(r"(?:FROM|JOIN)\s+kurser", kilde, re.I)


# --- Backtest og mapping ---------------------------------------------------

def test_backtest_inngang_er_dagen_etter_publisering(con, monkeypatch):
    from src import innside_backtest as bt
    monkeypatch.setattr(bt, "KOSTNAD", 0.0)
    kurs = (["2026-11-09", "2026-11-10", "2026-11-11", "2026-11-12"],
            {"2026-11-09": (90, 91), "2026-11-10": (100, 101), "2026-11-11": (110, 120), "2026-11-12": (120, 130)})
    # publisert 10. nov (midt på dagen): inngang = åpning 11. nov (110), IKKE 10. nov
    assert bt.avkastning(kurs, "2026-11-10T09:00:00", 1) == pytest.approx(130 / 110 - 1)
    assert bt.avkastning(kurs, "2026-11-10T09:00:00", 5) is None  # for kort historikk, ikke imputert


def test_backtest_kjor_rapporterer_manglende_kurs(con, monkeypatch):
    from src import innside_backtest as bt
    monkeypatch.setattr(ekstraksjon, "ANTHROPIC_API_KEY", "")
    _nyhet(con, "abc", "Mandatory notification of trade", "2026-11-10T09:00:00", _kjop_tekst("Per", "CEO", 20000, 50, "2026-11-09"))
    ekstraksjon.kjor(con)
    r = bt.kjor(con)
    assert r["kjop_totalt"] == 1 and r["uten_kursdata"] == 1


def test_map_velg_krever_nordisk_suffiks_og_navnetreff():
    from scripts.map_innside import velg
    kand = [{"symbol": "3MC.SG", "shortname": "Multiconsult ASA"}, {"symbol": "MULTI.OL", "shortname": "MULTICONSULT"}]
    k, sikker = velg("Multiconsult ASA", kand)
    assert k["symbol"] == "MULTI.OL" and sikker
    k, sikker = velg("Helt Annet Navn", [{"symbol": "XYZ.OL", "shortname": "Noe Annet"}])
    assert k["symbol"] == "XYZ.OL" and not sikker
    assert velg("Kesko", [{"symbol": "KESKOBU0126.HE", "shortname": "Kesko Oyj MO 26"}])[0] is None
