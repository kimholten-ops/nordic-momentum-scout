"""Rapporttall fra Ollama: validering, avledede egenskaper, import og bruk i panelet."""
from __future__ import annotations

import json

import pytest

from src import db, rapport


def test_valider_og_avledet():
    r = rapport.valider({"periode": "Q3 2026", "valuta": "NOK", "omsetning": "1 640,0", "omsetning_fjor": 1400,
                         "driftsresultat": 190, "driftsresultat_fjor": 150, "eps": 1.3, "eps_fjor": "0,9",
                         "utsikter": "Hevet", "tone": "1"})
    assert r["omsetning"] == 1640.0 and r["eps_fjor"] == 0.9 and r["utsikter"] == "hevet" and r["tone"] == 1
    a = rapport.avledet(r)
    assert a["rapport_omsvekst"] == pytest.approx(1640 / 1400 - 1)
    assert a["rapport_marginendring"] == pytest.approx(190 / 1640 - 150 / 1400)
    assert a["rapport_epsvekst"] == pytest.approx((1.3 - 0.9) / 0.9)
    assert a["rapport_utsikter"] == 1


def test_valider_avviser_tomt_og_rydder_urimelig():
    with pytest.raises(ValueError):
        rapport.valider({"periode": "Q1", "utsikter": "ingen"})
    a = rapport.avledet(rapport.valider({"omsetning": 100, "omsetning_fjor": 1, "tone": 9, "utsikter": "noe rart"}))
    assert a["rapport_omsvekst"] is None          # 9 900 % vekst = trolig enhetsfeil
    assert a["rapport_tone"] == 2 and a["rapport_utsikter"] is None


def test_import_og_panel_bruker_bare_kjente_rapporter(tmp_path, monkeypatch):
    from scripts import importer_tolkninger as imp
    from src.panel import bygg
    from tests.test_panel import _data

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    data = _data()
    s = data["kurser"]["A.OL"]
    dag = s.datoer[320]
    fil = tmp_path / "r.jsonl"
    res = {"periode": "Q2 2024", "omsetning": 120, "omsetning_fjor": 100, "driftsresultat": 12, "driftsresultat_fjor": 8,
           "utsikter": "senket", "tone": -1}
    fil.write_text(json.dumps({"group_id": "g1", "slug": "a", "publisert": dag + "T18:00:00", "modell": "q", "versjon": rapport.VERSJON,
                               "resultat": res}) + "\n" + json.dumps({"group_id": "g2", "feil": "x"}) + "\n", encoding="utf-8")
    assert imp.kjor_rapporter(fil) == {"lest": 2, "lagt_inn": 1, "fantes": 0, "feil": 1}
    with db.session() as con:
        rader = con.execute("SELECT * FROM rapporttall").fetchall()
        data["rapporter"] = {"a": [(r["publisert"], rapport.avledet(dict(r))) for r in rader]}
    df = bygg.bygg(data).set_index(["yahoo", "dato"])
    a = df.loc["A.OL"]
    etter = [d for d in a.index if d > dag][0]
    assert a.loc[etter, "rapport_omsvekst"] == pytest.approx(0.2) and a.loc[etter, "rapport_utsikter"] == -1
    if dag in a.index:  # publisert kl. 18: ukjent ved sluttkurs samme dag
        assert "rapport_omsvekst" not in a.columns or a.loc[dag, "rapport_omsvekst"] != a.loc[dag, "rapport_omsvekst"]


def test_oppgitt_vekst_brukes_naar_belop_mangler():
    a = rapport.avledet(rapport.valider({"omsetningsvekst_pst": 28, "utsikter": "ingen", "tone": 1}))
    assert a["rapport_omsvekst"] == pytest.approx(0.28)


def test_r1_lekkasje_og_nuller_fjernes():
    r = rapport.valider({"eps": 1.12, "eps_fjor": 0.85, "omsetning": 0, "omsetning_fjor": 0, "driftsresultat": 50,
                         "driftsresultat_fjor": 40, "omsetningsvekst_pst": 16, "utsikter": "uendret", "tone": 1})
    assert r["eps"] is None and r["eps_fjor"] is None and r["omsetning"] is None and r["omsetningsvekst_pst"] is None
    assert r["driftsresultat"] == 50
