"""Offentlige shortposisjoner i Sverige og Finland (Norge: src/sources/kontekst.py).

Finanstilsynene publiserer hver enkelt aktørs netto shortposisjon når den er
0,5 % eller mer av aksjene, og en ny melding når den endres eller faller
under 0,5 %. Vi rekonstruerer summen per aksje per dag og lagrer den i
tabellen `short` (isin, dato, prosent), samme form som de norske tallene.

- Sverige: Finansinspektionens historikkfil og aktuelle posisjoner (.ods).
- Finland: Finanssivalvontas tabell (samme data som «lyhyet positiot»).
Danmark har ikke en tilsvarende maskinlesbar historikk og er ikke med ennå.
"""
from __future__ import annotations

import io
from collections import defaultdict

import requests

from src.config import HTTP_TIMEOUT

UA = {"User-Agent": "Mozilla/5.0"}
SE_FILER = ["https://www.fi.se/BlankningsRegister/GetHistFile", "https://www.fi.se/BlankningsRegister/GetAktuellFile"]
FI_URL = "https://www.finanssivalvonta.fi/api/shortselling/datatable/all"
FI_KOLONNER = ["positionHolder", "issuerName", "isinCode", "netShortPositionInPercent", "positionDate"]
FRA = "2018-01-01"


def _pst(v) -> float:
    """'1,45' / 1.45 / '<0,5' -> andel i prosent; under 0,5 regnes som 0 (ikke offentlig)."""
    if v is None:
        return 0.0
    s = str(v).strip().replace(",", ".")
    if s.startswith("<"):
        return 0.0
    try:
        x = float(s)
    except ValueError:
        return 0.0
    return x if x >= 0.5 else 0.0


def summer(hendelser: list[tuple[str, str, str, float]]) -> list[tuple[str, str, float]]:
    """[(isin, aktør, dato, prosent)] -> [(isin, dato, sum av aktørenes siste posisjon)]."""
    per_isin: dict[str, list] = defaultdict(list)
    for isin, aktor, dato, p in hendelser:
        if isin and dato:
            per_isin[isin].append((dato, aktor, p))
    ut = []
    for isin, liste in per_isin.items():
        liste.sort()
        siste: dict[str, float] = {}
        for i, (dato, aktor, p) in enumerate(liste):
            siste[aktor] = p
            if i + 1 == len(liste) or liste[i + 1][0] != dato:
                ut.append((isin, dato, round(sum(siste.values()), 4)))
    return ut


def hent_se() -> list[tuple[str, str, str, float]]:
    import pandas as pd

    rader = []
    for url in SE_FILER:
        r = requests.get(url, headers=UA, timeout=HTTP_TIMEOUT * 6)
        r.raise_for_status()
        df = pd.read_excel(io.BytesIO(r.content), engine="odf", header=5)
        df = df.iloc[:, :5]
        df.columns = ["aktor", "emittent", "isin", "pst", "dato"]
        for x in df.itertuples(index=False):
            dato = str(x.dato)[:10]
            if isinstance(x.isin, str) and len(dato) == 10 and dato[4] == "-":
                rader.append((x.isin.strip(), str(x.aktor).strip(), dato, _pst(x.pst)))
    return rader


def hent_fi() -> list[tuple[str, str, str, float]]:
    data = {"draw": "1", "start": "0", "length": "100000", "lang": "fi", "search[value]": "", "search[regex]": "false"}
    for i, k in enumerate(FI_KOLONNER):
        data.update({f"columns[{i}][data]": k, f"columns[{i}][name]": "", f"columns[{i}][searchable]": "true",
                     f"columns[{i}][orderable]": "false", f"columns[{i}][search][value]": "",
                     f"columns[{i}][search][regex]": "false"})
    r = requests.post(FI_URL, data=data, headers={**UA, "X-Requested-With": "XMLHttpRequest"}, timeout=HTTP_TIMEOUT * 6)
    r.raise_for_status()
    return [(x.get("isinCode") or "", (x.get("positionHolder") or "").strip(), (x.get("positionDate") or "")[:10],
             _pst(x.get("netShortPositionInPercent"))) for x in r.json().get("data") or []]


def kjor(con) -> dict:
    kjente = {r[0] for r in con.execute("SELECT DISTINCT isin FROM selskap_isin")}
    ut = {}
    for land, hent in (("SE", hent_se), ("FI", hent_fi)):
        try:
            rader = [r for r in summer(hent()) if r[0] in kjente and r[1] >= FRA]
        except Exception as e:  # noqa: BLE001 — ett land feiler ikke de andre
            ut[land] = f"feil: {type(e).__name__}: {e}"[:200]
            continue
        con.executemany("INSERT INTO short (isin, dato, prosent) VALUES (?,?,?) "
                        "ON CONFLICT(isin, dato) DO UPDATE SET prosent = excluded.prosent", rader)
        ut[land] = len(rader)
    con.commit()
    return ut
