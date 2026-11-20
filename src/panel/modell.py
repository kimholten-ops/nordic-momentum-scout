"""Faktormodellen: LightGBM på panelet (src/panel/bygg.py).

- Lærer å RANGERE aksjene mot hverandre samme uke (mål = persentil av 20 d
  meravkastning), ikke å spå børsen.
- Én modell per region (Norden, USA): markedene oppfører seg ulikt.
- Testes ærlig først: kvartalsvis walk-forward der modellen bare ser rader
  med kjent fasit før testkvartalet. Tallene (rang-IC, tideler) vises på
  dashboardet, og «forventet meravkastning» for en aksje er snittet for
  tidelen den havner i, målt ut-av-utvalg — ikke modellens egen påstand.
- Forklaring per aksje: LightGBMs bidrag per egenskap (pred_contrib).
"""
from __future__ import annotations

import math
import warnings

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore", category=UserWarning)

IKKE_EGENSKAP = {"yahoo", "dato", "fasit_mer", "fasit_netto", "ut_dato", "kostnad"}
KATEGORISK = ["marked", "sektor"]
RUNDER = 300
PARAM = dict(objective="regression", learning_rate=0.03, num_leaves=15, min_data_in_leaf=300, bagging_fraction=0.8,
             bagging_freq=1, feature_fraction=0.8, lambda_l2=5.0, verbose=-1, seed=1, num_threads=0)
TEST_FRA = "2024-07-01"

NAVN = {
    "mom_12_1": "Momentum 12 mnd", "mom_6_1": "Momentum 6 mnd", "mom_1m": "Siste måned", "mom_1u": "Siste uke",
    "fra_52u_topp": "Avstand til 52-ukers topp", "over_52u_bunn": "Over 52-ukers bunn", "vol_60": "Volatilitet",
    "vol_20_60": "Volatilitet nå vs før", "log_omsetning": "Likviditet", "volumspike": "Volum siste uke",
    "over_ma200": "Over 200-dagers snitt", "rel_6m": "Styrke mot børsen 6 mnd", "indeks_over_ma200": "Børsens trend",
    "indeks_1m": "Børsen siste måned", "utbytte_12m": "Utbytte 12 mnd", "log_borsverdi": "Børsverdi",
    "sektor": "Sektor", "marked": "Børs", "dager_siden_rapport": "Dager siden rapport",
    "rapport_reaksjon": "Reaksjon på siste rapport", "rapport_volum": "Volum på rapportdagen",
    "drift_etter_rapport": "Utvikling etter rapport", "emisjon_90": "Emisjon siste 90 d",
    "tilbakekjop_90": "Tilbakekjøp siste 90 d", "innside_kjop_90": "Innsidekjøp 90 d",
    "innside_kjopere_90": "Innsidekjøpere 90 d", "innside_kjop_30": "Innsidekjøp 30 d",
    "innside_netto_mnok_90": "Netto innsidekjøp 90 d", "short_pst": "Short",
}
PROSENT = {"mom_12_1", "mom_6_1", "mom_1m", "mom_1u", "fra_52u_topp", "over_52u_bunn", "vol_60", "over_ma200",
           "rel_6m", "indeks_over_ma200", "indeks_1m", "utbytte_12m", "rapport_reaksjon", "drift_etter_rapport"}


def region(marked: str) -> str:
    return "USA" if marked == "US" else "Norden"


