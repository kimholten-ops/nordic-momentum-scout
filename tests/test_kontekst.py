"""Kontekstdata (rapporter, emisjoner, short, sektor), utbytte, filtre og lagring."""
from __future__ import annotations

from src import db, marked
from src.hjerne import kontekst as ktx
from src.sources import kontekst as kilde


def _serie(n=40, start=100.0, utbytte=None):
    from datetime import date, timedelta

    d0 = date(2025, 1, 1)
    datoer = [(d0 + timedelta(days=i)).isoformat() for i in range(n)]
    close = [start] * n
    s = marked.Serie(datoer, close[:], [c * 1.01 for c in close], [c * 0.99 for c in close], close[:], [1000] * n)
    s.utbytte = [None] * n
    for i, u in (utbytte or {}).items():
        s.utbytte[i] = u
    return s


def _item(slug, dato, group, isin="NO0000000001"):
    return {"group_id": group, "author": {"slug": slug, "isins": [isin]}, "content": {"publish_date": dato}}


def test_lagre_mfn_bare_kjente_og_dedup_paa_gruppe(tmp_path):
    p = tmp_path / "k.db"
    db.init(p)
    con = db.connect(p)
    n = kilde.lagre_mfn(con, [_item("a", "2025-03-01T07:00:00Z", "g1"), _item("a", "2025-03-01T07:00:05Z", "g1"),
                              _item("ukjent", "2025-03-01T07:00:00Z", "g2")], "rapport", {"a"})
    assert n == 1
    assert con.execute("SELECT publisert FROM selskapshendelser").fetchone()[0] == "2025-03-01T08:00:00"  # norsk tid
    assert con.execute("SELECT isin FROM selskap_isin WHERE slug='a'").fetchone()[0] == "NO0000000001"


def test_lagre_short():
    import sqlite3

    con = sqlite3.connect(":memory:")
    con.executescript(db.SCHEMA)
    n = kilde.lagre_short(con, [{"isin": "X", "events": [{"date": "2025-02-01T00:00:00", "shortPercent": 2.5},
                                                           {"date": "2025-02-10T00:00:00", "shortPercent": 0}]}])
    assert n == 2 and con.execute("SELECT COUNT(*) FROM short").fetchone()[0] == 2


def _kontekst():
    k = ktx.Kontekst(mfn_start="2024-01-01T08:00:00", short_start="2024-11-19")
    k.hendelser[("a", "rapport")] = ["2025-01-20T07:00:00", "2025-01-30T15:00:00"]
    k.hendelser[("a", "emisjon")] = ["2024-12-01T08:00:00"]
    k.isin["a"] = {"NO1"}
    k.short["NO1"] = (["2025-01-02", "2025-01-31"], [2.4, 0.0])
    k.info["A.OL"] = ("Energy", 1e8)
    return k


def test_kontekst_bruker_bare_det_som_var_kjent():
    s = _serie()
    kurser = {"A.OL": s}
    # Publisert 30. jan kl 10 — rapporten 30. jan kl 15 er IKKE kjent ennå.
    eg = ktx.egenskaper(_kontekst(), kurser, "a", "A.OL", "2025-01-30T10:00:00")
    assert eg["kontekst.rapport"] == "3–30 dager etter rapport"  # 20. jan
    assert eg["kontekst.emisjon90"] == "ja" and eg["kontekst.tilbakekjøp90"] == "nei"
    assert eg["kurs.short"] == "2 % eller mer"           # raden 31. jan er i fremtiden
    assert eg["selskap.sektor"] == "energi"
    assert eg["kurs.størrelse"] == "over 5 mrd"            # 1e8 aksjer x 100 kr = 10 mrd
    eg2 = ktx.egenskaper(_kontekst(), kurser, "a", "A.OL", "2025-01-30T16:00:00")
    assert eg2["kontekst.rapport"] == "0–2 dager etter rapport"


