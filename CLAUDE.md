# Nyhetsbriefing — CLAUDE.md

## Hva appen er

Daglig briefing-app, live på **https://nyheter.modr.no**. Kjører på VPS-en `MODR` (dette
repoet: `/root/nyheter-app`, remote `git@github.com:OleDrange/nyheter-app.git`, default-branch
**`master`**). To deler, frikoblet via et JSON-datalager på et delt Docker-volum:

- **Generator** (Python, cron 05:00 hver dag):
  - `news_briefing.py` — nyhetsbriefing fra RSS + Bergen-vær + markedssnapshot.
  - `research_briefing.py` — maks 5 nye fagfellevurderte studier fra Europe PMC.
- **Nettside** (`web/`, Astro 5 SSR på Node) — leser JSON ved hver forespørsel og viser
  dagens briefing + arkiv. Nytt *innhold* vises uten rebuild; *kodeendringer* krever rebuild.

## Utviklingsflyt (standard)

Utvikling skjer **direkte i dette repoet på serveren**. Etter hver endring skal Claude selv:

1. Skrive/redigere koden her.
2. Committe og pushe til `master`.
3. Deploye: `docker compose build web generator && docker compose up -d web`.

Testing skjer **live på https://nyheter.modr.no** — flyten er ikke ferdig før nettsiden
kjører den nye koden.

| Endring | Deploy | Merk |
|---|---|---|
| `web/` | `docker compose build web && docker compose up -d web` | Ny design gjelder hele arkivet umiddelbart (SSR re-rendrer eksisterende JSON). |
| `*.py` | `docker compose build generator` | **Må bygges eksplisitt** — bart `docker compose build` hopper den stille over (`profiles: batch`). Neste cron-kjøring bruker ny kode. Manuell testkjøring: `docker compose run --rm generator` (bruker Claude-kvote). |
| Dokumentasjon | kun commit + push | — |

Rollback: `git revert <commit> && git push`, deretter rebuild + `up -d web`.

## Drift (VPS)

- **`docker-compose.yml`, to tjenester:**
  - `web` — alltid oppe (`restart: unless-stopped`), på eksternt `web`-nett med alias
    `nyheter-web`, intern port **8080** (`HOST=0.0.0.0` er plattformkrav). Monterer
    `briefing-data:/data:ro`.
  - `generator` — batch (`profiles: ["batch"]`, startes ikke av `up -d`). Kjøres av cron,
    monterer `briefing-data:/data` (rw), `env_file: .env`. Dockerfile bruker **CMD**, ikke
    ENTRYPOINT — `docker compose run generator <cmd>` overstyrer hele jobben. Bruk
    `docker compose exec web …` for inspeksjon av data, aldri `run generator`.
- **Cron** (root sin crontab):
  ```cron
  0 5 * * * cd /root/nyheter-app && /usr/bin/docker compose run --rm generator >> /root/nyheter-cron.log 2>&1
  ```
  Tidspunktet styres av **verts**-TZ (`Europe/Oslo` via `timedatectl`; `systemctl restart cron`
  etter endring). **`CRON_TZ` virker ikke** på Debians cron — ikke legg den i crontab.
  Container-TZ (`TZ=Europe/Oslo` i Dockerfile + compose) styrer innholdets dato/værvinduer.
- **Proxy:** Caddy i `~/modr-proxy`. `nyheter.modr.no { encode gzip; reverse_proxy nyheter-web:8080 }`;
  `nyheter.modr.online` og `n.modr.no` 301-redirecter dit. Caddy har `admin off` → reload med
  `docker compose restart caddy` (validér først:
  `docker compose exec -T caddy caddy validate --config /etc/caddy/Caddyfile`).
- **Inspisere data:** `docker compose exec web ls -la /data/briefings` /
  `… cat /data/briefings/<dato>.json`.
- **Logger:** generator → `/root/nyheter-cron.log`; web → `docker compose logs -f web`.
- **Backup** (arkivet bor kun i volumet):
  `docker run --rm -v nyheter-app_briefing-data:/d -v /root:/b alpine tar czf /b/nyheter-backup.tgz -C /d .`
