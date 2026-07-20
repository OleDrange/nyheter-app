# Nyhetsbriefing — CLAUDE.md

## Hva appen er

Daglig briefing-app, live på **https://nyheter.modr.no**. Kjører på VPS-en `MODR` (dette
repoet: `/root/nyheter-app`, remote `git@github.com:OleDrange/nyheter-app.git`, default-branch
**`master`**). To deler, frikoblet via et JSON-datalager på et delt Docker-volum:

- **Generator** (Python, cron 05:00 hver dag):
  - `news_briefing.py` — nyhetsbriefing fra RSS + Bergen-vær + markedssnapshot.
  - `research_briefing.py` — maks 5 fagfellevurderte menneskestudier (longevity) fra Europe PMC.
- **Nettside** (`web/`, Astro 5 SSR på Node) — leser JSON ved hver forespørsel og viser
  dagens briefing + arkiv. Nytt *innhold* vises uten rebuild; *kodeendringer* krever rebuild.
  Samme app serverer også **https://forskning.modr.no** (host-rutet i `web/src/middleware.js`)
  med full forskningsbriefing; nyhetssiden viser kun titler som lenker dit.

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
  `nyheter.modr.online` og `n.modr.no` 301-redirecter dit. **forskning.modr.no** skal ha
  identisk blokk (samme container — appen ruter på host); krever DNS A-post →
  serverens IP før Caddy kan hente sertifikat. Caddy har `admin off` → reload med
  `docker compose restart caddy` (validér først:
  `docker compose exec -T caddy caddy validate --config /etc/caddy/Caddyfile`).
- **Inspisere data:** `docker compose exec web ls -la /data/briefings` /
  `… cat /data/briefings/<dato>.json`.
- **Logger:** generator → `/root/nyheter-cron.log`; web → `docker compose logs -f web`.
- **Backup** — **to volumer**, begge må med. `saved-data` er de eneste dataene i systemet
  som **ikke kan regenereres** (lagrede studier med dine notater og tagger):
  ```bash
  docker run --rm -v nyheter-app_briefing-data:/d -v /root:/b alpine tar czf /b/nyheter-backup.tgz -C /d .
  docker run --rm -v nyheter-app_saved-data:/d    -v /root:/b alpine tar czf /b/lagret-backup.tgz  -C /d .
  ```
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
- `MAX_PER_FEED = 25`, `MAX_DESC_CHARS = 300`, `NEWS_HISTORY_DAYS = 2`.
- **RSS hentes med `httpx`** (browser-UA i `_FETCH_HEADERS` + `follow_redirects=True`), så
  `feedparser.parse(resp.content)`. Mange norske aviser blokkerer feedparsers bot-UA — ikke
  bytt tilbake til `feedparser.parse(url)`.
- **Dedup før Claude:** `fetch_articles()` avslutter med `_dedup_articles()` (normalisert
  tittel + URL; beholder lengst ingress) — feedene overlapper mye.
- **Lokalt kutt-filter:** `_CUT_TITLE_RE` i `news_briefing.py` kutter artikler hvis TITTEL
  matcher kategorier systemprompten uansett forkaster (sport, krim, kjendis/underholdning,
  lokale ulykker) — gratis, før MAX_PER_FEED-telling, sparer input-tokens. Listen er bevisst
  konservativ (feilkutt kan ikke reddes av Claude): «drapssiktet»/«siktet for» er med, bare
  «drept» er det IKKE (ville kuttet krigsnyheter).
- **Dedup mot tidligere dager** (leseren skal ikke lese det samme to dager på rad):
  `_load_recent_briefing_points()` leser `news_md` fra de siste `NEWS_HISTORY_DAYS`
  dagsfilene i datalageret (ingen egen state-fil) og gir (1) URL-/tittelsett som
  `fetch_articles(skip=…)` filtrerer mekanisk bort *før* MAX_PER_FEED-telling, og
  (2) punkttekstene som sendes som unngå-liste i user-prompten («DEKKET I BRIEFINGENE
  DE SISTE DAGENE») — fanger samme sak med ny overskrift. Prompt-regel: gjenta kun ved
  vesentlig ny utvikling, og da med fokus på det nye.

### RSS-feeds

19 feeds i `RSS_FEEDS`-dict øverst i `news_briefing.py` (`"Kildenavn": "https://..."`).
Bekreft at ny URL gir HTTP 200 + gyldig XML før du legger den til.

