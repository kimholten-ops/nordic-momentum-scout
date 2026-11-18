"""Varsling: Telegram (alt) og web push til dashboard-PWA-en (kun forslag).

Telegram-oppsett (GitHub → Settings → Secrets and variables → Actions):
  1. I Telegram: snakk med @BotFather → /newbot → du får et token.
  2. Send en vilkårlig melding til den nye boten din (ellers kan den ikke
     skrive til deg).
  3. Kjør workflowen «Varslingstest» med token satt som secret
     TELEGRAM_TOKEN — den skriver ut chat-id-en din. Eller lokalt:
        TELEGRAM_TOKEN=... python -m src.notify --finn-chat-id
  4. Legg chat-id-en inn som secret TELEGRAM_CHAT_ID og kjør «Varslingstest»
     igjen. Den sier nøyaktig hva som er galt hvis noe feiler.

Web push-oppsett: se README («Varsler på telefonen»).

Meldinger skrives i Telegram-HTML. All tekst som kommer fra data (navn,
titler, feilmeldinger) MÅ gå gjennom esc(), ellers avviser Telegram hele
meldingen når et navn inneholder & eller <. send() faller i tillegg tilbake
til ren tekst hvis Telegram likevel ikke klarer å tolke HTML-en.
"""
from __future__ import annotations

import argparse
import html
import json
import re
import sys

import requests

from src.config import HTTP_TIMEOUT, TELEGRAM_CHAT_ID, TELEGRAM_TOKEN, env

WEBPUSH_VAPID_PRIVATE = env("WEBPUSH_VAPID_PRIVATE", "")
WEBPUSH_SUBSCRIPTIONS = env("WEBPUSH_SUBSCRIPTIONS", "")
WEBPUSH_KONTAKT = env("WEBPUSH_KONTAKT", "mailto:momentum@example.invalid")
DASHBOARD_URL = env("DASHBOARD_URL", "")


def esc(tekst) -> str:
    return html.escape(str(tekst if tekst is not None else ""), quote=False)


def _ren_tekst(tekst: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", tekst))


def _telegram(tekst: str, stille: bool) -> bool:
    if not (TELEGRAM_TOKEN and TELEGRAM_CHAT_ID):
        print("[notify] Telegram ikke konfigurert (TELEGRAM_TOKEN/TELEGRAM_CHAT_ID mangler):\n" + _ren_tekst(tekst))
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    data = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": tekst[:4000],
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
        "disable_notification": stille,
    }
    try:
        r = requests.post(url, json=data, timeout=HTTP_TIMEOUT)
        if r.status_code == 400 and "parse" in r.text.lower():
            data.pop("parse_mode")
            data["text"] = _ren_tekst(tekst)[:4000]
            r = requests.post(url, json=data, timeout=HTTP_TIMEOUT)
    except requests.RequestException as e:
        print(f"[notify] Telegram nettverksfeil: {type(e).__name__}")
        return False
    if not r.ok:
        print(f"[notify] Telegram svarte {r.status_code}: {r.text[:300]}")
    return r.ok


def _abonnementer() -> list[dict]:
    if not WEBPUSH_SUBSCRIPTIONS.strip():
        return []
    data = json.loads(WEBPUSH_SUBSCRIPTIONS)
    return data if isinstance(data, list) else [data]


def _webpush(tittel: str, tekst: str, url: str | None) -> int:
    """Sender til alle lagrede abonnementer. Returnerer antall levert."""
    if not (WEBPUSH_VAPID_PRIVATE and WEBPUSH_SUBSCRIPTIONS):
        return 0
    from pywebpush import WebPushException, webpush

    payload = json.dumps({"title": tittel, "body": tekst[:300], "url": url or DASHBOARD_URL or "./"})
    levert = 0
    for ab in _abonnementer():
        try:
            webpush(
                subscription_info=ab,
                data=payload,
                vapid_private_key=WEBPUSH_VAPID_PRIVATE,
                vapid_claims={"sub": WEBPUSH_KONTAKT},
                ttl=6 * 3600,
            )
            levert += 1
        except WebPushException as e:
            kode = getattr(e.response, "status_code", None)
            hint = " (abonnementet er utløpt — slå varsler på igjen i appen og oppdater secreten)" if kode in (404, 410) else ""
            print(f"[notify] web push feilet {kode}{hint}")
    return levert


