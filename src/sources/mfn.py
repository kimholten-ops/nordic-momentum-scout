"""Henter pressemeldinger fra mfn.se.

MFN rendrer feeden server-side, så vi slipper headless browser. Parseren er
bevisst posisjonsbasert i stedet for CSS-klassebasert: MFN kan endre
klassenavn når som helst, men rekkefølgen tidsstempel -> selskapslenke ->
tittellenke i dokumentet er selve strukturen i en feed-rad.
"""
from __future__ import annotations

import html
import re
from dataclasses import dataclass
from datetime import datetime

import requests

from src.config import DISTRIBUTORS, HTTP_TIMEOUT, MFN_FEED_URL, USER_AGENT

ORIGIN = r"(?:https?://(?:www\.)?mfn\.se)?"

ITEM_RE = re.compile(
    r'<a\b[^>]*?href="' + ORIGIN + r'/(?P<path>'
    r'(?:ob|cis|one|beq|mfn)/a/[^/"]+/[^"]+'   # distribuert via ekstern nyhetsbyrå
    r'|a/[^/"]+/[^"]+'                          # publisert direkte i MFN
    r')"(?P<attrs>[^>]*)>(?P<inner>.*?)</a>',
    re.S | re.I,
)
COMPANY_RE = re.compile(
    r'<a\b[^>]*?href="' + ORIGIN + r'/all/a/(?P<slug>[^/"?#]+)"[^>]*>(?P<inner>.*?)</a>',
    re.S | re.I,
)
# Dato og klokkeslett står enten sammenhengende ("2026-11-18 15:40:46") eller,
# fra og med MFNs nyere markup, i hver sin <span> — tillat tagger/blanke imellom.
TS_RE = re.compile(r"(\d{4}-\d{2}-\d{2})(?:[ T]|(?:\s|<[^>]+>)+)(\d{2}:\d{2}:\d{2})")
TAG_RE = re.compile(r"<[^>]+>")
TITLE_ATTR_RE = re.compile(r'\btitle="([^"]*)"', re.I)

# --- Brødtekst fra en enkelt artikkelside ----------------------------------
# MFN har ingen dokumentert artikkelmarkup, så dette er bevisst en grov
# heuristikk: fjern støy (script/style/navigasjon/footer), plukk innholdet i
# <article>/<main> om det finnes (ellers hele <body>), sett inn linjeskift
# der blokk-elementer sluttet, og strip resten av taggene. God nok til å gi
# en LLM løpetekst — ikke ment å være en pixel-perfekt utskrift av siden.
_STOY_RE = re.compile(
    r"<(script|style|nav|header|footer|button|form|aside)\b[^>]*>.*?</\1>",
    re.S | re.I,
)
_ARTICLE_RE = re.compile(r"<article\b[^>]*>(.*?)</article>", re.S | re.I)
_MAIN_RE = re.compile(r"<main\b[^>]*>(.*?)</main>", re.S | re.I)
_BODY_RE = re.compile(r"<body\b[^>]*>(.*?)</body>", re.S | re.I)
_BLOKKSLUTT_RE = re.compile(r"</(p|div|li|h[1-6]|tr|blockquote)>|<br\s*/?>", re.I)
BRODTEKST_MAKS_TEGN = 20_000


@dataclass
class Melding:
    url: str
    publisert: str
    selskap_slug: str
    selskap_navn: str | None
    tittel: str
    distributor: str | None
    sprak: str | None


def _tekst(raw: str) -> str:
    return html.unescape(TAG_RE.sub("", raw)).replace("\xa0", " ").strip()


def _gjett_sprak(tittel: str) -> str | None:
    t = f" {tittel.lower()} "
    treff = {
        "no": (" og ", " ikke ", " aksjer ", " selskapet ", "kjøp", "tildel", " før "),
        "sv": (" och ", " för ", " aktier ", " bolaget ", "återköp", " från "),
        "da": (" og ", " ikke ", " aktier ", " selskabet "),
        "fi": ("yhtiö", "osake", "tiedote", "katsaus", "kutsu"),
        "en": (" the ", " and ", " of ", " share ", " report ", " announces "),
    }
    poeng = {k: sum(1 for o in ord_ if o in t) for k, ord_ in treff.items()}
    beste = max(poeng, key=poeng.get)
    return beste if poeng[beste] else None


