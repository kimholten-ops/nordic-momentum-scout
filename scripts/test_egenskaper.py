"""Tester om nye egenskapsgrupper gjør faktormodellen bedre (Norden).

Bygger panelet én gang og kjører samme kvartalsvise walk-forward med og uten
hver gruppe. Sammenligner rang-IC uke for uke (paret t-verdi, justert for
overlappende fasit) og toppdesil. En gruppe bør bare slås på hvis den hjelper
tydelig; ellers legges navnene i PANEL_EGENSKAPER_AV.

  python -m scripts.test_egenskaper [--panel /tmp/panel.pkl]
"""
from __future__ import annotations

import math
import sys

import pandas as pd

from src import db
from src.panel import bygg, modell

GRUPPER = {
    "råvarer/valuta": ["rav_driv_1m", "rav_driv_1u", "rav_gap_1m", "rav_r2"],
    "USA-bransje": ["usa_sektor_1u", "usa_sektor_1m", "usa_gap_1m"],
}
OVERLAPP = 4  # 20 d fasit, ukentlige rader


def _ic(P: pd.DataFrame) -> pd.Series:
    return P.groupby("dato").apply(lambda g: g["p"].rank().corr(g["fasit_mer"].rank())).dropna()


def _kjor(df: pd.DataFrame, eg: list[str]) -> tuple[pd.Series, float, float]:
    P = modell.walk_forward(df, eg)
    P = P.assign(tidel=P.groupby("dato")["p"].rank(pct=True).mul(10).clip(upper=9.999).astype(int))
    return _ic(P), float(P[P.tidel == 9]["fasit_mer"].mean()), float(P[P.tidel == 0]["fasit_mer"].mean())


def main() -> int:
    if "--panel" in sys.argv:
        df = pd.read_pickle(sys.argv[sys.argv.index("--panel") + 1])
    else:
        with db.session() as con:
            df = modell.forbered(bygg.bygg(bygg.last_data(con)))
    df = df[df["region"] == "Norden"]
    alle_nye = [k for g in GRUPPER.values() for k in g]
    grunn = [e for e in modell.egenskaper(df, av=[]) if e not in alle_nye]

    varianter = {"grunnmodell": (df, grunn)}
    for navn, g in GRUPPER.items():
        varianter[f"+ {navn}"] = (df, grunn + [k for k in g if k in df])
    varianter["+ alle nye"] = (df, grunn + [k for k in alle_nye if k in df])
    # Short utenfor Oslo: grunnmodellen med short bare for Oslo (som før).
    gammel = df.copy()
    gammel.loc[gammel["marked"].astype(str) != "OL", "short_pst"] = float("nan")
    varianter["grunnmodell, short bare Oslo"] = (gammel, grunn)

    res = {}
    for navn, (d, eg) in varianter.items():
        res[navn] = _kjor(d, eg)
        ic, topp, bunn = res[navn]
        print(f"{navn:32} IC {ic.mean():+.4f}  toppdesil {topp:+.2%}  bunndesil {bunn:+.2%}  ({ic.size} uker)", flush=True)
    base = res["grunnmodell"][0]
    print("\nForskjell mot grunnmodellen (uke for uke):")
    for navn, (ic, topp, bunn) in res.items():
        if navn == "grunnmodell":
            continue
        diff = (ic - base).dropna()
        t = diff.mean() / diff.std() * math.sqrt(diff.size / OVERLAPP) if diff.std() else 0.0
        if navn.endswith("short bare Oslo"):
            diff, t = -diff, -t  # positivt = ny short (med Helsingfors/Stockholm) er bedre
            navn = "short også Helsingfors/Stockholm"
        print(f"{navn:32} ΔIC {diff.mean():+.4f} (t {t:+.1f}), bedre {(diff > 0).mean():.0%} av ukene")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
