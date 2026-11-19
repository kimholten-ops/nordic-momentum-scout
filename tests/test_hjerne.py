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
        # Aldri mer enn det som var kjent; høyst en uke gammel trening.
        assert r["trent_n"] <= kjente
        uke_for = (date.fromisoformat(r["inn_dato"]) - timedelta(days=simuler.RETREN_DAGER)).isoformat()
        kjent_uke_for = sum(
            1 for h in hendelser
            if (u := marked.utfall(kurser, h.yahoo, h.publisert, p.horisont)) and u.ut_dato < uke_for
        )
        assert r["trent_n"] >= kjent_uke_for
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


def test_posisjonsstorrelse_risiko_spredning_og_likviditet():
    p = simuler.Param(kapital=100_000, maks_andel=0.15, risiko=0.01, likviditet_tak=0.05)
    assert p.storrelse(0.04, None) == 15_000          # spredning binder: 15 % av kapitalen
    assert p.storrelse(0.10, None) == 10_000          # risiko binder: 1 % / 10 % stopp
    assert p.storrelse(0.04, 100_000) == 5_000        # likviditet binder: 5 % av dagsomsetning
    assert p.storrelse(0.10, None, kapital=50_000) == 5_000  # følger kapitalen


def test_papirhandler_bruker_ledig_kontant():
    hendelser, kurser = _verden(antall=80)
    p = simuler.Param(horisont=5, prior=5, min_edge=0.01, maks_apne=50, halveringstid=1e9, kapital=20_000,
                      maks_andel=0.5, risiko=1.0, min_posisjon=3000)
    res = simuler.kjor(hendelser, kurser, p)
    assert res.trades
    for dag, verdi in res.egenkapital:
        apne = [t for t in res.trades if t.inn_dato <= dag and (t.status == "åpen" or t.ut_dato > dag)]
        assert sum(t.belop for t in apne) <= verdi * 1.07  # aldri mer investert enn porteføljen er verdt


def test_kursrader_uten_sluttkurs_ignoreres_og_overskriver_ikke(tmp_path, monkeypatch):
    """Regresjon 19. nov: Yahoo leverte 18. nov uten close; INSERT OR REPLACE
    slettet gode tall, og hjernen fant 0 kandidater."""
    import pandas as pd
    from src import db
    from src.sources import prices

    p = tmp_path / "k.db"
    db.init(p)
    con = db.connect(p)
    con.execute("INSERT INTO kurser (yahoo,dato,open,high,low,close,volum) VALUES ('X.OL','2026-11-17',10,11,9,10.5,100)")
    con.execute("INSERT INTO kurser (yahoo,dato,open,high,low,close,volum) VALUES ('X.OL','2026-11-18',10.5,11,10,10.8,100)")
    df = pd.DataFrame({"Open": [10.5, 11.0], "High": [11, 11.5], "Low": [10, 10.9], "Close": [float("nan"), float("nan")],
                       "Volume": [100, 50]}, index=pd.to_datetime(["2026-11-18", "2026-11-19"]))

    class YF:
        @staticmethod
        def download(*a, **k): return df
    import sys
    monkeypatch.setitem(sys.modules, "yfinance", YF)
    prices._last_ned(con, ["X.OL"], "2026-11-10")
    rader = con.execute("SELECT dato, close FROM kurser WHERE yahoo='X.OL' ORDER BY dato").fetchall()
    assert [tuple(r) for r in rader] == [("2026-11-17", 10.5), ("2026-11-18", 10.8)]

    con.execute("INSERT INTO kurser (yahoo,dato,open,high,low,close,volum) VALUES ('Y.OL','2026-11-18',5,5,5,NULL,1)")  # gammel dårlig rad
    con.execute("INSERT INTO kurser (yahoo,dato,open,high,low,close,volum) VALUES ('Y.OL','2026-11-17',5,5,5,5,1)")
    k = marked.last_kurser(con)
    assert k["Y.OL"].datoer == ["2026-11-17"] and k["Y.OL"].close[-1] == 5