Ikke prøv disse igjen: Reuters (RSS stengt), Finansavisen (ingen RSS), Oslo Børs (kun
NewsWeb API; dekkes via E24 Børs), forskning.no (JS-rendret, ingen feed), samt de utdaterte
URLene `nrk.no/nyheter/rss.xml`, `e24.no/rss.xml`, `dn.no/rss.xml`.

### Briefing-seksjoner

`SYSTEM_PROMPT` styrer output: Bloomberg-stil (tall og fakta, ingen fyllord), men skrevet
for en **smart allmennleser** — fagbegreper/forkortelser/ukjente selskaper forklares kort
inne i punktet (maks to setninger per punkt; setning to kun til forklaring/konsekvens).
Maks 550 ord, 7 «## »-seksjoner (emojiene brukes av nettsidens parsing):

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

### Vær (Bergen + Oslo + Alicante)

`fetch_weather(lat, lon)` → MET Locationforecast, **`complete`-endepunktet** (UV finnes ikke i
`compact`; API-et dekker hele verden, så Alicante går fint). `fetch_all_weather()` henter alle
stedene i `WEATHER_LOCATIONS` og returnerer `(bergen, weather_alt)` — Bergen lagres som
`weather` (og brukes i terminal/Notion), Oslo/Alicante i `weather_alt` (kun steder som lyktes).
Per sted returneres `summary`, `rain_hours`, `sun_periods` (kl. 05–21), `max_uv`/`max_uv_hour`,
`max_temp`/`max_temp_hour`, `temp_0700`, `fetched_at` (HH:MM, vises i panelet), `hourly` —
timesserie for i dag `[{ hour, temp, precip, wind, gust, uv, symbol }]` — og `daily`:
7 dagsvarsler (i dag + `_WEATHER_DAYS_AHEAD = 6`) fra `_build_daily()` med
`{ date, min_temp, max_temp, precip, max_wind, max_gust, max_uv, symbols, hours }`.
`symbols` er tre periodesymboler (morgen 05–11 / ettermiddag 11–17 / kveld 17–23): det
**vanligste** symbolet i perioden vinner, ved likt antall det mest optimistiske
(`_SYMBOL_SEVERITY` som tie-break); `hours` er detaljrader med `span` 1 (timesoppløsning,
første ~2 døgn) eller 6 (6-timersblokker lenger ut — mer gir ikke MET). Nedbørsummen unngår
dobbelttelling i 1t→6t-overgangen via et `covered_until`-vindu.

### Marked

`fetch_market_snapshot()` via `yfinance`: Brent, S&P 500, OBX (`OBX.OL`), BTC (`BTC-USD`),
ETH (`ETH-USD`) og Nordnet Global (nøkkel `nordnet`, MSCI World-proxy via `URTH`).
Dataene sendes **ikke** til Claude — Claude forklarer *hvorfor* markedet beveget seg.

### Dagens quiz (lokalt spørsmålsbibliotek)

`fetch_daily_quiz()` i `news_briefing.py` trekker spørsmål fra et **lokalt norsk
bibliotek** i `quiz_bank/<kategori>.json` (ligger i repoet, følger med i imaget via
`COPY . .`). Ingen ekstern API, ingen Claude-bruk. **Én fil = én kategori**; hver dag
trekkes ett nytt spørsmål per kategorifil, så **antall spørsmål/dag = antall filer** —
legg til en ny `quiz_bank/*.json` for flere spørsmål/dag, uten kodeendring.

- **Filformat:** `{ "category": "<visningsnavn>", "questions": [ { "difficulty":
  "easy|medium|hard", "question", "answer", "options": [4 alternativer, answer inkludert] } ] }`.
  `options` stokkes ved servering; `answer` er fasitteksten.
- **Rekkefølge:** `_QUIZ_CATEGORY_ORDER` (filnavn uten `.json`) styrer rekkefølgen;
  ukjente filer legges bakerst alfabetisk.
- **Nivårotasjon:** `_QUIZ_DIFFICULTY_CYCLE` (easy→medium→hard) roterer per dag/kategori
  (`(dag-ordinal + kategori-indeks) % 3`), med fallback til andre nivåer, og til slutt
  gjenbruk hvis banken er mindre enn retention-vinduet.
