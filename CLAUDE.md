# Nyhetsbriefing — CLAUDE.md

## Hva prosjektet er

En selvstendig daglig briefing-app, hostet på VPS bak `*.modr.no`-proxyen, i to deler:

- **Generator** (Python) — kjører én gang i døgnet (cron 05:00) og skriver dagens briefing som JSON:
  - **`news_briefing.py`** — daglig nyhetsbriefing fra RSS-feeds (vær, marked, nyheter).
  - **`research_briefing.py`** — daglig forskningsbriefing: nye fagfellevurderte studier (trening/helse/medisin) fra Europe PMC, abstract-form, maks 5 per dag. Gjenbruker hjelpefunksjoner fra `news_briefing.py`.
- **Nettside** (`web/`, Astro SSR på Node) — leser JSON-filene og viser dagens briefing + arkiv på `nyheter.modr.no`.

Primær output er nå **nettsiden** (via et delt JSON-datalager), ikke Notion. Notion er valgfri/legacy.

> Drift, deploy og oppdateringsflyt: se **«Drift på VPS»** og **«Oppdateringsflyt»** nedenfor.
> Migreringshistorikk og full begrunnelse: `MIGRERINGSPLAN.md`.

## Kjøre scriptene

```bash
python news_briefing.py          # nyhetsbriefing til terminal
python news_briefing.py --save   # lagrer også briefing_YYYY-MM-DD.md

python research_briefing.py          # forskningsbriefing til terminal
python research_briefing.py --save   # lagrer også forskningsbrief_YYYY-MM-DD.md
```

Begge scriptene skriver **alltid** dagens briefing til datalageret via `store_briefing()`
(`<BRIEFING_DATA_DIR>/briefings/<dato>.json`, default `./briefings/` lokalt, `/data/briefings/` i container).
`--save` legger i tillegg til en markdown-backup. Se «Datalager — JSON-kontrakten» nedenfor.

På Windows: dobbeltklikk `run_briefing.bat` — kjører begge etter hverandre.

## Miljøvariabler

Kopier `.env.example` til `.env` og fyll inn verdiene. Scriptet leser `.env` automatisk.

| Variabel | Påkrevd | Beskrivelse |
|---|---|---|
| `ANTHROPIC_API_KEY` | Ja | Claude API-nøkkel |
| `NOTION_API_KEY` | Nei | Notion integration token |
| `NOTION_PARENT_PAGE_ID` | Nei | ID på Notion-siden å opprette undersider under |

## Viktige designvalg — ikke endre uten grunn

- **Modell:** `claude-sonnet-4-6` — ikke bytt til opus eller haiku. (Tidligere `claude-sonnet-4-20250514`, pensjonert 15. juni 2026.)
- **Streaming:** Claude-output streames direkte til terminal, ikke bufret.
- **Notion er valgfritt/legacy:** publiseres kun hvis begge Notion-variabler er satt; ellers myk feil. Primær output er JSON-datalageret (nettsiden). På VPS holdes `NOTION_*` tomme i `.env`.
- **RSS-feil er myke:** én feed som feiler stopper ikke resten av kjøringen.
- **Artikler uten dato:** inkluderes alltid (kan ikke fastslå alder).
- **`MAX_PER_FEED = 15`:** beskytter mot feeds med hundrevis av innlegg.

## RSS-henting — teknisk

Feeds hentes med `httpx` (browser-lignende User-Agent + `follow_redirects=True`) og parseres av `feedparser`. Dette er nødvendig fordi mange norske nyhetsnettsteder blokkerer `feedparser`s standard bot-identifikasjon. Ikke bytt tilbake til `feedparser.parse(url)`.

```python
resp = httpx.get(url, headers=_FETCH_HEADERS, timeout=10, follow_redirects=True)
resp.raise_for_status()
feed = feedparser.parse(resp.content)
```

`_FETCH_HEADERS` er definert som konstant rett under `MAX_DESC_CHARS`.

