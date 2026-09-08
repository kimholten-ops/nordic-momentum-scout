"""Bekrefter at poll_news lagrer nye rader, og at en ny polling av samme
feed ikke gir duplikater. Ingen nettverkskall: mfn.hent() erstattes med
parsing av den lokale fixturen.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from src import db, poll_news
from src.sources import mfn

FIXTURE = Path(__file__).parent / "fixtures" / "mfn_feed.html"


@pytest.fixture
def _isolert_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")


@pytest.fixture
def _fast_feed(monkeypatch):
    html_tekst = FIXTURE.read_text(encoding="utf-8")
    monkeypatch.setattr(mfn, "hent", lambda feed=None: mfn.parse(html_tekst))


def test_forste_polling_lagrer_nye_rader(_isolert_db, _fast_feed):
    nye = poll_news.kjor()
    assert nye == 2

    with db.session() as con:
        assert con.execute("SELECT COUNT(*) FROM nyheter").fetchone()[0] == 2


def test_andre_polling_gir_null_nye_rader(_isolert_db, _fast_feed):
    forste = poll_news.kjor()
    andre = poll_news.kjor()

    assert forste == 2
    assert andre == 0

    with db.session() as con:
        assert con.execute("SELECT COUNT(*) FROM nyheter").fetchone()[0] == 2
