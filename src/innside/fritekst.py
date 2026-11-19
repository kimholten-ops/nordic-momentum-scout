"""Regelbasert uttrekk fra fritekst-meldinger (Oslo Børs m.fl.) — uten KI.

Mange norske, svenske og danske meldinger følger samme mal:

  «Margrethe Hauge, CEO and primary insider in Goodtech ASA, has on 30
   October 2026 purchased 10,000 shares ... at an average price of NOK 14.40
   per share. Following the transaction, Margrethe Hauge holds 380,343 shares»
  «Svend Egil Larsen, investeringsdirektør (CIO) ..., har i dag ... kjøpt
   4 072 aksjer ... til NOK 1,27 per aksje. Etter denne transaksjonen eier
   Selaco AS 2 748 139 aksjer»

Parseren er bevisst forsiktig: den tolker bare meldinger med ÉN tydelig kjøp-
eller salgssetning, og gir opp (None) når noe tyder på emisjon, tildeling,
opsjoner, lån eller flere transaksjoner. Da går meldingen videre til KI
(eller lokal Ollama) eller blir liggende som «trenger_llm». Treffsikkerheten
er målt mot KI-uttrekket i tests/test_fritekst.py.
"""
from __future__ import annotations

import re
from datetime import date

from src.innside.parser import Handel, rolle_fra_tekst

TALL = r"\d{1,3}(?:[  ,.]\d{3})+|\d+"
PRIS = r"\d{1,3}(?:\.\d{3})+,\d+|\d{1,3}(?:,\d{3})+\.\d+|\d+(?:[ \u00a0]\d{3})*(?:[.,]\d+)?"
VALUTA = r"NOK|SEK|EUR|DKK|USD|kr\.?|kroner"
ENHET = r"(?:ordinary\s+|common\s+|class\s+[AB]\s+)?(?:shares|aksjer|aktier|osaketta|stk\.?\s+aktier)"

KJOP = r"bought|purchased|acquired|has\s+bought|kjøpt|kjøper|købt|erhvervet|köpt|förvärvat|förvärvade|ostanut"
SALG = r"sold|disposed\s+of|solgt|sælger|sålt|avyttrat|avyttrade|myynyt"
TRANS = re.compile(
    rf"\b(?P<verb>{KJOP}|{SALG})\b(?:\s+\w+){{0,4}}?\s+(?:a\s+total\s+of\s+|totalt\s+|i\s+alt\s+|sammanlagt\s+)?"
    rf"(?P<antall>{TALL})\s+{ENHET}",
    re.IGNORECASE,
)
KURS_RE = re.compile(  # dansk/norsk «til kurs 246,91» (uten valuta)
    rf"(?:til|till|at|à)\s+(?:en\s+)?(?:gjennomsnittlig\s+|gennemsnitlig\s+)?kurs(?:en)?\s+(?:på\s+|af\s+)?"
    rf"(?:(?P<v1>{VALUTA})\s*)?(?P<p1>{PRIS})",
    re.IGNORECASE,
)
# Substantivform: «the purchase of 54,700 common shares», «salg av 10 000 aksjer».
TRANS_SUBST = re.compile(
    rf"\b(?P<verb>purchase|acquisition|sale|disposal|kjøp|salg|köp|försäljning|køb)\s+(?:of|av|af)\s+"
    rf"(?:a\s+total\s+of\s+|totalt\s+)?(?P<antall>{TALL})\s+{ENHET}",
    re.IGNORECASE,
)
PRIS_RE = re.compile(
    rf"(?:price\s+of|price\s+per\s+share\s+of|at|til|à|kurs(?:en)?\s+(?:på|av)?|till|á|pris\s+(?:på|af))\s+"
    rf"(?:a\s+)?(?:weighted\s+)?(?:volume[- ]weighted\s+)?(?:average\s+)?(?:price\s+of\s+)?"
    rf"(?:(?P<v1>{VALUTA})\s*(?P<p1>{PRIS})|(?P<p2>{PRIS})\s*(?P<v2>{VALUTA}))",
    re.IGNORECASE,
)
BEHOLDNING = [
    re.compile(rf"(?:following|after)\s+(?:the|this|these)\s+(?:transactions?|purchases?|sales?|trades?|acquisitions?)"
               rf",?\s+.{{0,140}}?\b(?:holds?|owns?|controls?|has)\s+(?:a\s+total\s+of\s+|in\s+total\s+)?(?P<n>{TALL})\s+{ENHET}",
               re.IGNORECASE),
    re.compile(rf"etter\s+(?:denne\s+|disse\s+|dette|det\s+)?(?:transaksjonen|transaksjonene|handelen|kjøpet|salget)?,?\s+"
               rf"(?:eier|har|kontrollerer)\s+.{{0,120}}?(?P<n>{TALL})\s+aksjer", re.IGNORECASE),
    re.compile(rf"efter\s+(?:transaktionen|förvärvet|affären|handeln),?\s+(?:äger|innehar|har)\s+.{{0,80}}?(?P<n>{TALL})\s+aktier",
               re.IGNORECASE),
    re.compile(rf"(?:efter\s+handlen|beholdning(?:en)?\s+(?:er|udgør))\s+.{{0,80}}?(?P<n>{TALL})\s+(?:stk\.?\s+)?aktier",
               re.IGNORECASE),
]
FLERE_PERSONER = re.compile(
    r"følgende\s+(?:primærinnsidere|personer|ledende|nærstående)|the\s+following\s+(?:primary\s+insiders|persons|PDMRs?|"
    r"members|individuals|employees|executives)|följande\s+(?:personer|insynspersoner|befattningshavare)|"
    r"følgende\s+ledende\s+medarbejdere|nedenstående",
    re.IGNORECASE,
)
# Et tall etter disse ordene er et totalbeløp, ikke pris per aksje.
TOTAL_FOR = re.compile(r"(?:svarende|tilsvarende|motsvarande|corresponding|equivalent|amounting|totalt?|total\s+(?:value|amount)|in\s+total|i\s+alt)\W*(?:\w+\W+){0,2}$",
                       re.IGNORECASE)
