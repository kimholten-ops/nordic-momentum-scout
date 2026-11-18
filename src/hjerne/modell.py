"""Additiv modell med krymping og tidsvekting.

forventet = basis + sum(effekt[egenskap = verdi])

- Hver effekt er et vektet snitt av restleddet for hendelsene med den
  verdien, krympet mot 0 med HJERNE_PRIOR_STYRKE "late" observasjoner. En
  verdi sett tre ganger flytter derfor nesten ingenting; sett femti ganger
  flytter den nesten fullt.
- Effektene estimeres med backfitting (noen runder der hver egenskap
  tilpasses restleddet etter de andre), så to korrelerte egenskaper ikke
  teller samme signal dobbelt.
- Vekten til en observasjon halveres for hver HJERNE_HALVERINGSTID dager
  siden utfallet ble kjent: modellen følger markedet når det endrer seg, men
  glemmer ikke alt.

Bevisst enkelt. Med noen hundre observasjoner per år er dette nær det
datamengden tåler; et nevralt nett ville bare pugge støy.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date

RUNDER = 4


@dataclass
class Observasjon:
    egenskaper: dict[str, str]
    y: float
    kjent: str  # dato utfallet ble kjent (utgangsdag)


@dataclass
class Modell:
    basis: float = 0.0
    effekter: dict[str, dict[str, float]] = field(default_factory=dict)
    vekt: dict[str, dict[str, float]] = field(default_factory=dict)  # effektiv vekt per verdi
    sigma: float = 0.05
    n: int = 0
    n_eff: float = 0.0
    trent_til: str | None = None

    def forutsi(self, egenskaper: dict[str, str]) -> tuple[float, float, list[tuple[str, float]]]:
        """(forventet netto meravkastning, sikkerhet P(snitt > 0), bidrag)."""
        bidrag = []
        pred = self.basis
        svakest = self.n_eff
        for f, v in egenskaper.items():
            e = self.effekter.get(f, {}).get(v, 0.0)
            pred += e
            bidrag.append((f"{f}={v}", e))
            svakest = min(svakest, self.vekt.get(f, {}).get(v, 0.0))
        # Usikkerheten styres av den tynneste cellen prediksjonen hviler på.
        se = self.sigma / math.sqrt(svakest + 1.0)
        sikkerhet = 0.5 * (1 + math.erf(pred / (se * math.sqrt(2)))) if se > 0 else 0.5
        bidrag.sort(key=lambda x: -abs(x[1]))
        return pred, sikkerhet, bidrag

    def som_dict(self) -> dict:
        return {
            "basis": self.basis, "sigma": self.sigma, "n": self.n, "n_eff": round(self.n_eff, 1),
            "trent_til": self.trent_til,
            "effekter": {
                f: {v: {"effekt": round(e, 5), "vekt": round(self.vekt[f][v], 1)} for v, e in sorted(vs.items())}
                for f, vs in sorted(self.effekter.items())
            },
        }


def tren(obs: list[Observasjon], dag: str, prior: float, halveringstid: float) -> Modell:
    m = Modell(trent_til=dag)
    if not obs:
        return m
    d0 = date.fromisoformat(dag)
    w = [0.5 ** (max(0, (d0 - date.fromisoformat(o.kjent)).days) / halveringstid) for o in obs]
    sw = sum(w)
    m.n, m.n_eff = len(obs), sw
    m.basis = sum(wi * o.y for wi, o in zip(w, obs)) / (sw + prior)

    egenskaper = sorted({f for o in obs for f in o.egenskaper})
    for f in egenskaper:
        m.effekter[f] = {}
        m.vekt[f] = {}
        for wi, o in zip(w, obs):
            v = o.egenskaper.get(f)
            if v is not None:
                m.vekt[f][v] = m.vekt[f].get(v, 0.0) + wi

    # Hold en løpende prediksjon per observasjon; når én egenskap tilpasses,
    # trekkes dens gamle bidrag ut og det nye legges inn. O(n x egenskaper).
    pred = [m.basis] * len(obs)
    for _ in range(RUNDER):
        for f in egenskaper:
            gammel = m.effekter[f]
            sum_rest: dict[str, float] = {}
            for wi, o, p in zip(w, obs, pred):
                v = o.egenskaper.get(f)
                if v is None:
                    continue
                sum_rest[v] = sum_rest.get(v, 0.0) + wi * (o.y - (p - gammel.get(v, 0.0)))
            ny = {v: s_ / (m.vekt[f][v] + prior) for v, s_ in sum_rest.items()}
            for k, o in enumerate(obs):
                v = o.egenskaper.get(f)
                if v is not None:
                    pred[k] += ny.get(v, 0.0) - gammel.get(v, 0.0)
            m.effekter[f] = ny

    rest = [(wi, o.y - m.forutsi(o.egenskaper)[0]) for wi, o in zip(w, obs)]
    var = sum(wi * r * r for wi, r in rest) / sw
    m.sigma = max(math.sqrt(var), 0.01)
    return m
