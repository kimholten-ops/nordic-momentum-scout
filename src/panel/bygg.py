"""Faktorpanel: én rad per aksje per uke (fredag), med egenskaper kjent ved
sluttkurs den dagen og fasit = meravkastning de neste 20 handelsdagene.

Hvorfor panel og ikke bare hendelser: ~450 aksjer x ~170 uker gir ~70 000
eksempler i stedet for ~2 600 innsidekjøp, nok til å lære vekten av 20–30
faktorer. Innsidekjøp er én faktor blant mange.

Ingen fremtidsinformasjon i egenskapene:
- kurs/volum til og med dag t (sluttkurs),
- rapporter, innsidehandler, emisjoner med publiseringstid før kl. 17:30 dag t,
- short med dato før dag t.
Fasit: kjøp på åpning dag t+1, salg på slutt dag t+H, pluss utbytte, minus
børsindeksen samme periode (+ anslått indeksutbytte) og kostnad tur/retur.
"""
from __future__ import annotations

import csv
import math
from bisect import bisect_left, bisect_right
from collections import defaultdict
from datetime import date, timedelta

import numpy as np
import pandas as pd

from src import marked
from src.config import INNSIDE_FX_NOK, KURTASJE_KR, ROOT, SLIPPAGE_ETTER_OMSETNING

HORISONT = 20
POSISJON_KR = 15_000  # typisk posisjon (15 % av 100 000)
MIN_HISTORIKK = 260   # handelsdager før første rad (12-mnd momentum)
SLUTT_DAG = "17:30:00"

KATEGORISKE = ["marked", "sektor"]


KURTASJE_USA = 0.0015  # antatt kurtasje per side for amerikanske aksjer (andel); juster ved behov


def _kostnad(oms: float | None, usa: bool = False) -> float:
    kurt = 2 * (KURTASJE_USA if usa else KURTASJE_KR / POSISJON_KR)
    if oms is None or not math.isfinite(oms):
        return kurt + 2 * SLIPPAGE_ETTER_OMSETNING[-1][1]
    return kurt + 2 * next(s for g, s in SLIPPAGE_ETTER_OMSETNING if oms >= g)


def _ukedager(datoer: list[str]) -> list[str]:
    """Siste handelsdag i hver ISO-uke."""
    siste: dict[tuple, str] = {}
    for d in datoer:
        siste[date.fromisoformat(d).isocalendar()[:2]] = d
    return sorted(siste.values())


def last_data(con) -> dict:
    """Leser alt panelet trenger fra databasen én gang."""
    kurser = marked.last_kurser(con)
    sel = {}
    for r in con.execute("SELECT slug, yahoo FROM selskaper WHERE yahoo IS NOT NULL AND yahoo <> ''"):
        sel.setdefault(r["yahoo"], r["slug"])
    slugs_for: dict[str, set] = defaultdict(set)
    for r in con.execute("SELECT slug, yahoo FROM selskaper WHERE yahoo IS NOT NULL AND yahoo <> ''"):
        slugs_for[r["yahoo"]].add(r["slug"])

    tab = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    hend: dict[tuple[str, str], list[str]] = defaultdict(list)
    if "selskapshendelser" in tab:
        for r in con.execute("SELECT slug, type, publisert FROM selskapshendelser ORDER BY publisert"):
            hend[(r[0], r[1])].append(r[2])

    innside: dict[str, list[tuple]] = defaultdict(list)  # slug -> [(publisert, retning, verdi_nok, person)]
    for r in con.execute(
        "SELECT selskap_slug, publisert, retning, verdi, valuta, person FROM innsidehandler "
        "WHERE gyldig = 1 AND instrument = 'aksje' AND retning IN ('kjop', 'salg') "
        "ORDER BY publisert"
    ):
        v = (r["verdi"] or 0) * INNSIDE_FX_NOK.get((r["valuta"] or "NOK").upper(), 1.0)
        innside[r["selskap_slug"]].append((r["publisert"], r["retning"], v, r["person"]))

    isin: dict[str, set] = defaultdict(set)
    short: dict[str, tuple[list, list]] = {}
    if "short" in tab:
        for r in con.execute("SELECT slug, isin FROM selskap_isin"):
            isin[r[0]].add(r[1])
        for r in con.execute("SELECT isin, dato, prosent FROM short ORDER BY isin, dato"):
            d, p = short.setdefault(r[0], ([], []))
            d.append(r[1])
            p.append(r[2])
    info = {}
    if "selskapsinfo" in tab:
        info = {r[0]: (r[1], r[2]) for r in con.execute("SELECT yahoo, sektor, aksjer FROM selskapsinfo")}
    usa = {}
    fil = ROOT / "universe" / "usa.csv"
    if fil.exists():
        with fil.open(encoding="utf-8") as fh:
            usa = {r["symbol"]: r for r in csv.DictReader(fh)}
    return {"kurser": kurser, "slug": sel, "usa": usa, "slugs_for": slugs_for, "hend": hend, "innside": innside,
            "isin": isin, "short": short, "info": info,
            "short_start": min((d[0] for d, _ in short.values() if d), default=None)}


