"""Tester parseren mot en lokal HTML-fixture. Ingen nettverkskall."""
from __future__ import annotations

from pathlib import Path

from src.sources import mfn

FIXTURE = Path(__file__).parent / "fixtures" / "mfn_feed.html"


def _last() -> str:
    return FIXTURE.read_text(encoding="utf-8")


def test_parse_finner_begge_url_former():
    meldinger = mfn.parse(_last())
    assert len(meldinger) == 2

    prefikset, direkte = meldinger
    assert prefikset.url == (
        "https://mfn.se/ob/a/equinor/2405146789-kvartalsrapport-q1"
    )
    assert direkte.url == (
        "https://mfn.se/a/norsk-hydro/hydro-kjoper-tilbake-egne-aksjer"
    )


def test_selskap_og_distributor():
    prefikset, direkte = mfn.parse(_last())

    assert prefikset.selskap_slug == "equinor"
    assert prefikset.selskap_navn == "Equinor"
    assert prefikset.distributor == "Oslo Børs / NewsWeb"

    assert direkte.selskap_slug == "norsk-hydro"
    assert direkte.selskap_navn == "Norsk Hydro"
    assert direkte.distributor == "MFN"


def test_html_entiteter_i_tittel_dekodes():
    prefikset, direkte = mfn.parse(_last())

    # Tittel hentet fra title-attributtet, entitet dekodet.
    assert prefikset.tittel == "Equinor: Kvartalsrapport Q1 2024 & utbytte"
    # Tittel hentet fra lenketeksten (ingen title-attributt), entitet dekodet.
    assert direkte.tittel == "Hydro kjøper tilbake egne aksjer – oppdatering"


def test_vedleggslenke_uten_tekst_hoppes_over():
    meldinger = mfn.parse(_last())
    urler = [m.url for m in meldinger]
    assert not any(url.endswith(".pdf") for url in urler)


def test_publiseringstidspunkt():
    prefikset, direkte = mfn.parse(_last())
    assert prefikset.publisert == "2024-05-14T08:32:11"
    assert direkte.publisert == "2024-05-14T08:20:05"


def test_parser_nyere_markup_med_dato_og_tid_i_egne_spans():
    """Regresjon: MFN delte tidsstempelet i <span class="compressed-date"> og
    <span class="compressed-time">; parseren ga da 0 meldinger i live-feeden."""
    from pathlib import Path
    from src.sources import mfn

    html = (Path(__file__).parent / "fixtures" / "mfn_feed_2026-11.html").read_text(encoding="utf-8")
    ms = mfn.parse(html)
    assert len(ms) >= 10
    assert all(m.publisert.count("T") == 1 and len(m.publisert) == 19 for m in ms)
    assert all(m.selskap_slug and m.tittel for m in ms)
