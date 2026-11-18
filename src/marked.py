"""Kursoppslag delt mellom backtest og hjerne: inngang/utgang, meravkastning
mot børsens indeks, likviditet og handelskostnad.

Regler som gjelder overalt:
- En hendelse er kjent når den er PUBLISERT. Inngang er åpningskursen første
  handelsdag etter publiseringsdagen — aldri samme dag, selv om meldingen kom
  før børsåpning (vi poller for sjelden til å stole på det).
- Utgang er sluttkurs h handelsdager etter inngangsdagen.
- Meravkastning = aksjens avkastning minus indeksens avkastning over samme
  dager (indeksen fra inngangsdagens sluttkurs forrige dag til utgangsdagen).
- Mangler kurs, er utfallet None — aldri imputert.
"""
from __future__ import annotations

import statistics
from bisect import bisect_right
from dataclasses import dataclass
from datetime import date

from src.config import (
    INDEKSER,
    INNSIDE_FX_NOK,
    KURTASJE_KR,
    POSISJON_KR,
    SLIPPAGE_ETTER_OMSETNING,
)

VALUTA_ETTER_SUFFIKS = {".OL": "NOK", ".ST": "SEK", ".HE": "EUR", ".CO": "DKK"}


@dataclass
class Serie:
    datoer: list[str]
    open: list[float | None]
    high: list[float | None]
    low: list[float | None]
    close: list[float | None]
    volum: list[int | None]

    def indeks_etter(self, dag: str) -> int:
        """Første posisjon med dato STRENGT etter `dag` (YYYY-MM-DD)."""
        return bisect_right(self.datoer, dag)

    def indeks_til_og_med(self, dag: str) -> int:
        """Siste posisjon med dato <= dag, eller -1."""
        return bisect_right(self.datoer, dag) - 1


def last_kurser(con) -> dict[str, Serie]:
    ut: dict[str, Serie] = {}
    for r in con.execute("SELECT yahoo, dato, open, high, low, close, volum FROM kurser ORDER BY yahoo, dato"):
        s = ut.get(r["yahoo"])
        if s is None:
            s = ut[r["yahoo"]] = Serie([], [], [], [], [], [])
        s.datoer.append(r["dato"])
        s.open.append(r["open"])
        s.high.append(r["high"])
        s.low.append(r["low"])
        s.close.append(r["close"])
        s.volum.append(r["volum"])
    return ut


def suffiks(yahoo: str | None) -> str | None:
    if not yahoo:
        return None
    return next((s for s in INDEKSER if yahoo.upper().endswith(s)), None)


def indeks_for(yahoo: str | None) -> str | None:
    s = suffiks(yahoo)
    return INDEKSER.get(s) if s else None


def omsetning_nok(serie: Serie, i: int, dager: int = 20) -> float | None:
    """Median daglig omsetning (kurs x volum) i NOK de `dager` dagene FØR i."""
    fra = max(0, i - dager)
    verdier = [
        c * v for c, v in zip(serie.close[fra:i], serie.volum[fra:i]) if c and v
    ]
    if not verdier:
        return None
    return statistics.median(verdier)


def kostnad(omsetning: float | None, posisjon_kr: float = POSISJON_KR) -> float:
    """Kostnad for en hel runde (kjøp + salg) som andel av posisjonen."""
    kurtasje = 2 * KURTASJE_KR / posisjon_kr
    if omsetning is None:
        return kurtasje + 2 * SLIPPAGE_ETTER_OMSETNING[-1][1]
    slipp = next(s for grense, s in SLIPPAGE_ETTER_OMSETNING if omsetning >= grense)
    return kurtasje + 2 * slipp


def til_nok(belop: float | None, yahoo: str | None) -> float | None:
    if belop is None:
        return None
    valuta = VALUTA_ETTER_SUFFIKS.get(suffiks(yahoo) or "", "NOK")
    return belop * INNSIDE_FX_NOK.get(valuta, 1.0)


@dataclass
class Utfall:
    inn_dato: str
    ut_dato: str
    inn: float
    ut: float
    raa: float            # brutto avkastning
    marked: float | None  # indeksens avkastning samme periode
    mer: float            # meravkastning (raa - marked, eller raa uten indeks)
    kost: float
    netto_mer: float      # mer - kost


MAKS_DAGER_TIL_INNGANG = 7


def inngangsindeks(s: Serie, publisert: str) -> int | None:
    """Posisjonen til inngangsdagen, eller None når den ikke kan fastslås:
    hendelsen er eldre enn kurshistorikken (i == 0), eller neste kursdag er
    mer enn en uke unna (aksjen handlet ikke da)."""
    i = s.indeks_etter(publisert[:10])
    if i == 0 or i >= len(s.datoer):
        return None
    if (date.fromisoformat(s.datoer[i]) - date.fromisoformat(publisert[:10])).days > MAKS_DAGER_TIL_INNGANG:
        return None
    return i


def utfall(kurser: dict[str, Serie], yahoo: str, publisert: str, h: int) -> Utfall | None:
    s = kurser.get(yahoo)
    if not s:
        return None
    i = inngangsindeks(s, publisert)
    if i is None or i + h >= len(s.datoer):
        return None
    inn, ut = s.open[i], s.close[i + h]
    if not inn or not ut:
        return None
    raa = ut / inn - 1
    marked = None
    idx = kurser.get(indeks_for(yahoo) or "")
    if idx:
        # Samme vindu som aksjen: indeksens åpning inngangsdagen (eller forrige
        # sluttkurs hvis åpning mangler) til sluttkurs utgangsdagen.
        a = idx.indeks_til_og_med(s.datoer[i])
        b = idx.indeks_til_og_med(s.datoer[i + h])
        if a >= 0 and b >= a and idx.close[b]:
            start = idx.open[a] if idx.datoer[a] == s.datoer[i] and idx.open[a] else idx.close[a]
            if start:
                marked = idx.close[b] / start - 1
    mer = raa - (marked or 0.0)
    oms = til_nok(omsetning_nok(s, i), yahoo)
    k = kostnad(oms)
    return Utfall(s.datoer[i], s.datoer[i + h], inn, ut, raa, marked, mer, k, mer - k)


def atr_andel(serie: Serie, i: int, dager: int = 20) -> float | None:
    """Gjennomsnittlig true range / kurs de `dager` dagene før i."""
    fra = max(1, i - dager)
    tr = []
    for j in range(fra, i):
        h, lo, pc = serie.high[j], serie.low[j], serie.close[j - 1]
        if h and lo and pc:
            tr.append(max(h - lo, abs(h - pc), abs(lo - pc)) / pc)
    return sum(tr) / len(tr) if tr else None
