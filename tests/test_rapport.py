"""Rapporttall fra Ollama: validering, avledede egenskaper, import og bruk i panelet."""
from __future__ import annotations

import json

import pytest

from src import db, rapport


def test_valider_og_avledet():
    r = rapport.valider({"periode": "Q3 2026", "valuta": "NOK", "omsetning": "1 520,0", "omsetning_fjor": 1310,
                         "driftsresultat": 182, "driftsresultat_fjor": 141, "eps": 1.12, "eps_fjor": "0,85",
                         "utsikter": "Hevet", "tone": "1"})
    assert r["omsetning"] == 1520.0 and r["eps_fjor"] == 0.85 and r["utsikter"] == "hevet" and r["tone"] == 1
    a = rapport.avledet(r)
    assert a["rapport_omsvekst"] == pytest.approx(1520 / 1310 - 1)
    assert a["rapport_marginendring"] == pytest.approx(182 / 1520 - 141 / 1310)
    assert a["rapport_epsvekst"] == pytest.approx((1.12 - 0.85) / 0.85)
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
    fil.write_text(json.dumps({"group_id": "g1", "slug": "a", "publisert": dag + "T18:00:00", "modell": "q",
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
