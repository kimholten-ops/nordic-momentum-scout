"""Arbeidskøen til vakten på PC-en (scripts/vakt.py).

GitHub Actions lager etter hver nyhetsrunde en liten fil med ferske meldinger
som trenger en språkmodell: innsidemeldinger reglene ikke klarte,
uklassifiserte børsmeldinger og nye kvartalsrapporter. Filen legges på
releasen «data-siste» (åpen lenke), så PC-en slipper å laste ned hele
databasen hvert tiende minutt.

  python -m scripts.arbeidsko lag          # data/arbeidsko.json
  python -m scripts.arbeidsko last-opp     # -> release data-siste (krever gh)
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta

from src import db
from src.config import ROOT, TZ

FIL = ROOT / "data" / "arbeidsko.json"
NAVN = "arbeidsko.json"
DAGER_MELDINGER = 3
DAGER_RAPPORTER = 7
MAKS_TEKST = 20_000


def lag(con, na: datetime | None = None) -> dict:
    from scripts import ollama_tolk as ot

    na = na or datetime.now(TZ)
    fra_m = (na - timedelta(days=DAGER_MELDINGER)).isoformat(timespec="seconds")
    fra_r = (na - timedelta(days=DAGER_RAPPORTER)).isoformat(timespec="seconds")
    felt = ("url", "tittel", "publisert", "selskap_slug")
    innside = [{k: r[k] for k in felt} for r in ot.ko(con) if (r["publisert"] or "") >= fra_m]
    nyheter = [{**{k: r[k] for k in felt}, "brodtekst": (r["brodtekst"] or "")[:MAKS_TEKST] or None}
               for r in ot.ko_nyheter(con) if (r["publisert"] or "") >= fra_m]
    rapporter = [{k: r[k] for k in ("group_id", "url", "slug", "publisert", "tittel")}
                 for r in ot.ko_rapporter(con) if (r["publisert"] or "") >= fra_r]
    nyest = lambda liste: sorted(liste, key=lambda r: r["publisert"] or "", reverse=True)  # noqa: E731
    return {"laget": na.isoformat(timespec="seconds"), "innside": nyest(innside),
            "nyheter": nyest(nyheter), "rapporter": nyest(rapporter)}


def main() -> int:
    if sys.argv[1:2] == ["last-opp"]:
        from scripts.db_lagring import REPO, TAG, _gh, _har_gh

        if not _har_gh() or not FIL.exists():
            print("Hopper over opplasting (mangler gh/GH_TOKEN eller fil).")
            return 0
        r = _gh("release", "upload", TAG, f"{FIL}#{NAVN}", "--clobber", sjekk=False)
        print(f"Lastet opp {NAVN} til {REPO}@{TAG}." if r.returncode == 0 else f"Opplasting feilet: {r.stderr[:300]}")
        return 0
    with db.session() as con:
        ko = lag(con)
    FIL.parent.mkdir(parents=True, exist_ok=True)
    FIL.write_text(json.dumps(ko, ensure_ascii=False), encoding="utf-8")
    print(f"Arbeidskø: {len(ko['innside'])} innside, {len(ko['nyheter'])} nyheter, {len(ko['rapporter'])} rapporter.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
