"""Medieomtale og oppmerksomhet (forberedelse, brukes ikke av modellene ennå).

Samler inn nå, så vi har noen måneder med historikk å teste på senere:

- Nyhetsstrømmer (RSS) fra DN, Dagens Industri, Kauppalehti og Børsen:
  overskrift og ingress, koblet til selskapene som nevnes.
- Google Nyheter-søk per selskap (roterer gjennom universet): fanger opp
  omtale i alle medier, også de uten egen strøm.
- Google Trends «trending nå» for Norge, Sverige, Danmark og Finland:
  når et selskapsnavn er blant de mest søkte, er det et oppmerksomhetssignal.

Bare overskrift, ingress, lenke og tidspunkt lagres; aldri hele artikler.
E24 er utelatt fordi vilkårene for strømmen deres ikke tillater slik bruk.
Forum (Reddit, Shareville, Finansavisen) stenger for maskinell innhenting.

Vakten på PC-en vurderer tonen i hver omtale med Ollama (src/medietone.py).
"""
from __future__ import annotations

import html
import json
import re
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from urllib.parse import quote

import requests

from src.config import HTTP_TIMEOUT, TZ

UA = {"User-Agent": "Mozilla/5.0 (compatible; momentum-research)"}
STROMMER = {
    "dn": "https://services.dn.no/api/feed/rss/",
    "di": "https://www.di.se/rss",
    "kauppalehti": "https://feeds.kauppalehti.fi/rss/main",
    "borsen": "https://borsen.dk/rss",
}
TRENDS = {land: f"https://trends.google.com/trending/rss?geo={land}" for land in ("NO", "SE", "DK", "FI")}
GNEWS = "https://news.google.com/rss/search?q={q}&hl={hl}&gl={gl}&ceid={gl}:{hl}"
GNEWS_SPRAK = {".OL": ("no", "NO"), ".ST": ("sv", "SE"), ".CO": ("da", "DK"), ".HE": ("fi", "FI")}
GNEWS_PER_RUNDE = 30          # selskaper per kjøring (roterer)
MIN_NAVN = 5                   # kortere navn (Aker, NKT, RTX) gir for mange feiltreff i fritekst
SUFFIKSER = r"\b(ASA|AS|AB|Oyj|Abp|A/S|plc|Holding|Holdings|Group|Groupe|\(publ\))\.?$"
GENERISKE = {"Nordic", "Norwegian", "Ocean", "Energy", "Group", "Solar", "Sparebank", "Capital", "Invest"}

SKJEMA = """
CREATE TABLE IF NOT EXISTS medieomtale (
    url       TEXT PRIMARY KEY,
    kilde     TEXT NOT NULL,
    type      TEXT NOT NULL,      -- medie | nyhetssok | trend
    publisert TEXT,
    hentet    TEXT NOT NULL,
    tittel    TEXT NOT NULL,
    ingress   TEXT,
    slugs     TEXT NOT NULL       -- JSON-liste over selskaper som nevnes
);
CREATE INDEX IF NOT EXISTS medieomtale_publisert ON medieomtale (publisert);
CREATE TABLE IF NOT EXISTS medietone (
    url       TEXT NOT NULL,
    slug      TEXT NOT NULL,
    tone      INTEGER,            -- -1 negativ, 0 nøytral, 1 positiv for aksjen
    relevant  INTEGER,            -- 1 hvis omtalen faktisk handler om selskapet
    modell    TEXT,
    tid       TEXT,
    PRIMARY KEY (url, slug)
);
CREATE TABLE IF NOT EXISTS medier_rotasjon (slug TEXT PRIMARY KEY, sist TEXT);
"""


def _tekst(x: str | None) -> str:
    x = re.sub(r"<!\[CDATA\[|\]\]>", "", x or "")
    return html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", x))).strip()


def parse_rss(xml: str) -> list[dict]:
    ut = []
    for item in re.findall(r"<item\b.*?>(.*?)</item>", xml, re.S):
        def felt(navn):
            m = re.search(rf"<{navn}\b[^>]*>(.*?)</{navn}>", item, re.S)
            return _tekst(m.group(1)) if m else ""
        lenke = felt("link") or felt("guid")
        tid = felt("pubDate") or felt("dc:date")
        try:
            pub = (parsedate_to_datetime(tid) if "," in tid or tid[:2].isdigit() and " " in tid
                   else datetime.fromisoformat(tid.replace("Z", "+00:00")))
            pub = pub.astimezone(TZ).replace(tzinfo=None).isoformat(timespec="seconds")
        except (TypeError, ValueError):
            pub = None
        if lenke and felt("title"):
            ut.append({"url": lenke, "tittel": felt("title"), "ingress": felt("description")[:600], "publisert": pub,
                       "trafikk": felt("ht:approx_traffic")})
    return ut


def kjernenavn(navn: str) -> str:
    n = re.sub(SUFFIKSER, "", navn.strip(), flags=re.I).strip(" -,")
    return re.sub(SUFFIKSER, "", n, flags=re.I).strip(" -,")


