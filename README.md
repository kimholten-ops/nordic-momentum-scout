# momentum-varsling

Nyhetsdrevet varslingssystem for nordisk børs. **Ikke** en autonom tradingbot:
systemet foreslår, du bestemmer.

Kjører gratis på GitHub Actions med SQLite som database. Ingen server, ingen
abonnement, ingen Vercel eller Supabase.

## Status: fase 0 — kun innsamling

Dette repoet gjør i dag én ting: henter pressemeldinger fra MFN hvert 10.
minutt i børstid og lagrer dem sammen med daglige kurser. Ingen analyse,
ingen signaler, ingen varsler. Det er meningen. Uten fire til seks uker med
egne data har du ingenting å teste ideen mot.

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
```

### Tester

```bash
pip install -r requirements-dev.txt
pytest
```

Testene kjører kun mot en lokal HTML-fixture (`tests/fixtures/mfn_feed.html`)
og gjør ingen nettverkskall. De dekker begge URL-formene, HTML-entiteter i
tittelen, at vedleggslenker uten tekst hoppes over, og at en ny polling av
samme feed ikke gir duplikater.

På GitHub:

1. Push repoet (privat repo holder — 2000 gratis Actions-minutter i måneden,
   og dette bruker rundt 400).
2. Settings → Actions → General → Workflow permissions → **Read and write**.
   Uten dette får ikke jobben lov til å committe databasen tilbake.
3. Secrets: `MOMENTUM_UA`. Legg også inn `TELEGRAM_TOKEN` + `TELEGRAM_CHAT_ID`
   hvis du vil ha varsel når en jobb feiler og ukesrapport lørdag — det er det
   eneste Telegram brukes til i fase 0. Uten dem skrives meldingene til
   jobb-loggen i stedet.
4. Kjør `Poll nyheter` manuelt én gang og sjekk at et data-commit dukker opp.

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

| Tabell | Innhold |
|---|---|
| `nyheter` | Én rad per pressemelding. Endres aldri i ettertid. |
| `kurser` | Daglig OHLCV per Yahoo-symbol. |
| `selskaper` | Speiler `universe/tickers.csv`. |
| `umappede` | Slugs uten ticker, med frekvens. |
| `kjorelogg` | Siste 500 kjøringer, så du ser om cronen lever. |

`nyheter.hentet` minus `nyheter.publisert` er den viktigste kolonnen i hele
repoet. Den måler hvor sent du faktisk får vite ting. Er medianen over ti
minutter, er ikke en post-nyhet-strategi realistisk med dette oppsettet.

## Faseplan

- **Fase 0** (uke 1–6) — innsamling. Du er her.
- **Fase 1** — klassifisering. Regelbasert forfilter, deretter Claude Haiku
  på det som passerer. Faste kategorier du skriver selv.
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
