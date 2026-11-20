# momentum-varsling

Nyhetsdrevet varslingssystem for nordisk børs. **Ikke** en autonom tradingbot:
systemet foreslår, du bestemmer.

Kjører gratis på GitHub Actions med SQLite som database. Ingen server, ingen
abonnement, ingen Vercel eller Supabase.

## Status: fase 1 — innsamling + klassifisering

Repoet henter pressemeldinger fra MFN hvert 10. minutt i børstid, lagrer dem
sammen med daglige kurser (fase 0), dedupliserer dem til hendelser og
klassifiserer det som ikke er åpenbar rutine med Claude Haiku (fase 1).

Fortsatt **ingen signaler, ingen inn-/utpriser, ingen varsler om handler**.
Det er meningen — kategorien og begrunnelsen modellen gir er råstoff for
backtesten i fase 2, ikke et kjøpsråd. Uten reelle data derfra har du
ingenting å teste ideen mot.

## Innsidehandel (src/innside/)

Egen signalkilde ved siden av nyhetsklassifiseringen: meldinger om
transaksjoner utført av ledende personer (MAR art. 19). Selskaper som
Nasdaq-noterte bruker et fast skjema som parses med regler; Oslo Børs-
meldinger er fritekst (skjemaet ligger i et PDF-vedlegg) og trekkes ut av
Haiku — uten kursdata, og uten å gjette tall som ikke står i teksten.

```bash
python -m src.innside.soek      # engangs: hent innsidemeldinger bakover via MFN-søk
python -m src.innside_kjor      # trekk ut handler, lag signaler (kjører også i poll-workflowen)
```

Engangskjøring (Actions → *Innside backfill og backtest*): henter historikk,
trekker ut handler, mapper selskaper til tickere, henter kurser og skriver
backtest-tabellen til jobbsammendraget. Backtesten går inn på åpningskursen
dagen etter publisering og trekker fra kostnader; den er **ikke**
markedsjustert ennå, så tallene sier om det finnes noe å følge opp, ikke at
det er en edge.

Signalet er en regelbasert score (rolle, klynge av kjøpere, verdi) over
åpne aksjekjøp — salg, tildelinger, opsjoner og obligasjoner er lagret men
teller ikke. Vektene er startverdier som skal kalibreres i backtesten.
Telegram-varsel er av til du setter repo-variabelen `INNSIDE_VARSLE=1`.

## Dashboard (GitHub Pages)

`site/index.html` er en statisk side som leser `site/data.json`; workflowen
*Dashboard (GitHub Pages)* bygger json-filen fra databasen etter hver
datakjøring og publiserer. Slå på under Settings → Pages → Source:
**GitHub Actions**. Lokalt: `python -m src.eksport_dashboard && python -m http.server -d site`.
NB: Pages fra privat repo krever betalt GitHub-abonnement, og siden blir
offentlig tilgjengelig på nettet (den har `noindex`, men ingen innlogging).

## Arkiv og regelbasert uttrekk (uten KI)

`python -m src.innside.arkiv --fra 2023-01-01` (eller workflowen **Innside
arkiv**) henter innsidemeldinger fra MFNs åpne JSON-feed
(`mfn.se/all/s/nordic.json?tag=sub:ci:insider`). Feeden har hele teksten og
selskapets ticker (børs:symbol), så selskaper mappes automatisk til Yahoo.

Meldingene tolkes med regler, uten KI-kostnad:
- `src/innside/parser.py`: Nasdaq-skjemaet («Nature of transaction: …»).
- `src/innside/fritekst.py`: fritekst på norsk/engelsk/svensk/dansk («…, CEO,
  has on 30 October purchased 10,000 shares at NOK 14.40 …»). Målt mot
  KI-svar på 635 meldinger: tolker ~43 %, enig i ~97 %. Gir opp ved emisjon,
  tildeling, opsjoner, egne aksjer, lister og flere handler i én setning.