# Ord som betyr at dette ikke er et vanlig kjøp/salg i markedet. Da gir vi opp.
KOMPLISERT = re.compile(
    r"subscri|allot|allocat|grant|award|exercis|vesting|vested|incentive|\bLTI\b|\bRSU|\bPSU|bonus|private\s+placement|"
    r"rights\s+issue|consideration\s+shares|emisjon|tegn(?:et|ing)|tildel|innløs|conversion|convertible|warrant|"
    r"option|opsjon|optioner|loan|lån|pledge|pant|gift|gave|lending|teckn|tilldel|incitament|share\s+programme|"
    r"aksjeprogram|aktieprogram|spin-?off|merger|fusjon|dividend|utbytte|transfer(?:red)?|overført|overdrag|"
    r"employee\s+share|share\s+purchase\s+program|savings\s+program|spareprogram|aksjesparepro|\bESPP\b|"
    r"buy-?back|tilbakekjøp|återköp|tilbagekøb|own\s+shares|egne\s+aksjer|egna\s+aktier|egne\s+aktier|treasury",
    re.IGNORECASE,
)
MAANEDER = {m: i + 1 for i, ms in enumerate([
    ("january", "januar", "januari", "jan"), ("february", "februar", "februari", "feb"), ("march", "mars", "marts", "mar"),
    ("april", "apr"), ("may", "mai", "maj"), ("june", "juni", "jun"), ("july", "juli", "jul"),
    ("august", "augusti", "aug"), ("september", "sep", "sept"), ("october", "oktober", "okt", "oct"),
    ("november", "nov"), ("december", "desember", "dec", "des")]) for m in ms}
DATO_RE = re.compile(r"\b(\d{1,2})\.?\s+([A-Za-zæøåäö]+)\s+(\d{4})\b|\b(\d{4})-(\d{2})-(\d{2})\b")
ROLLEORD = re.compile(
    r"chief\s+\w+\s+officer|\bCEO\b|\bCFO\b|\bCOO\b|\bCTO\b|\bCIO\b|administrerende\s+direktør|konsernsjef|"
    r"finansdirektør|\w*direktør|\bvd\b|verkställande\s+direktör|ekonomichef|finanschef|adm\.?\s*dir\w*|"
    r"chair(?:man|woman|person)?(?:\s+of\s+the\s+board)?|styreleder|styrets\s+leder|styrelsens\s+ordförande|ordförande|"
    r"bestyrelsesformand|board\s+member|member\s+of\s+the\s+board|director(?:\s+at\s+the\s+board)?|styremedlem|"
    r"styrelseledamot|bestyrelsesmedlem|primary\s+insider|primærinnsider|close(?:ly)?\s+(?:associate[ds]?|related)|"
    r"nærstående|närstående|nærtstående|related\s+part(?:y|ies)|wholly[- ]owned|heleide|controlled\s+by|kontrollert\s+av|"
    r"daglig\s+leder|bestyrelsesmedlem|bestyrelsesformand|næstformand|senior\s+vice\s+president|\bEVP\b|\bSVP\b|executive\s+vice\s+president|head\s+of\s+[\w ]{2,30}|leder\s+for\s+[\w ]{2,30}",
    re.IGNORECASE,
)


