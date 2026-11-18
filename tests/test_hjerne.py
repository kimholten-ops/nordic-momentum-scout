"""Hjernen: lærer den et ekte mønster, og ser den aldri inn i fremtiden?"""
from __future__ import annotations

import random
from datetime import date, timedelta

import pytest

from src import marked
from src.hjerne import modell, simuler
from src.hjerne.hendelser import Hendelse


def _dager(n: int, start=date(2026, 1, 5)) -> list[str]:
    ut, d = [], start
    while len(ut) < n:
        if d.weekday() < 5:
            ut.append(d.isoformat())
        d += timedelta(days=1)
    return ut


def _serie(datoer, close):
    return marked.Serie(list(datoer), list(close), [c * 1.01 for c in close], [c * 0.99 for c in close],
                        list(close), [10_000_000] * len(close))


def _verden(antall=120, horisont=5, seed=1):
    """Flat indeks. Aksjer der hendelser med 'signal=ja' stiger 6 % etter
    inngang og 'signal=nei' faller 6 %."""
    rng = random.Random(seed)
    dager = _dager(400)
    kurser = {"OSEBX.OL": _serie(dager, [1000.0] * len(dager))}
    hendelser = []
    for k in range(antall):
        sym = f"A{k}.OL"
        pub_i = 40 + k * 2
        ja = rng.random() < 0.5
        close = [100.0] * len(dager)
        for j in range(pub_i + 1, len(dager)):
            close[j] = 106.0 if ja else 94.0
        s = _serie(dager, close)
        s.open[pub_i + 1] = 100.0  # inngang på åpning dagen etter
        kurser[sym] = s
        hendelser.append(Hendelse(f"h{k}", "innside", f"a{k}", f"A{k}", sym, dager[pub_i] + "T12:00:00",
                                  {"innside.signal": "ja" if ja else "nei"}))
    return hendelser, kurser


def test_modell_krymper_tynne_celler():
    obs = [modell.Observasjon({"f": "a"}, 0.10, "2026-01-05")] * 2 + [modell.Observasjon({"f": "b"}, 0.0, "2026-01-05")] * 50
    m = modell.tren(obs, "2026-01-06", prior=15, halveringstid=1e9)
    assert m.effekter["f"]["a"] < 0.03  # to observasjoner på +10 % er ikke bevis


def test_tidsvekt_gir_nyere_utfall_mer_vekt():
    obs = [modell.Observasjon({"f": "a"}, -0.05, "2026-01-01")] * 30 + [modell.Observasjon({"f": "a"}, 0.05, "2026-06-01")] * 30
    m = modell.tren(obs, "2026-06-02", prior=1, halveringstid=30)
    assert m.forutsi({"f": "a"})[0] > 0.03


def test_hjernen_laerer_ekte_moenster_og_handler_bare_paa_det():
    hendelser, kurser = _verden()
    res = simuler.kjor(hendelser, kurser, simuler.Param(horisont=5, prior=5, min_edge=0.01, min_sikkerhet=0.6,
                                                        maks_apne=50, halveringstid=1e9))
    assert res.trades, "skal handle når mønsteret er lært"
    valgte = {t.nokkel for t in res.trades}
    assert all(h.egenskaper["innside.signal"] == "ja" for h in hendelser if h.nokkel in valgte)
    k = simuler.kalibrering(res.prediksjoner)
    assert k["rangkorrelasjon"] > 0.5
    assert res.modell.effekter["innside.signal"]["ja"] > 0.03


def test_ingen_lekkasje_trening_bare_paa_kjente_utfall():
    hendelser, kurser = _verden(antall=30)
    p = simuler.Param(horisont=5, prior=5, min_edge=0.01, maks_apne=50, halveringstid=1e9)
    res = simuler.kjor(hendelser, kurser, p)
    for r in res.prediksjoner:
        # treningsobservasjoner må ha utgangsdag strengt før denne inngangsdagen
        kjente = sum(
            1 for h in hendelser
            if (u := marked.utfall(kurser, h.yahoo, h.publisert, p.horisont)) and u.ut_dato < r["inn_dato"]
        )
        assert r["trent_n"] == kjente
    # første hendelse har ingenting å lære av
    assert res.prediksjoner[0]["trent_n"] == 0 and res.prediksjoner[0]["valgt"] == 0