- **Feilvarsling:** `healthcheck.py` (sist i `docker-entrypoint.sh`) sjekker at dagens JSON
  har `news_md`. Feil → POST til `ALERT_WEBHOOK_URL`; suksess → ping `HEARTBEAT_URL`
  (dead-man's-switch som fanger at cron aldri kjørte). Begge valgfrie (se `notify.py`).

## Miljøvariabler (`.env`)

- `ANTHROPIC_API_KEY` — påkrevd.
- `ALERT_WEBHOOK_URL`, `HEARTBEAT_URL` — valgfrie (varsling).
- `NOTION_API_KEY`, `NOTION_PARENT_PAGE_ID` — **legacy**, holdes tomme på VPS. Notion-publisering
  skjer kun hvis begge er satt (myk feil ellers); en ugyldig ikke-tom nøkkel gir en rød
  støylinje hver kjøring.

## Generator

Kjøring: `python news_briefing.py` / `python research_briefing.py` (+ `--save` for
markdown-backup). Begge skriver **alltid** dagens briefing til datalageret via
`store_briefing()`.

### Designvalg — ikke endre uten grunn

- **Modell:** `claude-sonnet-4-6` (begge scriptene). Ikke bytt til opus/haiku.
- **Streaming:** Claude-output streames til terminal, ikke bufret.
- **Myke feil:** én RSS-feed, vær- eller markedsfeil stopper ikke resten av kjøringen.
- **Artikler uten dato inkluderes alltid** (kan ikke fastslå alder).
- `MAX_PER_FEED = 15`, `MAX_DESC_CHARS = 300`.
- **RSS hentes med `httpx`** (browser-UA i `_FETCH_HEADERS` + `follow_redirects=True`), så
  `feedparser.parse(resp.content)`. Mange norske aviser blokkerer feedparsers bot-UA — ikke
  bytt tilbake til `feedparser.parse(url)`.
- **Dedup før Claude:** `fetch_articles()` avslutter med `_dedup_articles()` (normalisert
  tittel + URL; beholder lengst ingress) — feedene overlapper mye.

### RSS-feeds

19 feeds i `RSS_FEEDS`-dict øverst i `news_briefing.py` (`"Kildenavn": "https://..."`).
Bekreft at ny URL gir HTTP 200 + gyldig XML før du legger den til.

Ikke prøv disse igjen: Reuters (RSS stengt), Finansavisen (ingen RSS), Oslo Børs (kun
NewsWeb API; dekkes via E24 Børs), forskning.no (JS-rendret, ingen feed), samt de utdaterte
URLene `nrk.no/nyheter/rss.xml`, `e24.no/rss.xml`, `dn.no/rss.xml`.

### Briefing-seksjoner

`SYSTEM_PROMPT` styrer output: Bloomberg-stil (tall og fakta, ingen fyllord), maks 450 ord,
7 «## »-seksjoner (emojiene brukes av nettsidens parsing):

| Seksjon | Maks punkter |
|---|---|
| 🏥 Helse og medisin | 3 |
| 🔬 Forskning og vitenskap | 3 |
| 🤖 AI, teknologi og startups | 3 (startups: 1) |
| 🌍 Internasjonalt | 1 |
| 🇳🇴 Norsk økonomi | 3 |
| 📈 Marked og makro | 3 (krypto: 1) |
| 🏙️ Bergen og Vestland | 3 (kun direkte hverdagskonsekvens) |

Innenrikspolitikk uten markedseffekt og eiendomsmarkedet kuttes alltid.

### Vær (Bergen)

`fetch_bergen_weather()` → MET Locationforecast, **`complete`-endepunktet** (UV finnes ikke i
`compact`). Returnerer `summary`, `rain_hours`, `sun_periods` (kl. 05–21), `max_uv`/`max_uv_hour`,
`max_temp`/`max_temp_hour`, `temp_0700` og `hourly` — timesserie
`[{ hour, temp, precip, uv, symbol }]` til værspilleren (`symbol` er MET-symbolkode;
`precip`/`symbol` fra `next_1_hours`, `temp`/`uv` fra `instant.details`).

### Marked

`fetch_market_snapshot()` via `yfinance`: Brent, S&P 500, OBX (`OBX.OL`), BTC (`BTC-USD`),
ETH (`ETH-USD`) og Nordnet Global (nøkkel `nordnet`, MSCI World-proxy via `URTH`).
Dataene sendes **ikke** til Claude — Claude forklarer *hvorfor* markedet beveget seg.

### Dagens quiz (OpenTDB)

`fetch_daily_quiz()` i `news_briefing.py` henter 5 flervalgsspørsmål fra **Open Trivia
Database** (`opentdb.com/api.php` — gratis, ingen nøkkel, **engelsk**; ingen Claude-bruk).
Nivåstige `_QUIZ_LADDER`: 1×easy + 2×medium + 2×hard → nivå 1–5. Rate limit 1 kall/5 s
(`_QUIZ_RATE_LIMIT_S`). Dedup mot `quiz_seen.json` i `BRIEFING_DATA_DIR` (normalisert
spørsmålstekst, prunes etter `_QUIZ_SEEN_RETENTION_DAYS = 365`) — **må persisteres**
(volumet). Myk feil → tom liste, `quiz`-feltet utelates den dagen.

### Forskningsbriefing (`research_briefing.py`)

- **Kilde:** Europe PMC `search`-REST (ingen nøkkel), `resultType=core` (fulle abstracts),
  `SRC:MED` = kun fagfellevurdert.
- **Konstanter øverst i fila:** `LOOKBACK_DAYS = 2`, `MAX_ITEMS = 5`, `CANDIDATE_POOL = 25`,
  `EUROPE_PMC_QUERY` (endre denne for å justere tema).
- **Format per studie:** `## [tittel](url)` + **Hva som ble gjort** / **Resultat** / **Relevans**
  (nettsiden parser disse etikettene). Heller færre enn svake.
- **Dedup:** `research_seen_dois.json` (DOI-er, prunes etter `SEEN_RETENTION_DAYS = 14`).
  Kun studier Claude faktisk valgte markeres som sett. Ligger i `BRIEFING_DATA_DIR` —
  **må persisteres** (volumet), ellers nullstilles dedup.
- Gjenbruker hjelpefunksjoner fra `news_briefing.py` (bl.a. `store_briefing`).

## Datalager — JSON-kontrakten

`store_briefing()` (i `news_briefing.py`) skriver/merger til
`<BRIEFING_DATA_DIR>/briefings/<dato>.json` — begge scriptene skriver inn i **samme dagsfil**,
kun egne felter oppdateres. Skrivingen er **atomisk** (`.tmp` + `os.replace`).
`BRIEFING_DATA_DIR=/data` i container; default `.` lokalt (→ repo-lokal `briefings/`, gitignored).

```json
{
  "date": "2026-06-28",
  "created_at": "ISO-tidsstempel",
  "news_md": "nyhetsbriefing (markdown)",
  "research_md": "forskningsbriefing (markdown)",
  "weather": { ... },          // fetch_bergen_weather()-dict
  "market": { ... },           // fetch_market_snapshot()-dict
  "research_items": [ { "title", "url", "journal", "date" } ],
  "quiz": [ { "level", "difficulty", "category", "question", "options", "answer" } ]
}
```

`weather`/`market` lagres strukturert hver dag → historiske figurer (f.eks. markedsgrafene)
bygges uten ekstra datainnhenting.

## Nettsiden (`web/`)

- Astro 5, `output: 'server'`, `@astrojs/node` standalone. Lytter på `0.0.0.0:8080` i
  container (`HOST`/`PORT` env). `BRIEFING_DIR=/data/briefings` i prod-Dockerfile; uten
  env-var faller `briefings.js` tilbake til repo-lokal `briefings/`.
- **Ruter:** `/` (nyeste), `/arkiv` (liste), `/b/<dato>` (én dag).
- **Komponenter:**
  - `BriefingView.astro` — deler topp-grid + nyhetskort + forskningskort mellom forside og enkeltdag.
  - `WeatherCard.astro` — vær-widget; viser `WeatherPlayer.astro` når `weather.hourly` finnes,
    ellers statisk stat-grid (gamle briefinger).
  - `WeatherPlayer.astro` — time-for-time «video» (ikon/temp/status/nedbør/UV) med slider og
    autoplay (stopper ved brukerinput, respekterer `prefers-reduced-motion`) + dagssammendrag-rad.
    Symbol→ikon-map speiler `_SYMBOL_NO` i generatoren. Klientlogikk via `define:vars={{ frames }}`
    (inline, ingen bundling).
  - `MarketStrip.astro` — markedswidget med dagsendring + mini-dagsgraf per ticker
    (`MarketTrend.astro` — inline SVG, ingen klient-JS). Serien fra `getMarketHistory()`
    (default 5 dager, `endDate` avgrenser til dagen som vises). `MARKET_KEYS` styrer rekkefølgen.
  - `QuizCard.astro` — «Dagens hjernetrim»: 5 flervalgsspørsmål fra `quiz`-feltet
    (vises kun når feltet finnes). Fasit skjult til bruker trykker et alternativ —
    riktig grønt (`--up`), feil rødt (`--down`), score-linje når alle 5 er besvart.
    Inline-script, ingen bundling.
  - `ThemePicker.astro` — temavelger i headeren.
- **`src/lib/briefings.js`:** `listDates()`, `getBriefing(date)`, `renderMarkdown()` (marked),
  `getMarketHistory()`, `splitNewsSections(news_md)` → `[{ emoji, title, html }]` (per
  «## »-seksjon; håndterer flagg-emoji som 🇳🇴), `splitResearch(research_md)` →
  `[{ title, url, parts, html }]` (`parts` = de merkede avsnittene Hva som ble gjort/Resultat/
  Relevans; `html` er fallback), `formatDateNo()`/`weekdayNo()` (lokaltid-trygg norsk dato).
- **Temaer:** 5 stk via `[data-theme]` på `<html>`, lagres i `localStorage` (`theme`), settes
  før paint av `is:inline`-skript i `<head>`. **Nytt tema = (1) `[data-theme="<id>"]`-blokk i
  `src/styles/global.css`, (2) én linje i `src/lib/themes.js`** — resten bygges fra registeret.
- **Stiler:** `src/styles/global.css` (design-tokens som CSS-variabler, importert i `Base.astro`).
- Rask lokal sjekk (valgfritt): `cd web && npm run dev` → `localhost:4321` (trenger JSON i
  repo-lokal `briefings/`).

## Fallgruver

- **`master`, ikke `main`.**
- **`docker compose build` bygger IKKE generatoren** (`profiles: batch` → hoppes stille over).
  Bruk alltid `docker compose build web generator`. Klassisk symptom på gammel generator-kode:
  vær uten `hourly` → nettsiden faller tilbake til statisk stat-grid.
- **Generator-container har CMD, ikke ENTRYPOINT** — `docker compose run generator <cmd>` kjører
  `<cmd>` i stedet for briefingen; uten `<cmd>` kjøres hele briefingen (Claude-kvote). Inspiser
  data via `docker compose exec web …`.
- **Persistente data på volumet:** `briefings/<dato>.json`, `research_seen_dois.json` og
  `quiz_seen.json` MÅ ligge i `/data` (`BRIEFING_DATA_DIR=/data`), ellers tomt arkiv +
  nullstilt dedup.
- **Tidssone:** cron-tidspunkt = verts-TZ (`CRON_TZ` virker ikke på Debian); innholdets
  dato/værvinduer = container-TZ. Begge skal være `Europe/Oslo`.
- **`docker-entrypoint.sh` må ha LF** (sikret av `.gitattributes`).
- **Caddy:** `admin off` → reload kun via `docker compose restart caddy`.

## Avhengigheter

```bash
pip install -r requirements.txt    # httpx, feedparser, anthropic, notion-client, yfinance
cd web && npm install              # astro, @astrojs/node, marked
```

Python 3.10+ (image: 3.12). Node 20+ (image: 22).
