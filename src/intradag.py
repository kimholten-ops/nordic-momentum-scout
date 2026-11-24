"""Kurser gjennom børsdagen (Yahoo, ca. 15 minutters forsinkelse).

Kjøres i hver nyhetsrunde, men henter nye kurser høyst hvert
INTRADAG_MINUTTER. minutt i børstiden. Brukes til:

1. Kandidatene: kjøpsgrense og stopp regnes fra siste kurs i stedet for
   gårsdagens sluttkurs, og vi ser hvordan kursen har reagert på meldingen
   (src/hjerne/kandidater.py, src/hjerne/forklaring.py).
2. Varsel når en åpen papirhandel treffer stoppkursen, og påminnelse om
   planlagt salg samme dag.

Bare aksjer som kan kjøpes på aksjesparekonto (src/ask.py) hentes.

  python -m src.intradag            # hent hvis det er på tide, varsle
  python -m src.intradag --tving    # hent uansett tid
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta

from src import ask, db
from src.config import TZ, env
from src.notify import esc, send

INTRADAG_MINUTTER = int(env("INTRADAG_MINUTTER", "30"))
APNER, STENGER = "09:00", "17:35"  # Oslo 09:00–16:20, Stockholm/København/Helsingfors til 17:30
SALG_PAAMINNELSE = "15:30"

SKJEMA = """
CREATE TABLE IF NOT EXISTS intradag (
    yahoo   TEXT PRIMARY KEY,
    dato    TEXT NOT NULL,
    tid     TEXT NOT NULL,      -- siste kurs, lokal tid HH:MM
    sist    REAL NOT NULL,
    apning  REAL,
    hoy     REAL,
    lav     REAL,
    volum   REAL,
    bars    TEXT                -- JSON [[HH:MM, sluttkurs], ...] for dagen
);
CREATE TABLE IF NOT EXISTS intradag_varsel (
    nokkel  TEXT NOT NULL,
    type    TEXT NOT NULL,
    tid     TEXT NOT NULL,
    PRIMARY KEY (nokkel, type)
);
"""


def _symboler(con) -> list[str]:
    status = ask.beregn(con)
    # Bare aksjer som har handlet siste to uker (ikke avnoterte).
    fersk = {r[0] for r in con.execute("SELECT yahoo FROM kurser WHERE dato >= date('now', '-14 days') GROUP BY yahoo")}
    syms = {r[1] for r in con.execute("SELECT slug, yahoo FROM selskaper WHERE aktiv = 1 AND yahoo LIKE '%.%'")
            if ask.ok(status.get(r[0], (None,))[0]) and r[1] in fersk}
    syms |= {r[0] for r in con.execute("SELECT yahoo FROM hjerne_trades WHERE status = 'åpen' AND yahoo IS NOT NULL")}
    return sorted(s for s in syms if s)


def _hent(symboler: list[str], na: datetime) -> dict[str, dict]:
    import yfinance as yf

    ut: dict[str, dict] = {}
    for i in range(0, len(symboler), 200):
        gruppe = symboler[i:i + 200]
        df = yf.download(gruppe, period="1d", interval="15m", group_by="ticker", auto_adjust=False,
                         progress=False, threads=True, prepost=False)
        if df is None or df.empty:
            continue
        for sym in gruppe:
            try:
                d = df[sym] if len(gruppe) > 1 else df
            except KeyError:
                continue
            d = d.dropna(subset=["Close"])
            if d.empty:
                continue
            idx = d.index.tz_convert(TZ) if d.index.tz is not None else d.index.tz_localize("UTC").tz_convert(TZ)
            if idx[-1].date() != na.date():
                continue  # ingen handel i dag ennå
            # Yahoo merker bar med starttid; siste kurs gjelder slutten av baren.
            slutt = [(t + timedelta(minutes=15)).strftime("%H:%M") for t in idx]
            ut[sym] = {
                "dato": na.date().isoformat(), "tid": min(slutt[-1], na.strftime("%H:%M")),
                "sist": float(d["Close"].iloc[-1]), "apning": float(d["Open"].iloc[0]),
                "hoy": float(d["High"].max()), "lav": float(d["Low"].min()), "volum": float(d["Volume"].sum()),
                "bars": [[s, round(float(c), 4)] for s, c in zip(slutt, d["Close"])],
            }
    return ut


def last(con, dato: str | None = None) -> dict[str, dict]:
    """Dagens intradagkurser per Yahoo-symbol (tom hvis ingen i dag)."""
    if not con.execute("SELECT name FROM sqlite_master WHERE name = 'intradag'").fetchone():
        return {}
    dato = dato or datetime.now(TZ).date().isoformat()
    return {r["yahoo"]: {**dict(r), "bars": json.loads(r["bars"] or "[]")}
            for r in con.execute("SELECT * FROM intradag WHERE dato = ?", (dato,))}


def kurs_ved(info: dict, tid: str) -> float | None:
    """Siste kjente kurs ved klokkeslett `tid` (HH:MM) i dag, ellers None."""
    for t, c in reversed(info.get("bars") or []):
        if t <= tid:
            return c
    return None


def _varslet(con, nokkel: str, type_: str) -> bool:
    return con.execute("SELECT 1 FROM intradag_varsel WHERE nokkel = ? AND type = ?", (nokkel, type_)).fetchone() is not None


def _kr(v: float) -> str:
    return f"{v:,.2f}".replace(",", " ").replace(".", ",") + " kr"


def varsle(con, kurser: dict[str, dict], na: datetime) -> int:
    n = 0
    tid = na.strftime("%H:%M")
    for t in con.execute("SELECT * FROM hjerne_trades WHERE status = 'åpen'").fetchall():
        k = kurser.get(t["yahoo"])
        if not k:
            continue
        ticker = (t["yahoo"] or "").split(".")[0]
        endring = k["sist"] / t["inn"] - 1 if t["inn"] else 0
        if t["stopp"] and k["lav"] <= t["stopp"] and not _varslet(con, t["nokkel"], "stopp"):
            tekst = (f"🛑 <b>Stopp nådd: {esc(t['navn'])}</b> ({esc(ticker)})\n"
                     f"Kursen var nede i {_kr(k['lav'])}, under stoppen på {_kr(t['stopp'])}. "
                     f"Siste kurs {_kr(k['sist'])} kl. {k['tid']} ({endring * 100:+.1f} % fra kjøp).\n"
                     "Planen var å selge her.\n<i>Papirhandel/læring — ikke salgsråd.</i>")
            if send(tekst, push=True, tittel=f"Stopp nådd: {t['navn']}"):
                con.execute("INSERT OR IGNORE INTO intradag_varsel VALUES (?,?,?)", (t["nokkel"], "stopp", na.isoformat()))
                n += 1
        elif (t["plan_ut_dato"] == na.date().isoformat() and tid >= SALG_PAAMINNELSE
              and not _varslet(con, t["nokkel"], "salg")):
            tekst = (f"⏰ <b>Planlagt salg i dag: {esc(t['navn'])}</b> ({esc(ticker)})\n"
                     f"Holdetiden er ute. Planen er å selge på sluttkurs i dag. "
                     f"Siste kurs {_kr(k['sist'])} kl. {k['tid']} ({endring * 100:+.1f} % fra kjøp).\n"
                     "<i>Papirhandel/læring — ikke salgsråd.</i>")
            if send(tekst, push=True, tittel=f"Selg i dag: {t['navn']}"):
                con.execute("INSERT OR IGNORE INTO intradag_varsel VALUES (?,?,?)", (t["nokkel"], "salg", na.isoformat()))
                n += 1
    con.commit()
    return n


def kjor(tving: bool = False, na: datetime | None = None) -> dict:
    na = na or datetime.now(TZ)
    with db.session() as con:
        con.executescript(SKJEMA)
        if not tving and (na.weekday() >= 5 or not (APNER <= na.strftime("%H:%M") <= STENGER)):
            return {"hoppet_over": "utenfor børstid"}
        sist = con.execute("SELECT MAX(tidspunkt) FROM kjorelogg WHERE jobb = 'intradag' AND status = 'ok'").fetchone()[0]
        if not tving and sist and datetime.fromisoformat(sist) > na - timedelta(minutes=INTRADAG_MINUTTER - 3):
            return {"hoppet_over": f"hentet {sist[11:16]}"}
        symboler = _symboler(con)
        kurser = _hent(symboler, na)
        con.execute("DELETE FROM intradag WHERE dato < ?", (na.date().isoformat(),))
        con.executemany(
            "INSERT OR REPLACE INTO intradag (yahoo, dato, tid, sist, apning, hoy, lav, volum, bars) VALUES (?,?,?,?,?,?,?,?,?)",
            [(s, k["dato"], k["tid"], k["sist"], k["apning"], k["hoy"], k["lav"], k["volum"], json.dumps(k["bars"]))
             for s, k in kurser.items()],
        )
        con.commit()
        varslet = varsle(con, last(con, na.date().isoformat()), na)
        res = {"symboler": len(symboler), "med_kurs_i_dag": len(kurser), "varslet": varslet}
        db.logg(con, "intradag", "ok", json.dumps(res))
    return res


if __name__ == "__main__":
    try:
        print(kjor(tving="--tving" in sys.argv))
    except Exception as e:  # noqa: BLE001
        print(f"intradag feilet: {type(e).__name__}: {e}", file=sys.stderr)
        sys.exit(1)
