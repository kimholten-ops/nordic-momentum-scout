"""Walk-forward: spill av historien dag for dag som om hjernen var i drift.

For hver handelsdag D:
  1. tren modellen på hendelser med utfall kjent før D,
  2. vurder hendelsene med inngang D (lagres alle, valgt eller ikke),
  3. åpne papirhandler for de beste, innenfor ledig plass,
  4. følg åpne handler: stopp (dagens low under stopp) eller tidsutgang.

Etter siste dag lages forslag for hendelser som ennå ikke har en inngangsdag
(publisert etter siste kursdag) — det er disse som varsles.
"""
from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from datetime import date, timedelta

from src import marked
from src.config import (
    HJERNE_HALVERINGSTID,
    HJERNE_HORISONT,
    HJERNE_KAPITAL,
    HJERNE_LIKVIDITET_TAK,
    HJERNE_MAKS_ANDEL,
    HJERNE_MIN_POSISJON,
    HJERNE_RISIKO,
    HJERNE_MAKS_APNE,
    HJERNE_MIN_EDGE,
    HJERNE_MIN_SIKKERHET,
    HJERNE_PRIOR_STYRKE,
    HJERNE_STOPP_ATR,
)
from src.hjerne.hendelser import Hendelse
from src.hjerne.modell import Modell, Observasjon, tren

STOPP_MIN, STOPP_MAKS = 0.04, 0.20
LIMIT_PAASLAG = 0.01  # maks inngang = siste sluttkurs + 1 %


@dataclass
class Param:
    horisont: int = HJERNE_HORISONT
    halveringstid: float = HJERNE_HALVERINGSTID
    prior: float = HJERNE_PRIOR_STYRKE
    min_edge: float = HJERNE_MIN_EDGE
    min_sikkerhet: float = HJERNE_MIN_SIKKERHET
    maks_apne: int = HJERNE_MAKS_APNE
    kapital: float = HJERNE_KAPITAL
    stopp_atr: float = HJERNE_STOPP_ATR

    maks_andel: float = HJERNE_MAKS_ANDEL
    risiko: float = HJERNE_RISIKO
    likviditet_tak: float = HJERNE_LIKVIDITET_TAK
    min_posisjon: float = HJERNE_MIN_POSISJON

    def storrelse(self, stopp_andel: float, omsetning_nok: float | None, kapital: float | None = None) -> float:
        """Beløp i kroner for én posisjon (se config for reglene)."""
        k = self.kapital if kapital is None else kapital
        grenser = [k * self.maks_andel, k * self.risiko / max(stopp_andel, 0.01)]
        if omsetning_nok:
            grenser.append(omsetning_nok * self.likviditet_tak)
        return round(min(grenser), -2)

    @property
    def posisjon(self) -> float:
        """Typisk posisjon (8 % stopp, god likviditet) — til visning."""
        return self.storrelse(0.08, None)


@dataclass
class Trade:
    nokkel: str
    slug: str
    navn: str
    yahoo: str
    inn_dato: str
    inn: float
    stopp: float
    plan_ut_dato: str | None
    pred: float
    ut_dato: str | None = None
    ut: float | None = None
    status: str = "åpen"   # åpen | stoppet | tid
    raa: float | None = None
    marked: float | None = None
    kost: float = 0.0
    netto: float | None = None
    pnl_kr: float | None = None
    belop: float = 0.0


@dataclass
class Resultat:
    prediksjoner: list[dict] = field(default_factory=list)
    trades: list[Trade] = field(default_factory=list)
    egenkapital: list[tuple[str, float]] = field(default_factory=list)
    forslag: list[dict] = field(default_factory=list)
    modell: Modell | None = None
    hoppet_over: int = 0  # hendelser uten kurs


def stopp_andel(serie: marked.Serie, i: int, k: float) -> float:
    atr = marked.atr_andel(serie, i) or 0.03
    return min(STOPP_MAKS, max(STOPP_MIN, k * atr))


def _egenskaper(h: Hendelse) -> dict[str, str]:
    return {"kilde": h.kilde, **h.egenskaper}


def _indeksavkastning(kurser, yahoo: str, fra: str, til: str) -> float | None:
    idx = kurser.get(marked.indeks_for(yahoo) or "")
    if not idx:
        return None
    a, b = idx.indeks_til_og_med(fra), idx.indeks_til_og_med(til)
    if a < 0 or b < a or not idx.close[b]:
        return None
    start = idx.open[a] if idx.datoer[a] == fra and idx.open[a] else idx.close[a]
    return idx.close[b] / start - 1 if start else None


def _legg_til_handelsdager(fra: date, n: int) -> date:
    d = fra
    while n > 0:
        d += timedelta(days=1)
        if d.weekday() < 5:
            n -= 1
    return d


