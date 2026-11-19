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
    monkeypatch.setattr(db_lagring, "_release_finnes", lambda: False)
    monkeypatch.setattr(db_lagring, "TMP", tmp_path / "tmp")
    assert db_lagring.hent(f) == 0 and f.exists()


def test_db_lagring_stopper_hvis_release_finnes_men_nedlasting_feiler(tmp_path, monkeypatch):
    from scripts import db_lagring

    monkeypatch.setattr(db_lagring, "_release_finnes", lambda: True)
    monkeypatch.setattr(db_lagring, "_har_gh", lambda: True)
    monkeypatch.setattr(db_lagring, "TMP", tmp_path / "tmp")
    monkeypatch.setattr(db_lagring.time, "sleep", lambda s: None)

    def feil(*a, **k):
        raise RuntimeError("nett nede")
    monkeypatch.setattr(db_lagring, "_gh", feil)
    assert db_lagring.hent(tmp_path / "m.db") == 1
