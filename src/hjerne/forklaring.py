"""Kort forklaring i vanlig språk for hver kandidat.

Lages av tallene som allerede finnes (ingen KI, ingen kostnad): hva som
skjedde, hva hjernen venter, hva som teller for og mot, aksjescoren og
planen. Brukes på dashboardet og i varslene.
"""
from __future__ import annotations

import re
from datetime import date

NAVN = {
    "innside.rolle": "hvem som kjøpte", "innside.kjøpere30d": "antall innsidere som har kjøpt siste 30 dager",
    "innside.verdi": "hvor mye som ble kjøpt", "innside.andel": "hvor mye beholdningen økte",
    "innside.mønster": "kjøpsmønsteret", "innside.historikk": "personens tidligere kjøp",
    "innside.kurs_vs_innsider": "dagens kurs mot innsiderens pris", "innside.flyt90": "innsidehandler siste 90 dager",
    "kurs.likviditet": "hvor mye aksjen omsettes", "kurs.momentum20": "kursen siste måned",
    "kurs.trend6m": "kursen siste halvår", "kurs.uke52": "kursen mot siste års topp og bunn",
    "kurs.reaksjon": "kursreaksjonen på meldingen", "kurs.volum": "volumet på meldingsdagen",
    "kurs.regime": "børsen generelt", "kurs.marked": "børsen", "kontekst.emisjon90": "emisjon siste 90 dager",
    "nyhet.kategori": "typen nyhet", "nyhet.retning": "nyheten", "nyhet.overraskelse": "hvor overraskende nyheten er",
    "kilde": "typen hendelse",
}
# Hele setningsbiter for de vanligste verdiene; ellers «navn (verdi)».
SETNING = {
    "kurs.likviditet=høy": "at aksjen er mye omsatt", "kurs.likviditet=middels": "at aksjen er greit omsatt",
    "kurs.likviditet=lav": "at aksjen er lite omsatt",
    "kurs.momentum20=ned >10%": "at kursen har falt over 10 % siste måned",
    "kurs.momentum20=opp >10%": "at kursen har steget over 10 % siste måned",
    "kurs.momentum20=flat": "at kursen har stått omtrent stille siste måned",
    "kurs.trend6m=ned >20%": "at kursen har falt over 20 % siste halvår",
    "kurs.trend6m=opp >20%": "at kursen har steget over 20 % siste halvår",
    "kurs.uke52=nær bunnen": "at kursen er nær bunnen for det siste året",
    "kurs.uke52=nær toppen": "at kursen er nær toppen for det siste året",
    "kurs.reaksjon=etter børstid": "at meldingen kom etter at børsen stengte",
    "kurs.volum=etter børstid": "at meldingen kom etter at børsen stengte",
    "kurs.reaksjon=rolig": "at kursen knapt reagerte på meldingen", "kurs.volum=normalt": "at volumet var helt normalt",
    "kurs.reaksjon=opp >3%": "at kursen steg over 3 % på meldingen", "kurs.reaksjon=ned >3%": "at kursen falt over 3 % på meldingen",
    "kurs.volum=høyt (3x+)": "at det ble handlet over tre ganger vanlig volum",
    "kurs.regime=fallende marked": "at børsen er i en fallende trend", "kurs.regime=stigende marked": "at børsen er i en stigende trend",
    "innside.kurs_vs_innsider=rundt innsiders kurs": "at du kan kjøpe omtrent til samme kurs som innsideren",
    "innside.kurs_vs_innsider=under innsiders kurs": "at du kan kjøpe billigere enn innsideren",
    "innside.kurs_vs_innsider=over innsiders kurs": "at kursen allerede er over det innsideren betalte",
    "innside.mønster=rutine (samme måned før)": "at innsideren pleier å kjøpe på denne tiden av året",
    "innside.mønster=første kjøp på 1+ år": "at det er innsiderens første kjøp på over et år",
    "innside.mønster=gjentatt kjøp": "at innsideren kjøper igjen",
    "innside.historikk=tidligere kjøp gikk bra": "at innsiderens tidligere kjøp har gått bra",
    "innside.historikk=tidligere kjøp gikk dårlig": "at innsiderens tidligere kjøp har gått dårlig",
    "innside.historikk=ingen kjente tidligere": "at vi ikke kjenner innsiderens tidligere kjøp",
    "innside.kjøpere30d=3+": "at tre eller flere innsidere har kjøpt siste måned",
    "innside.kjøpere30d=2": "at to innsidere har kjøpt siste måned",
    "innside.verdi=>1M": "at kjøpet er på over 1 million kroner",
    "innside.verdi=100k-1M": "at kjøpet er på mellom 100 000 kroner og 1 million",
    "innside.verdi=<100k": "at kjøpet er lite (under 100 000 kroner)",
    "kontekst.emisjon90=ja": "at selskapet har hentet penger i en emisjon nylig",
}
FILTER = {
    "kurs.likviditet=lav": "aksjen omsettes for lite, så kurtasje og kursforskjell spiser gevinsten",
    "innside.kurs_vs_innsider=ukjent": "vi vet ikke hvilken kurs innsideren betalte (ofte tildeling eller opsjoner)",
}


def _pst(x: float | None, fortegn: bool = True) -> str:
    if x is None:
        return "–"
    t = f"{x * 100:+.1f}" if fortegn else f"{x * 100:.0f}".replace("-", "")
    return t.replace(".", ",").replace("-", "−") + " %"


def _kr(x: float | None) -> str:
    if not x:
        return "–"
    return (f"{x:,.2f}" if x < 100 else f"{x:,.1f}" if x < 1000 else f"{x:,.0f}").replace(",", " ").replace(".", ",") + " kr"


