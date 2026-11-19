"""Henter historiske innsidemeldinger fra MFNs åpne JSON-feed — uten KI.

  python -m src.innside.arkiv --fra 2023-01-01

Kilden er https://mfn.se/all/s/nordic.json?tag=sub:ci:insider, som gir hele
meldingsteksten og selskapets tickere (børs:symbol, f.eks. XOSL:EQNR) og
ISIN. Feeden er udokumentert: vi henter rolig (pause mellom sidene) og
identifiserer oss med MOMENTUM_UA.

For hver melding:
  1. lagres den i «nyheter» (uten brødtekst — databasen committes til git,
     og teksten kan hentes på nytt fra MFN ved behov),
  2. tolkes den med regelparserne (Nasdaq-skjema og fritekst),
  3. meldinger reglene ikke klarer, merkes «trenger_llm» og kan tolkes
     senere av KI eller lokal Ollama (python -m src.innside_kjor).
Selskaper med minst ett innsidekjøp legges i universe/tickers.csv med
Yahoo-ticker utledet fra MFN-tickeren. Trygt å kjøre flere ganger.
"""
from __future__ import annotations

import argparse
import csv
import time
from datetime import datetime, timezone

import requests

from src import db
from src.config import HTTP_TIMEOUT, TZ, UNIVERSE_CSV, USER_AGENT
from src.innside.deteksjon import er_innsidemelding, er_korreksjon
from src.innside.kjor import EKSTRAKSJON_VERSJON, _lagre_handler, _marker, tolk_med_regler
from src.universe import KOLONNER, last_universe

FEED = "https://mfn.se/all/s/nordic.json?tag=sub:ci:insider"
SIDE = 500

# Markedsidentifikator (MIC) i MFN -> Yahoo-suffiks og børsnavn.
MIC_TIL_YAHOO = {
    "XOSL": (".OL", "Oslo Børs"), "XOAS": (".OL", "Euronext Growth Oslo"), "MERK": (".OL", "Euronext Expand Oslo"),
    "XSTO": (".ST", "Nasdaq Stockholm"), "FNSE": (".ST", "First North Stockholm"),
    "XHEL": (".HE", "Nasdaq Helsinki"), "FNFI": (".HE", "First North Helsinki"),
    "XCSE": (".CO", "Nasdaq København"), "FNDK": (".CO", "First North København"),
}


def yahoo_fra_mfn(tickers: list[str] | None) -> tuple[str, str, str] | None:
    """(ticker, yahoo, børs) for første nordiske notering, ellers None.
    «XSTO:VOLV B» -> ("VOLV B", "VOLV-B.ST", "Nasdaq Stockholm")."""
    for t in tickers or []:
        mic, _, sym = t.partition(":")
        if mic in MIC_TIL_YAHOO and sym:
            suff, bors = MIC_TIL_YAHOO[mic]
            return sym, sym.strip().replace(" ", "-") + suff, bors
    return None


def lokal_tid(utc: str) -> str:
    """MFN gir UTC («2026-11-19T08:41:49Z»); databasen bruker norsk lokal tid."""
    t = datetime.fromisoformat(utc.replace("Z", "+00:00")).astimezone(TZ)
    return t.replace(tzinfo=None).isoformat(timespec="seconds")


def hent_side(offset: int, okt: requests.Session, forsok: int = 3) -> list[dict]:
    for n in range(forsok):
        try:
            r = okt.get(f"{FEED}&limit={SIDE}&offset={offset}", timeout=max(HTTP_TIMEOUT, 60))
            r.raise_for_status()
            return r.json().get("items") or []
        except (requests.RequestException, ValueError):
            if n == forsok - 1:
                raise
            time.sleep(5 * (n + 1))
    return []


