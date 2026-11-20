"""Faktormodellen: bygg panel, test ærlig (walk-forward), tren og scor alle aksjer.

  python -m src.panel_kjor
"""
from __future__ import annotations

import json
import time
from datetime import datetime

from src import db
from src.config import TZ
from src.panel import bygg, modell


def main() -> int:
    t0 = time.time()
    with db.session() as con:
        data = bygg.last_data(con)
        df = modell.forbered(bygg.bygg(data))
        print(f"Panel: {len(df)} rader, {df['yahoo'].nunique()} aksjer, {df['dato'].min()} → {df['dato'].max()} "
              f"({time.time() - t0:.0f} s)")
        na = datetime.now(TZ).isoformat(timespec="seconds")
        con.execute("DELETE FROM panel_score")
        for reg, sub in df.groupby("region"):
            eg = modell.egenskaper(sub)
            ev = modell.evaluer(modell.walk_forward(sub, eg))
            if not ev:
                print(f"{reg}: for lite data")
                continue
            sc = modell.score(sub, eg, ev["tideler"])
            ev.update({"aksjer": int(len(sc)), "egenskaper": eg})
            con.execute("INSERT OR REPLACE INTO panel_status (region, oppdatert, data) VALUES (?,?,?)",
                        (reg, na, json.dumps(ev, ensure_ascii=False)))
            for r in sc.to_dict("records"):
                con.execute(
                    "INSERT OR REPLACE INTO panel_score (yahoo, dato, region, persentil, tidel, forventet_mer, kostnad, bidrag) "
                    "VALUES (?,?,?,?,?,?,?,?)",
                    (r["yahoo"], r["dato"], r["region"], r["persentil"], r["tidel"], r["forventet_mer"], r["kostnad"],
                     json.dumps({"opp": r["opp"], "ned": r["ned"]}, ensure_ascii=False)))
            print(f"{reg}: rang-IC {ev['ic']:+.3f} (t {ev['ic_t']:+.1f}) over {ev['uker']} uker; "
                  f"tideler {' '.join(f'{t['mer']:+.1%}' for t in ev['tideler'])}; "
                  f"topp 20 {ev['topp20_mer']:+.2%} mot snitt {ev['alle_mer']:+.2%}; {len(sc)} aksjer scoret")
        con.commit()
        db.logg(con, "panel", "ok", f"{len(df)} rader, {time.time() - t0:.0f} s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
