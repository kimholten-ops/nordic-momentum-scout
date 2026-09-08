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