- **Dedup + spaced repetition:** `quiz_seen.json` i `BRIEFING_DATA_DIR` (normalisert
  spørsmålstekst → `{ "last": dato, "reps": antall ganger vist }`, prunes etter
  `_QUIZ_SEEN_RETENTION_DAYS = 365`) — **må persisteres** (volumet). Bakoverkompatibel med
  det gamle formatet (verdi = ren datostreng = vist én gang). I tillegg til dagens ferske
  spørsmål hentes **ett tidligere sett spørsmål tilbake som repetisjon** når det er forfalt:
  et spørsmål vist `reps` ganger forfaller når alderen ≥ `_QUIZ_REVIEW_INTERVALS[reps-1]`
  (`[7, 30, 90, 180]` dager, klemt) — utvidende intervall (retrieval practice + spacing).
  Mest forfalte velges, legges sist, merkes `repeat: True` (`QuizCard` viser 🔁-badge og
  grønn kant). Ingen forfalte (tidlige dager) → intet repetisjonsspørsmål.
- Myk feil → tom liste, `quiz`-feltet utelates den dagen.

Per nå finnes `norsk_samfunn.json` og `medisin_og_kropp.json` (~60 spm hver, dekker ~2 mnd).
`historie` og `geografi` står i `_QUIZ_CATEGORY_ORDER`, men filene er ikke lagt til ennå —
legg dem til for å komme opp i 4 spm/dag.

### Dagens gåter (lokal gåtebank)

`fetch_daily_riddles()` i `news_briefing.py`: 3 norske **logikkgåter** (nivå 1–3, ingen
faktakunnskap) trekkes fra en **lokal gåtebank** i `riddle_bank/gaater.json` (ligger i
repoet, følger med i imaget via `COPY . .`). Ingen Claude-bruk, ingen ekstern API.
- **Filformat:** `{ "riddles": [ { "level": 1|2|3, "genre": "<navn fra _RIDDLE_GENRES>",
  "question", "answer", "explanation" } ] }`. Per nå 150 gåter: 50 per nivå = 5 per
  sjanger per nivå — banken dekker 50 dager uten gjentak. **Utvid ved å legge til flere
  oppføringer i fila** (fasit må være verifisert; `genre` må matche navnene i
  `_RIDDLE_GENRES` for at sjangerrotasjonen skal treffe).
- **Nivåkrav** (gjelder også nye gåter i banken): nivå 1 = oppvarming (1–2 steg, < 2 min),
  nivå 2 = 3–4 resonneringssteg (3–5 min), nivå 3 = skikkelig nøtt (4–6 steg, gjerne to
  teknikker kombinert, penn og papir, 10–20 min). Entydig fasit; `explanation` = ryddig
  løsningsvei (maks 3 setninger nivå 1–2, maks 5 nivå 3).
- **Sjangerrotasjon:** `_RIDDLE_GENRES` (10 typer) roteres deterministisk per dag
  (`_todays_riddle_genres()`: vindu på 3 som flyttes 3 plasser per dag-ordinal; 10 og 3 er
  innbyrdes primiske, så alle kombinasjoner nås over 10 dager). Dagens sjanger per nivå
  styrer trekket fra banken.
- **Dedup — samme gåte trekkes aldri to ganger** så lenge nivået har usette gåter:
  `riddles_seen.json` i `BRIEFING_DATA_DIR` (**må persisteres**, prunes etter
  `_RIDDLES_SEEN_RETENTION_DAYS = 120`). Trekk per nivå: usett i dagens sjanger → ellers
  usett på nivået → ellers (alt sett) gjenbrukes den som ble vist for lengst siden (LRU).
Myk feil (manglende/korrupt bank) → `riddles`-feltet utelates den dagen.

### Dagens inspirasjon (podcast-råd + boktips)

`fetch_daily_learning()` i `news_briefing.py`: 1–2 podcast-råd + 1–2 boktips, kuratert av
Claude i ett lite kall. Profil (i `_LEARNING_SYSTEM_PROMPT`): selvutvikling først, deretter
de nyeste viktigste teknologitrendene (særlig AI) — gjelder både episodevalg og boktips.
Boktips i tillegg: utgitt **2020+**, fakta-/tallbasert (statistikk og undersøkelser, ikke
erfarings-/følelsesbaserte memoarer); smaksankere «Suveren på jobb» og «Factfulness».
Episoder hentes fra `PODCAST_FEEDS` (Lenny's Podcast, Huberman Lab, Tim Ferriss, Dwarkesh,
Diary Of A CEO, Hard Fork, All-In — RSS, siste `_LEARNING_LOOKBACK_DAYS = 14` dager).
Claude refererer episoder kun via indeks-id mot vår liste, så podcast/tittel/URL aldri kan
hallusineres — kun rådsteksten («tip») og boktipsene kommer fra Claude. Dedup:
`learning_seen.json` i `BRIEFING_DATA_DIR` (**må persisteres**; episode- + boktitler,
prunes etter `_LEARNING_SEEN_RETENTION_DAYS = 180`; tidligere bøker sendes som unngå-liste).
Myk feil → `learning`-feltet utelates den dagen.