**Dedup før Claude:** `fetch_articles()` kjører `_dedup_articles()` til slutt som slår sammen
nær-identiske saker på tvers av feeds (normalisert tittel + URL; beholder varianten med
lengst ingress). Sparer Claude-input-tokens og fjerner støy uten kvalitetstap — feedene
overlapper mye (NRK toppsaker/siste, BBC/Guardian world+business, to ScienceDaily).
`MAX_DESC_CHARS = 300` begrenser ingresslengden per artikkel.

## Aktive RSS-feeds (19 stk)

| Kilde | Kategori |
|---|---|
| NRK Nyheter | `https://www.nrk.no/toppsaker.rss` |
| NRK Siste | `https://www.nrk.no/nyheter/siste.rss` |
| Bergens Tidende | `https://www.bt.no/rss.xml` |
| E24 | `https://e24.no/rss2/` |
| E24 Børs og finans | `https://e24.no/rss2/?seksjon=boers-og-finans` |
| The Guardian World | `https://www.theguardian.com/world/rss` |
| The Guardian Business | `https://www.theguardian.com/business/rss` |
| BBC World | `http://feeds.bbci.co.uk/news/world/rss.xml` |
| BBC Business | `http://feeds.bbci.co.uk/news/business/rss.xml` |
| Dagens Næringsliv | `https://services.dn.no/api/feed/rss/` |
| VentureBeat AI | `https://venturebeat.com/category/ai/feed/` |
| MIT Technology Review | `https://www.technologyreview.com/feed/` |
| ScienceDaily | `https://www.sciencedaily.com/rss/top/science.xml` |
| Nature News | `https://www.nature.com/nature.rss` |
| Phys.org | `https://phys.org/rss-feed/` |
| Titan (UiO) | `https://titan.uio.no/rss.xml` (Atom) |
| Aftenposten Viten | `https://www.aftenposten.no/rss/viten` |
| ScienceDaily Helse | `https://www.sciencedaily.com/rss/health_medicine.xml` |
| STAT News | `https://www.statnews.com/feed/` |

**Fjernede kilder og årsak:**
- Reuters: offentlige RSS-feeds stengt 2020.
- Finansavisen: tilbyr ikke offentlige RSS-feeds.
- Oslo Børs: ingen offentlig RSS (børsmeldinger er tilgjengelig via NewsWeb API, ikke RSS). Børsnyheter dekkes via E24 Børs og finans.
- forskning.no: ingen fungerende offentlig RSS-feed (Labrador CMS, `/rss` er en JS-rendret HTML-side uten autodiscovery-tag; alle vanlige feed-URLer gir 404). Norsk vitenskap dekkes via Titan (UiO) og Aftenposten Viten.

Ikke bruk `https://www.nrk.no/nyheter/rss.xml` (404), `https://e24.no/rss.xml` (404) eller `https://www.dn.no/rss.xml` (ugyldig) — disse er utdaterte URLer.

## Endre RSS-feeds

Rediger `RSS_FEEDS`-dict øverst i `news_briefing.py`. Format: `"Kildenavn": "https://..."`.
Bekreft alltid at en ny URL faktisk returnerer gyldig RSS (HTTP 200 + XML) før du legger den til.

## Briefing-seksjoner (7 stk)

`SYSTEM_PROMPT` styrer hva Claude skriver. Stil: Bloomberg-terminal — tall og fakta, ingen fyllord. Maks 450 ord totalt.

| Seksjon | Innhold | Maks punkter |
|---|---|---|
| 🏥 Helse og medisin | Legemiddelgodkjenninger, klinisk evidens, folkehelsevarsler | 3 |
| 🔬 Forskning og vitenskap | Vitenskapelige gjennombrudd, klima/energiforskning, ny teknologi | 3 |
| 🤖 AI, teknologi og startups | Nye AI-modeller, AI-regulering, Apple/Google/Meta, startups >100 MUSD | 3 (startups: 1) |
| 🌍 Internasjonalt | Geopolitikk, naturkatastrofer, G20-valg | 1 |
| 🇳🇴 Norsk økonomi | Norges Bank, oljesektor, kronekurs, norske børsselskaper | 3 |
| 📈 Marked og makro | Rentevedtak, inflasjon, handelskrig, kvartalstall, krypto | 3 (krypto: 1) |
| 🏙️ Bergen og Vestland | Kun direkte hverdagskonsekvens (kollektiv, vedtak, helse) | 3 |

