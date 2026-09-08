"""Mønstre for åpenbart rutinemessige børsmeldinger. Ren data, ingen logikk.

src/klassifisering/forfilter.py kompilerer og bruker disse. Hvert mønster er
et (regelnavn, regex)-par. Regex er case-insensitive og matches mot
tittelen alene — forfilteret kjøres før brødtekst hentes. Dekker
norsk/svensk/dansk/engelsk/finsk der det er relevant for regelen.

Legg til nye mønstre etter hvert som du ser rutinemeldinger som slipper
gjennom. Ikke fjern et mønster uten å sjekke testen for regelen i
tests/test_forfilter.py.
"""
from __future__ import annotations

RUTINE_MONSTRE: list[tuple[str, str]] = [
    # --- Flaggemeldinger (større eierandeler) ------------------------------
    ("flagging", r"notification of major holdings"),
    ("flagging", r"flaggningsmeddelande|flaggning\b"),
    ("flagging", r"flagging(?:smelding)?\b"),

    # --- Tilbakekjøp av egne aksjer -----------------------------------------
    ("tilbakekjop", r"repurchases? of (?:own )?shares?(?: during week)?"),
    ("tilbakekjop", r"återköp av (?:egna )?aktier"),
    ("tilbakekjop", r"buyback of (?:own )?shares?(?: during week)?"),
    ("tilbakekjop", r"tilbakekjøp av egne aksjer"),
    ("tilbakekjop", r"tilbagekøb af (?:egne )?aktier"),

    # --- Innkalling til generalforsamling ------------------------------------
    ("generalforsamling", r"notice (?:of|convening)(?: the| an)? (?:annual|extraordinary)?\s*general meeting"),
    ("generalforsamling", r"kallelse till (?:årsstämma|extra bolagsstämma|bolagsstämma)"),
    ("generalforsamling", r"kutsu .*yhtiökokoukseen"),
    ("generalforsamling", r"innkalling til (?:ordinær |ekstraordinær )?generalforsamling"),
    ("generalforsamling", r"indkaldelse til (?:ordinær |ekstraordinær )?generalforsamling"),

    # --- Måneds-/trafikkstatistikk -------------------------------------------
    ("manedsstatistikk", r"monthly statistics"),
    ("manedsstatistikk", r"månadsstatistik"),
    ("manedsstatistikk", r"traffic (?:performance|statistics)"),
    ("manedsstatistikk", r"månedsstatistikk|trafikktall"),

    # --- MAR: ledende personers transaksjoner (innsidehandel, rutinemessig
    # meldeplikt — ikke å forveksle med selve saken som utløste handelen) ----
    ("mar_ledelse", r"managers.{0,2} transactions?"),
    ("mar_ledelse", r"persons? discharging managerial responsibilities"),
    ("mar_ledelse", r"transaksjon(?:er)? (?:utført|foretatt) av (?:primærinnsider|ledende ansatt|person med ledelsesansvar)"),
    ("mar_ledelse", r"transaktion(?:er)? (?:utförd|genomförd) av (?:person i ledande ställning|närstående)"),

    # --- Analyse-/IR-innhold produsert av tredjepart -------------------------
    ("tredjepart_analyse", r"\bbiostock\b"),
    ("tredjepart_analyse", r"\banalysguiden\b"),
    ("tredjepart_analyse", r"\bkalqyl\b"),
    ("tredjepart_analyse", r"\bredeye\b"),
]