### Dagens refleksjon (`reflection`-feltet)

`fetch_daily_reflection(news_md, learning)` i `news_briefing.py`: inntil to **åpne
refleksjonsspørsmål** (uten fasit) generert av Claude i ett lite kall
(`_REFLECTION_SYSTEM_PROMPT`, JSON-array parses inline). Ett spørsmål forankres i en konkret
sak fra dagens nyheter (`focus: "nyheter"`), ett i dagens inspirasjon — podcast-råd/boktips
(`focus: "inspirasjon"`). Mangler en kilde, lages kun spørsmålet for den som finnes; maks ett
per `focus`. Elaborering/refleksjon støtter læring. **Kalles sist i `main()`** (etter at
`news_md` og `learning` er klare, før `store_briefing`). Ingen dedup/persistert state —
spørsmålene varierer med dagens innhold. Myk feil → tom liste → `reflection`-feltet utelates.
Nettsiden viser dem i `ReflectionCard.astro` («Til ettertanke»-seksjonen, etter Inspirasjon).

### SK Brann (`brann`-feltet)

`fetch_brann_info()` i `news_briefing.py` — ingen Claude-bruk:
- **NIFS-API** (`api.nifs.no`, åpent, ingen nøkkel): tabellplassering, siste resultat og
  neste kamp i Eliteserien. Brann herrer = team-id `1`, Eliteserien = turnering-id `5`;
  riktig sesong-stage slås opp per år (`yearStart == inneværende år`).
- **Google News RSS** (`"SK Brann"`-søk): siste nyhet (`_BRANN_NEWS_MAX = 1`; skader/
  overganger o.l.), kildenavnet løftes ut av tittelen (« - Kilde»-suffikset).
  `BrannCard.astro` viser uansett maks 1 (`slice(0, 1)`) så gamle briefinger med flere
  lagrede nyheter også viser én.
Myk feil per del; feltet utelates kun hvis alt feiler.

### Forskningsbriefing (`research_briefing.py`)

Målgruppe: **longevity** — menneskestudier med tydelige tall som leseren kan handle på selv.
Utvalget skjer i **tre trinn** (spørring → lokal scoring → Claude), ikke hos Claude alene.

**1. Europe PMC-spørring — her håndheves kvalitetskravene.** `search`-REST (ingen nøkkel),
`resultType=core` (fulle abstracts). `_PMC_SUFFIX` krever `SRC:MED` (fagfellevurdert),
`MESH:"Humans"` (ingen mus/celler) og `PUB_TYPE` = RCT / metaanalyse / systematisk oversikt.
Fire kategorier i `CATEGORY_QUERIES`: **longevity / trening / kosthold / sovn_stress**.
Kryss-kategori-duplikater fjernes (første kategori vinner).

- **Emneordene er bundet til tittelen** (`TITLE:"exercise"`), ikke fritekst. Uten det matcher
  Europe PMC ordet hvor som helst i artikkelen, og poolen fylles av kreft, cellegift og
  antipsykotika (ett tilfeldig «exercise» i et endometriose-abstract gjorde studien til en
  «trenings»-studie). **Ikke bytt tilbake til fritekst.** Fallgruver funnet ved testing:
  `TITLE:"fiber"` matcher «Thulium **Fiber** Laser» (bruk `"dietary fiber"`), `TITLE:"stress"`
  matcher «oxidative stress», og `TITLE:"recovery"` matcher postoperativ restitusjon — de to
  siste må stå som fraser («psychological stress», «stress reduction» …).
- **`LOOKBACK_DAYS = 180`, ikke 2.** Forskning har ingen nyhetssyklus, og et kort vindu gjør
  kvalitetsfiltrene *utilgjengelige*: Europe PMC tildeler MeSH/PUB_TYPE uker etter publisering,
  så en to dager gammel artikkel er ennå ikke merket som menneskestudie eller RCT (målt på
  `exercise`: 2 dager → 0 treff med `MESH:"Humans"`, 30 dager → 24). Vinduet gir 494 studier
  (~2,7 nye i døgnet). 365 dager gir 1 090 (~3,0/døgn) — nesten samme *tilsig*, bare et større
  reservoar; halvårsvinduet er valgt bevisst så alt vi viser er publisert siste seks måneder.
- **Hele poolen hentes, ikke bare de nyeste.** `_fetch_all_pages()` paginerer via Europe PMCs
  `cursorMark` (`PAGE_SIZE = 100`, tak `MAX_FETCH_PER_CATEGORY = 600`). Tidligere hentet vi kun
  de 100 nyest indekserte per kategori, som utelot ~80 % av vinduet fra scoringen uten å gi noe
  igjen — «nyest først» betyr lite når vinduet uansett er 180 dager.