def _heltall(s: str) -> float:
    return float(re.sub(r"[  ,.]", "", s))


def _pris(s: str) -> float | None:
    s = s.replace(" ", " ").strip()
    if re.fullmatch(r"\d{1,3}(?:,\d{3})+\.\d+", s):       # 1,234.50
        s = s.replace(",", "")
    elif re.fullmatch(r"\d{1,3}(?:\.\d{3})+,\d+", s):     # 1.090,27 (dansk)
        s = s.replace(".", "").replace(",", ".")
    else:
        s = s.replace(" ", "").replace(",", ".")          # 1 234,50 / 1,27 / 14.40
    try:
        return float(s)
    except ValueError:
        return None


def _valuta(v: str) -> str:
    v = v.upper().rstrip(".")
    return "NOK" if v in ("KR", "KRONER") else v


def _setninger(tekst: str) -> list[str]:
    tekst = re.sub(r"\s+", " ", tekst)
    # Ikke del på desimalpunktum, «Mr.», «ASA.» midt i navn holder vi ut med.
    return re.split(r"(?<=[a-zæøåäö0-9\)])\.\s+(?=[A-ZÆØÅÄÖ])", tekst)


def _dato(s: str, publisert: str | None) -> str | None:
    s = HODE_RE.sub("", s, count=1)
    if re.search(r"\b(?:i dag|today|idag|i\s+dag)\b", s, re.IGNORECASE) and publisert:
        return publisert[:10]
    funnet = list(DATO_RE.finditer(s))
    # Dateline først («Oslo, 22 October 2026:») og så «has on 20 October» — ta den siste.
    for m in reversed(funnet):
        if m.group(4):
            return f"{m.group(4)}-{m.group(5)}-{m.group(6)}"
        mnd = MAANEDER.get(m.group(2).lower().rstrip("."))
        if mnd:
            try:
                return date(int(m.group(3)), mnd, int(m.group(1))).isoformat()
            except ValueError:
                continue
    return None


HODE_RE = re.compile(r"^.*?\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(?::\d{2})?\s*")  # «Tittel 2026-11-02 08:00:00 »
DATOLINJE_RE = re.compile(r"^[^.:]{0,70}?\d{4}\s*[:\-–]\s*")              # «Oslo, 22 October 2026: »


def _uten_hode(setning: str) -> str:
    return DATOLINJE_RE.sub("", HODE_RE.sub("", setning, count=1), count=1)


def _person(setning: str) -> str | None:
    s = _uten_hode(setning)
    m = re.match(r"\s*([A-ZÆØÅÄÖ][\w.\-'’]+(?:\s+(?:[A-ZÆØÅÄÖ][\w.\-'’]+|af|von|van|de|der)){0,5})\s*,", s)
    return m.group(1).strip() if m else None


PRISORD_RE = re.compile(
    rf"\b(?:price|pris|kurs|gjennomsnittskurs|snittkurs|kursen|försäljningspris|köpkurs|köpeskilling|anskaffelseskurs|"
    rf"gennemsnitskurs)\b[^0-9\n]{{0,45}}?(?:(?P<v1>{VALUTA})\s*)?(?P<p1>{PRIS})(?:\s*(?P<v2>{VALUTA}))?",
    re.IGNORECASE,
)