def _dato(s: str | None) -> str:
    if not s:
        return ""
    d = date.fromisoformat(s[:10])
    mnd = ["jan.", "feb.", "mars", "april", "mai", "juni", "juli", "aug.", "sep.", "okt.", "nov.", "des."][d.month - 1]
    return f"{d.day}. {mnd}"


def _faktor(nokkel: str) -> str:
    if nokkel in SETNING:
        return SETNING[nokkel]
    f, _, v = nokkel.partition("=")
    return f"{NAVN.get(f, f.split('.')[-1])} ({v})"


def _personer(tekst: str) -> str:
    """«A (CEO), B (CFO), C (styre)» -> «A (CEO) og 2 andre innsidere»."""
    deler = [d.strip(" ,") for d in re.findall(r"[^()]+?\([^)]*\)", tekst)] or [tekst.strip()]
    if len(deler) <= 2:
        return " og ".join(deler)
    return f"{deler[0]} og {len(deler) - 1} andre innsidere"


def _liste(deler: list[str]) -> str:
    return deler[0] if len(deler) == 1 else ", ".join(deler[:-1]) + " og " + deler[-1]


def tekst(c: dict) -> str:
    setn: list[str] = []
    # 1) Hva skjedde
    besk = c.get("beskrivelse") or ""
    if c.get("kilde") == "innside" and besk.startswith("Innsidekjøp: "):
        hvem, _, belop = besk.removeprefix("Innsidekjøp: ").partition("~")
        hvem, belop = hvem.strip().rstrip(","), belop.strip().replace("NOK", "kr")
        setn.append(f"{_dato(c.get('publisert'))}: {_personer(hvem)} kjøpte aksjer" + (f" for rundt {belop}." if belop else "."))
    elif besk:
        kat, sep, tittel = besk.partition(": ")
        if sep and re.fullmatch(r"[a-z_]+", kat):  # nyhet: «kategori: tittel»
            kat = kat.replace("oppkjop", "oppkjøp").replace("_", "/")
            besk = f"Børsmelding ({kat}): {tittel}"
        setn.append(f"{_dato(c.get('publisert'))}: {besk.rstrip('.')}.")
    if (c.get("hendelser_i_perioden") or 1) > 1:
        setn.append(f"Det er {c['hendelser_i_perioden']} slike meldinger i selskapet den siste tiden.")

    # 2) Hva hjernen venter, på den horisonten som ser best ut
    k, lg = c.get("kort") or {}, c.get("lang") or {}
    h = lg if (lg.get("forventet") or -9) > (k.get("forventet") or -9) else k
    if h:
        setn.append(f"Hjernen venter {_pst(h.get('forventet'))} mot børsen etter kostnader de neste {h.get('dager')} "
                    f"handelsdagene, med omtrent {_pst(h.get('p_gevinst'), False)} sjanse for gevinst.")
        bidrag = [b for b in (h.get("bidrag") or []) if abs(b[1]) >= 0.001]
        for_ = [_faktor(b[0]) for b in bidrag if b[1] > 0][:2]
        mot = [_faktor(b[0]) for b in bidrag if b[1] < 0][:2]
        if for_:
            setn.append(f"Det som teller mest i favør er {_liste(for_)}.")
        if mot:
            setn.append(f"Det som trekker ned er {_liste(mot)}.")

    # 3) Aksjescoren (faktormodellen)
    ps = c.get("aksjescore") or {}
    if ps:
        sc = round(ps["persentil"] * 100)
        nivaa = "blant de beste" if sc >= 80 else "over middels" if sc >= 55 else "rundt middels" if sc >= 34 else "blant de svakeste"
        b = ps.get("bidrag") or {}
        opp = [f"{x['navn'].lower()} ({x['verdi']})" for x in (b.get("opp") or [])[:2]]
        ned = [f"{x['navn'].lower()} ({x['verdi']})" for x in (b.get("ned") or [])[:1]]
        s = f"Aksjescoren er {sc} av 100, {nivaa} i {ps.get('region', 'regionen')}"
        if opp:
            s += f", trukket opp av {_liste(opp)}"
        if ned:
            s += f" og ned av {ned[0]}"
        setn.append(s + ".")

    # 4) Hva det betyr
    filt = c.get("filter")
    if filt:
        grunn = FILTER.get(filt) or ("aksjen er blant de svakeste på aksjescoren" if filt.startswith("aksjescore") else filt)
        setn.append(f"Den er filtrert bort fordi {grunn}.")
    elif c.get("anbefaling") == "kjøp":
        setn.append(f"Plan: kjøp for inntil {_kr(c.get('limit'))} ved åpning {_dato(c.get('inn_dato'))}, "
                    f"mål rundt {_kr(h.get('maal'))} innen {_dato(h.get('ut_dato'))}, og selg hvis kursen faller til "
                    f"{_kr(c.get('stopp'))}.")
    elif c.get("anbefaling") == "følg med":
        setn.append("Ikke kjøp ennå: forventningen er positiv, men for liten eller for usikker etter kostnader.")
    elif c.get("anbefaling") == "unngå":
        setn.append("Unngå: forventningen er tydelig negativ.")
    if c.get("status") == "løpende" and c.get("hittil") is not None:
        x = c["hittil"]
        if x >= 0.01:
            setn.append(f"Kursen har steget {_pst(x, False)} siden første mulige kjøpsdag, så noe av gevinsten kan allerede være tatt ut.")
        elif x <= -0.01:
            setn.append(f"Kursen har falt {_pst(-x, False)} siden første mulige kjøpsdag.")
    return " ".join(setn)