def kjor(hendelser: list[Hendelse], kurser: dict[str, marked.Serie], p: Param | None = None) -> Resultat:
    p = p or Param()
    res = Resultat()
    siste_kursdag = max((s.datoer[-1] for s in kurser.values() if s.datoer), default=None)
    if not siste_kursdag:
        return res

    # Plasser hver hendelse i tid: inngangsdag og (hvis modent) utfall.
    planlagt: dict[str, list[tuple[Hendelse, int]]] = {}
    obs: list[tuple[str, Observasjon]] = []  # (kjent-dato, observasjon)
    live: list[Hendelse] = []
    for h in hendelser:
        s = kurser.get(h.yahoo)
        if not s or not s.datoer:
            res.hoppet_over += 1
            continue
        if s.indeks_etter(h.publisert[:10]) >= len(s.datoer):
            if h.publisert[:10] >= (date.fromisoformat(siste_kursdag) - timedelta(days=4)).isoformat():
                live.append(h)
            continue
        i = marked.inngangsindeks(s, h.publisert)
        if i is None:  # eldre enn kurshistorikken, eller aksjen handlet ikke
            res.hoppet_over += 1
            continue
        planlagt.setdefault(s.datoer[i], []).append((h, i))
        u = marked.utfall(kurser, h.yahoo, h.publisert, p.horisont)
        if u:
            obs.append((u.ut_dato, Observasjon(_egenskaper(h), u.netto_mer, u.ut_dato)))
    obs.sort(key=lambda x: x[0])
    utfall_for = {}
    for h in hendelser:
        if h.yahoo in kurser:
            u = marked.utfall(kurser, h.yahoo, h.publisert, p.horisont)
            if u:
                utfall_for[h.nokkel] = u.netto_mer

    apne: list[Trade] = []
    kapital = p.kapital
    alle_dager = sorted({d for s in kurser.values() for d in s.datoer if d >= min(planlagt, default=siste_kursdag)})
    modell: Modell | None = None
    trent_med = -1

    def tren_til(dag: str) -> Modell:
        nonlocal modell, trent_med
        n = 0
        while n < len(obs) and obs[n][0] < dag:
            n += 1
        if modell is None or n != trent_med:
            modell = tren([o for _, o in obs[:n]], dag, p.prior, p.halveringstid)
            trent_med = n
        return modell

    for dag in alle_dager:
        # 1) vurder dagens hendelser og åpne nye handler på åpningskurs
        kandidater = planlagt.get(dag, [])
        if kandidater:
            m = tren_til(dag)
            vurdert = []
            for h, i in kandidater:
                pred, sikk, bidrag = m.forutsi(_egenskaper(h))
                rad = {
                    "nokkel": h.nokkel, "kilde": h.kilde, "slug": h.slug, "navn": h.navn, "publisert": h.publisert,
                    "inn_dato": dag, "pred": pred, "sikkerhet": sikk, "valgt": 0,
                    "utfall": utfall_for.get(h.nokkel), "trent_n": m.n, "bidrag": bidrag[:4],
                    "beskrivelse": h.beskrivelse,
                }
                res.prediksjoner.append(rad)
                if pred >= p.min_edge and sikk >= p.min_sikkerhet:
                    vurdert.append((pred, h, i, rad))
            vurdert.sort(key=lambda x: -x[0])
            for pred, h, i, rad in vurdert:
                if len(apne) >= p.maks_apne or any(t.slug == h.slug for t in apne):
                    continue
                s = kurser[h.yahoo]
                inn = s.open[i]
                if not inn:
                    continue
                sa = stopp_andel(s, i, p.stopp_atr)
                belop = p.storrelse(sa, marked.til_nok(marked.omsetning_nok(s, i), h.yahoo), kapital)
                belop = min(belop, kapital - sum(x.belop for x in apne))  # bare ledig kontant
                if belop < p.min_posisjon:
                    continue
                # Salgsdag: kjent kursdag, ellers anslått frem i tid (ukedager).
                plan = (s.datoer[i + p.horisont] if i + p.horisont < len(s.datoer) else
                        _legg_til_handelsdager(date.fromisoformat(s.datoer[-1]), i + p.horisont - len(s.datoer) + 1).isoformat())
                t = Trade(h.nokkel, h.slug, h.navn, h.yahoo, dag, inn, inn * (1 - sa), plan, pred, belop=belop)
                apne.append(t)
                res.trades.append(t)
                rad["valgt"] = 1
        # 2) følg åpne handler gjennom dagen, også dem som åpnet i dag (en handel
        #    som skal ut på sluttkurs i dag teller som åpen ved dagens åpning)
        for t in list(apne):
            s = kurser[t.yahoo]
            j = s.indeks_til_og_med(dag)
            if j < 0 or s.datoer[j] != dag:
                continue
            lo, op = s.low[j], s.open[j]
            if lo is not None and lo <= t.stopp:
                _lukk(t, dag, min(op or t.stopp, t.stopp), "stoppet", kurser, p)
            elif t.plan_ut_dato == dag:
                _lukk(t, dag, s.close[j], "tid", kurser, p)
            if t.status != "åpen":
                apne.remove(t)
                kapital += t.pnl_kr or 0.0

        res.egenkapital.append((dag, round(kapital + sum(_urealisert(t, kurser, dag, p) for t in apne), 2)))

    # Åpne handler ved slutt: merk mot siste kurs (status forblir 'åpen').
    for t in apne:
        s = kurser[t.yahoo]
        t.ut, t.ut_dato = s.close[-1], s.datoer[-1]
        t.raa = t.ut / t.inn - 1 if t.ut else None

    # 3) forslag for hendelser uten inngangsdag ennå
    m = tren_til((date.fromisoformat(siste_kursdag) + timedelta(days=1)).isoformat())
    res.modell = m
    ledig = p.maks_apne - len(apne)
    rangert = []
    for h in live:
        pred, sikk, bidrag = m.forutsi(_egenskaper(h))
        rangert.append((pred, sikk, bidrag, h))
    rangert.sort(key=lambda x: -x[0])
    for pred, sikk, bidrag, h in rangert:
        s = kurser[h.yahoo]
        sist = s.close[-1]
        if not sist:
            continue
        aksepter = (pred >= p.min_edge and sikk >= p.min_sikkerhet and ledig > 0
                    and not any(t.slug == h.slug for t in apne))
        limit = sist * (1 + LIMIT_PAASLAG)
        sa = stopp_andel(s, len(s.datoer), p.stopp_atr)
        belop = p.storrelse(sa, marked.til_nok(marked.omsetning_nok(s, len(s.datoer)), h.yahoo), kapital)
        inn_dag = _legg_til_handelsdager(date.fromisoformat(max(h.publisert[:10], siste_kursdag)), 1)
        res.forslag.append({
            "nokkel": h.nokkel, "kilde": h.kilde, "slug": h.slug, "navn": h.navn, "yahoo": h.yahoo,
            "publisert": h.publisert, "pred": pred, "sikkerhet": sikk, "bidrag": bidrag[:4],
            "siste_kurs": sist, "limit": limit, "stopp": limit * (1 - sa), "belop": belop,
            "inn_dato": inn_dag.isoformat(), "ut_dato": _legg_til_handelsdager(inn_dag, p.horisont).isoformat(),
            "beskrivelse": h.beskrivelse, "anbefalt": int(aksepter),
        })
        if aksepter:
            ledig -= 1
    return res