Innenrikspolitikk uten markedseffekt og eiendomsmarkedet kuttes alltid.

## Værvarsling Bergen

`fetch_bergen_weather()` bruker MET Norway Locationforecast API (`complete`-endepunktet — ikke `compact`, da UV-data kun er tilgjengelig i `complete`).

Returnerer:
- `summary` — nåværende vær + eventuell ettermiddagsendring
- `rain_hours` — tidsspenn der nedbør >= 1 mm/t resten av i dag
- `sun_periods` — sammenhengende klarvær-perioder (dagstid kl. 05–21)
- `max_uv` — høyeste UV-indeks (clear-sky) i dag
- `max_uv_hour` — timen med maks UV
- `max_temp` — høyeste temperatur i dag
- `max_temp_hour` — timen med maks temp
- `temp_0700` — temperatur kl. 07:00
- `hourly` — timesserie for i dag (fra genereringstidspunktet og ut dagen) til værspilleren
  på nettsiden. Liste av `{ hour, temp, precip, uv, symbol }` der `symbol` er MET-symbolkoden
  (f.eks. `partlycloudy_day`). `precip`/`symbol` hentes fra `next_1_hours`, `temp`/`uv` fra
  `instant.details` — alt i samme dagsløkke som de øvrige feltene.

Feil i værhenting stopper ikke resten av kjøringen (myk feil).

## Markedssnapshot

`fetch_market_snapshot()` henter Brent, S&P 500, OBX-indeksen (`OBX.OL`), BTC (`BTC-USD`), ETH (`ETH-USD`) og **Nordnet Global** (`nordnet`-nøkkelen — MSCI World-proxy via `URTH`; bytt ticker i `yf.Tickers(...)` + terminal/Notion-utskriften for en annen global indeks) via `yfinance`.
Dataene vises i terminal og Notion men sendes **ikke** til Claude — Claude skal forklare *hvorfor* markedet beveget seg, ikke gjenta prisene.
Feil i markedsdata stopper ikke resten av kjøringen (myk feil).

## Notion-struktur

Siden bygges opp i denne rekkefølgen:
1. Værseksjon (heading_1 + værsummary, klarvær-perioder, nedbørstimer, UV/temp-stats)
2. Markedssnapshot (Brent, S&P 500, OBX, EUR/NOK, USD/NOK)
3. Nyhetsinnhold fra Claude (7 seksjoner)

Notion godtar maks 100 blokker per API-kall — lange briefinger splittes automatisk.

## Forskningsbriefing (`research_briefing.py`)

Egen daglig briefing kun om ny forskning. Henter kandidatstudier fra **Europe PMC** (ett REST-kall, ingen API-nøkkel), lar Claude velge de mest relevante og skrive abstract-form på norsk.

**Kilde:** Europe PMC `search`-endepunkt (`https://www.ebi.ac.uk/europepmc/webservices/rest/search`), `resultType=core` (gir fulle abstracts), `SRC:MED` = kun fagfellevurdert (MEDLINE/PubMed), ikke preprints. Dekker både PubMed og preprints hvis man bytter `SRC`-filter — vi bruker kun MEDLINE.

**Konfig** (konstanter øverst i fila — speiler `RSS_FEEDS`-mønsteret):
- `MODEL` / `MAX_TOKENS` — som nyhetsbriefen (`claude-sonnet-4-6`).
- `LOOKBACK_DAYS = 2` — datovindu på publiseringsdato (toleranse for indekseringsforsinkelse).
- `MAX_ITEMS = 5` — maks studier (styres også i `SYSTEM_PROMPT`).
- `CANDIDATE_POOL = 25` — antall ferske studier som hentes og sendes til Claude for kurering
  (Claude velger uansett kun 5; 25 er rikelig og holder input-tokens nede).
