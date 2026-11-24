"""Rangerer ferske hendelser som kandidater, på kort og lang sikt.

For hver hendelse publisert de siste KANDIDAT_DAGER dagene i et selskap med
kurser:

- forventet netto meravkastning på kort (HJERNE_HORISONT) og lang
  (HJERNE_HORISONT_LANG) sikt, fra hver sin modell trent på alle kjente utfall;
- sannsynlighet for gevinst i én enkelt handel: P(netto > 0) under modellens
  spredning. Den er ærlig lav, fordi enkeltaksjer svinger mye;
- inngang (maks limit), stopp, break-even, forventet kurs og utgangsdato;
- score 0–100: hvor høyt kandidatens forventning ligger sammenlignet med alle
  hendelser hjernen har vurdert (40 % kort, 60 % lang). Score 80 betyr «bedre
  enn ca. 80 % av det den har sett», ikke 80 % sjanse for gevinst.

Kursmålet antar flatt marked: inngang × (1 + forventet meravkastning +
kostnad), dvs. kursen som gir den forventede nettogevinsten.
"""
from __future__ import annotations

import math
from bisect import bisect_right
from datetime import date, timedelta

from src import ask, marked
from src.hjerne import forklaring
from src.config import ASK_FOKUS, PANEL_FILTER_PERSENTIL, REGEL_ADVARSEL, REGEL_LIKVID, REGEL_PLUSS
from src.hjerne.hendelser import Hendelse
from src.hjerne.modell import Modell
from src.hjerne.simuler import LIMIT_PAASLAG, Param, _egenskaper, _legg_til_handelsdager, stopp_andel

KANDIDAT_DAGER = 10
VEKT_KORT, VEKT_LANG = 0.4, 0.6
ANBEFALING_ORDEN = ["kjøp", "følg med", "nøytral", "unngå"]
UNNGAA_UNDER = 0.02  # begge horisonter under -2 % netto meravkastning


def _p_gevinst(pred: float, sigma: float) -> float:
    return 0.5 * (1 + math.erf(pred / (max(sigma, 1e-6) * math.sqrt(2))))


def _persentil(sortert: list[float], v: float) -> float:
    return bisect_right(sortert, v) / len(sortert) if sortert else 0.5


def _horisont(m: Modell, eg: dict, h: int, inn: float, kost: float, inn_dato: date, posisjon: float, sikk_min: float, edge_min: float) -> dict:
    pred, sikk, bidrag = m.forutsi(eg)
    return {
        "dager": h,
        "forventet": pred,                      # netto meravkastning
        "gevinst_kr": posisjon * pred,
        "p_gevinst": _p_gevinst(pred, m.sigma),
        "sikkerhet": sikk,                      # P(forventningen > 0)
        "maal": inn * (1 + pred + kost),
        "ut_dato": _legg_til_handelsdager(inn_dato, h).isoformat(),
        "kjop": pred >= edge_min and sikk >= sikk_min,
        "bidrag": bidrag[:5],
    }


def regelsjekk(eg: dict[str, str]) -> dict:
    """Den enkle regelstrategien fra analysen: likvid, ingen advarsel, minst
    ett plusstegn. Gjennomsiktig ved siden av modellen."""
    pluss = [f"{f.split('.')[-1]}: {v}" for f, v in REGEL_PLUSS if eg.get(f) == v]
    advarsler = [f"{f.split('.')[-1]}: {v}" for f, v in REGEL_ADVARSEL if eg.get(f) == v]
    likvid = eg.get("kurs.likviditet") in REGEL_LIKVID
    return {"ok": likvid and not advarsler and bool(pluss), "likvid": likvid, "pluss": pluss, "advarsler": advarsler}