def forbered(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    if "innside_netto_mnok_90" in df:
        x = df["innside_netto_mnok_90"]
        df["innside_netto_mnok_90"] = np.sign(x) * np.log10(1 + x.abs())
    for k, tak in (("volumspike", 20), ("rapport_volum", 30), ("utbytte_12m", 0.25)):
        if k in df:
            df[k] = df[k].clip(upper=tak)
    df["region"] = df["marked"].astype(str).map(region)
    return df


def egenskaper(df: pd.DataFrame) -> list[str]:
    return [c for c in df.columns if c not in IKKE_EGENSKAP and c != "region" and df[c].notna().any()]


class _Modell:
    """Tynn innpakning rundt lightgbm.Booster (uten scikit-learn-avhengighet)."""

    def __init__(self, booster):
        self.booster_ = booster

    def predict(self, X: pd.DataFrame) -> np.ndarray:
        return self.booster_.predict(X)


def _tren(tr: pd.DataFrame, eg: list[str]) -> _Modell:
    import lightgbm as lgb

    y = tr.groupby("dato")["fasit_mer"].rank(pct=True)
    ds = lgb.Dataset(tr[eg], label=y, categorical_feature=[k for k in KATEGORISK if k in eg], free_raw_data=False)
    return _Modell(lgb.train(PARAM, ds, num_boost_round=RUNDER))


def walk_forward(df: pd.DataFrame, eg: list[str], test_fra: str = TEST_FRA) -> pd.DataFrame:
    d = df.dropna(subset=["fasit_mer"])
    starter = pd.date_range(test_fra, d["dato"].max(), freq="QS").strftime("%Y-%m-%d").tolist()
    ut = []
    for i, st in enumerate(starter):
        slutt = starter[i + 1] if i + 1 < len(starter) else "9999"
        tr, te = d[d["ut_dato"] < st], d[(d["dato"] >= st) & (d["dato"] < slutt)]
        if te.empty or len(tr) < 2000:
            continue
        ut.append(te.assign(p=_tren(tr, eg).predict(te[eg])))
    return pd.concat(ut) if ut else pd.DataFrame()


def evaluer(P: pd.DataFrame, horisont: int = 20) -> dict:
    """Rang-IC per uke, tideler og toppvalg — alt ut-av-utvalg."""
    if P.empty:
        return {}
    ic = P.groupby("dato").apply(lambda g: g["p"].rank().corr(g["fasit_mer"].rank())).dropna()
    P = P.assign(tidel=P.groupby("dato")["p"].rank(pct=True).mul(10).clip(upper=9.999).astype(int))
    tideler = P.groupby("tidel").agg(mer=("fasit_mer", "mean"), netto=("fasit_netto", "mean"), n=("fasit_mer", "size"))
    topp = P.groupby("dato", group_keys=False).apply(lambda g: g.nlargest(20, "p"))
    overlapp = max(1.0, horisont / 5)  # ukentlige rader, 20 d fasit: ~4 overlappende
    return {
        "uker": int(ic.size), "rader": int(len(P)), "fra": str(P["dato"].min()), "til": str(P["dato"].max()),
        "ic": float(ic.mean()), "ic_t": float(ic.mean() / ic.std() * math.sqrt(ic.size / overlapp)) if ic.std() else 0.0,
        "ic_andel_pos": float((ic > 0).mean()),
        "tideler": [{"tidel": int(t) + 1, "mer": float(r.mer), "netto": float(r.netto), "n": int(r.n)}
                    for t, r in tideler.iterrows()],
        "topp20_mer": float(topp["fasit_mer"].mean()), "topp20_netto": float(topp["fasit_netto"].mean()),
        "alle_mer": float(P["fasit_mer"].mean()), "alle_netto": float(P["fasit_netto"].mean()),
    }


def _verdi_tekst(k: str, v) -> str:
    if v is None or (isinstance(v, float) and not math.isfinite(v)):
        return "–"
    if k in PROSENT:
        return f"{v:+.0%}".replace("+-", "-") if k not in ("vol_60", "utbytte_12m") else f"{v:.0%}"
    if k == "log_omsetning":
        x = 10 ** v
        return f"{x / 1e9:.1f} mrd NOK/d" if x >= 1e9 else f"{x / 1e6:.1f} MNOK/d"
    if k == "log_borsverdi":
        return f"{10 ** v / 1e9:.1f} mrd NOK"
    if k == "marked":
        return {"OL": "Oslo", "ST": "Stockholm", "HE": "Helsingfors", "CO": "København", "US": "USA"}.get(str(v), str(v))
    if k == "short_pst":
        return f"{v:.1f} %"
    if k in ("emisjon_90", "tilbakekjop_90"):
        return "ja" if v else "nei"
    if isinstance(v, float):
        return f"{v:.1f}" if abs(v) < 100 else f"{v:.0f}"
    return str(v)


def score(df: pd.DataFrame, eg: list[str], tideler: list[dict]) -> pd.DataFrame:
    """Trener på alt med kjent fasit og scorer siste uke. Returnerer én rad
    per aksje: persentil, forventet meravkastning (tidelssnitt ut-av-utvalg)
    og de viktigste bidragene."""
    tr = df.dropna(subset=["fasit_mer"])
    siste = df[df["dato"] == df.groupby("yahoo")["dato"].transform("max")]
    # Bare aksjer med kurs de siste ti dagene (avnoterte og stoppede faller ut).
    siste = siste[siste["dato"] >= (pd.Timestamp(df["dato"].max()) - pd.Timedelta(days=10)).strftime("%Y-%m-%d")]
    if tr.empty or siste.empty:
        return pd.DataFrame()
    m = _tren(tr, eg)
    p = m.predict(siste[eg])
    bidrag = m.booster_.predict(siste[eg], pred_contrib=True)[:, :-1]
    pct = pd.Series(p, index=siste.index).rank(pct=True)
    forv = {t["tidel"]: t["mer"] for t in tideler}
    rader = []
    for (ix, rad), b, q in zip(siste.iterrows(), bidrag, pct):
        orden = np.argsort(b)
        fmt = lambda j: {"navn": NAVN.get(eg[j], eg[j]), "verdi": _verdi_tekst(eg[j], rad[eg[j]]), "bidrag": float(b[j])}
        tidel = min(10, int(q * 10) + 1)
        rader.append({
            "yahoo": rad["yahoo"], "dato": rad["dato"], "region": rad["region"], "persentil": float(q),
            "tidel": tidel, "forventet_mer": forv.get(tidel), "kostnad": float(rad["kostnad"]),
            "opp": [fmt(j) for j in orden[::-1][:3] if b[j] > 0], "ned": [fmt(j) for j in orden[:3] if b[j] < 0],
        })
    return pd.DataFrame(rader)