**2. Lokal scoring (`_score_candidate`) — gratis grovsortering før Claude.** Rangerer alt som
er hentet. Poeng for studiedesign (`pubTypeList`), utvalgsstørrelse (log10, dempet),
tydelige effektmål (HR/RR/OR/CI/p — mangler de, trekkes det fra: da er det ingen «Resultat» å
skrive) og harde utfall. **Trekk fra** for smale pasientgrupper (`_NARROW_POPULATION` — «patients
with …» er det mest treffsikre signalet) og medikament-/apparat-/genetikkstudier (`_DRUG_TERMS`):
en RCT på trening hos slagpasienter sier lite om hva en frisk leser bør gjøre. Under `MIN_SCORE`
forkastes helt.

- **Scoringen kjører på FULLT abstract — kutt aldri før scoring.** `MAX_ABSTRACT_CHARS = 4000`
  brukes kun når prompten bygges (`build_candidates_text`). Tidligere ble abstractet kuttet til
  1200 tegn *før* scoringen, men Resultat-delen (HR/RR/CI/p, utvalgsstørrelse) står typisk etter
  1200 tegn og 97 % av abstractene er lengre. Det fjernet nøyaktig de signalene scoringen gir
  poeng for: 20. juli 2026 falt 52 kvalifiserte kandidater til **0**, og forskningsbriefingen
  uteble helt. Besparelsen var ~1,5 øre/dag.
- **Utvalg i to trinn:** `CANDIDATE_FLOOR_PER_CATEGORY = 4` garantert per kategori (bevarer
  bredde — briefingen grupperes etter kategori), deretter fylles opp til `CANDIDATE_POOL = 40`
  av de høyest scorede på tvers. ~23 000 input-tokens/dag ≈ 0,07 $ ≈ 0,7 kr.

**3. Claude (`MAX_ITEMS = 7`)** velger og forklarer. Format per studie:
`## [tittel](url)` + **Kategori** + **Metode** / **Resultat** / **Hva det betyr for deg** /
**Forbehold** — 3–4 setninger på de tre første. Nettsiden parser etikettene;
`splitResearch()` løfter Kategori ut som eget `category`-felt (`normalizeCategory()` godtar både
visningsnavn og slug). Heller færre enn svake.

- `research_items` i JSON-en har også `category` (kandidatens kilde-kategori).
- **Dedup — tre nivåer** i `research_seen_dois.json` (`{doi: {last, picked, refused}}`;
  gammelt format = ren datostreng leses som `picked: true`). Valgt av Claude → blokkert
  `SEEN_RETENTION_DAYS = 400` dager (leseren skal **aldri** se samme studie to ganger).
  **Avvist av sikkerhetsklassifikatoren** (refusal) → `refused: true`, blokkert like lenge
  som picked — en refusal er deterministisk, og uten flagget kom samme abstract tilbake
  etter karantenen og betalte en ny bisect-runde med prober; refused-DOI-er lagres **også
  når kjøringen gir opp helt** (før exit). Sendt, men ikke valgt → karantene
  `UNPICKED_COOLDOWN_DAYS = 14` dager, så den ikke brenner input-tokens hver dag,
  men får komme tilbake (poolen er liten). Uten dette ville et bredt vindu servert de
  samme toppkandidatene daglig. Ligger i `BRIEFING_DATA_DIR` — **må persisteres** (volumet).