Det reglene ikke klarer, merkes «trenger_llm». Velg selv hvem som tolker resten
(`INNSIDE_LLM`): `anthropic` (Haiku, ca. 2–3 øre per melding), `ollama`
(lokal modell, gratis) eller `av`. Titler som åpenbart er tildeling,
spareprogram, emisjon o.l. merkes «ingen handler» uten KI.

**Betalt API brukes bare på ferske meldinger.** GitHub Actions sender bare
meldinger publisert de siste `LLM_FRISK_TIMER` (48) timene til Claude, høyst
30 børsmeldinger og 15 innsidemeldinger per kjøring. Alt eldre etterslep tas
av Ollama på PC-en. Svarer API-et at kreditten er tom (eller nøkkelen er
ugyldig), stopper kallene for den kjøringen uten at noe merkes som feilet,
og det kommer høyst én Telegram-melding om dagen.

**Ollama på egen PC** (gratis): `ollama pull qwen2.5:7b`, så i repomappen:
```powershell
python -m scripts.ollama_tolk          # kan avbrytes og startes igjen
```
Skriptet tar to køer: innsidemeldinger (`data/ollama/tolkninger.jsonl`) og
uklassifiserte børsmeldinger (`data/ollama/klassifiseringer.jsonl`,
`--bare nyheter` for bare den). Det pusher hver 50. tolkning til grenen `ollama-tolkninger` (uten å røre arbeidsmappen eller
databasen lokalt). Workflowen **Importer Ollama-tolkninger** henter dem
derfra annenhver time og legger dem inn i databasen.
Regn med 5–30 sekunder per melding avhengig av maskinen.

## Faktormodellen / aksjescore (src/panel/)

Gir hver aksje (Norden + ca. 500 amerikanske i `universe/usa.csv`) en score
hver uke. Ett panel med én rad per aksje per uke (~140 000 rader) og
egenskaper kjent ved sluttkurs: momentum (12, 6, 1 mnd, 1 uke), avstand til
52-ukers topp/bunn, volatilitet, likviditet, volum, 200-dagers snitt, styrke
mot børsen, børsens trend, utbytte, børsverdi, sektor, reaksjon og volum på
siste kvartalsrapport, emisjon/tilbakekjøp, innsidekjøp og short. Mål: 20
handelsdagers meravkastning mot børsen (kjøp på åpning neste dag).

- LightGBM, én modell per region, lærer å rangere aksjene mot hverandre.
- Testet kvartalsvis walk-forward fra juli 2024 (bare rader med kjent fasit
  før testkvartalet). Rang-IC og avkastning per tidel vises i fanen
  «Aksjescore»; «forventet 20 d» er tidelens faktiske snitt ut-av-utvalg.
- Forklaring per aksje: egenskapene som trakk scoren mest opp og ned.
- Kandidater i Norden med score i nederste tredjedel filtreres bort
  (`PANEL_FILTER_PERSENTIL`).
- USA-universet er dagens aksjer (overlevelsesskjevhet): les USA-scoren med
  skepsis. Oppdater listen med `python -m scripts.oppdater_usa`.

Kjøres i «Kurser og rapport» (`python -m src.panel_kjor`, under ett minutt).

## Hjernen (src/hjerne/)

Lærer av utfallet til hver hendelse — innsidekjøp og (når nyhetsklassifiseringen
har data) klassifiserte nyheter — og simulerer handler med det den har lært.

- **Mål:** netto meravkastning over `HJERNE_HORISONT` handelsdager (standard 5):
  aksjens avkastning fra åpning dagen etter publisering, minus børsindeksen,
  minus kurtasje og slippage.