def test_nye_kursegenskaper_bruker_bare_data_foer_inngang():
    from src.hjerne.hendelser import kursegenskaper

    dager = _dager(300)
    close = [100.0] * 299 + [100.0]
    close[260] = 108.0                       # 52-ukers topp (106 er innen 5 %)
    aksje = _serie(dager, close)
    pub = dager[280]
    aksje.close[280] = 106.0                 # reaksjon +6 % på meldingsdagen
    aksje.volum[280] = 50_000_000            # 5x normalt
    idx = _serie(dager, [1000.0 + k for k in range(300)])  # jevnt stigende marked
    k = {"A.OL": aksje, "OSEBX.OL": idx}
    eg = kursegenskaper(k, "A.OL", pub + "T10:00:00")
    assert eg["reaksjon"] == "opp >3%" and eg["volum"] == "høyt (3x+)"
    assert eg["regime"] == "stigende marked" and eg["uke52"] == "nær toppen" and eg["trend6m"] == "flat"
    # Samme melding etter stengetid: reaksjonen er ikke kjent før inngangen.
    eg2 = kursegenskaper(k, "A.OL", pub + "T18:30:00")
    assert eg2["reaksjon"] == "etter børstid" and eg2["volum"] == "etter børstid"
    # Fremtidige kurser påvirker ikke egenskapene.
    aksje.close[290] = 1.0
    assert kursegenskaper(k, "A.OL", pub + "T10:00:00") == eg


def test_innsideparametere_rutine_historikk_kurs_og_flyt(tmp_path):
    """Rutine (samme måned tidligere år), personens historikk (kun kjente
    utfall), kurs mot innsiders pris og netto innsideflyt siste 90 dager."""
    from src import db
    from src.hjerne.hendelser import innsidehendelser

    p = tmp_path / "t.db"
    db.init(p)
    con = db.connect(p)
    con.execute("INSERT INTO selskaper (slug, navn, ticker, yahoo, marked, aktiv) VALUES ('abc','ABC','ABC','ABC.OL','Oslo',1)")
    dager = _dager(520, start=date(2024, 1, 1))
    serie = _serie(dager, [100.0] * len(dager))
    kurser = {"ABC.OL": serie, "OSEBX.OL": _serie(dager, [1000.0] * len(dager))}

    def handel(nid, dag, person, retning="kjop", pris=100.0):
        con.execute("INSERT INTO nyheter (id, url, publisert, hentet, selskap_slug, selskap_navn, tittel) "
                    "VALUES (?,?,?,?,?,?,?)", (nid, f"u{nid}", dag + "T10:00:00", "nå", "abc", "ABC", "Mandatory notification"))
        con.execute("INSERT INTO innsidehandler (nyhet_id, linje, versjon, kilde, selskap_slug, publisert, rolle, retning, "
                    "instrument, antall, pris, valuta, verdi, person, noekkel) VALUES (?,0,'e2','regel','abc',?,?,?,?,?,?,?,?,?,?)",
                    (nid, dag + "T10:00:00", "ceo", retning, "aksje", 1000, pris, "NOK", 1000 * pris, person,
                     f"{nid}|{person}|{retning}"))

    handel(1, "2024-03-05", "Kari Nordmann")                    # første kjøp
    handel(2, "2025-03-12", "Kari Nordmann")                    # samme måned året etter = rutine
    handel(3, "2025-03-01", "Per Selger", retning="salg")       # én selger
    handel(4, "2025-06-02", "Ola Ny", pris=80.0)                # kurs 100 er 25 % over innsiders pris
    con.commit()
    hs = {h.publisert[:10]: h.egenskaper for h in innsidehendelser(con, kurser, horisont=5)}
    assert hs["2024-03-05"]["innside.mønster"] == "ukjent (kort historikk)"
    assert hs["2025-03-12"]["innside.mønster"] == "rutine (samme måned før)"
    assert hs["2025-03-12"]["innside.historikk"] == "tidligere kjøp gikk dårlig"   # flat kurs - kostnad < 0
    assert hs["2025-03-12"]["innside.flyt90"] == "like mange eller flere selgere"   # 1 kjøper, 1 selger
    assert hs["2025-03-12"]["innside.kurs_vs_innsider"] == "rundt innsiders kurs"
    assert hs["2025-06-02"]["innside.kurs_vs_innsider"] == "langt over (>10 %)"
    assert hs["2025-06-02"]["innside.mønster"] == "første kjøp på 1+ år"
    assert hs["2025-06-02"]["innside.historikk"] == "ingen kjente tidligere"