def send(tekst: str, stille: bool = False, push: bool = False, tittel: str | None = None, url: str | None = None) -> bool:
    """Telegram alltid; web push i tillegg når push=True (forslag/signaler).
    Returnerer True hvis minst én kanal leverte."""
    ok = _telegram(tekst, stille)
    if push:
        ren = _ren_tekst(tekst)
        forste, _, resten = ren.partition("\n")
        ok = (_webpush(tittel or forste, resten.strip() or forste, url) > 0) or ok
    return ok


# --- Diagnose -------------------------------------------------------------------

def finn_chat_id() -> int:
    if not TELEGRAM_TOKEN:
        print("TELEGRAM_TOKEN er ikke satt.")
        return 1
    r = requests.get(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates", timeout=HTTP_TIMEOUT)
    if not r.ok:
        print(f"Telegram svarte {r.status_code}: {r.text[:200]} — er tokenet riktig?")
        return 1
    chats = {}
    for u in r.json().get("result", []):
        m = u.get("message") or u.get("channel_post") or {}
        c = m.get("chat") or {}
        if c.get("id"):
            chats[c["id"]] = c.get("username") or c.get("title") or c.get("first_name")
    if not chats:
        print("Ingen meldinger funnet. Send en melding til boten din i Telegram og kjør igjen.")
        return 1
    for cid, navn in chats.items():
        print(f"chat-id: {cid}   ({navn})  -> legg inn som secret TELEGRAM_CHAT_ID")
    return 0


def test() -> int:
    feil = 0
    print("== Telegram")
    if not TELEGRAM_TOKEN:
        print("  ✗ TELEGRAM_TOKEN er tom. Secreten finnes ikke, eller workflowen sender den ikke videre.")
        feil += 1
    else:
        r = requests.get(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getMe", timeout=HTTP_TIMEOUT)
        if not r.ok:
            print(f"  ✗ Tokenet avvises ({r.status_code}). Lag nytt hos @BotFather og oppdater secreten.")
            feil += 1
        else:
            print(f"  ✓ Token gyldig: @{r.json()['result'].get('username')}")
            if not TELEGRAM_CHAT_ID:
                print("  ✗ TELEGRAM_CHAT_ID er tom. Forsøker å finne den:")
                finn_chat_id()
                feil += 1
            elif _telegram("✅ Testmelding fra momentum-varsling. Telegram virker.", stille=False):
                print("  ✓ Testmelding sendt")
            else:
                print("  ✗ Sending feilet (se feilmeldingen over). 'chat not found' = feil chat-id, "
                      "eller du har ikke sendt boten en melding ennå.")
                feil += 1
    print("== Web push")
    if not (WEBPUSH_VAPID_PRIVATE and WEBPUSH_SUBSCRIPTIONS):
        print("  – ikke satt opp (valgfritt): WEBPUSH_VAPID_PRIVATE og WEBPUSH_SUBSCRIPTIONS")
    else:
        try:
            n = _webpush("Momentum-varsling", "Testvarsel: web push virker.", None)
            print(f"  {'✓' if n else '✗'} levert til {n} av {len(_abonnementer())} enheter")
            feil += 0 if n else 1
        except Exception as e:
            print(f"  ✗ {type(e).__name__}: {e}")
            feil += 1
    return 1 if feil else 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--test", action="store_true", help="diagnose og testmelding på alle kanaler")
    p.add_argument("--finn-chat-id", action="store_true")
    a = p.parse_args()
    if a.finn_chat_id:
        sys.exit(finn_chat_id())
    sys.exit(test())