def parse(html_tekst: str) -> list[Melding]:
    """Plukker ut feed-rader i dokumentrekkefølge."""
    hendelser: list[tuple[int, str, object]] = []
    for m in TS_RE.finditer(html_tekst):
        hendelser.append((m.start(), "ts", f"{m.group(1)}T{m.group(2)}"))
    for m in COMPANY_RE.finditer(html_tekst):
        hendelser.append((m.start(), "selskap", (m.group("slug"), _tekst(m.group("inner")))))
    for m in ITEM_RE.finditer(html_tekst):
        hendelser.append((m.start(), "item", m))
    hendelser.sort(key=lambda h: h[0])

    meldinger: list[Melding] = []
    sett: set[str] = set()
    ts: str | None = None
    selskap: tuple[str, str] | None = None

    for _, art, verdi in hendelser:
        if art == "ts":
            ts = verdi
            selskap = None
        elif art == "selskap":
            selskap = verdi
        elif art == "item":
            m = verdi
            if ts is None:
                continue
            deler = m.group("path").split("/")
            if deler[0] == "a":
                pfx, slug = "mfn", deler[1]
            else:
                pfx, slug = deler[0], deler[2]
            url = f"https://mfn.se/{m.group('path')}"
            if url in sett:
                continue
            tittel_attr = TITLE_ATTR_RE.search(m.group("attrs") or "")
            tittel = _tekst(tittel_attr.group(1)) if tittel_attr else _tekst(m.group("inner"))
            if not tittel:
                continue  # rene ikon-/vedleggslenker
            sett.add(url)
            navn = selskap[1] if selskap and selskap[0] == slug else None
            meldinger.append(
                Melding(
                    url=url,
                    publisert=ts,
                    selskap_slug=slug,
                    selskap_navn=navn or None,
                    tittel=tittel,
                    distributor=DISTRIBUTORS.get(pfx),
                    sprak=_gjett_sprak(tittel),
                )
            )
    return meldinger


def hent(url: str | None = None) -> list[Melding]:
    r = requests.get(
        url or MFN_FEED_URL,
        headers={"User-Agent": USER_AGENT, "Accept-Language": "nb,no,en;q=0.8"},
        timeout=HTTP_TIMEOUT,
    )
    r.raise_for_status()
    return parse(r.text)


def parse_brodtekst(html_tekst: str) -> str:
    """Plukker løpeteksten ut av en MFN-artikkelside. Se kommentaren ved
    _STOY_RE for forbeholdene."""
    uten_stoy = _STOY_RE.sub(" ", html_tekst)
    for finn in (_ARTICLE_RE, _MAIN_RE, _BODY_RE):
        m = finn.search(uten_stoy)
        if m:
            uten_stoy = m.group(1)
            break
    med_linjeskift = _BLOKKSLUTT_RE.sub("\n", uten_stoy)
    tekst = _tekst(med_linjeskift)
    tekst = re.sub(r"[ \t]+", " ", tekst)
    tekst = re.sub(r"\n[ \t]*\n+", "\n\n", tekst)
    return tekst.strip()[:BRODTEKST_MAKS_TEGN]


def hent_brodtekst(url: str) -> str:
    r = requests.get(
        url,
        headers={"User-Agent": USER_AGENT, "Accept-Language": "nb,no,en;q=0.8"},
        timeout=HTTP_TIMEOUT,
    )
    r.raise_for_status()
    return parse_brodtekst(r.text)


if __name__ == "__main__":
    import sys

    ms = hent(sys.argv[1] if len(sys.argv) > 1 else None)
    print(f"{len(ms)} meldinger fra feeden ({datetime.now():%H:%M:%S})\n")
    for m in ms[:15]:
        print(f"{m.publisert}  {m.selskap_slug:<24} {m.tittel[:70]}")
