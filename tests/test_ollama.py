"""Ollama-flyten: køen på PC-en og importen i GitHub."""
from __future__ import annotations

import json

import pytest

from src import db
from src.innside.kjor import EKSTRAKSJON_VERSJON


@pytest.fixture
def con(tmp_path, monkeypatch):
    p = tmp_path / "t.db"
    monkeypatch.setattr(db, "DB_PATH", p, raising=False)
    db.init(p)
    c = db.connect(p)
    yield c, p
    c.close()


def _venter(c, url, slug, tittel, publisert, sprak="en", yahoo="A.OL"):
    cur = c.execute(
        "INSERT INTO nyheter (url, publisert, hentet, selskap_slug, selskap_navn, tittel, sprak, ticker, yahoo) "
        "VALUES (?,?,?,?,?,?,?,?,?)", (url, publisert, "nå", slug, slug.upper(), tittel, sprak, "A", yahoo))
    c.execute("INSERT INTO innside_behandlet VALUES (?,?,?,?,?)", (cur.lastrowid, EKSTRAKSJON_VERSJON, "trenger_llm", "", "nå"))
    c.commit()
    return cur.lastrowid


def test_ko_filtrerer_stoy_og_duplikater_paa_spraak(con):
    from scripts.ollama_tolk import ko
    c, _ = con
    _venter(c, "u1", "abc", "Indberetning af ledende medarbejderes transaktioner", "2026-11-10T09:00:00", "da")
    _venter(c, "u2", "abc", "Notification of managers' transactions", "2026-11-10T09:01:00", "en")
    _venter(c, "u3", "abc", "Allocation of shares under the share savings programme", "2026-11-11T09:00:00")
    _venter(c, "u4", "xyz", "Mandatory notification of trade", "2026-11-12T09:00:00")
    assert sorted(r["url"] for r in ko(c)) == ["u2", "u4"]


def test_import_legger_inn_handler_og_utvider_universet(con, tmp_path, monkeypatch):
    from scripts import importer_tolkninger as imp
    from src.innside import arkiv
    c, p = con
    csv = tmp_path / "tickers.csv"
    monkeypatch.setattr(arkiv, "UNIVERSE_CSV", csv)
    monkeypatch.setattr("src.universe.UNIVERSE_CSV", csv)
    n1 = _venter(c, "u1", "nyco", "Mandatory notification of trade", "2026-11-10T09:00:00", yahoo="NYCO.OL")
    n2 = _venter(c, "u2", "abc", "Mandatory notification of trade", "2026-11-10T10:00:00")
    _venter(c, "u3", "abc", "Mandatory notification of trade", "2026-11-10T11:00:00")
    kjop = {"person": "Kari", "rolle_tekst": "CEO", "rolle": "ceo", "naerstaaende": False, "retning": "kjop",
            "instrument": "aksje", "antall": 1000, "pris": 10.0, "valuta": "NOK", "transaksjonsdato": "2026-11-09",
            "beholdning_etter": 5000}
    fil = tmp_path / "t.jsonl"
    fil.write_text("\n".join(json.dumps(x) for x in [
        {"url": "u1", "modell": "m", "handler": [kjop]},
        {"url": "u2", "modell": "m", "handler": []},
        {"url": "u3", "modell": "m", "feil": "timeout"},
        {"url": "ukjent", "modell": "m", "handler": []},
    ]), encoding="utf-8")
    c.close()
    monkeypatch.setenv("MOMENTUM_DB", str(p))
    s = imp.kjor(fil)
    assert (s["lagt_inn"], s["ingen_handler"], s["feil"], s["ukjent_url"], s["nye_selskaper"]) == (1, 1, 1, 1, 1)
    c2 = db.connect(p)
    status = dict(c2.execute("SELECT nyhet_id, status FROM innside_behandlet").fetchall())
    assert status[n1] == "ok" and status[n2] == "ingen_handler"
    assert c2.execute("SELECT kilde, antall, beholdning_etter FROM innsidehandler").fetchone()[:] == ("ollama", 1000, 5000)
    assert "nyco,NYCO,A,NYCO.OL,Oslo Børs,1" in csv.read_text(encoding="utf-8")
    assert imp.kjor(fil)["fantes"] == 2   # idempotent