def test_hendelse_eldre_enn_kurshistorikk_hoppes_over():
    dager = _dager(30)
    kurser = {"X.OL": _serie(dager, [100.0] * 30)}
    h = Hendelse("gammel", "innside", "x", "X", "X.OL", "2019-03-01T10:00:00", {})
    assert marked.utfall(kurser, "X.OL", h.publisert, 5) is None
    res = simuler.kjor([h], kurser)
    assert res.prediksjoner == [] and res.hoppet_over == 1


def test_stopp_utloses_og_forslag_har_inn_stopp_utgang():
    hendelser, kurser = _verden(antall=60)
    # siste hendelse: fall rett etter inngang, men med signal=ja
    sym = hendelser[-1].yahoo
    s = kurser[sym]
    i = s.indeks_etter(hendelser[-1].publisert[:10])
    s.low[i + 1] = 50.0
    res = simuler.kjor(hendelser, kurser, simuler.Param(horisont=5, prior=5, min_edge=0.01, maks_apne=50,
                                                        halveringstid=1e9))
    t = next((t for t in res.trades if t.yahoo == sym), None)
    assert t is not None, "signal=ja skal handles"
    assert t.status == "stoppet" and t.ut <= t.stopp
    # en fersk hendelse uten inngangsdag blir forslag
    siste = kurser["OSEBX.OL"].datoer[-1]
    ny = Hendelse("ny", "innside", "a0", "A0", hendelser[0].yahoo, siste + "T18:00:00", {"innside.signal": "ja"})
    res = simuler.kjor(hendelser + [ny], kurser, simuler.Param(horisont=5, prior=5, min_edge=0.01, maks_apne=50,
                                                               halveringstid=1e9))
    f = next(f for f in res.forslag if f["nokkel"] == "ny")
    assert f["anbefalt"] == 1 and f["stopp"] < f["limit"] and f["ut_dato"] > f["inn_dato"] > siste


def test_kandidater_rangeres_med_inngang_stopp_og_to_horisonter():
    from src.hjerne import kandidater as kand

    hendelser, kurser = _verden(antall=80)
    siste = kurser["OSEBX.OL"].datoer[-1]
    nye = [
        Hendelse("ja1", "innside", "a0", "A0", hendelser[0].yahoo, siste + "T18:00:00", {"innside.signal": "ja"}),
        Hendelse("nei1", "innside", "a1", "A1", hendelser[1].yahoo, siste + "T18:00:00", {"innside.signal": "nei"}),
        Hendelse("ja0", "innside", "a0", "A0", hendelser[0].yahoo, siste + "T09:00:00", {"innside.signal": "ja"}),
    ]
    pk = simuler.Param(horisont=5, prior=5, min_edge=0.01, maks_apne=50, halveringstid=1e9)
    pl = simuler.Param(horisont=10, prior=5, min_edge=0.01, maks_apne=50, halveringstid=1e9)
    alle = hendelser + nye
    mk = simuler.kjor(alle, kurser, pk).modell
    ml = simuler.kjor(alle, kurser, pl).modell
    ut = kand.ranger(alle, kurser, mk, ml, pk, pl)
    assert [c["slug"] for c in ut].count("a0") == 1  # én rad per selskap
    a0 = next(c for c in ut if c["slug"] == "a0")
    assert a0["hendelser_i_perioden"] == 2 and a0["nokkel"] == "ja1"
    assert ut[0]["slug"] == "a0" and ut[0]["anbefaling"] == "kjøp" and ut[0]["rang"] == 1
    nei = next(c for c in ut if c["slug"] == "a1")
    assert nei["anbefaling"] == "unngå" and nei["score"] < a0["score"]
    for c in (a0, nei):
        assert c["stopp"] < c["limit"] < c["break_even"]
        assert 0 <= c["kort"]["p_gevinst"] <= 1 and c["lang"]["dager"] == 10
        assert c["lang"]["ut_dato"] > c["kort"]["ut_dato"] > c["inn_dato"]
    assert a0["kort"]["maal"] > a0["break_even"]  # positiv forventning => forventet kurs over break-even