- **Modell:** additiv modell med krymping (`HJERNE_PRIOR_STYRKE`) og tidsvekt
  (`HJERNE_HALVERINGSTID` dager), over grove egenskaper: rolle, antall kjøpere
  siste 30 dager, verdi, andel av beholdningen, likviditet, 20-dagers momentum,
  6-måneders trend, avstand til 52-ukers topp/bunn, kursreaksjon og volum på
  meldingsdagen (bare når meldingen kom i børstiden), markedsregime (indeks
  over/under 200-dagers snitt), børs, volatilitet (ATR20), styrke mot børsen
  siste 20 dager, og for nyheter kategori/retning/overraskelse.
- **Kontekst** (`src/hjerne/kontekst.py`, data fra `src.kontekst_kjor`):
  dager siden siste kvartals-/årsrapport, tilbakekjøp og emisjon siste 90
  dager (MFN), netto shortandel for Oslo-aksjer (Finanstilsynet), sektor og
  børsverdi (Yahoo: antall aksjer × kurs). Alt regnes fra det som var kjent
  før publisering; mangler kilden, blir verdien «ukjent». Engangsfylling:
  Actions → «Kontekst arkiv (engangs)». Daglig oppdatering i «Kurser og rapport».
  Alle vises på kandidatene, men modellen bruker foreløpig bare «emisjon 90 d»
  (`HJERNE_EGENSKAPER_AV`): de andre ga mer støy enn signal i walk-forward-testen.
- **Totalavkastning:** utbytte (Yahoo, per eksdato) legges til aksjens
  avkastning. OSEBX er totalavkastningsindeks; de andre børsenes prisindekser
  får et anslått utbyttetillegg (`INDEKS_UTBYTTE_AAR`).
- **Harde filtre** (`HJERNE_UTELAT`): illikvide aksjer og kjøp der innsiders
  kurs er ukjent papirhandles og anbefales aldri — begge var negative i både
  2023–24 og 2025–26.
- **Regelsjekk** (`REGEL_*`): en enkel, gjennomsiktig strategi ved siden av
  modellen (likvid, ingen advarsel, minst ett plusstegn), vist som
  «✓ Regelsjekk» på kandidatene.
- **Walk-forward:** hver dag trenes modellen bare på utfall som var kjent før
  den dagen. Den lærer av alle hendelser, ikke bare dem den handlet.
- **Papirhandel:** åpner når forventet verdi ≥ `HJERNE_MIN_EDGE` og sikkerhet ≥
  `HJERNE_MIN_SIKKERHET`; stopp = inngang − `HJERNE_STOPP_ATR` × ATR20;
  tidsutgang etter horisonten; maks `HJERNE_MAKS_APNE` åpne av `HJERNE_KAPITAL`.
- **Forslag:** hendelser uten inngangsdag ennå blir forslag med inngang (maks
  limit), stopp og utgangsdato, og varsles én gang.
- **Lærer den noe?** Dashboardets «Hjerne»-fane viser forventet mot faktisk
  utfall i fem grupper og rangkorrelasjonen. Den må bli tydelig positiv før
  forslagene er verdt noe.

Kjøres i «Kurser og rapport» (kveld) og «Poll nyheter». Alt kan overstyres
med repo-variabler med samme navn som i `src/config.py`.

## Varsler på telefonen

**Telegram** (anbefalt, enklest):
1. Telegram → @BotFather → `/newbot` → kopier tokenet.
2. Send en melding til den nye boten.
3. Secret `TELEGRAM_TOKEN` = tokenet. Kjør workflowen **Varslingstest** — den
   skriver ut chat-id-en din.
4. Secret `TELEGRAM_CHAT_ID` = chat-id-en. Kjør **Varslingstest** igjen; du
   skal få en testmelding.

**Web push via dashboard-appen** (valgfritt, i tillegg):
1. Lokalt: `python -m scripts.lag_vapid`. Secret `WEBPUSH_VAPID_PRIVATE` og
   variabel `WEBPUSH_VAPID_PUBLIC`. Variabel `WEBPUSH_KONTAKT` = `mailto:deg@…`,
   og `DASHBOARD_URL` = adressen til dashboardet.