- `EUROPE_PMC_QUERY` — bred, redigerbar emnespørring (trening + helse + klinisk medisin). Endre denne for å justere tema.

**Format per studie:** `## [tittel](url)` + **Hva som ble gjort** / **Resultat** / **Relevans**. Claude velger opptil 5; heller færre enn svake.

**Dedup:** `research_seen_dois.json` holder DOI-er for studier som allerede er dekket, og pruner etter `SEEN_RETENTION_DAYS = 14`. Hindrer at samme studie gjentas dag etter dag. Kun studier Claude faktisk valgte (URL dukker opp i output) markeres som sett. Lagres i `BRIEFING_DATA_DIR` (på volumet i container — MÅ persisteres, ellers nullstilles dedup hver kjøring), faller tilbake til scriptmappa lokalt.

**Notion:** egen seksjon adskilt fra nyhetsbriefen — undersiden **«Forskning Arkiv»** og ankeret **«Forskningsbriefinger»** på samme `NOTION_PARENT_PAGE_ID`. Gjenbruker `markdown_to_notion_blocks`, `_get_or_create_archive` og `_get_or_create_anchor` (sistnevnte to tar nå valgfri `title`/`anchor_text`).

**Lagring:** `--save` skriver `forskningsbrief_YYYY-MM-DD.md`.

Feil i henting er myk (tom liste → avslutter uten å krasje bat-fila).

## Datalager — JSON-kontrakten (kilde for nettsiden)

`store_briefing()` (i `news_briefing.py`, importert av `research_briefing.py`) skriver/merger
dagens briefing til `<BRIEFING_DATA_DIR>/briefings/<dato>.json`. Begge scriptene skriver inn i
**samme dagsfil** — kun feltene den enkelte kjøringen produserte oppdateres. Skrivingen er
**atomisk** (skriv `.tmp`, så `os.replace`) slik at nettsiden aldri leser en halvskrevet fil.

Skjema:

```json
{
  "date": "2026-06-28",
  "created_at": "ISO-tidsstempel",
  "news_md": "nyhetsbriefing (markdown)",
  "research_md": "forskningsbriefing (markdown)",
  "weather": { ... },          // fetch_bergen_weather()-dict
  "market": { ... },           // fetch_market_snapshot()-dict
  "research_items": [ { "title", "url", "journal", "date" } ]
}
```

`weather`/`market` lagres strukturert hver dag → kan bygge figurer (markedstrend over tid osv.)
uten ekstra datainnhenting.

## Nettsiden (`web/` — Astro)

- **Astro 5 i SSR-modus** (`output: 'server'`, `@astrojs/node` standalone). Leser JSON ved hver
  forespørsel → nytt innhold vises **uten** bygge-steg. Designendringer krever rebuild av web-imaget.
- Ruter: `/` (nyeste), `/arkiv` (liste), `/b/<dato>` (én dag).
- **Design:** «moderne dashboard» — kort-basert, sans-serif.
  Stiler i `src/styles/global.css` (design-tokens som CSS-variabler, importeres i `Base.astro`).
- **Værspiller:** `WeatherCard.astro` viser `WeatherPlayer.astro` når `weather.hourly` finnes —
  en time-for-time «video» av dagens vær (ikon, temp, status, nedbør, UV) med slider og
  play/pause, og en statisk dagssammendrag-rad under slideren (min/maks temp, maks UV, total
  nedbør kl. 06–21, beregnet fra `hourly`). **Autospiller som default**; stopper når brukeren tar på slideren/knappen
  (respekterer `prefers-reduced-motion`). Symbol→ikon/etikett-map speiler `_SYMBOL_NO` i
  generatoren. Klientlogikken sendes inn med `define:vars={{ frames }}` (inline, ingen bundling).
  Faller tilbake til den statiske stat-griden for gamle briefinger uten `hourly`.