- **Legacy:** kategorien `medisin` produseres ikke lenger, men finnes i arkiverte briefinger —
  derfor ligger den fortsatt sist i `RESEARCH_CATEGORIES` (`web/src/lib/briefings.js`) og i
  `CATEGORY_LABELS`. Tomme grupper skjules av `ResearchList.astro`.
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
  "weather": { ... },          // fetch_weather()-dict for Bergen (inkl. daily/hourly)
  "weather_alt": { "oslo": { ... }, "alicante": { ... } },  // samme form som weather
  "market": { ... },           // fetch_market_snapshot()-dict
  "research_items": [ { "title", "url", "journal", "date", "category" } ],
  "quiz": [ { "level", "difficulty", "category", "question", "options", "answer", "repeat"? } ],
  "riddles": [ { "level", "question", "answer", "explanation" } ],
  "learning": { "podcasts": [ { "podcast", "episode", "url", "date", "tip" } ],
                "books": [ { "title", "author", "year", "why" } ] },
  "reflection": [ { "focus": "nyheter|inspirasjon", "prompt" } ],
  "brann": { "team", "season",
             "table": { "place", "played", "won", "draw", "lost", "points", "teams" },
             "last_match": { "opponent", "home", "date", "round", "stadium",
                             "brann_goals", "opponent_goals", "outcome" },
             "next_match": { "opponent", "home", "date", "round", "stadium" },
             "news": [ { "title", "url", "source", "published" } ] }
}
```

`weather`/`market` lagres strukturert hver dag → historiske figurer (f.eks. markedsgrafene)
bygges uten ekstra datainnhenting.

## Nettsiden (`web/`)

- Astro 5, `output: 'server'`, `@astrojs/node` standalone. Lytter på `0.0.0.0:8080` i
  container (`HOST`/`PORT` env). `BRIEFING_DIR=/data/briefings` i prod-Dockerfile; uten
  env-var faller `briefings.js` tilbake til repo-lokal `briefings/`.
- **Ruter (nyheter):** `/` (nyeste), `/arkiv` (liste), `/b/<dato>` (én dag).
- **Ruter (forskning):** `/forskning`, `/forskning/arkiv`, `/forskning/b/<dato>`.
  `src/middleware.js` ruter host `forskning.*` internt til disse (rene URL-er på
  subdomenet: `/`, `/arkiv`, `/b/<dato>`) og setter `locals.fbase` = lenkeprefiks
  ('' på subdomenet, '/forskning' ved sti-tilgang/dev). `Base.astro` tar
  `site="forskning"` + `base` for egen header/nav. Anker `#s<i>` per studie
  (i = posisjon i `research_md`) — nyhetssidens tittelliste lenker dit.
- **Ruter (felles for begge vertsnavn):** `/lagret` og `/api/*`. Disse skrives **ikke** om av
  middleware (`isShared`) — de finnes kun på rot-nivå og spenner over begge sider.

### Lagrede studier («pin»)

Pin en studie fra forskningssidene; den huskes permanent og vises på `/lagret` med filter,
søk, notat og tagger. Se `PLAN-LAGREDE-STUDIER.md` for full plan (leveranse 2: gåter/quiz,
eksport, repetisjon).

- **Eget volum, ikke arkivet.** `saved-data:/state` (rw) ved siden av `briefing-data:/data:ro`.
  Nettappen er eneste prosess eksponert mot internett og skal **aldri** kunne skrive inn i
  briefing-arkivet. `SAVED_DIR=/state` i container; default `state/` lokalt (gitignored).
- **`web/src/lib/saved.js`** — lagring. Alle mutasjoner går gjennom **én seriell promise-kø**:
  Node er én prosess, men samtidige POST-er kan ellers interleave read-modify-write og miste
  en lagring. Skriving er atomisk (`.tmp` + `rename`), som `store_briefing()`.
- **Fullt øyeblikksbilde, ikke referanse.** Briefinger er immutable, så det er ingenting å
  synkronisere; et snapshot fjerner en feilklasse (manglende arkivfil, drift i
  `splitResearch()`) for ~2 KB per studie. `date` + DOI beholdes som tilbakelenke.
- **ID-en gir idempotens:** `study:<doi>` (globalt unik), `riddle:`/`quiz:<sha1(spørsmål)[0:12]>`
  (innholdshash — de har ingen ID, og hashen overlever quizens repetisjonsmekanikk). Samme sak
  lagret to ganger blir én oppføring.
- **Innholdet utledes SERVER-SIDE** fra arkivet (`deriveStudy()` i `api/lagret.js`) — klienten
  sender kun `{date, url}`. Ellers kunne enhver med kodeordet plantet vilkårlig HTML i lageret,
  som senere rendres med `set:html`.
- **`web/src/lib/auth.js`** — lesing er åpent for alle, kun skriving krever kodeord.
  `SAVE_PASSPHRASE` sendes inn i compose som **enkeltvariabel**, bevisst ikke `env_file`
  (som ville gitt web-containeren `ANTHROPIC_API_KEY` den ikke trenger). Cookien er
  `<utløp>.<HMAC-SHA256(utløp, kodeord)>` — HttpOnly/Secure/SameSite=Lax, ett år, ingen
  sesjonslagring. Bytter du kodeord, blir alle utstedte cookies ugyldige automatisk.
  `crypto.timingSafeEqual` + rate-limiting (10 forsøk / 15 min / IP). **Uten `SAVE_PASSPHRASE`
  svarer skriveendepunktene 503 og pin-knappene skjules** — funksjonen feiler synlig, ikke åpent.
