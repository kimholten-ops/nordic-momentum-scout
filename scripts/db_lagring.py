"""Databasen lagres som et komprimert vedlegg på en GitHub-release, ikke i git.

  python -m scripts.db_lagring hent        # release -> data/momentum.db
  python -m scripts.db_lagring last-opp    # data/momentum.db -> release (VACUUM + gzip)

Hvorfor: i git lagrer hver commit en ny kopi av hele fila (~50 MB), og
GitHub advarer over 50 MB og stopper ved 100 MB. Et release-vedlegg kan være
opptil 2 GB, er gratis og vokser ikke i historikken.

- Release-taggen er «data-siste». Vedlegget «momentum.db.gz» er gjeldende
  database; «momentum-forrige.db.gz» er versjonen før siste opplasting
  (angreknapp hvis en jobb skulle skrive noe galt).
- Opplasting er trygg mot feil hos GitHub: ny fil lastes først opp som
  «momentum-ny.db.gz». Først når den ligger der, byttes navnene
  (gjeldende -> forrige, ny -> gjeldende). «gh release upload --clobber»
  sletter den gamle fila FØR den nye er oppe, og mistet begge kopiene ved en
  502 fra GitHub 25.11.2026.
- I Actions brukes gh-CLI med GITHUB_TOKEN. På PC-en (Ollama) lastes
  vedlegget ned over den åpne lenken (repoet er offentlig).
- Finnes ikke releasen ennå (første gang), brukes data/momentum.db fra
  repoet, og første opplasting oppretter releasen. Finnes releasen, men
  nedlastingen feiler, stopper jobben: den skal aldri fortsette på en
  gammel kopi og så overskrive nyere data.
"""
from __future__ import annotations

import gzip
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

from src.config import DB_PATH

TAG = "data-siste"
FIL = "momentum.db.gz"
FORRIGE = "momentum-forrige.db.gz"
NY = "momentum-ny.db.gz"
REPO = os.environ.get("GITHUB_REPOSITORY", "kimholten-ops/nordic-momentum-scout")
TMP = Path(os.environ.get("RUNNER_TEMP", "/tmp")) / "db-lagring"


def _gh(*args: str, sjekk: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["gh", *args, "--repo", REPO], capture_output=True, text=True, check=sjekk)