def _np(x) -> np.ndarray:
    return np.array([np.nan if v is None else float(v) for v in x], dtype=float)


def _rapportreaksjoner(s: marked.Serie, idx: marked.Serie | None, publiseringer: list[str]) -> list[tuple]:
    """-> [(reaksjonsdag-indeks, meravkastning den dagen, volumratio)] sortert."""
    ut = []
    for p in publiseringer:
        dag, tid = p[:10], p[11:19]
        i = bisect_left(s.datoer, dag)
        if i < len(s.datoer) and s.datoer[i] == dag and tid > "16:25:00":
            i += 1  # publisert etter stengetid: reaksjonen kommer neste handelsdag
        if i <= 0 or i >= len(s.datoer) or not s.close[i] or not s.close[i - 1]:
            continue
        r = s.close[i] / s.close[i - 1] - 1
        if idx:
            a, b = idx.indeks_til_og_med(s.datoer[i - 1]), idx.indeks_til_og_med(s.datoer[i])
            if a >= 0 and b > a and idx.close[a] and idx.close[b]:
                r -= idx.close[b] / idx.close[a] - 1
        vol_for = [v for v in s.volum[max(0, i - 60):i] if v]
        vr = (s.volum[i] or 0) / np.median(vol_for) if vol_for and np.median(vol_for) > 0 else np.nan
        ut.append((i, r, vr))
    return sorted(ut)