- **Filtrering og søk er server-side** via URL-parametre (`?q=&kategori=&tag=&sort=`): virker
  uten JS, URL-ene blir delbare/bokmerkbare, og det skalerer forbi noen tusen oppføringer.
- **Komponenter:** `SaveButton.astro` (ren markup; `saved` kommer fra serveren så stjernen er
  fylt i første paint) + `SaveRuntime.astro` (én delegert klikk-lytter for hele siden,
  kodeord-dialog ved 401, **angre-toast** ved avpinning — som sletter notat og tagger).
  Tagger normaliseres til små bokstaver, ellers får man «protein»/«Protein»/«proteiner» som
  tre filtre innen en måned.
- **Komponenter:**
  - `BriefingView.astro` — deler topp-grid + gåter/quiz + nyhetskort mellom forside og
    enkeltdag. Rekkefølge: vær/marked → nyheter → Gåter → Quiz → Inspirasjon →
    Til ettertanke → forskning (kun tittelliste med kategori-badge, lenker til `FORSKNING_URL`).
    `BrannCard` rendres inne i Bergen og Vestland-kortet (tittelmatch `/bergen/i`).
    Seksjonene har anker-id-er (`#vaer-marked`, `#nyheter`, `#gaater`, `#quiz`, `#inspirasjon`,
    `#refleksjon`) som headerens hopp-rad bruker (`.jumpnav` i `Base.astro`, kun
    nyhetssiden): 0,5 s scroll-animasjon; lenker uten mål på siden skjules av
    inline-skriptet, så raden forsvinner helt på f.eks. arkivsiden.
  - `LearningCard.astro` — «Dagens inspirasjon»: podcast-råd (🎧) og boktips (📚) fra
    `learning`-feltet, to kort i `cards-grid`. Ren HTML uten klient-JS.
  - `ReflectionCard.astro` — «Til ettertanke»: 1–2 åpne refleksjonsspørsmål fra
    `reflection`-feltet (📰 fra nyhetene / 🎧 fra inspirasjonen), accent-tonet kort. Ren HTML.
  - `BrannCard.astro` — SK Brann-blokk fra `brann`-feltet: tabellplassering, neste kamp
    (norsk dato/klokkeslett via Intl, følger container-TZ), siste resultat (farget utfall)
    og nyhetslenker. Ren HTML uten klient-JS.
  - `ResearchList.astro` — full forskningsvisning (forskning-sidene): studiekort gruppert
    etter kategori (`RESEARCH_CATEGORIES` i `briefings.js`), ukategoriserte under «Øvrig».
  - `StatsGuide.astro` + `StatsGuideNode.astro` — «Slik leser du forskningstall»: skjulbart
    oppslagsverk øverst på forskningsforsiden (p-verdi/KI/effektstørrelser). Innholdet er et
    rekursivt tre i `src/lib/statsGuide.js` (datatype → metode → eksempel) som
    `StatsGuideNode` rendrer generisk (`Astro.self`) — **ny metode/eksempel = ny node i
    datafila, ingen UI-endring**. Formler er semantisk HTML (`<sub>`/`<sup>`, `role="math"`
    + aria-label via `fm()`-hjelperen), bevisst ikke KaTeX (null klientavhengigheter).
    Inline-script (ingen bundling) håndterer: topptoggle — **alltid lukket ved lasting**
    (ingen persistert tilstand; lukking nullstiller alle indre noder + søket, så neste
    åpning starter sammenslått), accordion per node (flere kan stå åpne), dyplenking
    `#les-<node-id>` (åpner seksjonen + forfedre + scroller dit),
    piltast-/Home/End-navigasjon og søkefeltet (filtrerer på tittel/tagline/keywords,
    åpner treffstien). Node-`id` må være unik i hele treet.
  - `WeatherCard.astro` — vær-widget/dispatcher: viser `WeatherPanel.astro` når `weather.daily`
    finnes (nye briefinger), ellers `WeatherPlayer.astro` (kun `hourly`) eller statisk stat-grid
    (eldste briefinger) — arkivet ser uendret ut bakover.
  - `WeatherPanel.astro` — Yr-inspirert værpanel: stedvelger-pills (Bergen standard, Oslo/
    Alicante fra `weather_alt`; valget huskes i `localStorage` som `wx-loc`), nå-rad (ikon/temp/
    status + dagens ↑↓temp, nedbør, vind `5 (15) m/s` med kast, UV) og morgen/ettermiddag/
    kveld-ikoner. To `<details>`-folder: «Time for time» (klippes klient-side til «fra nå og ut
    dagen» når briefingen er dagens dato; i arkivet vises hele dagen) og «Neste 6 dager»
    (kompakte dagsrader, hver utvidbar til `WeatherHours.astro`-tabell). Alle steder SSR-rendres;
    inline-skript (`define:vars`) bytter bare panel og oppdaterer nå-avlesningen.
  - `WeatherHours.astro` — time-for-time-tabell (kl./ikon/temp/nedbør/vind/UV) for én dags
    `daily[i].hours`; `span=6`-rader vises som «02–08». Delte symbol-/formathjelpere ligger i
    `src/lib/weather.js` (speiler `_SYMBOL_NO` i generatoren).
  - `WeatherPlayer.astro` — (legacy, kun gamle briefinger) time-for-time «video» med slider og
    autoplay + dagssammendrag-rad. Klientlogikk via `define:vars={{ frames }}` (inline, ingen
    bundling).
  - `MarketStrip.astro` — markedswidget med dagsendring + mini-dagsgraf per ticker
    (`MarketTrend.astro` — inline SVG, ingen klient-JS; hover-verdier via native `<title>`).
    Serien fra `getMarketHistory()` (default 8 dager, `endDate` avgrenser til dagen som
    vises). **Prosent-badgen beregnes fra serien** (siste vs. nest siste punkt) så den
    alltid matcher grafen; lagret `*_chg` (yfinance, børsens handelsdøgn) er kun fallback
    ved < 2 punkter. Påfølgende identiske verdier (børsstengte dager) kollapses — første
    i runet + siste punkt beholdes; flat endring gir nøytral badge (`chg-flat`).
    Graf: 280×92 viewBox, områdefyll, etikett på første/siste punkt + min/maks når de
    ikke er endepunkter (maks over, min under sitt punkt); under 560px går
    `market__grid` til 1 kolonne. `MARKET_KEYS` styrer rekkefølgen.
  - `QuizCard.astro` — «Dagens quiz»: 3 flervalgsspørsmål fra `quiz`-feltet
    (vises kun når feltet finnes). Fasit skjult til bruker trykker et alternativ —
    riktig grønt (`--up`), feil rødt (`--down`), score-linje når alle er besvart.
    Inline-script, ingen bundling.
  - `RiddleCard.astro` — «Dagens gåter»: 3 logikkgåter fra `riddles`-feltet. Fasit +
    løsningsvei i `<details>` («Vis fasit»), ren HTML uten klient-JS. Gjenbruker
    `quiz-q__level`-badgene.
  - `ThemePicker.astro` — temavelger i headeren.
