"""Telegram. Gratis, umiddelbar push til mobil, ingen konto hos noen tredjepart.

Oppsett:
  1. Snakk med @BotFather i Telegram -> /newbot -> du får et token.
  2. Send en melding til din nye bot.
  3. Åpne https://api.telegram.org/bot<TOKEN>/getUpdates og les ut chat.id.
  4. Legg begge inn som GitHub-secrets: TELEGRAM_TOKEN og TELEGRAM_CHAT_ID.

I fase 0 brukes dette kun til feilmeldinger og ukesoppsummering. Ikke slå på
signalvarsler før du har tall som forsvarer dem.
"""
from __future__ import annotations

import requests

from src.config import HTTP_TIMEOUT, TELEGRAM_CHAT_ID, TELEGRAM_TOKEN


def send(tekst: str, stille: bool = False) -> bool:
    if not (TELEGRAM_TOKEN and TELEGRAM_CHAT_ID):
        print("[notify] Telegram ikke konfigurert, skriver til stdout i stedet:\n" + tekst)
        return False
    r = requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
        json={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": tekst[:4000],
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
            "disable_notification": stille,
        },
        timeout=HTTP_TIMEOUT,
    )
    if not r.ok:
        print(f"[notify] Telegram svarte {r.status_code}: {r.text[:200]}")
    return r.ok


if __name__ == "__main__":
    send("✅ Testmelding fra momentum-varsling.")
