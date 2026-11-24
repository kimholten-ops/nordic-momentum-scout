import json
import sqlite3
from datetime import datetime

from src import intradag
from src.config import TZ
from src.hjerne import forklaring
from src.hjerne.kandidater import _reaksjon

NK = {"dato": "2026-11-24", "tid": "11:00", "sist": 105.0, "lav": 99.0,
      "bars": [["09:15", 100.0], ["09:30", 101.0], ["10:00", 102.0], ["11:00", 105.0]]}


def test_kurs_ved_og_reaksjon():
    assert intradag.kurs_ved(NK, "09:40") == 101.0
    assert intradag.kurs_ved(NK, "09:00") is None
    # Melding i børstiden: fra kursen da den kom.
    assert abs(_reaksjon("2026-11-24T10:05:00", 98.0, NK, "2026-11-23") - (105 / 102 - 1)) < 1e-9
    # Før åpning / etter gårsdagens stengning: fra forrige sluttkurs.
    assert abs(_reaksjon("2026-11-24T07:00:00", 100.0, NK, "2026-11-23") - 0.05) < 1e-9
    assert abs(_reaksjon("2026-11-23T18:30:00", 100.0, NK, "2026-11-23") - 0.05) < 1e-9
    assert _reaksjon("2026-11-20T10:00:00", 100.0, NK, "2026-11-23") is None


def test_forklaring_med_reaksjon():
    c = {"naa": {"kurs": 105.0, "tid": "11:00", "siden_melding": 0.05}}
    assert "Etter meldingen har kursen steget 5,0 % (siste kurs 105,0 kr kl. 11:00)." in forklaring.tekst(c)
    assert forklaring.grunner(c)["for"][0] == "Kursen har steget 5,0 % etter meldingen"
    c["naa"]["siden_melding"] = -0.03
    assert forklaring.grunner(c)["mot"][0] == "Kursen har falt 3,0 % etter meldingen"


def _db():
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.executescript(intradag.SKJEMA + "CREATE TABLE hjerne_trades (nokkel, slug, navn, yahoo, inn_dato, inn, stopp, "
                      "plan_ut_dato, status);")
    con.execute("INSERT INTO hjerne_trades VALUES ('a','x','Aksje A','A.OL','2026-11-20',100,99.5,'2026-12-10','åpen')")
    con.execute("INSERT INTO hjerne_trades VALUES ('b','y','Aksje B','B.OL','2026-11-01',50,40,'2026-11-24','åpen')")
    return con


def test_stopp_og_salgsvarsel(monkeypatch):
    sendt = []
    monkeypatch.setattr(intradag, "send", lambda tekst, **kw: sendt.append(tekst) or True)
    con = _db()
    kurser = {"A.OL": NK, "B.OL": {**NK, "sist": 55.0, "lav": 54.0}}
    na = datetime(2026, 11, 24, 15, 45, tzinfo=TZ)
    assert intradag.varsle(con, kurser, na) == 2
    assert "Stopp nådd: Aksje A" in sendt[0] and "Planlagt salg i dag: Aksje B" in sendt[1]
    # Bare én gang per handel.
    assert intradag.varsle(con, kurser, na) == 0
    # Før 15:30 ingen salgspåminnelse.
    con2 = _db()
    sendt.clear()
    intradag.varsle(con2, {"B.OL": kurser["B.OL"]}, datetime(2026, 11, 24, 12, 0, tzinfo=TZ))
    assert sendt == []


def test_vakt_runde(monkeypatch, tmp_path):
    from scripts import ollama_tolk as ot
    from scripts import vakt

    filer = {n: tmp_path / f"{n}.jsonl" for n in ("innside", "nyheter", "rapporter")}
    tolket = []
    fake = lambda navn: (lambda r: tolket.append(navn) or {"url": r.get("url"), "group_id": r.get("group_id"),  # noqa: E731
                                                            "versjon": "r2", "resultat": {}, "handler": []})
    monkeypatch.setattr(vakt, "KOER", [(n, filer[n], "group_id" if n == "rapporter" else "url", fake(n), n) for n in filer])
    filer["nyheter"].write_text(json.dumps({"url": "u-gammel"}) + "\n", encoding="utf-8")
    ko = {"innside": [{"url": "i1", "selskap_slug": "a"}], "nyheter": [{"url": "u-gammel"}, {"url": "u-ny"}],
          "rapporter": [{"group_id": "g1", "slug": "b"}]}
    monkeypatch.setattr(ot, "ferdige", ot.ferdige)
    assert vakt.runde(ko) == 3
    assert tolket == ["innside", "nyheter", "rapporter"]
    assert vakt.runde(ko) == 0


def test_vakttid():
    from scripts import vakt

    assert vakt.i_vakttid(datetime(2026, 11, 24, 8, 0, tzinfo=TZ))
    assert not vakt.i_vakttid(datetime(2026, 11, 24, 18, 0, tzinfo=TZ))
    assert not vakt.i_vakttid(datetime(2026, 11, 28, 10, 0, tzinfo=TZ))  # lørdag