- **`src/lib/briefings.js`:** `listDates()`, `getBriefing(date)`, `renderMarkdown()` (marked),
  `getMarketHistory()`, `splitNewsSections(news_md)` → `[{ emoji, title, html }]` (per
  «## »-seksjon; håndterer flagg-emoji som 🇳🇴), `splitResearch(research_md)` →
  `[{ title, url, category, parts, html }]` (`parts` = de merkede avsnittene Metode/Resultat/
  Hva det betyr for deg/Forbehold — og Hva som ble gjort/Relevans i arkiverte briefinger;
  `category` løftes ut av **Kategori**-etiketten via `normalizeCategory()`; `html` er fallback),
  `formatDateNo()`/`weekdayNo()` (lokaltid-trygg norsk dato).
- **Temaer:** 5 stk via `[data-theme]` på `<html>`, lagres i `localStorage` (`theme`), settes
  før paint av `is:inline`-skript i `<head>`. **Nytt tema = (1) `[data-theme="<id>"]`-blokk i
  `src/styles/global.css`, (2) én linje i `src/lib/themes.js`** — resten bygges fra registeret.
- **Stiler:** `src/styles/global.css` (design-tokens som CSS-variabler, importert i `Base.astro`).
  Global tekstskala: `html { font-size: 112.5% }` (18px) — alle rem-størrelser følger denne.
  Sidebredde: `--maxw: 1400px`, sidemarger 16px (10px under 520px).
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
- **To volumer, ikke ett.** `briefing-data` (generatoren skriver, web leser read-only) og
  `saved-data` (kun web, rw). Backup-kommandoen må dekke **begge** — lagrede studier kan
  ikke regenereres.
- **Persistente data på volumet:** `briefings/<dato>.json`, `research_seen_dois.json`,
  `quiz_seen.json`, `riddles_seen.json` og `learning_seen.json` MÅ ligge i `/data`
  (`BRIEFING_DATA_DIR=/data`), ellers tomt arkiv + nullstilt dedup.
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