def test_kontekst_ukjent_selskap_far_ingen_mfn_egenskaper():
    eg = ktx.egenskaper(_kontekst(), {"B.ST": _serie()}, "b", "B.ST", "2025-01-30T10:00:00")
    assert not any(f.startswith("kontekst.") for f in eg)
    assert "kurs.short" not in eg  # bare Oslo


def test_utbytte_legges_til_avkastningen():
    s = _serie(utbytte={12: 5.0})  # eksdato innenfor perioden
    idx = _serie()
    kurser = {"A.OL": s, "OSEBX.OL": idx}
    u = marked.utfall(kurser, "A.OL", s.datoer[9], 5)  # inngang dag 10, ut dag 15
    assert abs(u.raa - 0.05) < 1e-9
    # Eksdato på inngangsdagen teller ikke (kjøpt på åpning = uten utbytte).
    s2 = _serie(utbytte={10: 5.0})
    assert abs(marked.utfall({"A.OL": s2, "OSEBX.OL": idx}, "A.OL", s2.datoer[9], 5).raa) < 1e-9


def test_prisindeks_far_utbyttetillegg():
    assert marked.indeks_utbytte("X.OL", 252) == 0.0
    assert abs(marked.indeks_utbytte("X.ST", 252) - 0.03) < 1e-9


def test_hardt_filter_og_regelsjekk():
    from src.hjerne.kandidater import regelsjekk
    from src.hjerne.simuler import Param

    p = Param()
    assert p.utelatt({"kurs.likviditet": "lav"}) == "kurs.likviditet=lav"
    assert p.utelatt({"kurs.likviditet": "høy", "innside.kurs_vs_innsider": "rundt innsiders kurs"}) is None
    ok = regelsjekk({"kurs.likviditet": "høy", "innside.verdi": ">1M", "kurs.reaksjon": "opp >3%"})
    assert ok["ok"] and ok["pluss"]
    assert not regelsjekk({"kurs.likviditet": "høy", "innside.verdi": ">1M", "kurs.reaksjon": "rolig"})["ok"]
    assert not regelsjekk({"kurs.likviditet": "lav", "innside.verdi": ">1M"})["ok"]


def test_db_lagring_bruker_repofila_naar_release_mangler(tmp_path, monkeypatch):
    from scripts import db_lagring

    f = tmp_path / "m.db"
    db.init(f)
    monkeypatch.setattr(db_lagring, "_release_status", lambda: "mangler")
    monkeypatch.setattr(db_lagring, "TMP", tmp_path / "tmp")
    assert db_lagring.hent(f) == 0 and f.exists()
    monkeypatch.setattr(db_lagring, "_release_status", lambda: "ukjent")
    assert db_lagring.hent(f) == 1  # aldri fortsett på en gammel kopi ved tvil


def test_db_lagring_stopper_hvis_release_finnes_men_nedlasting_feiler(tmp_path, monkeypatch):
    from scripts import db_lagring

    monkeypatch.setattr(db_lagring, "_release_status", lambda: "finnes")
    monkeypatch.setattr(db_lagring, "_har_gh", lambda: False)
    monkeypatch.setattr(db_lagring, "TMP", tmp_path / "tmp")
    monkeypatch.setattr(db_lagring.time, "sleep", lambda s: None)

    import requests

    def feil(*a, **k):
        raise requests.ConnectionError("nett nede")
    monkeypatch.setattr(requests, "get", feil)
    assert db_lagring.hent(tmp_path / "m.db") == 1


def test_avslaatte_egenskaper_brukes_ikke_av_modellen():
    from src.hjerne.hendelser import Hendelse
    from src.hjerne.simuler import _egenskaper

    h = Hendelse("n", "innside", "a", "A", "A.OL", "2025-01-01T10:00:00",
                 {"kontekst.emisjon90": "ja", "selskap.sektor": "energi", "kurs.likviditet": "høy"})
    eg = _egenskaper(h)
    assert eg["kontekst.emisjon90"] == "ja" and "selskap.sektor" not in eg and eg["kurs.likviditet"] == "høy"


