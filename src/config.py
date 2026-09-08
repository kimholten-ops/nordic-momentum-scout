"""Sentral konfigurasjon. Alt som kan tenkes å endres bor her."""
from __future__ import annotations

import os
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
DB_PATH = Path(os.environ.get("MOMENTUM_DB", DATA_DIR / "momentum.db"))
UNIVERSE_CSV = ROOT / "universe" / "tickers.csv"

TZ = ZoneInfo("Europe/Oslo")

# --- MFN ------------------------------------------------------------------
# Feed-URL. /all/s/nordic = alle nordiske pressemeldinger.
# Du kan lage egne filtrerte feeds på mfn.se og lime inn URL-en her,
# f.eks. kun Oslo Børs + Euronext Growth + Small Cap Stockholm.
MFN_FEED_URL = os.environ.get("MFN_FEED_URL", "https://mfn.se/all/s/nordic")

# Vær en høflig robot. Identifiser deg og ikke poll oftere enn nødvendig.
USER_AGENT = os.environ.get(
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
PRICE_LOOKBACK_DAYS = int(os.environ.get("PRICE_LOOKBACK_DAYS", "10"))
PRICE_BATCH_SIZE = 40

# --- Varsling (brukes fra fase 1, men heartbeat/feil allerede nå) ---------
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

# --- Børstid --------------------------------------------------------------
MARKET_OPEN = (9, 0)
MARKET_CLOSE = (16, 30)
