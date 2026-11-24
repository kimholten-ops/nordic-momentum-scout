import sqlite3

from src import ask


def test_vurder_hjemland_og_segment():
    assert ask.vurder({"NO0010096985"}, "Oslo Børs")[0] == ask.JA
    assert ask.vurder({"BMG359472021"}, "Oslo Børs") == (ask.UTENFOR, "selskapet er hjemmehørende i Bermuda")
    # Flyttet fra Bermuda til Kypros: én EØS-ISIN holder.
    assert ask.vurder({"BMG3682E1921", "CY0200352116"}, "Oslo Børs")[0] == ask.JA
    # Amerikansk ADR ved siden av norsk ISIN.
    assert ask.vurder({"NO0010096985", "US29446M1027"}, "Oslo Børs")[0] == ask.JA
    assert ask.vurder({"FO0000000179"}, "Oslo Børs")[0] == ask.UTENFOR
    assert ask.vurder({"NO0010000001"}, "Euronext Growth Oslo")[0] == ask.MHF
    # Nasdaq-segment slår markedsteksten.
    assert ask.vurder({"FI0000000001"}, "Helsinki", {"FI0000000001": "mhf"})[0] == ask.MHF
    assert ask.vurder({"SE0000000001"}, "First North Stockholm", {"SE0000000001": "hoved"})[0] == ask.JA
    assert ask.vurder(set(), "Oslo Børs")[0] == ask.UKJENT
    assert ask.vurder(set(), "", yahoo="AAPL")[0] == ask.UTENFOR


def test_ok():
    assert ask.ok(ask.JA) and ask.ok(ask.UKJENT) and ask.ok(None)
    assert not ask.ok(ask.UTENFOR)
    assert ask.ok(ask.MHF) == ask.ASK_MHF


def test_beregn_med_overstyring(tmp_path, monkeypatch):
    fil = tmp_path / "ask.csv"
    fil.write_text("# kommentar\nslug,status,merknad\nb,ja,flyttet til Norge\n", encoding="utf-8")
    monkeypatch.setattr(ask, "OVERSTYRING", fil)
    con = sqlite3.connect(":memory:")
    con.executescript(
        "CREATE TABLE selskaper (slug TEXT, marked TEXT, yahoo TEXT);"
        "CREATE TABLE selskap_isin (slug TEXT, isin TEXT);"
        "INSERT INTO selskaper VALUES ('a','Oslo Børs','A.OL'),('b','Oslo Børs','B.OL'),('c','Oslo Børs','C.OL');"
        "INSERT INTO selskap_isin VALUES ('a','NO1'),('b','BMG1'),('c','SGX1');")
    s = ask.beregn(con)
    assert s["a"][0] == ask.JA and s["b"] == (ask.JA, "flyttet til Norge") and s["c"][0] == ask.UTENFOR
    assert ask.per_yahoo(con)["C.OL"][0] == ask.UTENFOR