2. Kjør «Dashboard (GitHub Pages)». Åpne dashboardet på telefonen. iPhone:
   Del → «Legg til på Hjem-skjerm» og åpne appen derfra (krever iOS 16.4+).
3. Trykk 🔔 Varsler → tillat → kopier teksten → secret `WEBPUSH_SUBSCRIPTIONS`.
   Flere enheter: `[ {...}, {...} ]`.
4. Kjør «Varslingstest».

Web push brukes bare for kjøpsforslag; Telegram får også feil og ukesrapport.

## Oppsett

```bash
git clone <ditt-repo> && cd momentum-varsling
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env          # fyll inn e-posten din i MOMENTUM_UA
python -m src.db              # oppretter data/momentum.db

python -m src.sources.mfn     # test at parseren ser feeden
python -m src.poll_news --dry-run
python -m src.poll_news       # ekte lagring

export ANTHROPIC_API_KEY=sk-ant-...
python -m src.klassifiser     # dedup -> forfilter -> Claude Haiku
```

### Tester

```bash
pip install -r requirements-dev.txt
pytest
```

Alle tester kjører mot lokale fixtures og mocket HTTP — ingen nettverkskall,
ingen ekte LLM-kall. De dekker blant annet: begge MFN-URL-formene og
HTML-entiteter i tittelen, at en ny polling av samme feed ikke gir
duplikater, dedup på tvers av språk/distributør, at hver regel i
forfilteret faktisk treffer, JSON-validering og retry i LLM-kallet, og —
viktigst — at ingen kursdata noensinne havner i det som sendes til
Anthropic (`tests/test_kjor.py::test_ingen_kursdata_havner_i_llm_kallet`).

På GitHub:

1. Push repoet (privat repo holder — 2000 gratis Actions-minutter i måneden).
2. Settings → Actions → General → Workflow permissions → **Read and write**.
   Uten dette får ikke jobben lov til å committe databasen tilbake.
3. Secrets: `MOMENTUM_UA` og `ANTHROPIC_API_KEY`. Legg også inn
   `TELEGRAM_TOKEN` + `TELEGRAM_CHAT_ID` hvis du vil ha varsel når en jobb
   feiler og ukesrapport lørdag — det er det eneste Telegram brukes til.
   Uten dem skrives meldingene til jobb-loggen i stedet.
4. Valgfri variabel (Settings → Actions → Variables): `KLASSIFISERING_MAKS_KALL`
   for å endre hvor mange LLM-kall `poll-news.yml` gjør per kjøring (default 200).
5. I Settings → Secrets and variables → Actions kan du også sette:
   - **Variable** `MFN_FEED_URL` = `https://mfn.se/all/s/nordic` (valgfritt —
     koden har samme verdi som innebygd standard).
   - **Secret** `MOMENTUM_UA` = `momentum-varsling/0.1 (personlig prosjekt; <e-post>)`.

   Koden kjører fint uten dem (`src.config.env()` faller tilbake på
   standardverdiene selv om Actions setter nøkkelen med tom streng når
   secreten/variabelen ikke finnes), men `MOMENTUM_UA` bør peke på en ekte
   kontaktadresse siden MFN ikke har noe offentlig API.
6. Kjør `Poll nyheter` manuelt én gang og sjekk at et data-commit dukker opp.

## Ticker-mapping

MFN identifiserer selskaper med sin egen slug, ikke ticker. `universe/tickers.csv`
kobler de to. Den er ferdig utfylt med ~20 Oslo-navn; resten fyller du på
etter hvert:

```bash
python -m scripts.map_missing -n 50 >> universe/tickers.csv
```

Scriptet sorterer etter hvor mange nyheter selskapet faktisk har hatt, så
innsatsen går til de som betyr noe. Mapper du de 100 mest aktive, dekker du
det meste. Yahoo-suffikser: `.OL` Oslo, `.ST` Stockholm, `.CO` København,
`.HE` Helsinki.

