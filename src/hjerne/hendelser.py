"""Gjør rådata om til hendelser med egenskaper (features) hjernen kan lære av.

Én innsidehendelse = alle gyldige, dedupliserte åpne aksjekjøp i ett selskap
publisert samme dag. Én nyhetshendelse = én klassifisert, ikke-rutine
hendelse i et mappet selskap. Egenskapene er grove bøtter med vilje: med noen
hundre hendelser er finere oppdeling bare støy.

Egenskaper som bruker kurs (likviditet, momentum) regnes kun fra dager FØR
inngangsdagen.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

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


def kursegenskaper(kurser: dict[str, marked.Serie], yahoo: str, publisert: str) -> dict[str, str]:
    s = kurser.get(yahoo)
    if not s:
        return {}
    i = s.indeks_etter(publisert[:10])  # inngangsdagen; bare data før i brukes
    ut: dict[str, str] = {}
    oms = marked.til_nok(marked.omsetning_nok(s, i), yahoo)
    ut["likviditet"] = "ukjent" if oms is None else ("høy" if oms >= 50e6 else ("middels" if oms >= 5e6 else "lav"))
    if i >= 21 and s.close[i - 1] and s.close[i - 21]:
        r = s.close[i - 1] / s.close[i - 21] - 1
        ut["momentum20"] = "ned >10%" if r < -0.10 else ("opp >10%" if r > 0.10 else "flat")
    else:
        ut["momentum20"] = "ukjent"
    ut["marked"] = (marked.suffiks(yahoo) or "?").lstrip(".")
    return ut


def innsidehendelser(con, kurser: dict[str, marked.Serie]) -> list[Hendelse]:
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
