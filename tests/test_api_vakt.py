"""Betalt API: bare ferske meldinger, stopp når API-et er stengt (tom kreditt),
støytitler uten KI, og import av Ollama-klassifiseringer."""
from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest

from src import db
from src.config import TZ
from src.klassifisering import dedup, kjor, llm
from src.klassifisering.kategorier import PROMPT_VERSJON
from src.sources import mfn


@pytest.fixture
def con(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(llm, "ANTHROPIC_API_KEY", "test-nokkel")
    monkeypatch.setattr(mfn, "hent_brodtekst", lambda url: "Selskapet har signert en stor kontrakt.")
    with db.session() as c:
        yield c


def _nyhet(con, url, publisert, tittel="ABC: Signerer kontrakt med XYZ", slug="abc"):
    con.execute("INSERT INTO nyheter (url, publisert, hentet, selskap_slug, tittel, sprak) VALUES (?,?,?,?,?,?)",
                (url, publisert, publisert, slug, tittel, "no"))


def _na(timer_siden: float) -> str:
    return (datetime.now(TZ) - timedelta(hours=timer_siden)).replace(tzinfo=None).isoformat(timespec="seconds")


class _Stengt:
    status_code = 400
    text = '{"type":"error","error":{"type":"invalid_request_error","message":"Your credit balance is too low"}}'


def test_tom_kreditt_stopper_kallene_uten_aa_merke_meldingene(con, monkeypatch):
    _nyhet(con, "u1", _na(1))
    _nyhet(con, "u2", _na(2), tittel="DEF: Ny ordre", slug="def")
    dedup.bygg_hendelser(con)
    kall = []
    monkeypatch.setattr(llm.requests, "post", lambda *a, **k: kall.append(1) or _Stengt())
    r = kjor.kjor(con)
    assert r["api_stengt"] and len(kall) == 1                      # ett kall, så stopp
    assert con.execute("SELECT COUNT(*) FROM klassifiseringer").fetchone()[0] == 0  # prøves igjen senere


def test_gammelt_etterslep_gaar_ikke_til_betalt_api(con, monkeypatch):
    _nyhet(con, "gammel", _na(24 * 10))
    dedup.bygg_hendelser(con)
    monkeypatch.setattr(llm.requests, "post", lambda *a, **k: pytest.fail("skal ikke kalle API"))
    r = kjor.kjor(con)
    assert r["for_gamle"] == 1 and r["llm_kall"] == 0


def test_innside_stoytittel_og_stengt_api(con, monkeypatch):
    from src.innside import kjor as ekstraksjon
    from src.innside import llm as ilm

    _nyhet(con, "s1", _na(1), tittel="ABC: Mandatory notification of trade - allocation of shares under LTI")
    _nyhet(con, "s2", _na(1), tittel="DEF: Mandatory notification of trade", slug="def")
    monkeypatch.setattr(ilm, "tilgjengelig", lambda: True)
    monkeypatch.setattr(ilm, "ANTHROPIC_API_KEY", "x")
    monkeypatch.setattr(ilm.requests, "post", lambda *a, **k: _Stengt())
    r = ekstraksjon.kjor(con, hent_tekst=lambda url: "Uklar tekst uten tall.")
    assert r["stoy"] == 1 and r["api_stengt"] and r["trenger_llm"] == 1 and r["feilet"] == 0


def test_importer_ollama_klassifiseringer(con, tmp_path):
    from scripts import importer_tolkninger as imp

    _nyhet(con, "k1", _na(300))
    dedup.bygg_hendelser(con)
    hid = con.execute("SELECT id FROM hendelser").fetchone()[0]
    con.execute("INSERT INTO klassifiseringer (hendelse_id, prompt_versjon, modell, status, klassifisert_tidspunkt) "
                "VALUES (?,?,?,?,?)", (hid, PROMPT_VERSJON, "claude", "feilet", "x"))
    con.commit()
    fil = tmp_path / "kl.jsonl"
    res = {"kategori": "kontrakt_ordre", "retning": "positiv", "overraskelse": 3, "sikkerhet": 0.7,
           "begrunnelse": "Kontrakt.", "tallverdi": None, "valuta": None}
    fil.write_text(json.dumps({"url": "k1", "modell": "qwen2.5:7b", "resultat": res}) + "\n"
                   + json.dumps({"url": "ukjent", "resultat": res}) + "\n", encoding="utf-8")
    s = imp.kjor_klassifiseringer(fil)
    assert (s["lagt_inn"], s["ukjent_url"]) == (1, 1)
    rad = con.execute("SELECT modell, status, kategori FROM klassifiseringer WHERE hendelse_id = ?", (hid,)).fetchall()
    assert [tuple(r) for r in rad] == [("ollama:qwen2.5:7b", "ok", "kontrakt_ordre")]
    assert imp.kjor_klassifiseringer(fil)["fantes"] == 1
