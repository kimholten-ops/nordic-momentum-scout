"""Fast kategori-enum for klassifisering. Modellen skal velge nøyaktig én av
disse og aldri finne på nye — se src/klassifisering/llm.py, som validerer
output mot denne listen før noe lagres.

PROMPT_VERSJON bumpes hver gang prompten eller kategorilisten endres. En
hendelse klassifiseres én gang per promptversjon (UNIQUE-constraint på
klassifiseringer-tabellen i src/db.py), så en bump lar deg sammenligne
versjoner mot hverandre i stedet for å overskrive historikken.

Listen er et første forslag for fase 1 — juster den etter hva som faktisk
dukker opp i egne data, men husk å bumpe PROMPT_VERSJON når du gjør det.
"""
from __future__ import annotations

PROMPT_VERSJON = "v1"

# "rutine" settes normalt av det regelbaserte forfilteret (forfilter.py) før
# en hendelse når LLM-en, men er med i enumet slik at modellen også kan
# bruke den — det er nyttig signal om forfilteret har et hull.
KATEGORIER: list[str] = [
    "rutine",
    "resultat",
    "kontrakt_ordre",
    "oppkjop_fusjon",
    "kapitalendring",
    "ledelse_styre",
    "guiding",
    "produkt_teknologi",
    "juridisk_regulatorisk",
    "rating_analyse",
    "annet",
]

KATEGORI_BESKRIVELSE: dict[str, str] = {
    "rutine": "Administrativ rutinemelding uten selvstendig nyhetsverdi.",
    "resultat": "Kvartals- eller årsresultat, regnskapstall.",
    "kontrakt_ordre": "Ny kontrakt, ordre eller avtale av betydning.",
    "oppkjop_fusjon": "Oppkjøp, fusjon, salg eller kjøp av virksomhet.",
    "kapitalendring": "Emisjon, rettet emisjon, nytt tilbakekjøpsprogram, aksjesplitt.",
    "ledelse_styre": "Endring i toppledelse eller styresammensetning.",
    "guiding": "Resultatvarsel, oppjustert eller nedjustert prognose.",
    "produkt_teknologi": "Produktlansering, FoU-milepæl, godkjenning.",
    "juridisk_regulatorisk": "Søksmål, bot, regulatorisk vedtak.",
    "rating_analyse": "Endring i kredittrating fra et ratingbyrå.",
    "annet": "Passer ikke i noen av de andre kategoriene.",
}

RETNINGER: list[str] = ["positiv", "negativ", "nøytral"]