def _finn_pris(tekst: str) -> tuple[float | None, str | None]:
    for rx in (KURS_RE, PRISORD_RE):
        for m in rx.finditer(tekst):
            if TOTAL_FOR.search(tekst[max(0, m.start() - 40):m.start()]):
                continue
            v = m.group("v1") or (m.groupdict().get("v2"))
            return _pris(m.group("p1")), _valuta(v) if v else None
    for m in PRIS_RE.finditer(tekst):
        if TOTAL_FOR.search(tekst[max(0, m.start() - 40):m.start()]):
            continue  # «svarende til DKK 250.858» er totalbeløpet
        return _pris(m.group("p1") or m.group("p2")), _valuta(m.group("v1") or m.group("v2"))
    return None, None


def _beholdning(tekst: str) -> float | None:
    for r in BEHOLDNING:
        b = r.search(tekst)
        if b:
            # Nevnes både egne aksjer og samlet med nærstående, er samlet størst.
            setning = tekst[b.start(): b.end() + 160].split(". ")[0]
            tall = [_heltall(x) for x in re.findall(rf"({TALL})\s+{ENHET}", setning, re.IGNORECASE)]
            return max(tall) if tall else _heltall(b.group("n"))
    return None


def parse_fritekst(tekst: str, publisert: str | None = None) -> list[Handel] | None:
    """Én Handel per tydelig kjøps-/salgssetning (maks én per setning), ellers
    None (= «vet ikke», ikke «ingen handel»)."""
    if not tekst:
        return None
    setninger = _setninger(tekst[:4000])
    treff = [(i, m) for i, s in enumerate(setninger) for rx in (TRANS, TRANS_SUBST) for m in rx.finditer(s)]
    treff.sort(key=lambda x: (x[0], x[1].start()))
    if not treff or len({i for i, _ in treff}) != len(treff) or len(treff) > 4:
        return None  # ingen, eller flere handler i samme setning
    # Lister/tabeller med flere personer («følgende primærinnsidere ...») og
    # tekster med langt flere aksjeantall enn handlene vi fant: gi opp.
    if FLERE_PERSONER.search(tekst[:3000]):
        return None
    antall_nevnt = len(re.findall(rf"(?:{TALL})\s+{ENHET}", " ".join(setninger)[:3000], re.IGNORECASE))
    if antall_nevnt > 2 * len(treff) + 1:
        return None
    # Flere kjøps-/salgsverb fulgt av et tall enn handler vi fant: noe er oversett.
    verb_med_tall = re.findall(rf"\b(?:{KJOP}|{SALG})\b[^.]{{0,60}}?\d", " ".join(setninger), re.IGNORECASE)
    if len(verb_med_tall) > len(treff):
        return None
    if re.search(r"own\s+shares|egne\s+aksjer|egna\s+aktier|egne\s+aktier|tilbakekjøp|buy-?back", tekst[:400], re.IGNORECASE):
        return None
    ut: list[Handel] = []
    for i, m in treff:
        setning = setninger[i]
        neste = setninger[i + 1] if i + 1 < len(setninger) else ""
        if KOMPLISERT.search(setning) or (KOMPLISERT.search(neste) and len(treff) == 1):
            return None
        verb = m.group("verb").lower()
        retning = "kjop" if re.fullmatch(rf"{KJOP}|purchase|acquisition|kjøp|köp|køb", verb, re.IGNORECASE) else "salg"
        antall = _heltall(m.group("antall"))
        if not antall:
            return None
        pris, valuta = _finn_pris(setning[m.end():])
        if pris is None and len(treff) == 1:
            pris, valuta = _finn_pris(neste)
        kropp = _uten_hode(setning[: m.start()])
        roller = ROLLEORD.findall(kropp) or ROLLEORD.findall(_uten_hode(setning))
        rolle_tekst = ", ".join(dict.fromkeys(r.strip() for r in roller))[:120] or None
        rolle, naer = rolle_fra_tekst(rolle_tekst)
        if re.search(r"wholly[- ]owned|heleide|controlled\s+by|kontrollert\s+av|through\s+(?:his|her)\s+company|"
                     r"gjennom\s+sitt|related\s+party|nærtstående\s+til|närstående\s+till", setning, re.IGNORECASE):
            naer = True
        ut.append(Handel(
            person=_person(setning), rolle_tekst=rolle_tekst, rolle=rolle, naerstaaende=naer,
            retning=retning, instrument="aksje", antall=antall, pris=pris, valuta=valuta,
            transaksjonsdato=_dato(setning, publisert),
            beholdning_etter=_beholdning(" ".join(setninger[i:i + 3])) if len(treff) == 1 else None,
        ))
    return ut