def bygg(data: dict, horisont: int = HORISONT, fra: str | None = None, med_fasit: bool = True) -> pd.DataFrame:
    kurser = data["kurser"]
    rader = []
    for yahoo, s in kurser.items():
        idx_navn = marked.indeks_for(yahoo)
        er_usa = marked.suffiks(yahoo) == ".US"
        if (not idx_navn or yahoo == idx_navn or len(s.datoer) < MIN_HISTORIKK + 5
                or yahoo not in (data["usa"] if er_usa else data["slug"])):
            continue
        idx = kurser.get(idx_navn)
        slugs = data["slugs_for"][yahoo]
        c = _np(s.close)
        v = _np(s.volum)
        o = _np(s.open)
        u = _np(s.utbytte) if s.utbytte else np.zeros(len(c))
        u = np.nan_to_num(u)
        oms = c * v
        logr = np.diff(np.log(c), prepend=np.nan)
        # Indeks justert til aksjens datoer.
        ic = np.full(len(c), np.nan)
        if idx:
            m = dict(zip(idx.datoer, idx.close))
            ic = _np([m.get(d) for d in s.datoer])
            ic = pd.Series(ic).ffill().to_numpy()
        rapporter = _rapportreaksjoner(s, idx, sorted(p for sl in slugs for p in data["hend"].get((sl, "rapport"), [])))
        rap_i = [x[0] for x in rapporter]
        emisjon = sorted(p for sl in slugs for p in data["hend"].get((sl, "emisjon"), []))
        tilbake = sorted(p for sl in slugs for p in data["hend"].get((sl, "tilbakekjop"), []))
        handler = sorted((h for sl in slugs for h in data["innside"].get(sl, [])), key=lambda h: h[0] or "")
        hp = [h[0] for h in handler]
        isiner = set().union(*(data["isin"].get(sl, set()) for sl in slugs)) if slugs else set()
        sektor, aksjer = data["info"].get(yahoo, (None, None))
        if er_usa:
            sektor = data["usa"][yahoo].get("sektor") or None
        fx = INNSIDE_FX_NOK.get(marked.VALUTA_ETTER_SUFFIKS.get(marked.suffiks(yahoo) or "", "NOK"), 1.0)

        for d in _ukedager(s.datoer[MIN_HISTORIKK:]):
            if fra and d < fra:
                continue
            t = s.indeks_til_og_med(d)
            if not (c[t] > 0):
                continue
            f: dict = {"yahoo": yahoo, "dato": d, "marked": (marked.suffiks(yahoo) or "?").lstrip(".")}
            # --- Kurs
            def ret(a, b):
                return c[b] / c[a] - 1 if a >= 0 and c[a] > 0 and c[b] > 0 else np.nan
            f["mom_12_1"] = ret(t - 252, t - 21)
            f["mom_6_1"] = ret(t - 126, t - 21)
            f["mom_1m"] = ret(t - 21, t)
            f["mom_1u"] = ret(t - 5, t)
            hoy = np.nanmax(c[max(0, t - 251):t + 1])
            lav = np.nanmin(c[max(0, t - 251):t + 1])
            f["fra_52u_topp"] = c[t] / hoy - 1 if hoy > 0 else np.nan
            f["over_52u_bunn"] = c[t] / lav - 1 if lav > 0 else np.nan
            f["vol_60"] = np.nanstd(logr[t - 59:t + 1]) * math.sqrt(252)
            f["vol_20_60"] = np.nanstd(logr[t - 19:t + 1]) / f["vol_60"] * math.sqrt(252) if f["vol_60"] > 0 else np.nan
            om20 = np.nanmedian(oms[t - 19:t + 1]) * fx
            f["log_omsetning"] = math.log10(om20) if om20 and om20 > 0 else np.nan
            v5, v60 = np.nanmean(v[t - 4:t + 1]), np.nanmedian(v[t - 59:t + 1])
            f["volumspike"] = v5 / v60 if v60 and v60 > 0 else np.nan
            f["over_ma200"] = c[t] / np.nanmean(c[t - 199:t + 1]) - 1
            if np.isfinite(ic[t]) and t >= 126 and np.isfinite(ic[t - 126]) and ic[t - 126] > 0:
                f["rel_6m"] = f["mom_6_1"] - (ic[t - 21] / ic[t - 126] - 1) if np.isfinite(ic[t - 21]) else np.nan
                f["indeks_over_ma200"] = ic[t] / np.nanmean(ic[t - 199:t + 1]) - 1
                f["indeks_1m"] = ic[t] / ic[t - 21] - 1 if ic[t - 21] > 0 else np.nan
            f["utbytte_12m"] = u[max(0, t - 251):t + 1].sum() / c[t]
            f["log_borsverdi"] = math.log10(aksjer * c[t] * fx) if aksjer else np.nan
            f["sektor"] = sektor or "ukjent"
            # --- Rapporter
            j = bisect_right(rap_i, t) - 1
            if j >= 0 and t - rap_i[j] <= 63:
                f["dager_siden_rapport"] = t - rap_i[j]
                f["rapport_reaksjon"] = rapporter[j][1]
                f["rapport_volum"] = rapporter[j][2]
                f["drift_etter_rapport"] = ret(rap_i[j], t)
            grense = d + "T" + SLUTT_DAG
            d90 = (date.fromisoformat(d) - timedelta(days=90)).isoformat()
            d30 = (date.fromisoformat(d) - timedelta(days=30)).isoformat()
            if slugs and data["hend"]:
                f["emisjon_90"] = float(any(d90 <= p <= grense for p in emisjon))
                f["tilbakekjop_90"] = float(any(d90 <= p <= grense for p in tilbake))
            # --- Innsidere
            a, b = bisect_left(hp, d90), bisect_right(hp, grense)
            vind = handler[a:b]
            kj = [h for h in vind if h[1] == "kjop"]
            sa = [h for h in vind if h[1] == "salg"]
            f["innside_kjop_90"] = len(kj)
            f["innside_kjopere_90"] = len({h[3] for h in kj})
            f["innside_kjop_30"] = sum(1 for h in kj if h[0] >= d30)
            f["innside_netto_mnok_90"] = (sum(h[2] for h in kj) - sum(h[2] for h in sa)) / 1e6
            # --- Short (Oslo)
            if marked.suffiks(yahoo) == ".OL" and data["short_start"] and d > data["short_start"]:
                sp = 0.0
                for isin in isiner:
                    ds, ps = data["short"].get(isin, ([], []))
                    k = bisect_left(ds, d) - 1
                    if k >= 0:
                        sp = max(sp, ps[k])
                f["short_pst"] = sp
            # --- Fasit
            f["kostnad"] = _kostnad(om20, er_usa)
            if med_fasit:
                inn_i, ut_i = t + 1, t + horisont
                if ut_i < len(c) and c[ut_i] > 0:
                    inn = o[inn_i] if o[inn_i] > 0 else c[inn_i]
                    if inn > 0:
                        raa = (c[ut_i] + u[inn_i + 1:ut_i + 1].sum()) / inn - 1
                        mr = ic[ut_i] / ic[t] - 1 if np.isfinite(ic[t]) and np.isfinite(ic[ut_i]) and ic[t] > 0 else np.nan
                        mr += marked.indeks_utbytte(yahoo, horisont)
                        f["fasit_mer"] = raa - mr
                        f["fasit_netto"] = raa - mr - f["kostnad"]
                        f["ut_dato"] = s.datoer[ut_i]
            rader.append(f)
    df = pd.DataFrame(rader)
    for k in KATEGORISKE:
        if k in df:
            df[k] = df[k].astype("category")
    return df