- **Markedswidget + dagsgrafer:** `MarketStrip.astro` viser Brent, S&P 500, OBX, BTC, ETH,
  Nordnet Global med dagsendring og en kompakt **mini-dagsgraf** per ticker (`MarketTrend.astro`
  — inline SVG, ingen klient-JS; 3–5 punkter, ett per dag, med verdien skrevet over hvert punkt
  og dag-i-måneden under, fargelagt av trenden, siste punkt fremhevet). Serien bygges av
  `getMarketHistory()` i `briefings.js`, som leser `market` fra de siste briefingene (default
  **5 dager**, `[{ date, value }]` per ticker; `endDate` avgrenser til t.o.m. dagen som vises).
  `MARKET_KEYS` styrer rekkefølgen.
- **Temaer:** 5 fargetemaer (Lys, Sepia, Skumring, Mørk, Midnatt) valgt via `[data-theme]` på
  `<html>`. Velges med `ThemePicker.astro`-knappen i headeren; valget lagres i `localStorage`
  (nøkkel `theme`) og settes **før paint** av et `is:inline`-skript i `<head>` (unngår blink;
  faller tilbake til `prefers-color-scheme` ved første besøk). **Legge til et tema = (1) ny
  `[data-theme="<id>"]`-blokk i `global.css`, (2) én linje i `src/lib/themes.js`** — knappen/menyen
  bygges fra registeret, så ingen annen kode trenger endres.
  Komponenter: `WeatherCard.astro` (vær-widget fra `weather`-objektet), `MarketStrip.astro`
  (marked fra `market`-objektet, opp/ned-farger), `BriefingView.astro` (deler topp-grid +
  nyhetskort + forskningskort mellom forside og enkeltdag).
- `src/lib/briefings.js`:
  - `listDates()`, `getBriefing(date)`, `renderMarkdown()` (marked; `•` → `- `).
  - `splitNewsSections(news_md)` → `[{ emoji, title, html }]` (ett kort per «## »-seksjon;
    håndterer flagg-emoji som 🇳🇴 via `Regional_Indicator`).
  - `splitResearch(research_md)` → `[{ title, url, parts, html }]` (ett kort per studie).
    `parts` er de merkede avsnittene `[{ label, html }]` (Hva som ble gjort / Resultat /
    Relevans) som vises som separate, ikon-merkede blokker — Relevans fremhevet som konklusjon.
    `html` er fallback hvis et abstract ikke har merkede deler.
  - `formatDateNo()` / `weekdayNo()` — norsk dato (lokaltid-trygg, ingen UTC-skift).
- **`BRIEFING_DIR`:** prod (Dockerfile) setter `/data/briefings` eksplisitt. Uten env-var
  (lokal dev) faller `briefings.js` tilbake til **repo-lokal `briefings/`** — samme mappe
  generatoren skriver til lokalt (`BRIEFING_DATA_DIR=.`). `briefings/` er git-/docker-ignorert.
- **Lokal dev:** `cd web && npm run dev` → `http://localhost:4321`. Trenger JSON i repo-lokal
  `briefings/` (kjør generatoren lokalt, eller bruk eksisterende filer der). HMR oppdaterer live.
- Lytter på `0.0.0.0:8080` i container (`HOST`/`PORT` env). Bind til `0.0.0.0` er et plattformkrav.

## Drift på VPS

- **Vert:** VPS-en `MODR`, kjører som **root**, repo klonet til `/root/nyheter-app`.
  Repo: `git@github.com:OleDrange/nyheter-app.git`, **default-branch er `master`** (ikke `main`).
  **Verts-TZ er `Europe/Oslo`** (`timedatectl`) — kritisk for når cron fyrer, se «Tidsplan».
- **To tjenester** (`docker-compose.yml`):
  - `web` — alltid oppe (`docker compose up -d web`), `restart: unless-stopped`, på det eksterne
    `web`-nettet med alias **`nyheter-web`**, intern port **8080**. Monterer `briefing-data:/data:ro`.
  - `generator` — batch, `profiles: ["batch"]` (startes IKKE av `up -d`), kjøres av cron med
    `docker compose run --rm generator`. Monterer `briefing-data:/data` (rw), `env_file: .env`.
    Dockerfile bruker **CMD** (ikke ENTRYPOINT), se fallgruver.
