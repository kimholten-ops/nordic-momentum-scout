"""Lager universe/usa.csv: amerikanske aksjer til faktormodellen.

Utvalg: børsverdi 1–150 mrd. USD og kurs over 3 USD, de 500 mest omsatte
(kurs x volum siste dag i Nasdaqs aksjeliste), pluss egne aksjer (MINE).
Listen er et øyeblikksbilde av dagens noterte aksjer — historikken har derfor
overlevelsesskjevhet (aksjer som forsvant er ikke med). Oppdateres sjelden.

  python -m scripts.oppdater_usa
"""
from __future__ import annotations

import csv
import sys

import requests

from src.config import ROOT

URL = "https://api.nasdaq.com/api/screener/stocks?tableonly=true&limit=10000&download=true"
MINE = ["SOFI", "ASTS", "IREN", "ONDS"]
ANTALL = 500
FIL = ROOT / "universe" / "usa.csv"


def _tall(x: str) -> float:
    try:
        return float(str(x).replace("$", "").replace(",", "") or 0)
    except ValueError:
        return 0.0


def main() -> int:
    r = requests.get(URL, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"}, timeout=60)
    r.raise_for_status()
    rader = r.json()["data"]["rows"]
    kand = []
    for x in rader:
        sym = (x.get("symbol") or "").strip().replace("/", "-")
        mcap, kurs, vol = _tall(x.get("marketCap")), _tall(x.get("lastsale")), _tall(x.get("volume"))
        if not sym or "^" in sym or x.get("country") not in ("United States", "", None):
            continue
        kand.append({"symbol": sym, "navn": (x.get("name") or "")[:80], "sektor": x.get("sector") or "",
                     "bransje": x.get("industry") or "", "borsverdi": mcap, "omsetning": kurs * vol, "kurs": kurs})
    valgt = sorted((k for k in kand if 1e9 <= k["borsverdi"] <= 150e9 and k["kurs"] >= 3),
                   key=lambda k: -k["omsetning"])[:ANTALL]
    have = {k["symbol"] for k in valgt}
    for m in MINE:
        if m not in have:
            valgt += [k for k in kand if k["symbol"] == m] or [{"symbol": m, "navn": m, "sektor": "", "bransje": "",
                                                                 "borsverdi": 0, "omsetning": 0, "kurs": 0}]
    with FIL.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["symbol", "navn", "sektor", "bransje", "mine"])
        for k in sorted(valgt, key=lambda k: k["symbol"]):
            w.writerow([k["symbol"], k["navn"], k["sektor"], k["bransje"], int(k["symbol"] in MINE)])
    print(f"{len(valgt)} amerikanske aksjer skrevet til {FIL} ({sum(k['symbol'] in MINE for k in valgt)} egne).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