def _lukk(t: Trade, dag: str, pris: float | None, status: str, kurser, p: Param) -> None:
    if not pris:
        return
    s = kurser[t.yahoo]
    i = s.indeks_til_og_med(t.inn_dato)
    t.ut_dato, t.ut, t.status = dag, pris, status
    t.raa = pris / t.inn - 1
    t.marked = _indeksavkastning(kurser, t.yahoo, t.inn_dato, dag)
    t.kost = marked.kostnad(marked.til_nok(marked.omsetning_nok(s, i), t.yahoo), t.belop)
    t.netto = t.raa - t.kost
    t.pnl_kr = round(t.belop * t.netto, 2)


def _urealisert(t: Trade, kurser, dag: str, p: Param) -> float:
    s = kurser[t.yahoo]
    j = s.indeks_til_og_med(dag)
    c = s.close[j] if j >= 0 else None
    return t.belop * (c / t.inn - 1) if c else 0.0


MIN_TRENT = 30


def kalibrering(prediksjoner: list[dict], boetter: int = 5) -> dict:
    """Lærer hjernen noe? Del vurderte hendelser med kjent utfall i fem like
    store grupper etter forventet verdi og se om faktisk utfall stiger. Bare
    vurderinger gjort med minst MIN_TRENT treningsobservasjoner telles —
    før det gjetter modellen bare 0."""
    med = sorted(
        (r for r in prediksjoner if r["utfall"] is not None and r["trent_n"] >= MIN_TRENT), key=lambda r: r["pred"]
    )
    if len(med) < boetter * 4:
        return {"n": len(med), "grupper": [], "rangkorrelasjon": None}
    grupper = []
    for b in range(boetter):
        del_ = med[b * len(med) // boetter:(b + 1) * len(med) // boetter]
        grupper.append({
            "fra": round(del_[0]["pred"], 4), "til": round(del_[-1]["pred"], 4), "n": len(del_),
            "forventet": round(statistics.mean(r["pred"] for r in del_), 4),
            "faktisk": round(statistics.mean(r["utfall"] for r in del_), 4),
            "treff": round(sum(r["utfall"] > 0 for r in del_) / len(del_), 3),
        })
    return {"n": len(med), "grupper": grupper, "rangkorrelasjon": round(_spearman([r["pred"] for r in med], [r["utfall"] for r in med]), 3)}


def _rang(xs: list[float]) -> list[float]:
    orden = sorted(range(len(xs)), key=lambda i: xs[i])
    r = [0.0] * len(xs)
    for pos, i in enumerate(orden):
        r[i] = pos
    return r


def _spearman(a: list[float], b: list[float]) -> float:
    ra, rb = _rang(a), _rang(b)
    ma, mb = statistics.mean(ra), statistics.mean(rb)
    tel = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    nev = math.sqrt(sum((x - ma) ** 2 for x in ra) * sum((y - mb) ** 2 for y in rb))
    return tel / nev if nev else 0.0
