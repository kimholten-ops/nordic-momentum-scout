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
KLASSIFISERING_MAKS_KALL = int(env("KLASSIFISERING_MAKS_KALL", "200"))

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
INNSIDE_MAKS_LLM_KALL = int(env("INNSIDE_MAKS_LLM_KALL", "40"))
# Telegram-varsel for innsidesignaler er av som standard: i papirhandelsfasen
# skal signalene logges og måles før de varsler.
INNSIDE_VARSLE = env("INNSIDE_VARSLE", "0") == "1"

# --- Markedsindekser (meravkastning) -----------------------------------------
# Børssuffiks -> Yahoo-indeks. Brukes til å trekke markedet fra avkastningen.
INDEKSER = {".OL": "OSEBX.OL", ".ST": "^OMXSPI", ".HE": "^OMXH25", ".CO": "^OMXC25"}
KURSHISTORIKK_DAGER = int(env("KURSHISTORIKK_DAGER", "800"))

# --- Handelskostnader (backtest og papirhandel) -------------------------------
# Kurtasje i kroner per handel og posisjonsstørrelse avgjør kurtasje i prosent;
# slippage avhenger av likviditet (median daglig omsetning siste 20 dager, NOK).
KURTASJE_KR = float(env("KURTASJE_KR", "29"))
POSISJON_KR = float(env("POSISJON_KR", "3300"))
# Slippage PER SIDE ≈ halve spreaden: likvide 0,1 %, mellomstore 0,4 %, små 1 %.
SLIPPAGE_ETTER_OMSETNING = [(50_000_000, 0.001), (5_000_000, 0.004), (0, 0.01)]

# --- Hjerne (src/hjerne/) -------------------------------------------------------
HJERNE_HORISONT = int(env("HJERNE_HORISONT", "5"))          # handelsdager i en trade (kort sikt)
HJERNE_HORISONT_LANG = int(env("HJERNE_HORISONT_LANG", "20"))  # lang sikt på kandidatsiden
HJERNE_HALVERINGSTID = float(env("HJERNE_HALVERINGSTID", "30"))  # dager: nyere utfall veier mer
HJERNE_PRIOR_STYRKE = float(env("HJERNE_PRIOR_STYRKE", "15"))  # krymping mot 0 (antall "late" observasjoner)
HJERNE_MIN_EDGE = float(env("HJERNE_MIN_EDGE", "0.005"))    # forventet netto meravkastning for å handle
HJERNE_MIN_SIKKERHET = float(env("HJERNE_MIN_SIKKERHET", "0.6"))  # P(netto meravkastning > 0)
HJERNE_MAKS_APNE = int(env("HJERNE_MAKS_APNE", "3"))
HJERNE_KAPITAL = float(env("HJERNE_KAPITAL", "10000"))
HJERNE_STOPP_ATR = float(env("HJERNE_STOPP_ATR", "2.0"))    # stopp = inngang - k x ATR20
HJERNE_VARSLE = env("HJERNE_VARSLE", "1") == "1"

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
