"""Én test per regel i regler.py, med et realistisk titteleksempel per
språk. Formålet er at en endring i en regel som slutter å treffe et av disse
eksemplene, feiler synlig i stedet for stille."""
from __future__ import annotations

import pytest

from src.klassifisering.forfilter import sjekk_rutine

RUTINE_EKSEMPLER = [
    ("flagging", "Notification of Major Holdings in Equinor ASA"),
    ("flagging", "Flaggningsmeddelande i Fabege AB"),
    ("flagging", "Flaggingsmelding i Norsk Hydro"),
    ("tilbakekjop", "Equinor: Repurchases of shares during week 20"),
    ("tilbakekjop", "Återköp av aktier i Fabege AB"),
    ("tilbakekjop", "Buyback of own shares during week 12"),
    ("tilbakekjop", "Tilbakekjøp av egne aksjer i Kongsberg Gruppen"),
    ("tilbakekjop", "Tilbagekøb af egne aktier i Novo Nordisk"),
    ("generalforsamling", "Notice convening the annual general meeting"),
    ("generalforsamling", "Kallelse till årsstämma i Fabege AB"),
    ("generalforsamling", "Kutsu Metsä Groupin yhtiökokoukseen"),
    ("generalforsamling", "Innkalling til ordinær generalforsamling"),
    ("generalforsamling", "Indkaldelse til ordinær generalforsamling"),
    ("manedsstatistikk", "Monthly Statistics April 2024"),
    ("manedsstatistikk", "Månadsstatistik april 2024"),
    ("manedsstatistikk", "Norwegian Air Shuttle: Traffic performance April 2024"),
    ("manedsstatistikk", "Månedsstatistikk for april"),
    ("mar_ledelse", "Managers' transactions"),
    ("mar_ledelse", "Notification of transactions by persons discharging managerial responsibilities"),
    ("mar_ledelse", "Melding om transaksjon utført av primærinnsider"),
    ("mar_ledelse", "Anmälan om transaktion genomförd av person i ledande ställning"),
    ("tredjepart_analyse", "BioStock: Bolaget rapporterar positiva fas 2-data"),
    ("tredjepart_analyse", "Analysguiden: Uppdaterad analys av bolaget"),
    ("tredjepart_analyse", "Kalqyl: Ny analys publicerad"),
    ("tredjepart_analyse", "Redeye: Uppdaterad analys av bolaget"),
]


@pytest.mark.parametrize("forventet_regel,tittel", RUTINE_EKSEMPLER)
def test_rutinemonster_treffer(forventet_regel: str, tittel: str) -> None:
    assert sjekk_rutine(tittel) == forventet_regel


@pytest.mark.parametrize(
    "tittel",
    [
        "Equinor: Kvartalsrapport Q1 2024",
        "Kongsberg Gruppen tildelt kontrakt verdt 500 MNOK",
        "Norsk Hydro kjøper tilbake egne aksjer"  # merk: IKKE ukesrapport-formatet over
        " gjennom et nytt tilbakekjøpsprogram vedtatt av styret",
    ],
)
def test_reelle_nyheter_hoppes_ikke_over(tittel: str) -> None:
    assert sjekk_rutine(tittel) is None