def navnemonster(selskaper: list[tuple[str, str]]) -> list[tuple[str, re.Pattern]]:
    ut = []
    for slug, navn in selskaper:
        k = kjernenavn(navn or "")
        if len(k) < MIN_NAVN or k in GENERISKE:
            continue
        ut.append((slug, re.compile(rf"(?<![\w-]){re.escape(k)}(?![\w-])")))
    return ut


def finn_selskaper(tekst: str, monstre: list[tuple[str, re.Pattern]]) -> list[str]:
    return sorted({slug for slug, m in monstre if m.search(tekst)})


def _hent(url: str) -> str | None:
    try:
        r = requests.get(url, headers=UA, timeout=HTTP_TIMEOUT)
        return r.text if r.ok else None
    except requests.RequestException:
        return None


def _lagre(con, rad: dict, kilde: str, type_: str, slugs: list[str], na: str) -> int:
    cur = con.execute(
        "INSERT OR IGNORE INTO medieomtale (url, kilde, type, publisert, hentet, tittel, ingress, slugs) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (rad["url"], kilde, type_, rad.get("publisert"), na, rad["tittel"][:400], (rad.get("ingress") or "")[:600] or None,
         json.dumps(slugs)))
    return cur.rowcount


def kjor(con, na: datetime | None = None) -> dict:
    con.executescript(SKJEMA)
    na = na or datetime.now(TZ)
    nas = na.isoformat(timespec="seconds")
    sel = [(r[0], r[1], r[2]) for r in con.execute(
        "SELECT slug, navn, yahoo FROM selskaper WHERE aktiv = 1 AND yahoo LIKE '%.%' AND navn IS NOT NULL")]
    monstre = navnemonster([(s, n) for s, n, _ in sel])
    stat = {"medie": 0, "medie_med_selskap": 0, "nyhetssok": 0, "trend": 0, "feil": []}

    for kilde, url in STROMMER.items():
        xml = _hent(url)
        if xml is None:
            stat["feil"].append(kilde)
            continue
        for rad in parse_rss(xml):
            slugs = finn_selskaper(f"{rad['tittel']} {rad['ingress']}", monstre)
            n = _lagre(con, rad, kilde, "medie", slugs, nas)
            stat["medie"] += n
            stat["medie_med_selskap"] += n if slugs else 0

    for land, url in TRENDS.items():
        xml = _hent(url)
        if xml is None:
            stat["feil"].append(f"trends-{land}")
            continue
        for rad in parse_rss(xml):
            slugs = finn_selskaper(rad["tittel"], monstre)
            if slugs:  # bare søk som gjelder selskapene våre
                rad = {**rad, "url": f"trend:{land}:{rad['tittel']}:{na.date()}",
                       "ingress": f"Søketrafikk ca. {rad.get('trafikk') or '?'}"}
                stat["trend"] += _lagre(con, rad, f"trends-{land}", "trend", slugs, nas)

    # Google Nyheter: de selskapene som er lengst siden sjekket.
    sist = dict(con.execute("SELECT slug, sist FROM medier_rotasjon").fetchall())
    for slug, navn, yahoo in sorted(sel, key=lambda x: sist.get(x[0], ""))[:GNEWS_PER_RUNDE]:
        suf = "." + yahoo.rsplit(".", 1)[-1]
        hl, gl = GNEWS_SPRAK.get(suf, ("no", "NO"))
        k = kjernenavn(navn)
        q = quote(f'"{k}"' + ("" if len(k) >= MIN_NAVN else " aksje") + " when:7d")
        xml = _hent(GNEWS.format(q=q, hl=hl, gl=gl))
        con.execute("INSERT OR REPLACE INTO medier_rotasjon VALUES (?,?)", (slug, nas))
        if xml is None:
            continue
        for rad in parse_rss(xml):
            if k.lower() not in rad["tittel"].lower():
                continue  # Google gir også løse treff
            stat["nyhetssok"] += _lagre(con, rad, "google-nyheter", "nyhetssok", [slug], nas)
    con.commit()
    return stat


def tone_ko(con, dager: int = 3) -> list[dict]:
    """Omtaler med selskap som ikke er tonevurdert ennå (til vakten på PC-en)."""
    if not con.execute("SELECT name FROM sqlite_master WHERE name = 'medieomtale'").fetchone():
        return []
    fra = (datetime.now(TZ) - timedelta(days=dager)).replace(tzinfo=None).isoformat(timespec="seconds")
    ut = []
    for r in con.execute("SELECT url, tittel, ingress, slugs, publisert FROM medieomtale "
                         "WHERE type IN ('medie', 'nyhetssok') AND slugs <> '[]' AND COALESCE(publisert, hentet) >= ? "
                         "ORDER BY COALESCE(publisert, hentet) DESC", (fra,)):
        for slug in json.loads(r[3]):
            if not con.execute("SELECT 1 FROM medietone WHERE url = ? AND slug = ?", (r[0], slug)).fetchone():
                ut.append({"url": r[0], "slug": slug, "tittel": r[1], "ingress": r[2], "publisert": r[4]})
    return ut


if __name__ == "__main__":
    from src import db

    with db.session() as con:
        print(kjor(con))
