"""Kontekstdata til hjernen — gratis kilder, uten KI.

1. MFN (samme åpne feed som innsidearkivet, kompakt format uten brødtekst):
   kvartals-/årsrapporter, tilbakekjøp og emisjoner per selskap. Gir
   «dager siden rapport», «tilbakekjøp siste 90 d» og «emisjon siste 90 d».
   Selskapenes ISIN lagres samtidig (til shortregisteret).
2. Finanstilsynets shortregister (SSR, åpent API): netto shortandel per
   ISIN og dag for Oslo-aksjer. API-et gir to år bakover; vi lagrer alt vi
   ser, så historikken vokser.
3. Yahoo (yfinance): sektor, bransje og antall aksjer -> børsverdi på
   hendelsesdagen (aksjer x sluttkurs).

Alt er idempotent og trygt å kjøre flere ganger.
"""
from __future__ import annotations

import time
from datetime import date, datetime, timedelta

import requests

from src.config import HTTP_TIMEOUT, TZ, USER_AGENT

MFN = "https://mfn.se/all/s/nordic.json"
MFN_TAGGER = {
    "sub:report": "rapport",
    "sub:ca:shares:repurchase": "tilbakekjop",
    "sub:ca:shares:issuance": "emisjon",
}
SIDE = 500
SSR = "https://ssr.finanstilsynet.no/api/v2/instruments"
INFO_FORELDET_DAGER = 30


def _okt() -> requests.Session:
    okt = requests.Session()
    okt.headers.update({"User-Agent": USER_AGENT})
    return okt


def _lokal(utc: str) -> str:
    t = datetime.fromisoformat(utc.replace("Z", "+00:00")).astimezone(TZ)
    return t.replace(tzinfo=None).isoformat(timespec="seconds")


def _hent_json(okt: requests.Session, url: str, forsok: int = 3):
    for n in range(forsok):
        try:
            r = okt.get(url, timeout=max(HTTP_TIMEOUT, 60))
            r.raise_for_status()
            return r.json()
        except (requests.RequestException, ValueError):
            if n == forsok - 1:
                raise
            time.sleep(5 * (n + 1))
    return None


def lagre_mfn(con, items: list[dict], type_: str, kjente: set[str]) -> int:
    """Lagrer meldinger for kjente selskaper. Returnerer antall nye."""
    nye = 0
    for it in items:
        a = it.get("author") or {}
        slug = a.get("slug")
        c = it.get("content") or {}
        if slug not in kjente or not c.get("publish_date"):
            continue
        for isin in a.get("isins") or []:
            con.execute("INSERT OR IGNORE INTO selskap_isin (slug, isin) VALUES (?,?)", (slug, isin))
        gid = it.get("group_id") or it.get("news_id")
        fantes = con.execute("SELECT url FROM selskapshendelser WHERE group_id = ?", (gid,)).fetchone()
        if fantes is None:
            con.execute(
                "INSERT INTO selskapshendelser (group_id, slug, type, publisert, url, tittel) VALUES (?,?,?,?,?,?)",
                (gid, slug, type_, _lokal(c["publish_date"]), it.get("url"), (c.get("title") or "")[:300]),
            )
            nye += 1
        elif not fantes[0] and it.get("url"):  # eldre rader uten lenke (før rapporttolkningen)
            con.execute("UPDATE selskapshendelser SET url = ?, tittel = ? WHERE group_id = ?",
                        (it.get("url"), (c.get("title") or "")[:300], gid))
    return nye


