"""Sentral konfigurasjon. Alt som kan tenkes å endres bor her."""
from __future__ import annotations

import os
from pathlib import Path
from zoneinfo import ZoneInfo


def env(navn: str, standard: str = "") -> str:
    """Som os.environ.get, men tom streng teller som fravær.

    GitHub Actions setter alltid env-nøkkelen selv når secreten eller
    repo-variabelen ikke er definert — da med tom streng. Da gir
    os.environ.get(navn, standard) "" i stedet for standard, og f.eks.
    MFN_FEED_URL blir "" uten at noen ba om det.
    """
    return (os.environ.get(navn) or "").strip() or standard


ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
DB_PATH = Path(env("MOMENTUM_DB", str(DATA_DIR / "momentum.db")))
UNIVERSE_CSV = ROOT / "universe" / "tickers.csv"

TZ = ZoneInfo("Europe/Oslo")

# --- MFN ------------------------------------------------------------------
# Feed-URL. /all/s/nordic = alle nordiske pressemeldinger.
# Du kan lage egne filtrerte feeds på mfn.se og lime inn URL-en her,
# f.eks. kun Oslo Børs + Euronext Growth + Small Cap Stockholm.
MFN_FEED_URL = env("MFN_FEED_URL", "https://mfn.se/all/s/nordic")
# Feeden viser bare de ~50 nyeste meldingene uten limit. GitHub forsinker
# cron kraftig (i praksis 2-3 kjøringer i døgnet), så hver poll henter de
# siste N meldingene og fyller hullet siden forrige kjøring. 500 ≈ 1,5 døgn.
MFN_POLL_LIMIT = int(env("MFN_POLL_LIMIT", "500"))

# Vær en høflig robot. Identifiser deg og ikke poll oftere enn nødvendig.
USER_AGENT = env(
    "MOMENTUM_UA",
    "momentum-varsling/0.1 (personlig research-prosjekt; kontakt: sett MOMENTUM_UA)",
)
HTTP_TIMEOUT = 20

# Kildeprefiks i MFN-URL -> distributør. Nyttig metadata, ikke kritisk.
DISTRIBUTORS = {
    "ob": "Oslo Børs / NewsWeb",
    "cis": "Cision",
    "one": "GlobeNewswire",
    "beq": "beQuoted",
    "mfn": "MFN",
}

# --- Kursdata -------------------------------------------------------------
# yfinance-suffikser: .OL Oslo, .ST Stockholm, .CO København, .HE Helsinki
PRICE_LOOKBACK_DAYS = int(env("PRICE_LOOKBACK_DAYS", "10"))
PRICE_BATCH_SIZE = 40