def test_euronext_parse_og_rens():
    from src.sources import euronext

    csv = ('﻿"Historical Data"\n"From x"\nNO1\nDate;Open;High;Low;Last;Close;"Number of Shares";"Number of Trades";Turnover;vwap\n'
           "03/01/2025;0.00;0.00;0.00;0.00;10.0;0;0;0;0\n02/01/2025;9.8;10.1;9.7;10.0;10.0;500;3;5000;10\n")
    r = euronext.parse_csv(csv)
    assert r[0] == ("2025-01-02", 9.8, 10.1, 9.7, 10.0, 500) and r[1][1] is None and r[1][5] == 0
    serie = [("d1", 0, 0, 0, 10.0, 0), ("d2", 0, 0, 0, 0.05, 0), ("d3", 0, 0, 0, 10.2, 0),
             ("d4", 0, 0, 0, 25.0, 0), ("d5", 0, 0, 0, 10.1, 0), ("d6", 0, 0, 0, 16.0, 0), ("d7", 0, 0, 0, 16.5, 0)]
    assert [x[0] for x in euronext.rens(serie)] == ["d1", "d3", "d5", "d6", "d7"]  # ekte hopp (d6) beholdes


def test_euronext_fyller_bare_bakover_naar_kursene_stemmer(tmp_path):
    from src.sources import euronext

    p = tmp_path / "e.db"
    db.init(p)
    con = db.connect(p)
    con.execute("INSERT INTO kurser (yahoo,dato,close) VALUES ('X.OL','2025-01-03',10.0)")
    rader = [("2025-01-02", None, None, None, 9.9, 1), ("2025-01-03", None, None, None, 10.1, 1)]
    assert euronext.fyll(con, "X.OL", rader) == ("fylt bakover", 1)
    con.execute("INSERT INTO kurser (yahoo,dato,close) VALUES ('Y.OL','2025-01-03',5.0)")
    assert euronext.fyll(con, "Y.OL", rader) == ("avvik ved overgang", 0)
    assert euronext.fyll(con, "Z.OL", rader) == ("hele serien", 2)


def test_db_lagring_bytter_navn_etter_opplasting(tmp_path, monkeypatch):
    """Ny fil lastes opp først; gjeldende blir forrige og ny blir gjeldende."""
    import sqlite3
    import subprocess

    from scripts import db_lagring as d

    vedlegg = {"momentum.db.gz": 1, "momentum-forrige.db.gz": 2}
    kall = []

    def gh(*args, sjekk=True):
        kall.append(("gh", args[:2]))
        if args[:2] == ("release", "upload"):
            vedlegg["momentum-ny.db.gz"] = 3
        return subprocess.CompletedProcess(args, 0, "", "")

    def api(*args):
        if args[0] == "-X" and args[1] == "DELETE":
            i = int(args[2].rsplit("/", 1)[1])
            for k in [k for k, v in vedlegg.items() if v == i]:
                del vedlegg[k]
        elif args[0] == "-X" and args[1] == "PATCH":
            i = int(args[2].rsplit("/", 1)[1])
            navn = args[4].split("=", 1)[1]
            gammel = next(k for k, v in vedlegg.items() if v == i)
            vedlegg[navn] = vedlegg.pop(gammel)
        out = "\n".join(f"{k}\t{v}" for k, v in vedlegg.items())
        return subprocess.CompletedProcess(args, 0, out, "")

    db = tmp_path / "m.db"
    sqlite3.connect(db).close()
    monkeypatch.setattr(d, "_har_gh", lambda: True)
    monkeypatch.setattr(d, "_release_finnes", lambda: True)
    monkeypatch.setattr(d, "_gh", gh)
    monkeypatch.setattr(d, "_api", api)
    monkeypatch.setattr(d, "TMP", tmp_path / "t")
    monkeypatch.setattr(d.time, "sleep", lambda s: None)
    assert d.last_opp(db) == 0
    assert vedlegg == {"momentum-forrige.db.gz": 1, "momentum.db.gz": 3}