- **Tidsplan:** root sin crontab (host-TZ må være `Europe/Oslo`, se under):
  ```cron
  0 5 * * * cd /root/nyheter-app && /usr/bin/docker compose run --rm generator >> /root/nyheter-cron.log 2>&1
  ```
  Tidssonen styres av **verten**, satt med `timedatectl set-timezone Europe/Oslo` (+ `systemctl
  restart cron` — cron cacher TZ ved oppstart). Bruk **ikke** `CRON_TZ=Europe/Oslo` i crontab:
  Debians standard-`cron` respekterer den ikke (det er en cronie-utvidelse), så `0 5` tolkes alltid
  i systemets TZ. Med host-TZ = Oslo betyr `0 5` = 05:00 Oslo, og DST håndteres av OS-et.
  Sjekk med `date` (skal vise `CEST`/`CET`) og `crontab -l` (skal *ikke* inneholde `CRON_TZ`).
- **Proxy:** `~/modr-proxy` (Caddy). Primærdomenet er **`nyheter.modr.no`**; både
  `nyheter.modr.online` (gammelt domene) og `n.modr.no` (kort alias) 301-redirecter dit.
  Blokk i `Caddyfile`:
  `nyheter.modr.no { encode gzip; reverse_proxy nyheter-web:8080 }` + to `redir`-blokker.
  Caddy har **`admin off`** i den globale blokka, så admin-API-reload (`caddy reload … :2019`)
  virker ikke — last inn med `docker compose restart caddy`. Validér først med
  `docker compose exec -T caddy caddy validate --config /etc/caddy/Caddyfile`.
  TLS-certifikatene utstedes automatisk av Let's Encrypt; alle tre vertsnavn må ha DNS-A/AAAA
  mot VPS-en (allerede satt opp, samme IP som `dashboard.modr.no`).
- **Inspisere data uten å kjøre generatoren på nytt** (web monterer samme volum):
  `docker compose exec web ls -la /data/briefings` / `... cat /data/briefings/<dato>.json`.
- **Backup** av hele arkivet (bor kun i volumet):
  `docker run --rm -v nyheter-app_briefing-data:/d -v /root:/b alpine tar czf /b/nyheter-backup.tgz -C /d .`
- **Logger:** generator → `/root/nyheter-cron.log` (opprettes først ved første cron-kjøring);
  web → `docker compose logs -f web`.
- **Feilvarsling (mot stille feil):** `healthcheck.py` kjøres sist i `docker-entrypoint.sh` og
  verifiserer at dagens `briefings/<dato>.json` faktisk ble skrevet med `news_md`. Manglende/tom
  fil → POST til `ALERT_WEBHOOK_URL` (Slack/Discord-format). Ved suksess pinges `HEARTBEAT_URL`
  — pek den mot en ekstern dead-man's-switch (healthchecks.io o.l.) som varsler hvis pinget
  uteblir, slik at du også fanger at cron aldri kjørte. Begge env-vars er valgfrie (i `.env`,
  se `notify.py`); uten dem er varsling av.

## Oppdateringsflyt

**Utviklingsflyt (standard):** Utvikling og ny implementasjon skjer **direkte i dette repoet
på serveren** (`/root/nyheter-app` på VPS-en). Etter hver endring skal Claude selv:

1. Skrive/redigere koden i repoet her.
2. Committe og pushe til `master` (`git add -A && git commit -m "..." && git push`).
3. Oppdatere Docker slik at endringen er live:
   `docker compose build web generator && docker compose up -d web`.

Slik kan endringer testes **rett på nettsiden** (`https://nyheter.modr.no`) — det er der
brukeren verifiserer, ikke i en lokal dev-server. Ikke stopp etter kodeendringen; flyten er
ikke ferdig før nettsiden kjører den nye koden.

Data og presentasjon er frikoblet — endre det ene uten å røre det andre.

```bash
# på serveren: rediger → commit → push → deploy
git add -A && git commit -m "..." && git push          # til master
docker compose build web generator && docker compose up -d web
```

