"""Gjør rådata om til hendelser med egenskaper (features) hjernen kan lære av.

Én innsidehendelse = alle gyldige, dedupliserte åpne aksjekjøp i ett selskap
publisert samme dag. Én nyhetshendelse = én klassifisert, ikke-rutine
hendelse i et mappet selskap. Egenskapene er grove bøtter med vilje: med noen
hundre hendelser er finere oppdeling bare støy.

Egenskaper som bruker kurs (likviditet, momentum) regnes kun fra dager FØR
inngangsdagen.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from src import marked

ROLLE_RANG = ["ceo", "cfo", "styreleder", "styremedlem", "ledelse", "naerstaaende", "ukjent"]
ROLLE_GRUPPE = {
    "ceo": "ceo/cfo", "cfo": "ceo/cfo", "styreleder": "styreleder", "styremedlem": "styre",
    "ledelse": "ledelse", "naerstaaende": "nærstående/ukjent", "ukjent": "nærstående/ukjent",
}


@dataclass
class Hendelse:
    nokkel: str
    kilde: str                 # 'innside' | 'nyhet'
    slug: str
    navn: str
    yahoo: str
    publisert: str
    egenskaper: dict[str, str] = field(default_factory=dict)
    beskrivelse: str = ""


def _boette_verdi(nok: float | None) -> str:
    if nok is None:
        return "ukjent"
    return "<100k" if nok < 100_000 else ("100k-1M" if nok < 1_000_000 else ">1M")


def _boette_andel(andel: float | None) -> str:
    if andel is None:
        return "ukjent"
    return "ny posisjon" if andel >= 0.9 else ("stor økning" if andel >= 0.25 else "liten økning")


BORS_STENGER = "16:20"  # Oslo; tidligst av de nordiske børsene (konservativt)


def _r(a: float | None, b: float | None) -> float | None:
    return a / b - 1 if a and b else None


def kursegenskaper(kurser: dict[str, marked.Serie], yahoo: str, publisert: str) -> dict[str, str]:
    """Egenskaper fra kurs og volum. Alt er kjent FØR inngangen (åpning dagen
    etter publisering): dag i er inngangsdagen, og bare dager < i brukes."""
    s = kurser.get(yahoo)
    if not s:
        return {}
    i = s.indeks_etter(publisert[:10])  # inngangsdagen
    ut: dict[str, str] = {}
    oms = marked.til_nok(marked.omsetning_nok(s, i), yahoo)
    ut["likviditet"] = "ukjent" if oms is None else ("høy" if oms >= 50e6 else ("middels" if oms >= 5e6 else "lav"))
    r = _r(s.close[i - 1], s.close[i - 21]) if i >= 21 else None
    ut["momentum20"] = "ukjent" if r is None else ("ned >10%" if r < -0.10 else ("opp >10%" if r > 0.10 else "flat"))
    ut["marked"] = (marked.suffiks(yahoo) or "?").lstrip(".")

    # Langsiktig trend (ca. 6 måneder).
    r = _r(s.close[i - 1], s.close[i - 127]) if i >= 127 else None
    ut["trend6m"] = "ukjent" if r is None else ("ned >20%" if r < -0.20 else ("opp >20%" if r > 0.20 else "flat"))

    # Avstand til 52-ukers høyeste/laveste sluttkurs.
    if i >= 200 and s.close[i - 1]:
        vindu = [c for c in s.close[max(0, i - 252):i] if c]
        c = s.close[i - 1]
        ut["uke52"] = ("nær toppen" if c >= 0.95 * max(vindu) else
                       "nær bunnen" if c <= 1.10 * min(vindu) else "midt i spennet")
    else:
        ut["uke52"] = "ukjent"

    # Reaksjon og volum på meldingsdagen — bare når meldingen kom i børstiden
    # (ellers er reaksjonen ikke kjent før inngangen neste morgen).
    j = s.indeks_til_og_med(publisert[:10])
    i_borstid = (j >= 21 and s.datoer[j] == publisert[:10] and publisert[11:16] < BORS_STENGER)
    if i_borstid:
        r = _r(s.close[j], s.close[j - 1])
        idx = kurser.get(marked.indeks_for(yahoo) or "")
        if r is not None and idx:
            a = idx.indeks_til_og_med(s.datoer[j])
            if a >= 1 and idx.datoer[a] == s.datoer[j]:
                r -= _r(idx.close[a], idx.close[a - 1]) or 0.0
        ut["reaksjon"] = "ukjent" if r is None else ("ned >3%" if r < -0.03 else ("opp >3%" if r > 0.03 else "rolig"))
        v = [x for x in s.volum[j - 20:j] if x]
        med = sorted(v)[len(v) // 2] if v else None
        rv = s.volum[j] / med if med and s.volum[j] else None
        ut["volum"] = "ukjent" if rv is None else ("høyt (3x+)" if rv >= 3 else ("forhøyet" if rv >= 1.5 else "normalt"))
    else:
        ut["reaksjon"] = ut["volum"] = "etter børstid"

    # Markedsregime: børsens indeks over/under 200-dagers snitt.
    idx = kurser.get(marked.indeks_for(yahoo) or "")
    if idx and i >= 1:
        a = idx.indeks_til_og_med(s.datoer[i - 1])
        snitt = [c for c in idx.close[max(0, a - 199):a + 1] if c]
        ut["regime"] = ("ukjent" if a < 199 or len(snitt) < 150 or not idx.close[a] else
                        "stigende marked" if idx.close[a] > sum(snitt) / len(snitt) else "fallende marked")
    else:
        ut["regime"] = "ukjent"
    return ut


def _navn(person: str | None) -> str | None:
    if not person:
        return None
    n = re.sub(r"[^\wæøåäö ]", "", person.lower())
    return re.sub(r"\s+", " ", n).strip() or None


def _boette_kurs_vs_innsider(r: float | None) -> str:
    if r is None or abs(r) > 0.8:  # over 80 % avvik = trolig feil enhet/valuta
        return "ukjent"
    return ("under innsiders kurs" if r < -0.02 else "rundt innsiders kurs" if r <= 0.02 else
            "over (2–10 %)" if r <= 0.10 else "langt over (>10 %)")


def innsidehendelser(con, kurser: dict[str, marked.Serie], horisont: int | None = None) -> list[Hendelse]:
    from src.config import HJERNE_HORISONT

    horisont = horisont or HJERNE_HORISONT
    rader = con.execute(
        "SELECT h.*, s.yahoo, COALESCE(n.selskap_navn, h.selskap_slug) AS navn "
        "FROM innsidehandler h JOIN selskaper s ON s.slug = h.selskap_slug "
        "JOIN nyheter n ON n.id = h.nyhet_id "
        "WHERE h.gyldig = 1 AND h.retning = 'kjop' AND h.instrument = 'aksje' "
        "AND s.yahoo IS NOT NULL AND s.yahoo <> '' ORDER BY h.publisert, h.nyhet_id, h.linje"
    ).fetchall()
    sett: set[str] = set()
    kjop = []
    for r in rader:
        if r["noekkel"] not in sett:
            sett.add(r["noekkel"])
            kjop.append(dict(r))

    # Salg (for netto innsideflyt), dedupliserte på samme måte.
    salg_per_selskap: dict[str, list[tuple[str, str]]] = {}
    sett_s: set[str] = set()
    for r in con.execute(
        "SELECT selskap_slug, publisert, person, nyhet_id, noekkel FROM innsidehandler "
        "WHERE gyldig = 1 AND retning = 'salg' AND instrument = 'aksje' ORDER BY publisert"
    ):
        if r["noekkel"] in sett_s:
            continue
        sett_s.add(r["noekkel"])
        salg_per_selskap.setdefault(r["selskap_slug"], []).append(
            (r["publisert"][:10], _navn(r["person"]) or f"?{r['nyhet_id']}"))

    data_start = min((k["publisert"][:10] for k in kjop), default="2000-01-01")
    ett_aar_inn = (date.fromisoformat(data_start) + timedelta(days=365)).isoformat()

    # Personens tidligere kjøp: (publiseringsdag, utgangsdag, netto meravkastning).
    tidligere: dict[tuple[str, str], list[tuple[str, str | None, float | None]]] = {}
    for k in kjop:
        navn = _navn(k["person"])
        if not navn:
            continue
        u = marked.utfall(kurser, k["yahoo"], k["publisert"], horisont)
        tidligere.setdefault((k["selskap_slug"], navn), []).append(
            (k["publisert"][:10], u.ut_dato if u else None, u.netto_mer if u else None))

    dager: dict[tuple[str, str], list[dict]] = {}
    for k in kjop:
        dager.setdefault((k["selskap_slug"], k["publisert"][:10]), []).append(k)

    ut: list[Hendelse] = []
    for (slug, dag), ks in sorted(dager.items(), key=lambda x: x[0][1]):
        yahoo = ks[0]["yahoo"]
        siste = max(k["publisert"] for k in ks)
        fra = (datetime.fromisoformat(dag) - timedelta(days=30)).date().isoformat()
        kjopere = {
            (k["person"] or f"?{k['nyhet_id']}").strip().lower()
            for k in kjop
            if k["selskap_slug"] == slug and fra <= k["publisert"][:10] <= dag
        }
        rolle = min((k["rolle"] for k in ks), key=lambda r: ROLLE_RANG.index(r) if r in ROLLE_RANG else 99)
        verdier = [marked.til_nok(k["verdi"], yahoo) for k in ks if k["verdi"] is not None]
        verdi = sum(verdier) if verdier else None
        andeler = [k["antall"] / k["beholdning_etter"] for k in ks if k["antall"] and k.get("beholdning_etter")]
        andel = max(andeler) if andeler else None
        eg = {
            "innside.rolle": ROLLE_GRUPPE.get(rolle, "nærstående/ukjent"),
            "innside.kjøpere30d": "3+" if len(kjopere) >= 3 else str(len(kjopere)),
            "innside.verdi": _boette_verdi(verdi),
            "innside.andel": _boette_andel(min(andel, 1.0) if andel is not None else None),
        }

        # 1) Rutine: samme person kjøpte i samme kalendermåned et tidligere år.
        # 2) Historikk: hvordan gikk personens tidligere kjøp (kun utfall kjent før i dag)?
        navn_i_dag = {n for n in (_navn(k["person"]) for k in ks) if n}
        rutine = tidl_kjent = False
        tidl_utfall: list[float] = []
        for navn in navn_i_dag:
            for p_dag, ut_dag, mer in tidligere.get((slug, navn), []):
                if p_dag >= dag:
                    continue
                tidl_kjent = True
                if p_dag[5:7] == dag[5:7] and p_dag[:4] < dag[:4]:
                    rutine = True
                if ut_dag and ut_dag < dag and mer is not None:
                    tidl_utfall.append(mer)
        if not navn_i_dag:
            eg["innside.mønster"] = "ukjent person"
        elif rutine:
            eg["innside.mønster"] = "rutine (samme måned før)"
        elif dag < ett_aar_inn:
            eg["innside.mønster"] = "ukjent (kort historikk)"
        else:
            eg["innside.mønster"] = "gjentatt kjøp" if tidl_kjent else "første kjøp på 1+ år"
        if not tidl_utfall:
            eg["innside.historikk"] = "ingen kjente tidligere"
        else:
            snitt = sum(tidl_utfall) / len(tidl_utfall)
            eg["innside.historikk"] = "tidligere kjøp gikk bra" if snitt > 0 else "tidligere kjøp gikk dårlig"

        # 3) Kurs ved inngang mot prisen innsideren betalte (vektet snitt).
        s = kurser.get(yahoo)
        betalt = [(k["antall"], k["pris"]) for k in ks if k["antall"] and k["pris"]]
        r = None
        if s and betalt:
            vwap = sum(a * p for a, p in betalt) / sum(a for a, _ in betalt)
            i = s.indeks_etter(siste[:10])
            if i >= 1 and s.close[i - 1] and vwap:
                r = s.close[i - 1] / vwap - 1
        eg["innside.kurs_vs_innsider"] = _boette_kurs_vs_innsider(r)

        # 4) Netto innsideflyt siste 90 dager: ulike kjøpere mot ulike selgere.
        fra90 = (date.fromisoformat(dag) - timedelta(days=90)).isoformat()
        kjopere90 = {_navn(k["person"]) or f"?{k['nyhet_id']}" for k in kjop
                     if k["selskap_slug"] == slug and fra90 <= k["publisert"][:10] <= dag}
        selgere90 = {n for d, n in salg_per_selskap.get(slug, []) if fra90 <= d <= dag}
        eg["innside.flyt90"] = ("ingen salg" if not selgere90 else
                                "flere kjøpere enn selgere" if len(kjopere90) > len(selgere90) else
                                "like mange eller flere selgere")

        eg.update({f"kurs.{k}": v for k, v in kursegenskaper(kurser, yahoo, siste).items()})
        personer = ", ".join(sorted({f"{k['person'] or '?'} ({k['rolle_tekst'] or k['rolle']})" for k in ks}))[:200]
        ut.append(Hendelse(
            nokkel=f"innside:{slug}:{dag}", kilde="innside", slug=slug, navn=ks[0]["navn"], yahoo=yahoo,
            publisert=siste, egenskaper=eg,
            beskrivelse=f"Innsidekjøp: {personer}" + (f", ~{verdi:,.0f} NOK".replace(",", " ") if verdi else ""),
        ))
    return ut


def nyhetshendelser(con, kurser: dict[str, marked.Serie]) -> list[Hendelse]:
    rader = con.execute(
        "SELECT h.id, h.selskap_slug, h.publisert, h.tittel, s.yahoo, COALESCE(s.navn, h.selskap_slug) AS navn, "
        "  k.kategori, k.retning, k.overraskelse "
        "FROM klassifiseringer k JOIN hendelser h ON h.id = k.hendelse_id "
        "JOIN selskaper s ON s.slug = h.selskap_slug "
        "WHERE k.status = 'ok' AND k.kategori IS NOT NULL AND k.kategori <> 'rutine' "
        "AND s.yahoo IS NOT NULL AND s.yahoo <> '' "
        "AND k.id = (SELECT MAX(k2.id) FROM klassifiseringer k2 WHERE k2.hendelse_id = h.id AND k2.status = 'ok') "
        "ORDER BY h.publisert"
    ).fetchall()
    ut = []
    for r in rader:
        o = r["overraskelse"]
        eg = {
            "nyhet.kategori": r["kategori"],
            "nyhet.retning": r["retning"] or "ukjent",
            "nyhet.overraskelse": "ukjent" if o is None else ("høy (4-5)" if o >= 4 else ("middels (3)" if o == 3 else "lav (1-2)")),
        }
        eg.update({f"kurs.{k}": v for k, v in kursegenskaper(kurser, r["yahoo"], r["publisert"]).items()})
        ut.append(Hendelse(
            nokkel=f"nyhet:{r['id']}", kilde="nyhet", slug=r["selskap_slug"], navn=r["navn"], yahoo=r["yahoo"],
            publisert=r["publisert"], egenskaper=eg, beskrivelse=f"{r['kategori']}: {r['tittel'][:140]}",
        ))
    return ut


def alle(con, kurser: dict[str, marked.Serie]) -> list[Hendelse]:
    return sorted(innsidehendelser(con, kurser) + nyhetshendelser(con, kurser), key=lambda h: h.publisert)
