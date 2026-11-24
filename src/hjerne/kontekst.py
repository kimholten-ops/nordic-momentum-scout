"""Kontekstegenskaper: rapportvindu, tilbakekjøp, emisjon, sektor,
børsverdi og short. Alt regnes fra data kjent FØR hendelsen ble publisert.

Data hentes av src/kontekst_kjor.py. Mangler en kilde for et selskap eller
en periode, blir verdien «ukjent» — aldri gjettet.
"""
from __future__ import annotations

from bisect import bisect_left
from dataclasses import dataclass, field
from datetime import date, timedelta

from src import ask, marked

# MFN-arkivet trenger litt historikk før «ingen rapport på 90 dager» betyr noe.
OPPVARMING_DAGER = 100

SEKTOR_NORSK = {
    "Energy": "energi", "Basic Materials": "råvarer", "Industrials": "industri",
    "Consumer Cyclical": "konsum (syklisk)", "Consumer Defensive": "konsum (stabil)",
    "Healthcare": "helse", "Financial Services": "finans", "Technology": "teknologi",
    "Communication Services": "kommunikasjon", "Utilities": "kraft/forsyning", "Real Estate": "eiendom",
}


@dataclass
class Kontekst:
    hendelser: dict[tuple[str, str], list[str]] = field(default_factory=dict)  # (slug, type) -> publisert, sortert
    mfn_start: str | None = None
    isin: dict[str, set[str]] = field(default_factory=dict)
    short: dict[str, tuple[list[str], list[float]]] = field(default_factory=dict)  # isin -> (datoer, prosent)
    short_start: str | None = None
    info: dict[str, tuple[str | None, float | None]] = field(default_factory=dict)  # yahoo -> (sektor, aksjer)
    ask: dict[str, tuple[str, str]] = field(default_factory=dict)  # slug -> (status, forklaring), se src/ask.py


def last(con) -> Kontekst:
    k = Kontekst()
    tabeller = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    if "selskapshendelser" in tabeller:
        for r in con.execute("SELECT slug, type, publisert FROM selskapshendelser ORDER BY publisert"):
            k.hendelser.setdefault((r[0], r[1]), []).append(r[2])
        k.mfn_start = con.execute("SELECT MIN(publisert) FROM selskapshendelser").fetchone()[0]
    if "selskap_isin" in tabeller:
        for r in con.execute("SELECT slug, isin FROM selskap_isin"):
            k.isin.setdefault(r[0], set()).add(r[1])
    if "short" in tabeller:
        for r in con.execute("SELECT isin, dato, prosent FROM short ORDER BY isin, dato"):
            d, p = k.short.setdefault(r[0], ([], []))
            d.append(r[1])
            p.append(r[2])
        k.short_start = con.execute("SELECT MIN(dato) FROM short").fetchone()[0]
    if "selskapsinfo" in tabeller:
        for r in con.execute("SELECT yahoo, sektor, aksjer FROM selskapsinfo"):
            k.info[r[0]] = (r[1], r[2])
    if "selskaper" in tabeller:
        k.ask = ask.beregn(con)
    return k


def _dager_siden(liste: list[str], publisert: str) -> int | None:
    """Dager fra siste oppføring STRENGT før `publisert` (full tidsstempel)."""
    j = bisect_left(liste, publisert) - 1
    if j < 0:
        return None
    return (date.fromisoformat(publisert[:10]) - date.fromisoformat(liste[j][:10])).days


def egenskaper(k: Kontekst, kurser: dict[str, marked.Serie], slug: str, yahoo: str, publisert: str) -> dict[str, str]:
    ut: dict[str, str] = {}
    dag = publisert[:10]

    # 1) MFN: rapportvindu, tilbakekjøp og emisjon.
    dekket = any((slug, t) in k.hendelser for t in ("rapport", "tilbakekjop", "emisjon"))
    if not dekket:
        pass  # selskapet finnes ikke i MFN-arkivet: ingen MFN-egenskaper (heller enn falske «nei»)
    elif k.mfn_start and dag >= (date.fromisoformat(k.mfn_start[:10]) + timedelta(days=OPPVARMING_DAGER)).isoformat():
        d = _dager_siden(k.hendelser.get((slug, "rapport"), []), publisert)
        ut["kontekst.rapport"] = ("ingen rapport siste 90 d" if d is None or d > 90 else
                                  "0–2 dager etter rapport" if d <= 2 else
                                  "3–30 dager etter rapport" if d <= 30 else "31–90 dager etter rapport")
        for type_, navn in (("tilbakekjop", "kontekst.tilbakekjøp90"), ("emisjon", "kontekst.emisjon90")):
            d = _dager_siden(k.hendelser.get((slug, type_), []), publisert)
            ut[navn] = "ja" if d is not None and d <= 90 else "nei"

    # ASK: brukes som filter (HJERNE_UTELAT), ikke av modellen.
    if k.ask:
        ut["selskap.ask"] = k.ask.get(slug, (ask.UKJENT, ""))[0]

    # 2) Yahoo: sektor og børsverdi dagen før inngang.
    sektor, aksjer = k.info.get(yahoo, (None, None))
    if yahoo in k.info:
        ut["selskap.sektor"] = SEKTOR_NORSK.get(sektor or "", sektor or "ukjent")
    s = kurser.get(yahoo)
    if aksjer and s:
        i = s.indeks_etter(dag)
        c = s.close[i - 1] if 1 <= i <= len(s.close) else None
        verdi = marked.til_nok(aksjer * c, yahoo) if c else None
        ut["kurs.størrelse"] = ("ukjent" if verdi is None else "under 0,5 mrd" if verdi < 5e8 else
                                "0,5–5 mrd" if verdi < 5e9 else "over 5 mrd")

    # 3) Short (bare Oslo; registeret dekker bare norske aksjer). Posisjoner
    #    publiseres med forsinkelse: bruk rader datert før publiseringsdagen.
    if (marked.suffiks(yahoo) == ".OL" and k.short_start
            and dag > (date.fromisoformat(k.short_start) + timedelta(days=7)).isoformat()):
        siste = None
        for isin in k.isin.get(slug, ()):
            datoer, pst = k.short.get(isin, ([], []))
            j = bisect_left(datoer, dag) - 1
            if j >= 0:
                siste = max(siste or 0.0, pst[j])
        ut["kurs.short"] = ("ukjent ISIN" if not k.isin.get(slug) else
                            "ingen meldt" if not siste else "under 2 %" if siste < 2 else "2 % eller mer")
    return ut

