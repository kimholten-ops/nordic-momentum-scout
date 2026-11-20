"""Kurser fra Euronext for Oslo-aksjer Yahoo mangler.

Yahoo sletter historikken når en aksje tas av børs (oppkjøp, fusjon,
konkurs), og har for noen få aksjer bare én eller noen få dager. Det gir
skjevhet: innsidekjøp i selskaper som senere forsvant, faller ut av
læringen. Euronext sin åpne nedlasting gir ca. to år bakover per ISIN, også
for avnoterte aksjer.

- Aksjer uten noen Yahoo-kurser: hele Euronext-serien lagres.
- Aksjer med kort Yahoo-historikk: Euronext fyller bare dager FØR Yahoo
  starter, og bare hvis kursene stemmer på overgangsdagen (±3 %) — ellers
  kan en splitt eller emisjon gi et falskt hopp.
- Kursene er justert for splitt o.l. (adjusted=Y). Dager uten handel har
  0 i åpning/høy/lav: lagres som NULL.
"""
from __future__ import annotations

import time
from datetime import date, datetime, timedelta

import requests

from src.config import HTTP_TIMEOUT, TZ, USER_AGENT

URL = ("https://live.euronext.com/en/ajax/AwlHistoricalPrice/getFullDownloadAjax/{isin}-{mic}"
       "?format=csv&decimal_separator=.&date_form=d/m/Y&op=&adjusted=Y&base100=&startdate={fra}&enddate={til}")
MIC_OSLO = ("XOSL", "XOAS", "MERK")  # Oslo Børs, Euronext Expand, Euronext Growth
KORT_HISTORIKK = 250                 # færre Yahoo-rader enn dette => prøv Euronext
SJEKK_PA_NYTT_DAGER = 30
OVERGANG_TOLERANSE = 0.03


def _tall(x: str) -> float | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if v > 0 else None


def parse_csv(tekst: str) -> list[tuple]:
    """-> [(dato, open, high, low, close, volum)], sortert på dato."""
    ut = []
    for linje in tekst.splitlines():
        deler = linje.strip().split(";")
        if len(deler) < 7 or "/" not in deler[0]:
            continue
        try:
            d = datetime.strptime(deler[0], "%d/%m/%Y").date().isoformat()
        except ValueError:
            continue
        close = _tall(deler[5]) or _tall(deler[4])
        if close is None:
            continue
        vol = _tall(deler[6])
        ut.append((d, _tall(deler[1]), _tall(deler[2]), _tall(deler[3]), close, int(vol) if vol else 0))
    return sorted(set(ut))


def rens(rader: list[tuple], spiss: float = 0.4) -> list[tuple]:
    """Fjerner åpenbare datafeil: én dag som hopper >40 % og straks tilbake
    (feilhandel), og kurser under 2 % av dagen før (avnoteringsrester)."""
    ut = list(rader)
    i = 1
    while i < len(ut):
        forrige, na = ut[i - 1][4], ut[i][4]
        neste = ut[i + 1][4] if i + 1 < len(ut) else None
        tilbake = neste is not None and abs(na / forrige - 1) > spiss and abs(neste / forrige - 1) < spiss / 2
        if na < forrige * 0.02 or tilbake:
            del ut[i]
            continue
        i += 1
    return ut


def hent_serie(okt: requests.Session, isiner: list[str]) -> list[tuple]:
    fra, til = (date.today() - timedelta(days=3650)).isoformat(), date.today().isoformat()
    beste: dict[str, tuple] = {}
    for isin in isiner:
        for mic in MIC_OSLO:
            r = okt.get(URL.format(isin=isin, mic=mic, fra=fra, til=til), timeout=max(HTTP_TIMEOUT, 60))
            if r.status_code != 200:
                continue
            for rad in parse_csv(r.content.decode("utf-8-sig", errors="replace")):
                beste.setdefault(rad[0], rad)  # første treff per dato vinner
            time.sleep(0.5)
    return rens([beste[d] for d in sorted(beste)])


def _lagre(con, yahoo: str, rader: list[tuple]) -> int:
    n = 0
    for d, o, h, lo, c, v in rader:
        n += con.execute(
            "INSERT OR IGNORE INTO kurser (yahoo, dato, open, high, low, close, volum) VALUES (?,?,?,?,?,?,?)",
            (yahoo, d, o, h, lo, c, v)).rowcount
    return n


def fyll(con, yahoo: str, rader: list[tuple]) -> tuple[str, int]:
    """Fyller kurser for ett symbol etter reglene øverst. -> (utfall, nye rader)."""
    if not rader:
        return "ingen data", 0
    forste = con.execute("SELECT MIN(dato), COUNT(*) FROM kurser WHERE yahoo = ? AND close IS NOT NULL", (yahoo,)).fetchone()
    if not forste[1]:
        return "hele serien", _lagre(con, yahoo, rader)
    d0 = forste[0]
    y0 = con.execute("SELECT close FROM kurser WHERE yahoo = ? AND dato = ?", (yahoo, d0)).fetchone()[0]
    e0 = next((r[4] for r in rader if r[0] == d0), None)
    if e0 is None or abs(e0 / y0 - 1) > OVERGANG_TOLERANSE:
        return "avvik ved overgang", 0
    return "fylt bakover", _lagre(con, yahoo, [r for r in rader if r[0] < d0])


def kjor(con, maks: int | None = None) -> dict:
    con.execute("CREATE TABLE IF NOT EXISTS kurskilde (yahoo TEXT PRIMARY KEY, utfall TEXT, rader INTEGER, sjekket TEXT)")
    grense = (datetime.now(TZ) - timedelta(days=SJEKK_PA_NYTT_DAGER)).isoformat()
    mal = con.execute(
        "SELECT s.yahoo, GROUP_CONCAT(DISTINCT i.isin) FROM selskaper s JOIN selskap_isin i ON i.slug = s.slug "
        "WHERE s.yahoo LIKE '%.OL' "
        "AND (SELECT COUNT(*) FROM kurser k WHERE k.yahoo = s.yahoo AND k.close IS NOT NULL) < ? "
        "AND s.yahoo NOT IN (SELECT yahoo FROM kurskilde WHERE sjekket > ?) GROUP BY s.yahoo",
        (KORT_HISTORIKK, grense)).fetchall()
    if maks:
        mal = mal[:maks]
    okt = requests.Session()
    okt.headers.update({"User-Agent": USER_AGENT})
    stat: dict[str, int] = {"symboler": len(mal), "nye_rader": 0}
    for yahoo, isiner in mal:
        try:
            utfall, n = fyll(con, yahoo, hent_serie(okt, (isiner or "").split(",")))
        except requests.RequestException as e:
            utfall, n = f"feil: {type(e).__name__}", 0
        stat[utfall] = stat.get(utfall, 0) + 1
        stat["nye_rader"] += n
        con.execute("INSERT OR REPLACE INTO kurskilde (yahoo, utfall, rader, sjekket) VALUES (?,?,?,?)",
                    (yahoo, utfall, n, datetime.now(TZ).isoformat(timespec="seconds")))
        con.commit()
    return stat
