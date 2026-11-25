import sqlite3

from src import medietone
from src.panel.bygg import _for
from src.sources import faktorer, medier, short_norden


def test_short_summer_per_aktor():
    h = [("SE1", "A", "2025-01-02", 0.6), ("SE1", "B", "2025-01-02", 0.7), ("SE1", "A", "2025-01-05", 0.0),
         ("SE1", "B", "2025-01-09", 1.1)]
    assert short_norden.summer(h) == [("SE1", "2025-01-02", 1.3), ("SE1", "2025-01-05", 0.7), ("SE1", "2025-01-09", 1.1)]
    assert short_norden._pst("<0,5") == 0.0 and short_norden._pst("1,45") == 1.45 and short_norden._pst(0.49) == 0.0


def test_laks_publiseres_uken_etter():
    assert faktorer._uke_til_publisering("2026U43") == "2026-10-28"  # onsdag i uke 44


def test_for_er_strengt_foer():
    ds, vs = ["2026-01-01", "2026-01-02", "2026-01-05"], [1, 2, 3]
    assert _for(ds, vs, "2026-01-05") == 2 and _for(ds, vs, "2026-01-01") is None and _for(ds, vs, "2026-02-01") == 3


def test_rss_og_navnematch():
    xml = ("<rss><channel><item><title><![CDATA[Equinor selger seg ned]]></title><link>https://x/1</link>"
           "<description>Norsk Hydro og Aker nevnes</description><pubDate>Wed, 25 Nov 2026 10:11:28 +0100</pubDate>"
           "</item></channel></rss>")
    r = medier.parse_rss(xml)[0]
    assert r["url"] == "https://x/1" and r["publisert"] == "2026-11-25T10:11:28"
    m = medier.navnemonster([("equinor", "Equinor ASA"), ("norsk-hydro", "Norsk Hydro"), ("aker", "Aker"),
                             ("nordic", "Nordic")])
    assert medier.finn_selskaper(f"{r['tittel']} {r['ingress']}", m) == ["equinor", "norsk-hydro"]
    assert medier.kjernenavn("Pexip Holding ASA") == "Pexip"


def test_medietone_import():
    con = sqlite3.connect(":memory:")
    n = medietone.importer(con, [{"url": "u", "slug": "s", "resultat": {"tone": 1, "relevant": True}, "modell": "m"},
                                 {"url": "u2", "slug": "s", "feil": "x"}])
    assert n == {"lest": 2, "lagt_inn": 1}
    assert con.execute("SELECT tone, relevant FROM medietone").fetchone() == (1, 1)
