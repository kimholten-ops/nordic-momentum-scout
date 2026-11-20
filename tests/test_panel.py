"""Faktorpanelet: ingen fremtidsinformasjon i egenskapene, riktig fasit, og at
modellen kan trenes, testes og scores ende til ende på syntetiske data."""
from __future__ import annotations

import math
from datetime import date, timedelta

import numpy as np
import pytest

from src import marked
from src.panel import bygg, modell


def _serie(n, start=100.0, trend=0.0, seed=0):
    rng = np.random.default_rng(seed)
    d0 = date(2023, 1, 2)
    datoer, d = [], d0
    while len(datoer) < n:
        if d.weekday() < 5:
            datoer.append(d.isoformat())
        d += timedelta(days=1)
    c = start * np.exp(np.cumsum(rng.normal(trend, 0.02, n)))
    s = marked.Serie(datoer, list(c * 0.999), list(c * 1.01), list(c * 0.99), list(c), [100_000] * n)
    s.utbytte = [None] * n
    return s


def _data(n=420):
    kurser = {"A.OL": _serie(n, seed=1), "B.OL": _serie(n, trend=0.001, seed=2), "OSEBX.OL": _serie(n, seed=3)}
    innside = {"a": [(kurser["A.OL"].datoer[300] + "T18:00:00", "kjop", 1e6, "P")]}
    return {"kurser": kurser, "slug": {"A.OL": "a", "B.OL": "b"}, "slugs_for": {"A.OL": {"a"}, "B.OL": {"b"}},
            "hend": {}, "innside": innside, "isin": {}, "short": {}, "info": {}, "usa": {}, "short_start": None}


def test_panel_uten_fremtidsinformasjon_og_riktig_fasit():
    data = _data()
    df = bygg.bygg(data)
    a = df[df.yahoo == "A.OL"].set_index("dato")
    s = data["kurser"]["A.OL"]
    d = a.index[5]
    t = s.indeks_til_og_med(d)
    assert math.isclose(a.loc[d, "mom_1m"], s.close[t] / s.close[t - 21] - 1)
    # Fasit: kjøp på åpning t+1, salg på slutt t+20, minus indeksen.
    idx = data["kurser"]["OSEBX.OL"]
    forventet = (s.close[t + 20] / s.open[t + 1] - 1) - (idx.close[t + 20] / idx.close[t] - 1)
    assert math.isclose(a.loc[d, "fasit_mer"], forventet, rel_tol=1e-9)
    # Innsidekjøpet publisert kl. 18 dag 300 er ukjent ved sluttkurs samme dag.
    d300 = s.datoer[300]
    if d300 in a.index:
        assert a.loc[d300, "innside_kjop_90"] == 0
    etter = [x for x in a.index if x > d300][:1]
    assert a.loc[etter[0], "innside_kjop_90"] == 1
    # Siste uke har ingen fasit ennå, men scores.
    assert np.isnan(a.iloc[-1]["fasit_mer"])


def test_modell_ende_til_ende():
    pytest.importorskip("lightgbm")
    import pandas as pd

    rng = np.random.default_rng(0)
    rader = []
    datoer = pd.date_range("2023-07-07", "2025-06-27", freq="W-FRI").strftime("%Y-%m-%d")
    for i, d in enumerate(datoer):
        for j in range(60):
            x = rng.normal()
            rader.append({"yahoo": f"S{j}.OL", "dato": d, "marked": "OL", "sektor": "x", "mom_12_1": x,
                          "vol_60": rng.normal(), "kostnad": 0.01,
                          "fasit_mer": 0.02 * x + rng.normal(0, 0.05) if i < len(datoer) - 3 else np.nan,
                          "fasit_netto": np.nan,
                          "ut_dato": datoer[min(i + 4, len(datoer) - 1)] if i < len(datoer) - 3 else None})
    df = modell.forbered(pd.DataFrame(rader).astype({"marked": "category", "sektor": "category"}))
    df["fasit_netto"] = df["fasit_mer"] - 0.01
    eg = modell.egenskaper(df)
    ev = modell.evaluer(modell.walk_forward(df, eg, test_fra="2024-07-01"))
    assert ev["ic"] > 0.1 and len(ev["tideler"]) == 10
    sc = modell.score(df, eg, ev["tideler"])
    assert len(sc) == 60 and sc["persentil"].between(0, 1).all()
    beste = sc.sort_values("persentil").iloc[-1]
    assert beste["opp"] and beste["opp"][0]["navn"] == "Momentum 12 mnd"