def ranger(
    hendelser: list[Hendelse],
    kurser: dict[str, marked.Serie],
    kort: Modell,
    lang: Modell,
    p_kort: Param,
    p_lang: Param,
    regelscore: dict[tuple[str, str], float] | None = None,
    panel: dict[str, dict] | None = None,
) -> list[dict]:
    """panel: siste aksjescore per Yahoo-symbol fra faktormodellen (src/panel)."""
    if not kurser:
        return []
    siste_kursdag = max(s.datoer[-1] for s in kurser.values() if s.datoer)
    fra = (date.fromisoformat(siste_kursdag) - timedelta(days=KANDIDAT_DAGER)).isoformat()

    # Fordelingen av forventninger over alt hjernen har sett, per horisont.
    hist_k, hist_l = [], []
    for h in hendelser:
        if h.yahoo in kurser:
            eg = _egenskaper(h)
            hist_k.append(kort.forutsi(eg)[0])
            hist_l.append(lang.forutsi(eg)[0])
    hist_k.sort()
    hist_l.sort()

    ut = []
    for h in hendelser:
        s = kurser.get(h.yahoo)
        if not s or not s.datoer or h.publisert[:10] < fra:
            continue
        if ASK_FOKUS and not ask.ok(h.egenskaper.get("selskap.ask")):
            continue  # kan ikke kjøpes på aksjesparekonto (src/ask.py)
        sist = s.close[-1]
        if not sist:
            continue
        i = s.indeks_etter(h.publisert[:10])
        if i >= len(s.datoer):
            status, dag_nr, inngang_faktisk, hittil = "venter", 0, None, None
            inn_dato = _legg_til_handelsdager(date.fromisoformat(max(h.publisert[:10], siste_kursdag)), 1)
        else:
            dag_nr = len(s.datoer) - i
            inngang_faktisk = s.open[i]
            hittil = sist / inngang_faktisk - 1 if inngang_faktisk else None
            status = "løpende" if dag_nr <= p_kort.horisont else "sen"
            inn_dato = _legg_til_handelsdager(date.fromisoformat(siste_kursdag), 1)  # kjøp nå = neste åpning

        limit = sist * (1 + LIMIT_PAASLAG)
        oms = marked.til_nok(marked.omsetning_nok(s, len(s.datoer)), h.yahoo)
        sa = stopp_andel(s, len(s.datoer), p_kort.stopp_atr)
        belop = p_kort.storrelse(sa, oms)
        kost = marked.kostnad(oms, max(belop, 1.0))
        eg = _egenskaper(h)
        k = _horisont(kort, eg, p_kort.horisont, limit, kost, inn_dato, belop, p_kort.min_sikkerhet, p_kort.min_edge)
        lg = _horisont(lang, eg, p_lang.horisont, limit, kost, inn_dato, belop, p_kort.min_sikkerhet, p_kort.min_edge)
        score = round(100 * (VEKT_KORT * _persentil(hist_k, k["forventet"]) + VEKT_LANG * _persentil(hist_l, lg["forventet"])))

        # Merket følger den ABSOLUTTE forventningen; scoren er bare relativ.
        beste = max(k["forventet"], lg["forventet"])
        filt = p_kort.utelatt(h.egenskaper)
        ps = (panel or {}).get(h.yahoo)
        if not filt and ps and ps.get("region") == "Norden" and ps["persentil"] < PANEL_FILTER_PERSENTIL:
            filt = f"aksjescore=svak (tidel {ps['tidel']})"
        if filt:
            k["kjop"] = lg["kjop"] = False
        if (k["kjop"] or lg["kjop"]) and status != "sen":
            anbefaling = "kjøp"
        elif beste > 0:
            anbefaling = "følg med"
        elif beste > -UNNGAA_UNDER:
            anbefaling = "nøytral"
        else:
            anbefaling = "unngå"

        ut.append({
            "nokkel": h.nokkel, "kilde": h.kilde, "slug": h.slug, "navn": h.navn, "yahoo": h.yahoo,
            "ticker": h.yahoo.split(".")[0], "bors": (marked.suffiks(h.yahoo) or "").lstrip("."),
            "publisert": h.publisert, "beskrivelse": h.beskrivelse,
            "status": status, "dag_nr": dag_nr, "inngang_faktisk": inngang_faktisk, "hittil": hittil,
            "siste_kurs": sist, "siste_kursdag": siste_kursdag,
            "inn_dato": inn_dato.isoformat(), "limit": limit,
            "stopp": limit * (1 - sa),
            "kostnad": kost, "omsetning_nok": oms, "posisjon": belop,
            "antall": int(belop // limit) if limit else 0, "for_liten": belop < p_kort.min_posisjon,
            "kort": k, "lang": lg, "score": score, "anbefaling": anbefaling,
            "kjop_horisont": "begge" if k["kjop"] and lg["kjop"] else ("kort" if k["kjop"] else ("lang" if lg["kjop"] else None)),
            "break_even": limit * (1 + kost),
            "egenskaper": h.egenskaper,
            "regelscore": (regelscore or {}).get((h.slug, h.publisert[:10])),
            "filter": filt, "regelsjekk": regelsjekk(h.egenskaper), "aksjescore": ps,
            "ask": h.egenskaper.get("selskap.ask"),
        })
    # Én rad per selskap: den nyeste hendelsen, med antall hendelser i perioden.
    per_selskap: dict[str, dict] = {}
    for c in ut:
        forrige = per_selskap.get(c["slug"])
        c["hendelser_i_perioden"] = (forrige["hendelser_i_perioden"] if forrige else 0) + 1
        if forrige is None or c["publisert"] >= forrige["publisert"]:
            per_selskap[c["slug"]] = c
        else:
            forrige["hendelser_i_perioden"] = c["hendelser_i_perioden"]
    ut = list(per_selskap.values())
    ut.sort(key=lambda c: (c["status"] == "sen", ANBEFALING_ORDEN.index(c["anbefaling"]), -c["score"]))
    for n, c in enumerate(ut, 1):
        c["rang"] = n
        c["forklaring"] = forklaring.tekst(c)
        c["grunner"] = forklaring.grunner(c)
    return ut