# --- Varsling (brukes fra fase 1, men heartbeat/feil allerede nå) ---------
TELEGRAM_TOKEN = env("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = env("TELEGRAM_CHAT_ID", "")

# --- Klassifisering (fase 1) -----------------------------------------------
# Nøkkelen bor kun i miljøet/GitHub-secrets, aldri i repoet.
ANTHROPIC_API_KEY = env("ANTHROPIC_API_KEY", "")
ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_TIMEOUT = 30
# Haiku er billig nok til å kjøre på hver hendelse som passerer forfilteret.
KLASSIFISERING_MODELL = env("KLASSIFISERING_MODELL", "claude-haiku-4-5-20251001")
# Sikkerhetsnett mot en feed-eksplosjon som stille koster hundrevis av
# kroner: maks antall LLM-kall i én kjøring av src.klassifiser.
KLASSIFISERING_MAKS_KALL = int(env("KLASSIFISERING_MAKS_KALL", "30"))
# «anthropic» (Claude Haiku), «ollama» (lokal modell, brukes av scripts/ollama_tolk.py) eller «av».
KLASSIFISERING_LLM = env("KLASSIFISERING_LLM", "anthropic").lower()
# Bare meldinger yngre enn dette sendes til betalt API fra GitHub Actions.
# Eldre etterslep (arkiv, ting som ble liggende) tolkes gratis av Ollama på
# PC-en. Nov. 2026: jobben gikk fra 2 til 60 kjøringer om dagen, og hver
# kjøring brukte 200 kall på gammelt etterslep — det tømte API-kontoen.
LLM_FRISK_TIMER = float(env("LLM_FRISK_TIMER", "48"))

# --- Arkivimport (fase 1, src/backfill_arkiv.py) ---------------------------
# MFN dokumenterer ikke arkiv-/pagineringsformatet sitt. Dette er et
# rimelig utgangspunkt basert på at /all/a/<slug> er selskapets egen
# nyhetsside (se COMPANY_RE i src/sources/mfn.py) — sjekk mot ekte sider og
# juster URL-malen (og evt. sidetallsparameteren) hvis MFN bruker noe annet.
MFN_ARKIV_URL_MAL = env("MFN_ARKIV_URL_MAL", "https://mfn.se/all/a/{slug}?page={side}")
ARKIV_FORSINKELSE_SEK = float(env("ARKIV_FORSINKELSE_SEK", "2"))

# --- Innsidehandel (src/innside/) -------------------------------------------
# Grove valutakurser mot NOK. Brukes KUN til terskler i signalscoren, aldri
# til avkastning — nøyaktighet på noen få prosent spiller ingen rolle.
INNSIDE_FX_NOK = {
    "NOK": 1.0, "SEK": float(env("FX_SEK", "1.05")), "DKK": float(env("FX_DKK", "1.55")),
    "EUR": float(env("FX_EUR", "11.7")), "USD": float(env("FX_USD", "10.0")),
}
INNSIDE_MAKS_LLM_KALL = int(env("INNSIDE_MAKS_LLM_KALL", "15"))
# Hvem tolker meldinger regelparserne ikke klarer:
#   "anthropic" — Claude Haiku via API (koster litt, krever ANTHROPIC_API_KEY)
#   "ollama"    — lokal modell på egen PC (gratis), se README «Ollama»
#   "av"        — ingen KI; meldingene blir liggende som «trenger_llm»
INNSIDE_LLM = env("INNSIDE_LLM", "anthropic").lower()
OLLAMA_URL = env("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODELL = env("OLLAMA_MODELL", "qwen2.5:7b")
# Kontekstvindu for Ollama (tokens). Standard i Ollama er ofte 2–4k, som
# kutter lange børsmeldinger og rapporter stille.
OLLAMA_NUM_CTX = int(env("OLLAMA_NUM_CTX", "8192"))
# Telegram-varsel for innsidesignaler er av som standard: i papirhandelsfasen
# skal signalene logges og måles før de varsler.
INNSIDE_VARSLE = env("INNSIDE_VARSLE", "0") == "1"

# --- Markedsindekser (meravkastning) -----------------------------------------
# Børssuffiks -> Yahoo-indeks. Brukes til å trekke markedet fra avkastningen.
# «.US» er ikke et Yahoo-suffiks: amerikanske symboler har ingen endelse
# (SOFI, ASTS). marked.suffiks gir «.US» for dem; universe/usa.csv lister dem.
INDEKSER = {".OL": "OSEBX.OL", ".ST": "^OMXSPI", ".HE": "^OMXH25", ".CO": "^OMXC25", ".US": "^GSPC"}
# OSEBX er en totalavkastningsindeks (utbytte reinvestert); de andre er
# prisindekser. Aksjene får utbytte lagt til (kurser.utbytte), så
# prisindeksene får et anslått årlig direkteavkastningstillegg for at
# meravkastningen skal sammenligne likt med likt.
INDEKS_UTBYTTE_AAR = {".OL": 0.0, ".ST": 0.03, ".HE": 0.04, ".CO": 0.02, ".US": 0.013}
KURSHISTORIKK_DAGER = int(env("KURSHISTORIKK_DAGER", "1600"))  # fra ca. 2022: arkivet + 52 ukers bakblikk

# --- Handelskostnader (backtest og papirhandel) -------------------------------
# Kurtasje i kroner per handel og posisjonsstørrelse avgjør kurtasje i prosent;
# slippage avhenger av likviditet (median daglig omsetning siste 20 dager, NOK).
KURTASJE_KR = float(env("KURTASJE_KR", "29"))
POSISJON_KR = float(env("POSISJON_KR", "12500"))  # typisk posisjon; brukes i backtest og læringsmålet
# Slippage PER SIDE ≈ halve spreaden: likvide 0,1 %, mellomstore 0,4 %, små 1 %.
SLIPPAGE_ETTER_OMSETNING = [(50_000_000, 0.001), (5_000_000, 0.004), (0, 0.01)]

# --- Hjerne (src/hjerne/) -------------------------------------------------------
HJERNE_HORISONT = int(env("HJERNE_HORISONT", "5"))          # handelsdager i en trade (kort sikt)
HJERNE_HORISONT_LANG = int(env("HJERNE_HORISONT_LANG", "20"))  # lang sikt på kandidatsiden
HJERNE_HALVERINGSTID = float(env("HJERNE_HALVERINGSTID", "180"))  # dager: nyere utfall veier mer
# Krymping mot 0 (antall "late" observasjoner per verdi). Analysen nov. 2026:
# 50 halverte antall handler og snudde netto per handel fra ~0 til positiv;
# 15 lot hjernen tro for mye på små grupper.
HJERNE_PRIOR_STYRKE = float(env("HJERNE_PRIOR_STYRKE", "50"))
HJERNE_MIN_EDGE = float(env("HJERNE_MIN_EDGE", "0.005"))    # forventet netto meravkastning for å handle
HJERNE_MIN_SIKKERHET = float(env("HJERNE_MIN_SIKKERHET", "0.6"))  # P(netto meravkastning > 0)
HJERNE_MAKS_APNE = int(env("HJERNE_MAKS_APNE", "6"))
HJERNE_KAPITAL = float(env("HJERNE_KAPITAL", "100000"))   # papirkapital
# Posisjonsstørrelse = det MINSTE av:
#   - HJERNE_MAKS_ANDEL av kapitalen (spredning: ingen enkeltaksje over 15 %),
#   - HJERNE_RISIKO av kapitalen delt på stoppavstanden (taper du ut på
#     stoppen, mister du ca. 1 % av kapitalen),
#   - HJERNE_LIKVIDITET_TAK av median daglig omsetning (du skal ikke flytte kursen).
# Under HJERNE_MIN_POSISJON spiser kurtasjen for mye, og handelen droppes.
HJERNE_MAKS_ANDEL = float(env("HJERNE_MAKS_ANDEL", "0.15"))
HJERNE_RISIKO = float(env("HJERNE_RISIKO", "0.01"))
HJERNE_LIKVIDITET_TAK = float(env("HJERNE_LIKVIDITET_TAK", "0.05"))
HJERNE_MIN_POSISJON = float(env("HJERNE_MIN_POSISJON", "3000"))
HJERNE_STOPP_ATR = float(env("HJERNE_STOPP_ATR", "2.0"))    # stopp = inngang - k x ATR20
HJERNE_VARSLE = env("HJERNE_VARSLE", "1") == "1"

# Harde filtre: hendelser med disse egenskapene papirhandles og anbefales
# aldri, uansett hva modellen tror. Begge var klart negative både i 2023–24
# og i 2025–26 (illikvid = kostnaden spiser alt; ukjent innsiderkurs = ofte
# tildeling/opsjon/feil som slapp gjennom).
HJERNE_UTELAT = [("kurs.likviditet", "lav"), ("innside.kurs_vs_innsider", "ukjent")]

# Aksjesparekonto (src/ask.py): fokus på aksjer som kan kjøpes på ASK hos
# Nordnet. Andre aksjer (utenfor EØS, Euronext Growth/First North, USA)
# samles fortsatt inn og brukes til læring, men varsles, papirhandles og
# vises ikke som kandidater. ASK_MHF=1 når Growth/First North blir lov på
# ASK (foreslått fra 1.1.2027 i statsbudsjettet).
ASK_FOKUS = env("ASK_FOKUS", "1") == "1"
ASK_MHF = env("ASK_MHF", "0") == "1"
if ASK_FOKUS:
    HJERNE_UTELAT += [("selskap.ask", "utenfor EØS")] + ([] if ASK_MHF else [("selskap.ask", "Growth/First North")])

# Egenskaper som hentes og vises på dashboardet, men som modellen IKKE bruker.
# Walk-forward-test nov. 2026: med alle nye kontekstegenskaper falt
# rangkorrelasjonen fra 0,08 til 0,05 (mer støy enn signal med ~2 400
# hendelser). Bare «emisjon siste 90 d» var negativ i begge perioder og
# forbedret både kort og lang horisont; den er med. Fjern et navn her for å
# slå egenskapen på (eller sett repo-variabelen HJERNE_EGENSKAPER_AV).
HJERNE_EGENSKAPER_AV = [x for x in env(
    "HJERNE_EGENSKAPER_AV",
    "kontekst.rapport,kontekst.tilbakekjøp90,kurs.short,kurs.volatilitet,kurs.relstyrke20,selskap.sektor,kurs.størrelse",
).split(",") if x] + ["selskap.ask"]  # ASK-status er et filter, ikke et signal

# Faktormodellen (src/panel/): kandidater i Norden med aksjescore i nederste
# tredjedel filtreres bort. Test 2024–26: innsidekjøp i svakeste tredjedel
# ga -2,5 % mot børsen på 20 d, sterkeste tredjedel -0,1 %.
# Egenskaper faktormodellen ikke skal bruke (testet uten gevinst, se
# scripts/test_egenskaper.py). Kommaseparert i miljøvariabelen.
# Råvarer/valuta og USA-bransje ble testet 25.11.2026 uten sikker gevinst
# (ΔIC −0,005 til +0,004 over fire frø, t < 1). Dataene samles fortsatt inn.
PANEL_EGENSKAPER_AV = [x for x in env(
    "PANEL_EGENSKAPER_AV",
    "rav_driv_1m,rav_driv_1u,rav_gap_1m,rav_r2,usa_sektor_1u,usa_sektor_1m,usa_gap_1m",
).split(",") if x]
PANEL_FILTER_PERSENTIL = float(env("PANEL_FILTER_PERSENTIL", "0.333"))

# Regelsjekk (enkel, gjennomsiktig strategi ved siden av modellen): likvid,
# ingen advarsel og minst ett plusstegn. Lister fra analysen av 2023–24,
# testet på 2025–26 (20 d: +2,0 % over markedet, +1,0 % netto, n=64).
REGEL_LIKVID = ["høy", "middels"]
REGEL_ADVARSEL = [("kurs.reaksjon", "rolig"), ("kurs.volum", "normalt"), ("kurs.marked", "HE")]
REGEL_PLUSS = [("innside.verdi", ">1M"), ("kurs.marked", "CO"),
               ("innside.kurs_vs_innsider", "rundt innsiders kurs"),
               ("innside.kurs_vs_innsider", "under innsiders kurs"), ("kurs.reaksjon", "etter børstid")]

# --- Børstid --------------------------------------------------------------
MARKET_OPEN = (9, 0)
MARKET_CLOSE = (16, 30)


def valider() -> None:
    """Fanger de vanligste feilkonfigurasjonene tidlig, med en tydelig
    årsak — i stedet for at requests feiler langt nede med noe kryptisk
    som "MissingSchema: Invalid URL ''"."""
    if not MFN_FEED_URL.startswith("https://"):
        raise ValueError(f"MFN_FEED_URL er ugyldig: {MFN_FEED_URL!r}")
    if "example" in USER_AGENT or len(USER_AGENT) < 20:
        print("ADVARSEL: MOMENTUM_UA identifiserer deg ikke.")