| Endring | Rask sjekk før deploy (valgfritt) | Effekt live |
|---|---|---|
| Generator (`*.py`) | `python news_briefing.py` | **Krever rebuild først:** `docker compose build generator` — bart `docker compose build` hopper den over (pga. `profiles: batch`), så `run`/cron kjører ellers gammel kode. Web røres ikke. |
| Nettside (`web/`) | `cd web && npm run dev` | Etter `build web` + `up -d web`: ny design på all historikk på `nyheter.modr.no` umiddelbart (SSR re-rendrer eksisterende JSON). |

Rollback: `git revert <commit> && git push`, så `git pull && docker compose build && docker compose up -d web`.

## Fallgruver (les før du endrer deploy)

- **`master`, ikke `main`** — repoets default-branch.
- **`git pull` rebuild-er ikke imaget — og `docker compose build` bygger ikke generatoren.**
  Imagene brukes slik de sist ble *bygd*, ikke fra koden i arbeidstreet. Verre: generatoren ligger
  bak `profiles: ["batch"]`, så et bart `docker compose build` hopper den **stille** over (bygger
  bare `web`). Etter `git pull` må generatoren bygges eksplisitt: `docker compose build web generator`
  (eller engangs `docker compose run --rm --build generator`). Ellers kjører cron gammel kode.
  Klassisk symptom: vær uten `hourly`-feltet → nettsiden faller tilbake til statisk stat-grid uten
  time-for-time-spiller.
- **Generator bruker CMD, ikke ENTRYPOINT.** `docker compose run --rm generator <cmd>` overstyrer
  jobben. Ikke kjør `docker compose run --rm generator ls/cat …` med ENTRYPOINT-tankegang — bruk
  `docker compose exec web …` for inspeksjon, ellers risikerer du å kjøre hele briefingen (og bruke Claude-kvote).
- **Tidssone (to nivåer):** (1) *Innholdet* — scriptene bruker naiv `datetime.now()`/`.astimezone()`,
  så `TZ=Europe/Oslo` settes i containeren (Dockerfile + compose) for korrekt dato/værvinduer.
  (2) *Når cron fyrer* — styres av **verts**-TZ, ikke containeren. Sett den med
  `timedatectl set-timezone Europe/Oslo` + `systemctl restart cron`. **`CRON_TZ` i crontab virker
  ikke** på Debians `cron` — gjorde man det, ble `0 5` tolket som 05:00 UTC = 07:00 Oslo (akkurat
  den feilen som forsinket briefingen til den ble oppdaget 29. juni 2026). Host-TZ = Oslo er fasit.
- **Persistente data:** `briefings/<dato>.json` og `research_seen_dois.json` MÅ ligge på volumet
  (`BRIEFING_DATA_DIR=/data`). Uten det: tomt arkiv + dedup som nullstilles.
- **Backfill av gammel historikk:** `briefing_*.md`/`forskningsbrief_*.md` er gitignored og
  `.dockerignore`-et, så de finnes verken i repoet eller imaget — kun på den opprinnelige
  Windows-maskinen. `import_history.py` i container finner derfor ingenting; backfill må kjøres
  lokalt og JSON-filene overføres til volumet.
- **`docker-entrypoint.sh` må ha LF**-linjeskift (sikret av `.gitattributes`), ellers feiler den i Linux.
- **Notion-støy:** en ugyldig (men ikke-tom) `NOTION_API_KEY` i `.env` gir en rød «API token is
  invalid»-linje hver morgen (myk feil). Hold `NOTION_*` tomme på VPS, eller fjern Notion-koden.
- **Caddy reload** treffer admin-API på `[::1]:2019` (IPv6) som standard og feiler — bruk
  `--address 127.0.0.1:2019`, eller `docker compose restart caddy`.

## Avhengigheter

```bash
pip install -r requirements.txt    # generator: httpx, feedparser, anthropic, notion-client, yfinance
cd web && npm install              # nettside: astro, @astrojs/node, marked
```

Generator krever Python 3.10+ (image bruker 3.12). Nettsiden krever Node 20+ (image bruker 22).