Nyheter fra umappede selskaper lagres uansett — de får ticker automatisk
neste gang universet lastes.

## Datamodell

Databasen (`data/momentum.db`) ligger **ikke i git**, men som komprimert
vedlegg på GitHub-releasen `data-siste` (`scripts/db_lagring.py`). Hver jobb
henter den først og laster den opp til slutt; `momentum-forrige.db.gz` er
versjonen før siste opplasting. Et vedlegg kan være opptil 2 GB og vokser
ikke i git-historikken. Lokalt: `python -m scripts.db_lagring hent`.

| Tabell | Innhold |
|---|---|
| `nyheter` | Én rad per pressemelding, én per språk/distributør. Endres aldri i ettertid (`brodtekst` er unntaket — den fylles inn når en hendelse skal klassifiseres). |
| `kurser` | Daglig OHLCV per Yahoo-symbol. |
| `selskaper` | Speiler `universe/tickers.csv`. |
| `umappede` | Slugs uten ticker, med frekvens. |
| `kjorelogg` | Siste 500 kjøringer, så du ser om cronen lever. |
| `hendelser` | Dedupliserte nyheter — én rad per faktisk hendelse, med kanonisk tittel. |
| `hendelse_nyheter` | Kobling nyhet → hendelse (én nyhet tilhører nøyaktig én hendelse). |
| `klassifiseringer` | Resultat per hendelse per `PROMPT_VERSJON` — regelbasert (`rutine`) eller Claude Haiku (`ok`/`feilet`). |
| `selskapshendelser` | Rapporter, tilbakekjøp og emisjoner per selskap fra MFN (bare dato og type). |
| `selskap_isin` | ISIN per selskap (fra MFN), til koblingen mot shortregisteret. |
| `short` | Netto shortandel per ISIN og endringsdag (Finanstilsynet, Oslo). |
| `selskapsinfo` | Sektor, bransje og antall aksjer fra Yahoo (oppdateres månedlig). |
| `arkiv_fremdrift` | Hvor langt `src.backfill_arkiv` er kommet per selskap, for gjenopptakelse. |

`nyheter.hentet` minus `nyheter.publisert` er den viktigste kolonnen i fase
0. Den måler hvor sent du faktisk får vite ting. Er medianen over ti
minutter, er ikke en post-nyhet-strategi realistisk med dette oppsettet.

## Klassifisering (fase 1)

Rekkefølgen på hver kjøring av `src.klassifiser` er:

1. **Dedup** (`src/klassifisering/dedup.py`) — MFN publiserer ofte samme sak
   flere ganger (ett språk, én distributør hver). Rader fra samme selskap
   innen 10 minutter med tittellikhet over terskelen (token-overlapp +
   `difflib.SequenceMatcher`, ikke LLM — deterministisk og reproduserbart)
   slås sammen til én hendelse. Norsk foretrekkes som kanonisk tittel,
   deretter engelsk.
2. **Regelbasert forfilter** (`src/klassifisering/regler.py` +
   `forfilter.py`) — åpenbar rutine (flagging, tilbakekjøpsrapportering,
   innkalling til generalforsamling, månedsstatistikk, MAR-transaksjoner,
   tredjeparts IR-analyse) merkes `rutine` uten noe LLM-kall. Reglene er
   ren data; legg til flere etter hvert som du ser rutine slippe gjennom.
3. **Claude Haiku** (`src/klassifisering/llm.py`) — kun for det som består
   forfilteret. Brødteksten hentes fra MFN først her, ikke for alle
   nyheter. Modellen ser **kun tittel, brødtekst og publiseringstidspunkt —
   aldri kursdata** (se `bygg_prompt()` — signaturen har rett og slett ikke
   plass til det, og `tests/test_kjor.py` bekrefter det ende-til-ende).
   Output valideres mot et fast sett kategorier
   (`src/klassifisering/kategorier.py`); ugyldig JSON gir ett nytt forsøk,
   deretter `klassifisering_feilet` — jobben stopper aldri av den grunn.