def hent_mfn(con, fra: str | None = None, maks_sider: int | None = None, pause: float = 1.0) -> dict:
    """Blar bakover i hver tagg til «fra». Uten «fra»: fra siste lagrede
    melding minus 3 dager (inkrementelt), eller 2022-06-01 første gang."""
    kjente = {r[0] for r in con.execute("SELECT slug FROM selskaper")}
    okt = _okt()
    stat: dict[str, int] = {}
    for tagg, type_ in MFN_TAGGER.items():
        grense = fra
        if grense is None:
            siste = con.execute("SELECT MAX(publisert) FROM selskapshendelser WHERE type = ?", (type_,)).fetchone()[0]
            grense = (date.fromisoformat(siste[:10]) - timedelta(days=3)).isoformat() if siste else "2022-06-01"
        offset, sider, nye = 0, 0, 0
        while maks_sider is None or sider < maks_sider:
            d = _hent_json(okt, f"{MFN}?tag={tagg}&limit={SIDE}&offset={offset}&compact=true")
            items = (d or {}).get("items") or []
            if not items:
                break
            sider += 1
            nye += lagre_mfn(con, items, type_, kjente)
            eldste = min((it.get("content") or {}).get("publish_date", "9999") for it in items)
            con.commit()
            if eldste[:10] < grense:
                break
            offset += SIDE
            time.sleep(pause)
        stat[type_] = nye
    return stat


def lagre_short(con, instrumenter: list[dict]) -> int:
    n = 0
    for ins in instrumenter:
        isin = ins.get("isin")
        for e in ins.get("events") or []:
            if not isin or not e.get("date") or e.get("shortPercent") is None:
                continue
            con.execute(
                "INSERT INTO short (isin, dato, prosent) VALUES (?,?,?) "
                "ON CONFLICT(isin, dato) DO UPDATE SET prosent = excluded.prosent",
                (isin, e["date"][:10], float(e["shortPercent"])),
            )
            n += 1
    return n


def hent_short(con) -> int:
    return lagre_short(con, _hent_json(_okt(), SSR) or [])


def hent_info(con, maks: int | None = None, pause: float = 1.0) -> dict:
    """Sektor og antall aksjer fra Yahoo for selskaper som mangler info eller
    har info eldre enn INFO_FORELDET_DAGER. `maks` begrenser antall oppslag
    per kjøring (Yahoo struper ved mange kall)."""
    import yfinance as yf

    grense = (datetime.now(TZ) - timedelta(days=INFO_FORELDET_DAGER)).isoformat()
    symboler = [r[0] for r in con.execute(
        "SELECT DISTINCT s.yahoo FROM selskaper s LEFT JOIN selskapsinfo i ON i.yahoo = s.yahoo "
        "WHERE s.yahoo IS NOT NULL AND s.yahoo <> '' AND (i.oppdatert IS NULL OR i.oppdatert < ?) "
        "ORDER BY i.oppdatert IS NOT NULL, i.oppdatert", (grense,))]
    if maks is not None:
        symboler = symboler[:maks]
    stat = {"forsokt": len(symboler), "ok": 0, "tomme": 0, "feil": 0}
    for sym in symboler:
        try:
            info = yf.Ticker(sym).get_info() or {}
        except Exception:  # noqa: BLE001 — Yahoo feiler på mange måter
            stat["feil"] += 1
            time.sleep(pause * 3)
            continue
        aksjer = info.get("sharesOutstanding") or info.get("impliedSharesOutstanding")
        if not (info.get("sector") or aksjer):
            stat["tomme"] += 1
        else:
            stat["ok"] += 1
        con.execute(
            "INSERT INTO selskapsinfo (yahoo, sektor, bransje, aksjer, oppdatert) VALUES (?,?,?,?,?) "
            "ON CONFLICT(yahoo) DO UPDATE SET sektor = COALESCE(excluded.sektor, selskapsinfo.sektor), "
            " bransje = COALESCE(excluded.bransje, selskapsinfo.bransje), "
            " aksjer = COALESCE(excluded.aksjer, selskapsinfo.aksjer), oppdatert = excluded.oppdatert",
            (sym, info.get("sector"), info.get("industry"), float(aksjer) if aksjer else None,
             datetime.now(TZ).isoformat(timespec="seconds")),
        )
        con.commit()
        time.sleep(pause)
    return stat