def _har_gh() -> bool:
    return bool(shutil.which("gh") and (os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")))


def _release_status() -> str:
    """«finnes», «mangler» (sikkert 404) eller «ukjent» (nett-/tilgangsfeil)."""
    import requests

    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    hode = {"Authorization": f"Bearer {token}"} if token else {}
    for forsok in range(3):
        try:
            r = requests.get(f"https://api.github.com/repos/{REPO}/releases/tags/{TAG}", headers=hode, timeout=30)
            if r.status_code == 200:
                return "finnes"
            if r.status_code == 404:
                return "mangler"
        except requests.RequestException:
            pass
        time.sleep(5 * (forsok + 1))
    return "ukjent"


def _release_finnes() -> bool:
    return _release_status() == "finnes"


def _pakk_ut(gz: Path, mål: Path) -> None:
    mål.parent.mkdir(parents=True, exist_ok=True)
    tmp = mål.with_suffix(".db.ny")
    with gzip.open(gz, "rb") as inn, tmp.open("wb") as ut:
        shutil.copyfileobj(inn, ut)
    con = sqlite3.connect(tmp)
    try:
        con.execute("PRAGMA quick_check").fetchone()  # feiler på ødelagt fil
    finally:
        con.close()  # Windows: en åpen tilkobling låser fila
    for forsok in range(5):
        try:
            tmp.replace(mål)
            return
        except PermissionError:
            if forsok == 4:
                raise PermissionError(
                    f"{mål} er i bruk av et annet program. Lukk andre vinduer som kjører Ollama-tolkningen "
                    "(eller DB Browser o.l.) og prøv igjen.") from None
            time.sleep(3)


def hent(mål: Path = DB_PATH) -> int:
    TMP.mkdir(parents=True, exist_ok=True)
    status = _release_status()
    if status == "ukjent":
        print("FEIL: fikk ikke sjekket releasen (nett/tilgang). Stopper.", file=sys.stderr)
        return 1
    if status == "mangler":
        print(f"Ingen release «{TAG}» ennå: bruker {mål} fra repoet ({mål.stat().st_size if mål.exists() else 0} byte).")
        return 0
    gz = TMP / FIL
    for forsok in range(4):  # navnebyttet er ikke atomisk: prøv igjen et par ganger
        try:
            import requests  # den åpne lenken (repoet er offentlig)

            r = requests.get(f"https://github.com/{REPO}/releases/download/{TAG}/{FIL}", timeout=300)
            if r.status_code == 404 and forsok >= 1:
                # Midt i (eller etter et avbrutt) navnebytte: «ny» er den nyeste fullstendige.
                r = requests.get(f"https://github.com/{REPO}/releases/download/{TAG}/{NY}", timeout=300)
            r.raise_for_status()
            gz.write_bytes(r.content)
            _pakk_ut(gz, mål)
            shutil.copyfile(gz, TMP / FORRIGE)  # lastes opp som «forrige» ved neste last-opp
            print(f"Hentet databasen fra release «{TAG}»: {mål.stat().st_size / 1e6:.1f} MB.")
            return 0
        except PermissionError as e:  # fila er låst lokalt: ny nedlasting hjelper ikke
            print(f"FEIL: {e}", file=sys.stderr)
            return 1
        except Exception as e:  # noqa: BLE001
            print(f"Nedlasting feilet ({e}); prøver igjen.", file=sys.stderr)
            time.sleep(10 * (forsok + 1))
    print("FEIL: releasen finnes, men databasen kunne ikke hentes. Stopper.", file=sys.stderr)
    return 1


def _vedlegg() -> dict[str, int]:
    """Navn -> id for vedleggene på releasen (REST)."""
    r = _gh("api", f"repos/{REPO}/releases/tags/{TAG}", "--jq", ".assets[] | [.name, .id] | @tsv", sjekk=False)
    if r.returncode:
        raise RuntimeError(f"fikk ikke listet vedleggene: {r.stderr[:200]}")
    return {navn: int(i) for navn, i in (linje.split("\t") for linje in r.stdout.splitlines() if "\t" in linje)}


def _gi_navn(asset_id: int, navn: str) -> bool:
    return _gh("api", "-X", "PATCH", f"repos/{REPO}/releases/assets/{asset_id}", "-f", f"name={navn}",
               sjekk=False).returncode == 0


def _slett(asset_id: int) -> bool:
    return _gh("api", "-X", "DELETE", f"repos/{REPO}/releases/assets/{asset_id}", sjekk=False).returncode == 0


def last_opp(kilde: Path = DB_PATH) -> int:
    if not _har_gh():
        print("FEIL: last-opp krever gh og GH_TOKEN (kjøres i Actions).", file=sys.stderr)
        return 1
    TMP.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(kilde)
    con.execute("VACUUM")
    con.close()
    gz = TMP / FIL
    with kilde.open("rb") as inn, gzip.open(gz, "wb", compresslevel=6) as ut:
        shutil.copyfileobj(inn, ut)
    if not _release_finnes():
        _gh("release", "create", TAG, "--title", "Database (siste)", "--latest=false",
            "--notes", "Databasen til nordic-momentum-scout. Oppdateres av GitHub Actions; ikke slett. "
                       "Se scripts/db_lagring.py.")
    # 1) Last opp som «ny». Gjeldende og forrige røres ikke før den ligger der.
    ny = TMP / NY
    shutil.copyfile(gz, ny)
    for forsok in range(4):
        if _gh("release", "upload", TAG, str(ny), "--clobber", sjekk=False).returncode == 0 and NY in _vedlegg():
            break
        time.sleep(15 * (forsok + 1))
    else:
        print("FEIL: opplasting feilet (gjeldende database er urørt).", file=sys.stderr)
        return 1
    # 2) Bytt navn: forrige slettes, gjeldende -> forrige, ny -> gjeldende.
    for forsok in range(4):
        try:
            v = _vedlegg()
            if FIL in v and NY in v:
                if FORRIGE in v and not _slett(v[FORRIGE]):
                    raise RuntimeError("fikk ikke slettet forrige")
                if not _gi_navn(v[FIL], FORRIGE):
                    raise RuntimeError("fikk ikke gitt gjeldende nytt navn")
                v = _vedlegg()
            if NY in v and FIL not in v and not _gi_navn(v[NY], FIL):
                raise RuntimeError("fikk ikke gitt ny fil navnet til gjeldende")
            if FIL in _vedlegg():
                print(f"Lastet opp databasen: {kilde.stat().st_size / 1e6:.1f} MB "
                      f"({gz.stat().st_size / 1e6:.1f} MB komprimert).")
                return 0
        except RuntimeError as e:
            print(f"Navnebytte feilet ({e}); prøver igjen.", file=sys.stderr)
        time.sleep(10 * (forsok + 1))
    print(f"FEIL: databasen ligger som «{NY}», men navnebyttet feilet. Neste «hent» bruker den.", file=sys.stderr)
    return 1


def main() -> int:
    kmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if kmd == "hent":
        return hent()
    if kmd == "last-opp":
        return last_opp()
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