En hendelse klassifiseres **én gang per `PROMPT_VERSJON`**
(`UNIQUE(hendelse_id, prompt_versjon)`). Endrer du prompten eller
kategorilisten, bump `PROMPT_VERSJON` i `kategorier.py` — da klassifiseres
alt på nytt under den nye versjonen, og du kan sammenligne de to mot
hverandre i stedet for å miste historikken.

`KLASSIFISERING_MAKS_KALL` (default 200) er et sikkerhetsnett per kjøring:
resten av hendelsene tas opp igjen neste gang jobben kjører. Kostnaden er
alltid synlig i `kjorelogg` og i ukesrapporten (antall LLM-kall, estimerte
tokens, andel filtrert bort).

### Arkivimport

`src/backfill_arkiv.py` henter historikk fra `https://mfn.se/all/a/<slug>`
for hvert selskap i universet, kjører dedup og klassifisering på resultatet,
og lagres i den vanlige `nyheter`-tabellen. Kjøres manuelt via workflowen
**Arkivimport** (`workflow_dispatch`), ikke på cron:

```bash
python -m src.backfill_arkiv --ar-tilbake 4 --maks-kall-grense 200
```

Jobben er gjenopptakbar (`arkiv_fremdrift` holder styr på siste side per
selskap) og stopper seg selv etter `--maks-minutter` (default 45) slik at
GitHub Actions rekker å committe det som er hentet før tidsgrensen — kjør
den flere ganger til alle selskaper er `ferdig`.

MFN dokumenterer ikke sitt eget arkiv-/pagineringsformat. URL-malen i
`MFN_ARKIV_URL_MAL` (`src/config.py`) er et rimelig utgangspunkt basert på
at `/all/a/<slug>` er selskapets egen nyhetsside — sjekk mot ekte sider
først, og juster malen (og evt. sidetallsparameteren) om nødvendig.

### Kategorier

Listen i `src/klassifisering/kategorier.py` (`resultat`, `kontrakt_ordre`,
`oppkjop_fusjon`, `kapitalendring`, `ledelse_styre`, `guiding`,
`produkt_teknologi`, `juridisk_regulatorisk`, `rating_analyse`, `rutine`,
`annet`) er et førsteutkast for fase 1. Juster den etter hva som faktisk
dukker opp i dine egne data — men bump `PROMPT_VERSJON` når du gjør det.

## Faseplan

- **Fase 0** (uke 1–6) — innsamling.
- **Fase 1** — klassifisering. Regelbasert forfilter, deretter Claude Haiku
  på det som passerer. Faste kategorier du skriver selv. Du er her.
- **Fase 2** — backtest mot MFN-arkivet. Trekk fra 29 kr kurtasje hver vei og
  1 % slippage før du konkluderer.
- **Fase 3** — papirhandel i minst tre måneder, med varsler i samme format
  som senere blir ekte.
- **Fase 4** — vurdering. Krav for å gå live: 50–100 signaler, positiv
  forventningsverdi etter kostnader, og at du selv klarte å følge systemet.

Ikke hopp over en fase fordi de tre siste signalene traff.

## Merknader

- MFN har ikke et offentlig API. Parseren leser den server-rendrede feeden.
  Poll høflig, identifiser deg via `MOMENTUM_UA`, og les vilkårene deres.
- Parseren er posisjonsbasert, ikke klassebasert, så den tåler at MFN bytter
  CSS-navn. Endrer de rekkefølgen tidsstempel → selskap → tittel, brekker den.
  `src.report` vil da vise at antall nye nyheter faller til null.
- yfinance er forsinket og av og til upålitelig på småselskaper på
  Euronext Growth. For fase 0–3 er det godt nok.