def behandle(con, item: dict, na: str, stat: dict) -> dict | None:
    """Lagrer og tolker én melding. Returnerer selskapsinfo hvis den ga et
    innsidekjøp i aksjer (for å utvide universet)."""
    c = item.get("content") or {}
    tittel = c.get("title") or ""
    if not er_innsidemelding(tittel):
        stat["ikke_handel"] += 1  # f.eks. flaggemeldinger
        return None
    forfatter = item.get("author") or {}
    kart = yahoo_fra_mfn(forfatter.get("tickers"))
    if not kart:
        stat["ikke_nordisk"] += 1
        return None
    ticker, yahoo, bors = kart
    slug = forfatter.get("slug") or ""
    publisert = lokal_tid(c["publish_date"])
    cur = con.execute(
        "INSERT OR IGNORE INTO nyheter (url, publisert, hentet, selskap_slug, selskap_navn, tittel, sprak, "
        " distributor, ticker, yahoo) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (item["url"], publisert, na, slug, forfatter.get("name"), tittel,
         (item.get("properties") or {}).get("lang"), item.get("source"), ticker, yahoo),
    )
    n = con.execute("SELECT id, url, publisert, selskap_slug, tittel FROM nyheter WHERE url = ?", (item["url"],)).fetchone()
    if not cur.rowcount:
        stat["fantes"] += 1
        if con.execute("SELECT 1 FROM innside_behandlet WHERE nyhet_id = ? AND versjon = ?",
                       (n["id"], EKSTRAKSJON_VERSJON)).fetchone():
            return None
    else:
        stat["nye"] += 1
    handler, kilde = tolk_med_regler(c.get("text") or "", publisert)
    if handler is None:
        _marker(con, n["id"], "trenger_llm", "arkiv: reglene ga opp", na)
        stat["trenger_llm"] += 1
        return None
    _lagre_handler(con, n, handler, kilde, er_korreksjon(tittel))
    _marker(con, n["id"], "ok", f"arkiv {kilde}: {len(handler)} handler", na)
    stat[kilde] = stat.get(kilde, 0) + 1
    if any(h.retning == "kjop" and h.instrument == "aksje" for h in handler):
        return {"slug": slug, "navn": (forfatter.get("name") or slug).replace(",", " "), "ticker": ticker,
                "yahoo": yahoo, "marked": bors, "aktiv": 1}
    return None


def utvid_univers(con, selskaper: dict[str, dict]) -> int:
    kjente = {r[0] for r in con.execute("SELECT slug FROM selskaper")}
    nye = [s for slug, s in sorted(selskaper.items()) if slug and slug not in kjente]
    if nye:
        ny_fil = not UNIVERSE_CSV.exists()
        with UNIVERSE_CSV.open("a", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=KOLONNER)
            if ny_fil:
                w.writeheader()
            w.writerows(nye)
        last_universe(con)
    return len(nye)


def kjor(fra: str, maks_sider: int | None = None, pause: float = 1.0) -> dict:
    stat = {"sider": 0, "meldinger": 0, "nye": 0, "fantes": 0, "ikke_handel": 0, "ikke_nordisk": 0,
            "regel": 0, "fritekst": 0, "trenger_llm": 0, "eldste": None}
    selskaper: dict[str, dict] = {}
    okt = requests.Session()
    okt.headers.update({"User-Agent": USER_AGENT})
    na = datetime.now(TZ).isoformat(timespec="seconds")
    with db.session() as con:
        last_universe(con)
        offset = 0
        while maks_sider is None or stat["sider"] < maks_sider:
            items = hent_side(offset, okt)
            if not items:
                break
            stat["sider"] += 1
            for it in items:
                if (it.get("content") or {}).get("publish_date", "9999") < fra:
                    continue
                stat["meldinger"] += 1
                s = behandle(con, it, na, stat)
                if s:
                    selskaper.setdefault(s["slug"], s)
            con.commit()
            eldste = items[-1]["content"]["publish_date"]
            stat["eldste"] = eldste
            print(f"side {stat['sider']}: til {eldste[:10]}  (regel {stat['regel']}, fritekst {stat['fritekst']}, "
                  f"venter på KI {stat['trenger_llm']})", flush=True)
            if eldste < fra:
                break
            offset += SIDE
            time.sleep(pause)
        stat["nye_selskaper"] = utvid_univers(con, selskaper)
        db.logg(con, "innside_arkiv", "ok", ", ".join(f"{k} {v}" for k, v in stat.items()))
    return stat


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--fra", default="2023-01-01", help="eldste publiseringsdato (YYYY-MM-DD)")
    p.add_argument("--maks-sider", type=int, default=None)
    p.add_argument("--pause", type=float, default=1.0, help="sekunder mellom sidene")
    a = p.parse_args()
    s = kjor(datetime.fromisoformat(a.fra).replace(tzinfo=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
             a.maks_sider, a.pause)
    print(s)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
