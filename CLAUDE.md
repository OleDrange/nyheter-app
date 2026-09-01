# Nyhetsbriefing — CLAUDE.md

## Hva appen er

Daglig briefing-app, live på **https://nyheter.modr.no**. Kjører på VPS-en `MODR` (dette
repoet: `/root/nyheter-app`, remote `git@github.com:OleDrange/nyheter-app.git`, default-branch
**`master`**). To deler, frikoblet via et JSON-datalager på et delt Docker-volum:

- **Generator** (Python, cron 05:00 hver dag):
  - `news_briefing.py` — nyhetsbriefing fra RSS + Bergen-vær + markedssnapshot.
  - `research_briefing.py` — maks 5 fagfellevurderte menneskestudier (longevity) fra Europe PMC.
  - `supplement_briefing.py` — tilskudds-kunnskapsbasen (vitaminer/peptider), **ikke**
    dagsbasert. Bor under forskningssiden: `forskning.modr.no/tilskudd`.
  - `textile_briefing.py` — tekstil-kunnskapsbasen (fiber/kjemi/kvalitet), **ikke** dagsbasert.
- **Nettside** (`web/`, Astro 5 SSR på Node) — leser JSON ved hver forespørsel og viser
  dagens briefing + arkiv. Nytt *innhold* vises uten rebuild; *kodeendringer* krever rebuild.
  Samme app serverer **tre** nettsteder, host-rutet i `web/src/middleware.js`:
  **https://nyheter.modr.no**, **https://forskning.modr.no** (full forskningsbriefing;
  nyhetssiden viser kun titler som lenker dit) og **https://tekstil.modr.no**
  (kunnskapsbase om tekstil — se egen seksjon).

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
- **Cron** (root sin crontab) — én linje for alle generatorene:
  ```cron
  0 5 * * * cd /root/nyheter-app && /usr/bin/docker compose run --rm generator >> /root/nyheter-cron.log 2>&1
  ```
  Kommandoen er tom, så CMD kjører — `docker-entrypoint.sh`, som tar nyheter → forskning →
  tilskudd → tekstil → healthcheck etter hverandre med myk feil mellom hvert steg. **En ny
  generator legges til DER, ikke som en ny crontab-linje** (to samtidige `docker compose run` mot
  samme volum er unødvendig risiko). Rekkefølgen er bevisst: nyhetsbriefingen er det
  leseren venter på kl. 05, og de to kunnskapsbasene (tilskudd, tekstil) går sist fordi de
  akkumulerer og ikke har noen dagsfrist.
  Tidspunktet styres av **verts**-TZ (`Europe/Oslo` via `timedatectl`; `systemctl restart cron`
  etter endring). **`CRON_TZ` virker ikke** på Debians cron — ikke legg den i crontab.
  Container-TZ (`TZ=Europe/Oslo` i Dockerfile + compose) styrer innholdets dato/værvinduer.
- **Proxy:** Caddy i `~/modr-proxy`. `nyheter.modr.no { encode gzip; reverse_proxy nyheter-web:8080 }`;
  `nyheter.modr.online` og `n.modr.no` 301-redirecter dit. **forskning.modr.no** og
  **tekstil.modr.no** har identiske proxy-blokker (samme container — appen ruter på host);
  `t.modr.no` 301-redirecter til tekstil. **Korte alias må være redirect, ikke proxy:** en
  `reverse_proxy`-blokk på `t.modr.no` ville sendt Host-headeren `t.modr.no` inn i appen,
  som ikke matcher noe prefiks i `middleware.js` — og leseren hadde fått nyhetssiden. Nytt
  subdomene krever DNS A-post → serverens IP før Caddy kan hente sertifikat. Caddy har `admin off` → reload med
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

- **Modell:** `claude-opus-5` (alle fire generatorene). Byttet fra `claude-sonnet-4-6`
  1. september 2026. **Opus 5 tenker som standard** — det er ikke en gratis oppgradering, og
  to ting følger av det:
  - **`max_tokens` er et felles tak for tenking OG svar.** Alle takene ble hevet i samme
    slengen (`MAX_TOKENS` 4096 → 16000 i nyheter, 16000 → 32000 i de tre andre;
    `SYNTH_MAX_TOKENS` 4000 → 12000; læring 2000 → 8000, refleksjon 700 → 4000). Et for lavt
    tak gir ikke feilmelding — svaret kappes midt i, og JSON-parsingen faller til myk feil.
  - **`content[0]` er ikke nødvendigvis tekst.** Første blokk kan være en thinking-blokk.
    `_text_of()` i `news_briefing.py` slår sammen tekst-blokkene; de tre andre generatorene
    gjorde allerede dette i syntesen.
  - **Unntak: refusal-probene** (`CLAUDE_PROBE_MAX_TOKENS = 16`) kjører med
    `thinking={"type": "disabled"}` — de spør kun ja/nei om sikkerhetsklassifikatoren slår
    til, og tenking ville spist hele budsjettet. Lovlig fordi `effort` er `high` som standard
    (Opus 5 avviser avslått tenking først på `xhigh`/`max`).
  - **Pris:** $5/$25 per million tokens mot Sonnet 4.6 sine $3/$15, og tenketokens faktureres
    som output. Regn ~2× på kjøringen.
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
Maks 450 ord, 5 «## »-seksjoner (emojiene brukes av nettsidens parsing):

| Seksjon | Maks punkter | Intern fordeling |
|---|---|---|
| 🏥 Helse og medisin | 3 | klinisk evidens (behandlinger, FDA/EMA, folkehelsevarsler) |
| 🔬 Vitenskap og teknologi | 3 | **min. 2 vitenskap, maks 1 AI/tech** |
| 🌍 Internasjonalt | 1 | — |
| 📈 Økonomi og marked | 3 | **min. 2 internasjonalt makro, maks 1 norsk**; krypto maks 1 |
| 🏙️ Bergen og Vestland | 3 | kun direkte hverdagskonsekvens |

Innenrikspolitikk uten markedseffekt og eiendomsmarkedet kuttes alltid.

**Øverste seleksjonsregel (går foran seksjonskriteriene): retningsskifte, ikke hendelse.**
En sak må endre retningen på noe — vendepunkt i en trend, ny regulering som endrer
spillereglene, tall som bryter med forventningen, første gang noe skjer, strategiskifte hos
en aktør. Statusoppdateringer i en sak som allerede går sin gang kuttes, uansett hvor stor
saken er. Tom seksjon er bedre enn et punkt uten retningsskifte.

**Seksjonene ble slått sammen 11. august 2026** (7 → 5, tak 17 → 13 punkter): «Forskning og
vitenskap» + «AI, teknologi og startups» → 🔬 Vitenskap og teknologi, og «Norsk økonomi» +
«Marked og makro» → 📈 Økonomi og marked. Sammenslåingen er **ren promptendring** — nettsidens
parsing (`splitNewsSections()`) splitter generisk på «## » og plukker ledende emoji, så
arkiverte briefinger beholder sine gamle sju seksjoner og rendres uendret. Fordelingskravene
inne i de sammenslåtte seksjonene er poenget med dem: uten «maks 1 AI/tech» ville AI-nyheter
(det er alltid flest av dem) fortrengt vitenskapen helt.

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

**Systemet er købasert, ikke dagsbasert.** Studier vi har vurdert men ikke vist, ligger i en
varig kø sortert synkende på score (`research_queue.json`). En kjøring er fem steg, der de tre
midterste hoppes over når de ikke trengs:

1. Last kø + `seen`.
2. **Prun** (`_prune_queue`): fjern for gamle (`QUEUE_MAX_AGE_DAYS = 400` på publiseringsdato)
   og alt som allerede er vist.
3. **Påfyll** (`refill_queue`) — *kun hvis under `QUEUE_REFILL_BELOW = 60` i kø.* Spørring →
   lokal scoring → innsetting.
4. **Claude skriver omtaler** (`write_up_batch`) — *kun hvis under `WRITEUP_REFILL_BELOW = 10`
   ferdigskrevne.* `WRITEUP_BATCH_SIZE = 10` per kall.
5. **Publisér** (`pop_for_today`) — **aldri et API-kall**, ren sammensetting av lagret tekst.

`--dry-run` kjører alt unntatt steg 4, og publiserer ikke. Gratis, og eneste trygge måte å
teste hentingen på.

#### Køen (`research_queue.json`) — **må persisteres**

`{ version, updated, entries: [...] }`, `entries` sortert synkende på `score`. Status per
oppføring: `scored` (venter på tekst) → `ready` (har `writeup`, abstract slettet) eller
`rejected` (gravstein — Claude vraket den, eller refusal; blir liggende så den ikke settes inn
igjen). Køen er sin egen dedup: en DOI som allerede står der, settes aldri inn på nytt.

- **Ikke gjeninnfør karantene.** Fram til 24. juli 2026 fantes `UNPICKED_COOLDOWN_DAYS = 14`:
  studier sendt til Claude uten å bli valgt ble blokkert i 14 dager. Den fantes bare fordi
  Claudes vurdering ikke ble lagret. Med 40 kandidater/dag × 14 dager låste den ute opptil ~475
  studier samtidig — mer enn hele vinduet (473) — og systematisk de **høyest scorede**, siden
  poolen alltid var toppen av scoringen. Målt: 199 av 473 i karantene, 160 av dem over terskel
  (toppscore 12,6), mens de ferske toppet på 2,9. Forskningsbriefingen uteble 23. og 24. juli.
- **Claude velger ikke lenger, den skriver.** Den lokale scoringen bestemmer både hva som
  kommer i kø og rekkefølgen. Claude får kun studiene den skal skrive om — input falt fra
  ~23 000 tokens (40 kandidater, 35 kastet) til ~6 000 per kall, og hver studie koster tokens
  nøyaktig én gang, noensinne. Kvalitetskontrollen beholdes ved at Claude kan vrake en studie
  med `## SKIP [n] — begrunnelse`; den settes da til `rejected`.
- **Omtalene lagres per studie i Claudes vanlige format** (`## [tittel](url)` + etikettene), og
  settes sammen med `_STUDY_SEPARATOR` ved publisering. Det er derfor lagret tekst er trygt:
  `splitResearch()` splitter på nettopp den overskriften, så en omtale skrevet for tre dager
  siden parses identisk med en skrevet i dag. **Endrer du FORMAT-delen av `SYSTEM_PROMPT`,
  blir køens eksisterende omtaler stående i det gamle formatet** — begge må da parses.
- **Blokker mappes på URL, ikke rekkefølge** (`_parse_writeups`). Kan et svar ikke knyttes til
  noen studie, lagres **ingenting** — feil tekst under riktig tittel er verre enn en tapt dag.
- Hopper Claude stille over en studie `WRITEUP_MAX_PASSES = 2` ganger, settes den `rejected`.
- **Uttak** (`pop_for_today`): `MAX_ITEMS = 5` per dag, `MAX_PER_CATEGORY = 2` som **mykt** tak
  — har køen ikke nok kategorier, fylles dagen opp likevel. En skjev dag er bedre enn en tom.

**1. Europe PMC-spørring — her håndheves kvalitetskravene.** `search`-REST (ingen nøkkel),
`resultType=core` (fulle abstracts). `_PMC_SUFFIX` krever `SRC:MED` (fagfellevurdert),
`KW:"Humans"` (ingen mus/celler) og `PUB_TYPE` = RCT / metaanalyse / systematisk oversikt.
Fire kategorier i `CATEGORY_QUERIES`: **longevity / trening / kosthold / sovn_stress**.
Kryss-kategori-duplikater fjernes (første kategori vinner).

- **Menneskefilteret må være `KW:"Humans"` — `MESH:` er dødt i Europe PMC og skal ALDRI
  brukes.** Fram til 11. august 2026 sto det `MESH:"Humans"`, som matcher en forsvinnende og
  vilkårlig delmengde selv om studiene har «Humans» i `meshHeadingList`. Målt på
  `TITLE:"exercise" AND SRC:MED`: RCT alene → **385** treff, RCT + `MESH:"Humans"` → **1**.
  Over ti år slapp 13 av 8 304 RCT-er gjennom (0,16 %), mot ~14 % av metaanalysene — filteret
  fjernet altså i praksis **hele RCT-tilfanget** og etterlot oss med oversiktsartikler.
  (`MESH:"Animals"` → 0, `MESH:"Adult"` → 1: feltet virker ikke i det hele tatt.) `KW:` treffer
  MeSH-termene og diskriminerer riktig: `TITLE:"exercise"` beholder 38 %, `TITLE:"rats"` 5 %,
  `TITLE:"mice"` 14 %. Fiksen tok 180-dagerspoolen fra 398 til **5 226** studier (13×).

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
  `exercise`: 2 dager → 0 treff, 30 dager → 24). Vinduet gir **5 226** studier (~29 nye i
  døgnet, målt 11. august 2026 etter `KW:`-fiksen over). 365 dager gir 12 241 (~33,5/døgn) —
  nesten samme *tilsig*, bare et større reservoar; halvårsvinduet er valgt bevisst så alt vi
  viser er publisert siste seks måneder.
- **Hele poolen hentes, ikke bare de nyeste.** `_fetch_all_pages()` paginerer via Europe PMCs
  `cursorMark` (`PAGE_SIZE = 100`, tak `MAX_FETCH_PER_CATEGORY = 600`, sortert
  `P_PDATE_D desc`). Tidligere hentet vi kun de 100 nyest indekserte per kategori, som utelot
  ~80 % av vinduet fra scoringen. **Etter `KW:`-fiksen er 600-taket bindende igjen** (poolen er
  916–1 591 per kategori), så vi ser de 600 nyeste per kategori — rikelig til å fylle køen,
  siden påfyll uansett bare kjører når køen er under `QUEUE_REFILL_BELOW`. Hev taket hvis du
  vil score hele vinduet.

**2. Lokal scoring (`_score_candidate`) — gratis grovsortering før Claude.** Rangerer alt som
er hentet. Poeng for studiedesign (`pubTypeList`), utvalgsstørrelse (log10, dempet),
tydelige effektmål (HR/RR/OR/CI/p — mangler de, trekkes det fra: da er det ingen «Resultat» å
skrive) og harde utfall. **Trekk fra** for smale pasientgrupper (`_NARROW_POPULATION` — «patients
with …» er det mest treffsikre signalet) og medikament-/apparat-/genetikkstudier (`_DRUG_TERMS`):
en RCT på trening hos slagpasienter sier lite om hva en frisk leser bør gjøre. Under `MIN_SCORE`
forkastes helt.

- **`MIN_SCORE = 3.0` — studier under terskelen settes aldri i kø.** Køen skal ikke fylles av
  materiale som må siles ut igjen ved hvert uttak.
- **Tilsiget er den harde grensen, og køen skjuler den ikke — den gjør den synlig.** Symptomet
  å se etter er at «ferdigskrevne igjen i kø» faller mot 0 flere dager på rad. Skjer det, er
  riktig fiks å utvide `LOOKBACK_DAYS`, heve `MAX_FETCH_PER_CATEGORY` eller senke `MAX_ITEMS`
  — **ikke** å senke terskelen.
  - **Sjekk alltid spørringen først.** Da køen blødde tom 1.–11. august 2026 (92 → 0 «venter
    på tekst» på ti dager; ingen forskningsbriefing 10. august, 1 studie 11.) så det ut som
    naturlig uttørking av reservoaret — tilsiget var ~2,2/døgn mot 5 viste. Det var det ikke:
    `MESH:"Humans"` holdt 92 % av materialet ute (se over). Køen var tom, ingenting var
    frosset (`research_seen_dois.json`: 297 `picked`, 0 `refused`; køen: 64 `rejected`) — men
    årsaken lå i spørringen, ikke i kapasiteten. Mål `hitCount` per kategori før du justerer
    knappene.
- **Scoringen kjører på FULLT abstract — kutt aldri før scoring.** `MAX_ABSTRACT_CHARS = 4000`
  brukes kun når prompten bygges (`build_candidates_text`). Tidligere ble abstractet kuttet til
  1200 tegn *før* scoringen, men Resultat-delen (HR/RR/CI/p, utvalgsstørrelse) står typisk etter
  1200 tegn og 97 % av abstractene er lengre. Det fjernet nøyaktig de signalene scoringen gir
  poeng for: 20. juli 2026 falt 52 kvalifiserte kandidater til **0**, og forskningsbriefingen
  uteble helt. Besparelsen var ~1,5 øre/dag.

**3. Claude skriver omtalene.** Format per studie:
`## [tittel](url)` + **Kategori** + **Metode** / **Resultat** / **Hva det betyr for deg** /
**Forbehold** — 3–4 setninger på de tre første. Nettsiden parser etikettene;
`splitResearch()` løfter Kategori ut som eget `category`-felt (`normalizeCategory()` godtar både
visningsnavn og slug). Heller færre enn svake.

- `research_items` i JSON-en har også `category` (kandidatens kilde-kategori).
- **Dedup — to nivåer, begge varige,** i `research_seen_dois.json`
  (`{doi: {last, picked, refused}}`; gammelt format = ren datostreng leses som `picked: true`).
  Vist i en briefing → blokkert
  `SEEN_RETENTION_DAYS = 400` dager (leseren skal **aldri** se samme studie to ganger).
  **Avvist av sikkerhetsklassifikatoren** (refusal) → `refused: true`, blokkert like lenge
  som picked — en refusal er deterministisk, og uten flagget kom samme abstract tilbake og
  betalte en ny bisect-runde med prober; refused-DOI-er lagres **også når kjøringen gir opp
  helt** (før exit). Studier vi har vurdert men ikke vist, står i **køen**, ikke her.
  Ligger i `BRIEFING_DATA_DIR` — **må persisteres** (volumet).
  Oppføringer som verken er `picked` eller `refused` blokkerer ingenting: det er restene av
  den gamle karantenen, og de døde ut av seg selv da `_is_blocked()` ble forenklet.
- **Legacy:** kategorien `medisin` produseres ikke lenger, men finnes i arkiverte briefinger —
  derfor ligger den fortsatt sist i `RESEARCH_CATEGORIES` (`web/src/lib/briefings.js`) og i
  `CATEGORY_LABELS`. Kategorien vises som badge/pill i `ResearchList.astro`.
- **Claude dropper av og til lenken i overskriften** (`## tittel](url)` uten innledende `[`,
  eller helt uten URL — målt: 2 av 5 studier 8. august 2026). `splitResearch()` tåler den
  manglende `[`, men uten URL i det hele tatt hadde studien verken kilde, favoritt-knapp
  eller plass i biblioteket. `studiesForDay(b)` (`web/src/lib/briefings.js`) kobler derfor
  `research_items` på studiene: først på URL, deretter **posisjonsfallback** når antallet
  stemmer og plassen ikke er tatt av et URL-treff — `research_items` bygges fra
  `picked_entries` i samme rekkefølge som markdownen settes sammen, så posisjon er trygg.
  Den er felles kilde for `ResearchList.astro`, `deriveStudy()` (`api/lagret.js`) og
  `libraryEntries()` (`library.js`), så en studie som vises også kan pinnes og finnes igjen.
- Gjenbruker hjelpefunksjoner fra `news_briefing.py` (bl.a. `store_briefing`).

### Tekstil-kunnskapsbasen (`textile_briefing.py` + `textile_topics.py`)

Målgruppe: **innkjøp til en klesbutikk** som skal selge plagg som er bedre for kroppen og
varer lenger. Se `PLAN-TEKSTIL.md` for bakgrunnen og hva som gjenstår.

**Systemet er akkumulerende, ikke dagsbasert.** Der forskningsbriefingen publiserer en dag
og er ferdig med studien, rulles hver studie her INN i 1–3 **emner** som blir stående og
vokser. En kjøring er seks steg, der de tunge hoppes over når de ikke trengs:

1. Last kunnskapsbase, kø og dedup-cache. 2. Prun køen. 3. **Påfyll** (kun under
`QUEUE_REFILL_BELOW = 40`). 4. **Claude skriver omtaler** (kun under
`WRITEUP_REFILL_BELOW = 8`, `WRITEUP_BATCH_SIZE = 8`). 5. **Rull inn i KB**
(`MAX_ROLLIN_PER_RUN = 5`) — aldri et API-kall. 6. **Syntetiser emner**
(`MAX_SYNTH_PER_RUN = 3`).

`--dry-run` kjører alt unntatt 4–6. Gratis, og eneste trygge måte å teste spørringene på.
`--seed` er engangsknappen (6 omtalerunder, 60 innrullinger, 20 synteser) som gjør basen
brukbar fra dag én i stedet for om tre måneder — den koster vesentlig mer enn en vanlig dag.

**`textile_topics.py` er registeret og eneste sannhet.** ~34 emner i fire grupper
(`fiber` / `behandling` / `kvalitet` / `miljo`), hvert med `slug`, `name`, `blurb` og
`terms` (engelske søkeord for lokal emnetildeling). Nettsiden har **ingen egen emneliste** —
den leser gruppenavn, dommer og styrkenivåer ut av KB-en, så et nytt emne her dukker opp på
tekstil.modr.no ved neste generatorkjøring, uten kodeendring i `web/`. **`slug` endres
ALDRI** etter publisering: den er identiteten til all evidens som er rullet inn under emnet.
Fjerner du et emne fra registeret, slettes ingenting — det merkes `retired` og vises under
«Utgåtte emner».

#### To kilder, fordi ett fagfelt ikke dekker spørsmålet

- **Europe PMC** (`PMC_QUERIES`, 4 kategorier) — hud, allergi, toksikologi.
- **OpenAlex** (`OPENALEX_QUERIES`, 4 kategorier) — tekstilteknikk, holdbarhet, LCA. Åpent
  API, ingen nøkkel, `mailto` for polite pool. Abstracts kommer som **invertert indeks**
  (ord → posisjoner) og settes sammen igjen i `_oa_abstract()`.

**Ikke fjern OpenAlex.** Slitestyrke, fargeekthet, levetid og LCA publiseres i tidsskrifter
som ikke er indeksert i MEDLINE; med bare PMC står to av de fire temaene permanent tomme.
Begge kildene normaliseres til samme artikkel-dict og går gjennom samme kø, scoring og prompt.

**RCT-kravet fra `research_briefing.py` gjelder IKKE her, og det er bevisst.**
Tekstilallergi dokumenteres gjennom patch-test-serier, kohorter og eksponeringsmålinger —
det finnes knapt randomiserte forsøk på om en polyestergenser gir eksem. Krever man RCT,
står emnene tomme. Kvalitetskravet er flyttet til den lokale scoringen og til
`**Forbehold:**`-avsnittet, som skal si hva designet ikke kan vise.

**`LOOKBACK_DAYS = 730`** — tekstilkjemi har ingen nyhetssyklus i det hele tatt. Vinduet er
satt så «status» i et emne hviler på noe skrevet nylig nok til å reflektere gjeldende
REACH-restriksjoner, ikke fordi feltet beveger seg fort.

#### Lokal scoring — to støytyper som MÅ straffes

Kildene er brede med vilje (vi kan ikke spørre per emne: «formaldehyde AND textile» gir 6
treff på to år), så utvalget skjer i `_score_candidate`. To lister bærer mest:

- **`_LAB_NOISE`** — materialforskning som aldri handler om noe man kan gå med: EMI-skjerming,
  superkondensatorer, sensorer, sårbandasjer, elektrospinning, og **ren syntese**
  («Solvent-Free Synthesis of a Phosphorus-Based Flame Retardant» lå på 7.-plass i køen før
  «synthesis of»/«preparation of» ble lagt til). −5,0 på tittelen, −1,0 i abstractet.
- **`_OFF_TARGET`** — miljøstudier om hvor forurensningen HAVNER (innsjø, sediment, fisk,
  drikkevann). Faglig gode, men de svarer på et annet spørsmål: vi skal velge et plagg, ikke
  kartlegge en innsjø. «Global patterns of lake microplastic pollution» kom på 2. plass
  fordi den treffer emnet `mikrofiberutslipp` og er full av tall. −4,0 på tittelen.

Emnetildeling (`assign_topics`) er inngangsbilletten: treff i **tittelen** gir primæremne,
treff kun i sammendraget sekundært. Uten et eneste primæremne trekkes 2,5 — det er nesten
alltid en artikkel som nevner et tekstilord i forbifarten. Målt 22. august 2026:
506 i kø, 1 714 forkastet av scoringen.

#### Kostnaden vokser med basen, ikke med tilsiget

Henting og scoring er gratis; omtaler koster tokens nøyaktig én gang per studie, noensinne.
**Syntesen** er den eneste posten som skalerer med hvor stor basen blir, og bremses av to
ting: et emne står ikke for tur før det har fått `SYNTH_PENDING_MIN = 2` nye studier (et
emne uten sammendrag i det hele tatt trenger bare `SYNTH_FIRST_MIN = 1`), og inputen kappes
til de `SYNTH_MAX_STUDIES = 14` nyeste omtalene. Uten begge ville et emne med 40 studier
under seg kostet 40 omtaler i input hver gang én ny kom inn.

Syntesen svarer med **ett JSON-objekt** (`summary`, `verdict`, `confidence`, `criteria`,
`questions`) som `_parse_synth()` plukker ut med en balansert-klamme-skanner og validerer
mot registeret. Kan svaret ikke tolkes, står emnet **uendret** — et halvt oppdatert oppslag
er verre enn et gammelt. KB-en lagres etter hvert emne, så en feil på emne 3 ikke koster de
to første.

#### Dommen og kravene

Hvert emne ender på en `verdict`: `unngaa` / `dokumenter` / `foretrekk` / `noeytral` /
`ukjent`. Prompten sier eksplisitt at `ukjent` skal brukes når evidensen er tynn — et ærlig
«vi vet ikke» er mer verdt enn en anbefaling som ikke bærer. `criteria` (0–4 per emne) er
setninger som skal kunne stå ordrett i en kravspesifikasjon, med `strength` per krav;
`questions` (0–3) er spørsmål å stille en leverandør. Begge samles på tvers av emner på
`/tekstil/krav`.

#### Filer på volumet — MÅ persisteres

- `textile_kb.json` — **kunnskapsbasen. Mister du den, er ALT tapt:** den er summen av hver
  omtale og hver syntese systemet noen gang har skrevet, og kan ikke regenereres uten å
  betale for alt på nytt. Skal med i backup (samme volum som `briefing-data`).
- `textile_queue.json` — kø. Går den tapt, bygges den opp igjen, men Claude-omtalene i den
  er betalt for.
- `textile_seen.json` — `{id: {last, rolled, refused}}`. Samme to-nivå-logikk som
  `research_seen_dois.json`: innrullet → aldri hentet igjen; refusal → blokkert like lenge
  (deterministisk, og uten flagget betaler man hele isoler-og-fjern-runden på nytt).

Studien lagres **én gang** i `kb["studies"]`; emnene refererer til den med id. En studie som
treffer tre emner finnes fortsatt bare ett sted.

### Tilskudds-kunnskapsbasen (`supplement_briefing.py` + `supplement_topics.py`)

Målgruppe: **samme leser som forskningsbriefingen** — longevity, og hva han selv kan gjøre.
Bor på **https://forskning.modr.no/tilskudd**, ikke på eget subdomene: samme leser og samme
spørsmål som resten av forskningen, så en fjerde side ville splittet lesingen uten å gi noe.

**Formålet er å kunne si NEI raskt**, og det er skrevet inn i begge systempromptene. De
fleste tilskudd gjør ingenting målbart for en frisk person; verdien ligger i å slippe å
prøve dem, og i å vite hvilke få som har tall bak seg, i hvilken dose, for hvem.
Tilskuddslitteraturen er systematisk skjevfordelt mot positive funn (små utvalg,
industrifinansiering), så uten den eksplisitte instruksen vil Claude skrive positivt om alt.

Arkitekturen er **akkumulerende**, som tekstil: kø → omtaler → innrulling → syntese. En
kjøring er syv steg, der de tunge hoppes over: 1. last, 2. prun, 3. påfyll (kun under
`QUEUE_REFILL_BELOW = 40`), 4. **mål bevis-gulv** (gratis), 5. omtaler (kun under
`WRITEUP_REFILL_BELOW = 8`, `WRITEUP_BATCH_SIZE = 8`), 6. innrulling
(`MAX_ROLLIN_PER_RUN = 5`), 7. syntese (`MAX_SYNTH_PER_RUN = 3`). `--dry-run` kjører alt
unntatt 5–7 og er gratis. `--seed` er engangsknappen (8 omtalerunder, 80 innrullinger, 25
synteser). `--floor` måler bevis-gulvet på nytt uansett alder.

#### Tre ting skiller den fra tekstil-generatoren

**1. RCT-kravet GJELDER her.** Tekstil dropper det fordi randomiserte forsøk på plagg knapt
finnes; tilskudd er det motsatte — feltet er fullt av RCT-er og metaanalyser, og samtidig
fullt av små sponsede studier. `_PMC_SUFFIX` er derfor identisk med `research_briefing.py`
(`KW:"Humans"` + RCT/metaanalyse/systematisk oversikt + `SRC:MED`). **Ikke senk det** — da
fylles basen med nøyaktig det materialet som driver hypen, og verktøyet som skulle beskytte
mot overselging blir leverandøren av den. Målt 22. august 2026 gir filteret **2 448**
kvalifiserende studier på 730 dager fordelt på registerets stoffer.

**2. Én spørring PER STOFF, ikke per gruppe.** `topic_query()` bygges av `probe`-feltet i
registeret, så registeret er både stoffliste OG søkespesifikasjon — et nytt stoff blir søkt
opp uten kodeendring. Grunnen til at det ikke er gruppert: slår man ni vitaminer sammen i én
spørring og kutter på `MAX_FETCH_PER_TOPIC`, er det alltid det største stoffet som overlever
kuttet. K2 (9 kvalifiserende studier på to år) ville aldri sett dagens lys ved siden av
D-vitamin (417). Kostnaden er 34 HTTP-kall, og de er gratis.

**3. Bevis-gulvet er en FUNKSJON, ikke en mangel.** Stoffene i gruppen `uavklart` gir null
treff gjennom kvalitetsfilteret: **BPC-157 har 193 publikasjoner og 0 kontrollerte
menneskestudier**, de øvrige peptidene 395 og 0, medisinsopp 2 374 og 3.
`measure_evidence_floor()` teller begge tallene med to gratis `hitCount`-kall per stoff (kun
for stoffer uten evidens, målt på nytt etter `FLOOR_RECHECK_DAYS = 30`) og lagrer dem på
stoffet. `EvidenceFloor.astro` viser dem som selve oppslaget. **Ikke fjern disse stoffene
fordi de er tomme** — fraværet av evidens er svaret for nettopp de stoffene som markedsføres
hardest, og et stoff uten oppslag lar leseren tro at spørsmålet ikke er stilt.

#### Registeret (`supplement_topics.py`)

34 stoffer i seks grupper (`basis` / `ytelse` / `longevity` / `sovn` / `tarm` / `uavklart`).
Per stoff: `slug` (**endres ALDRI** etter publisering — identiteten til all evidens),
`name`, `kind`, `blurb`, `claim`, `terms`, `probe`, og valgfritt `floor_verdict`.

- **`claim` er ikke pynt.** Det er markedsføringspåstanden, og syntesen får den i prompten
  med instruks om å sette den opp mot hva studiene faktisk har målt (`measured`-feltet).
  For NAD-forløpere er påstanden «klarhet i hodet» mens utfallet i studiene er
  NAD+-konsentrasjon i helblod — å vise de to ved siden av hverandre (`.sclaim` øverst på
  hvert oppslag) er oppslagsverkets viktigste enkeltgrep.
- **`floor_verdict`** (kun `bpc-157` og `andre-peptider`, begge `risiko`) er dommen stoffet
  har så lenge det ikke finnes kvalifiserende evidens. Den utledes **ikke** av studier, men
  av regulatorisk status og av at stoffet injiseres uten humane sikkerhetsdata — derfor står
  den i registeret og ikke hos Claude. Uten feltet faller et tomt stoff til `ukjent`, som er
  riktig for et harmløst dårlig studert stoff (glysin) og feil for et gråmarkedspeptid:
  «ukjent» leses som «kanskje verdt et forsøk». `_load_kb()` håndhever den hver kjøring helt
  til stoffet faktisk får evidens og en syntese overtar dommen.

#### Domsaksen — fem nivåer, og to av dem MÅ holdes fra hverandre

`ta` / `vurder` / `dropp` / `risiko` / `ukjent`.

**`dropp` og `ukjent` er bevisst forskjellige dommer.** «Godt undersøkt, og effekten er
omtrent null» (multivitamin) og «vi vet ikke» er helt ulike svar, og et oppslagsverk som
slår dem sammen er verdiløst — `dropp` er en STERK konklusjon. Prompten sier det eksplisitt.
**`risiko` er skilt fra begge** fordi et uregulert peptid uten sikkerhetsdata ikke skal kunne
leses som «kanskje verdt et forsøk». De har også ulik farge i CSS: `dropp` er nøytral grå,
`risiko` er rød — å gi dem samme rødt ville gjort et virkningsløst multivitamin like
alarmerende som et gråmarkedspeptid.

#### Syntesen

Ett JSON-objekt per stoff, parset av `_parse_synth()` med samme balanserte-klamme-skanner som
tekstil, og validert mot registeret. Kan svaret ikke tolkes, står stoffet **uendret**.
Feltene: `summary`, **`measured`** (påstand mot måling — det viktigste), `verdict`,
`confidence`, `who` (skiller mellom påvist mangel og normal status — for de fleste vitaminer
er det hele forskjellen), `dose` (0–3, dosen brukt i STUDIENE, ikke på boksen, med `strength`
per rad), `interactions` (0–3) og `changes_verdict` (0–3, hva som ville endret dommen —
det leseren skal se etter framover).

#### Lokal scoring — to støytyper som MÅ straffes

Spørringene er presise (én per stoff), men presis er ikke relevant: D-vitamin gir 417 treff
og flertallet handler om graviditet, spedbarn, dialyse eller husdyr.

- **`_OFF_POPULATION`** — feil populasjon. Vitaminlitteraturen domineres av svangerskap,
  spedbarn og intensivmedisin. God forskning, feil spørsmål: leseren er en frisk voksen.
  Uten lista ville D-vitamin-oppslaget blitt skrevet på svangerskapsstudier. −4,0 på
  tittelen, −1,0 i abstractet. Dyre-/in vitro-ord står også her, fordi oversiktsartikler
  slipper gjennom `KW:"Humans"`.
- **`_NOT_A_SUPPLEMENT`** — stoffet er en biomarkør, en analysemetode eller en
  infusjonsbehandling, ikke noe man kan kjøpe og ta. «Serum zinc as a prognostic marker in
  sepsis» treffer stoffet `sink` perfekt og er helt ubrukelig. −4,0 på tittelen.

I tillegg gir `_DOSE_PATTERNS` +1,2 for oppgitt dose og −1,0 uten: uten dose kan omtalen
ikke si hva funnet faktisk gjelder. `MIN_SCORE = 3.0`.

#### Filer på volumet — MÅ persisteres

- `supplement_kb.json` — **kunnskapsbasen. Kan ikke regenereres** (summen av hver omtale og
  hver syntese). Ligger på `briefing-data`, altså dekket av backup-kommandoen.
- `supplement_queue.json` — kø. Bygges opp igjen, men omtalene i den er betalt for.
- `supplement_seen.json` — `{id: {last, rolled, refused}}`, samme to-nivå-logikk som
  `textile_seen.json` og `research_seen_dois.json`.

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
- **Ruter (tilskudd):** `/forskning/tilskudd` (oversikt), `/forskning/tilskudd/stoff/<slug>`
  (ett stoff), `/forskning/tilskudd/protokoll` (alle doseringer + ikke-lista +
  interaksjoner), `/forskning/tilskudd/studier` (alle studier, server-side søk/filter via
  `?q=&stoff=`). Ligger UNDER forskning-prefikset, så host-rutingen i `middleware.js`
  trengte ingen endring — på subdomenet blir URL-en `forskning.modr.no/tilskudd`.
  Stoffsidene ligger i undermappen `stoff/` og ikke rett under `tilskudd/`, slik at en
  fremtidig `slug` som «protokoll» eller «studier» ikke kan kollidere med en fast rute.
- **Ruter (tekstil):** `/tekstil` (oversikt), `/tekstil/emne/<slug>` (ett oppslag),
  `/tekstil/krav` (samlet kravspesifikasjon), `/tekstil/studier` (alle studier, server-side
  søk/filter via `?q=&emne=`). Host `tekstil.*` rutes internt hit, `locals.tbase` er
  lenkeprefikset. `Base.astro` tar `site="tekstil"` — den headeren har **ikke** dato eller
  hopp-rad (oppslagsverk, ikke dagsbriefing).
- **Ruter (felles for alle vertsnavn):** `/lagret` og `/api/*`. Disse skrives **ikke** om av
  middleware (`isShared`) — de finnes kun på rot-nivå og spenner over sidene.
- `middleware.js` er datadrevet (`SITES`-lista): et nytt subdomene = én linje der.

### Bibliotek (`/lagret`) og favoritter («pin»)

`/lagret` er **biblioteket**: alle nyhetspunkter, alle studier og alle boktips som noen gang
er vist, søkbare — pluss favorittmerkede gåter og quizspørsmål. Type-faner, filter, søk, notat, tagger, paginering
(40 per side). Se `PLAN-LAGREDE-STUDIER.md` for den opprinnelige planen.

- **To lag, ikke ett.** Biblioteket **utledes fra briefing-arkivet ved forespørsel**
  (`web/src/lib/library.js`) — ingenting kopieres inn i lageret. Briefinger er immutable, så
  arkivet er fasiten; biblioteket kan ikke komme i utakt og fylles av seg selv hver dag uten
  at web trenger skrivetilgang til `/data`. `saved.json` inneholder **kun favorittene**
  (merking + notat + tagger + repetisjonstilstand).
- **Stjernen er en favorittmarkering, ikke inngangsbilletten.** Fram til 24. juli 2026 var
  det motsatt: en studie du ikke pinnet, fantes ikke noe sted å søke i. Nå er ★ et *filter*
  (`?favoritt=1`) over alt vi har vist. Kun favoritter får notat, tagger og
  «Dagens repetisjon» — en repetisjonskø over 200+ studier hadde vært støy.
- **Cachen** i `library.js` nøkles på `briefingStamp()` (antall dagsfiler + sum av mtime).
  Filnavn alene holder **ikke**: begge generatorene skriver inn i samme dagsfil, så dagens
  fil endres etter at den først er lest.
- **Indeksert:** `news_md` (nyhetspunkter), `research_md` (studier) og `learning.books`
  (boktips). Bokas nøkkel er
  `book:<sha1(tittel + forfatter)>` — **uten år**, ellers ville en ny utgave blitt en ny
  oppføring. `journal`-feltet bærer «Forfatter · År», så forfatteren er søkbar.
- **Nyhetspunkter er egen type (`news`, 📰).** Enheten er ETT punkt i en seksjon, ikke hele
  seksjonen — det er den enheten leseren forholder seg til. `splitNewsSections()` returnerer
  derfor `points: [{ index, section, title, url, text, html, pinnable }]`, der `index` er
  **global for hele briefingen** (samme identifikasjon som gåter/quiz: `{date, index}`), og
  `newsPoints()` er den flate lista. Overskriften er lenketeksten når punktet åpner med en
  lenke; ellers første setning (kuttet på 160 tegn). Ligger lenken lenger inne i punktet
  (vanlig i eldre briefinger), brukes den likevel som kilde-URL. Punkter under 40 tegn er
  plassholdere («Ingen viktige hendelser.») → `pinnable: false`, ingen stjerne, ikke i
  biblioteket. ~19 punkter/dag gjør `news` til den klart største typen (466 mot 265 studier
  ved innføringen 5. august 2026) — type-fanen er derfor den viktigste filtreringen.
- **Nyheter er IKKE med i repetisjonspoolen** (`reviewPool()` filtrerer dem bort), selv om de
  står i biblioteket: en tre uker gammel nyhet er ikke noe å repetere, og med sitt volum ville
  de utgjort flertallet av poolen og fortrengt studiene og boktipsene kortet er til for. Har
  du favorittmerket et nyhetspunkt, kommer det likevel tilbake — favoritter går gjennom
  `saved.json` i `dailyReview()`, ikke gjennom poolen.
- **Gåter/quiz indekseres ikke i sin helhet** (banken er hundrevis av spørsmål) — de er med
  kun når de er favorittmerket. **Podkast-rådene er ikke pinnbare**: de er knyttet til én
  episode, ikke til noe du skal finne igjen. Pin-knappen for gåter/quiz/bøker trenger
  indeks i dagsfila; den slås opp med `resolveIndex()` for de radene som faktisk vises.
- **Eget volum, ikke arkivet.** `saved-data:/state` (rw) ved siden av `briefing-data:/data:ro`.
  Nettappen er eneste prosess eksponert mot internett og skal **aldri** kunne skrive inn i
  briefing-arkivet. `SAVED_DIR=/state` i container; default `state/` lokalt (gitignored).
- **`web/src/lib/saved.js`** — lagring. Alle mutasjoner går gjennom **én seriell promise-kø**:
  Node er én prosess, men samtidige POST-er kan ellers interleave read-modify-write og miste
  en lagring. Skriving er atomisk (`.tmp` + `rename`), som `store_briefing()`.
- **Fullt øyeblikksbilde, ikke referanse.** Briefinger er immutable, så det er ingenting å
  synkronisere; et snapshot fjerner en feilklasse (manglende arkivfil, drift i
  `splitResearch()`) for ~2 KB per studie. `date` + DOI beholdes som tilbakelenke.
- **ID-en gir idempotens:** `study:<doi>` (globalt unik), `news:<sha1(kilde-URL)[0:12]>` (samme
  sak dekket to dager på rad blir én oppføring; punkter uten lenke hasher tittelen),
  `riddle:`/`quiz:<sha1(spørsmål)[0:12]>`
  (innholdshash — de har ingen ID, og hashen overlever quizens repetisjonsmekanikk). Samme sak
  lagret to ganger blir én oppføring.
- **Innholdet utledes SERVER-SIDE** fra arkivet (`deriveStudy()` / `deriveNews()` /
  `deriveIndexed()` i `api/lagret.js`) — klienten sender kun `{date, url}` for studier og
  `{date, index}` for nyheter/gåter/quiz/bøker (indeksen er stabil fordi briefinger er immutable). Ellers kunne enhver med
  kodeordet plantet vilkårlig HTML i lageret, som senere rendres med `set:html`. Gåte-/
  quiz-tekst er ren tekst og escapes ved lagring.
- **`web/src/lib/auth.js`** — lesing er åpent for alle, kun skriving krever kodeord.
  `SAVE_PASSPHRASE` sendes inn i compose som **enkeltvariabel**, bevisst ikke `env_file`
  (som ville gitt web-containeren `ANTHROPIC_API_KEY` den ikke trenger). Cookien er
  `<utløp>.<HMAC-SHA256(utløp, kodeord)>` — HttpOnly/Secure/SameSite=Lax, ett år, ingen
  sesjonslagring. Bytter du kodeord, blir alle utstedte cookies ugyldige automatisk.
  `crypto.timingSafeEqual` + rate-limiting (10 forsøk / 15 min / IP). **Uten `SAVE_PASSPHRASE`
  svarer skriveendepunktene 503 og pin-knappene skjules** — funksjonen feiler synlig, ikke åpent.
- **Filtrering og søk er server-side** via URL-parametre
  (`?q=&type=&kategori=&tag=&sort=&favoritt=&side=`): virker
  uten JS, URL-ene blir delbare/bokmerkbare, og det skalerer forbi noen tusen oppføringer.
- **Komponenter:** `SaveButton.astro` (ren markup; `saved` kommer fra serveren så stjernen er
  fylt i første paint) + `SaveRuntime.astro` (én delegert klikk-lytter for hele siden,
  kodeord-dialog ved 401, **angre-toast** ved avpinning — som sletter notat og tagger).
  Tagger normaliseres til små bokstaver, ellers får man «protein»/«Protein»/«proteiner» som
  tre filtre innen en måned.
- **«Dagens repetisjon»** (`dailyReview()` i `library.js` + `ReviewCard.astro`): løfter ÉN
  tidligere vist oppføring tilbake på nyhetsforsiden — kun på dagens briefing, ikke i arkivet.
  Kun én om gangen — en liste med ti «husk denne» blir ignorert, én blir lest.
  - **Poolen er hele arkivet** (`reviewPool()` = biblioteket uten nyhetspunkter +
    `archiveDrills()`, som indekserer `riddles`/`quiz` fra dagsfilene): studier, boktips,
    gåter og quiz, ~550 oppføringer. Gåter/quiz er med **her**, men fortsatt ikke i biblioteket på `/lagret`.
    Alt yngre enn `REVIEW_MIN_AGE_DAYS = 7` holdes utenfor — du leste det nettopp.
  - **Valget er deterministisk per dato** (FNV-1a-hash av `<dagnummer>:<id>`, høyeste
    vinner): samme dag gir samme kort ved hver SSR-render, ny dag gir nytt kort, og ingen
    ny state-fil å persistere eller ta backup av.
  - **Favoritter får hver tredje dag** (`ord % 3`), rotert seg imellom, med det utvidende
    intervallet fra quizbanken: `REVIEW_INTERVALS = [7, 30, 90, 180]` dager, forfaller når
    alderen ≥ `intervals[reps]` (klemt). «Repetert»-knappen bumper `reps`/`lastReview` og
    vises kun for favoritter (den er en skriving). Oppføringer lagret før feltene fantes
    leses som `reps = 0` (bakoverkompatibelt).
  - **Ikke gå tilbake til «mest forfalt vinner» over kun `saved.json`.** Fram til 5. august
    2026 gjorde `dueItem()` det, og siden `reps` bare bumpes når man trykker «Repetert», var
    tilstanden absorberende: den eldste favoritten (én gåte fra 20. juli) ble vist hver
    eneste dag, og poolen var 5 favoritter i stedet for ~550 viste oppføringer.
- **Eksport:** `/api/eksport?format=md|json` respekterer gjeldende filtre, så det du ser på
  `/lagret` er det du får ut. Gjør at listen ikke er et fengsel, og fungerer som ekstra backup.
- **Komponenter:**
  - `BriefingView.astro` — deler topp-grid + gåter/quiz + nyhetskort mellom forside og
    enkeltdag. Nyhetsseksjonene rendres punkt for punkt (`s.points`) med favoritt-stjerne per
    punkt — ikke som én `set:html`-blokk; seksjoner uten punktliste faller tilbake til det. Rekkefølge: vær/marked → nyheter → Inspirasjon → Quiz → Gåter →
    Repetisjon → Til ettertanke → forskning (kun tittelliste med kategori-badge, lenker til
    `FORSKNING_URL`). **Rekkefølgen ligger kun her** (og speiles i `.jumpnav` i `Base.astro`) —
    endrer du den, må hopp-raden endres i samme slengen, ellers hopper leseren i utakt med siden.
    `BrannCard` rendres inne i Bergen og Vestland-kortet (tittelmatch `/bergen/i`).
    Seksjonene har anker-id-er (`#vaer-marked`, `#nyheter`, `#gaater`, `#quiz`, `#inspirasjon`,
    `#refleksjon`) som headerens hopp-rad bruker (`.jumpnav` i `Base.astro`, kun
    nyhetssiden): 0,5 s scroll-animasjon; lenker uten mål på siden skjules av
    inline-skriptet, så raden forsvinner helt på f.eks. arkivsiden.
  - `LearningCard.astro` — «Dagens inspirasjon»: podcast-råd (🎧) og boktips (📚) fra
    `learning`-feltet, to kort i `cards-grid`. Boktipsene har favoritt-stjerne
    (`SaveButton`, type `book`); podkast-rådene har ikke. Ingen egen klient-JS —
    stjernen bruker `SaveRuntime` som `BriefingView` allerede inkluderer.
  - `ReflectionCard.astro` — «Til ettertanke»: 1–2 åpne refleksjonsspørsmål fra
    `reflection`-feltet (📰 fra nyhetene / 🎧 fra inspirasjonen), accent-tonet kort. Ren HTML.
  - `BrannCard.astro` — SK Brann-blokk fra `brann`-feltet: tabellplassering, neste kamp
    (norsk dato/klokkeslett via Intl, følger container-TZ), siste resultat (farget utfall)
    og nyhetslenker. Ren HTML uten klient-JS.
  - `ResearchList.astro` — full forskningsvisning (forskning-sidene): **panorama-kort**, ett
    per studie i full bredde. Fra 1000px deles kortet i en venstreskinne (kategori-badge,
    tittel, tidsskrift · publiseringsdato, doi, ★) og avsnittene i to spalter — Metode/Resultat
    over Betydning/Forbehold. Under 1000px stables alt (mobilvisningen er uendret).
    **Ikke gå tilbake til én grid per kategori** (`.research-grid--wide`, `minmax(420px, 1fr)`
    → 3 kolonner på 1400px): dagene har 5 studier fordelt på 3–4 kategorier, altså 1–2 kort
    per grid, så hver rad hadde permanente hull — og på dager med én studie fylte siden en
    tredjedel av skjermen. Grupperingen ligger nå i **sorteringen** (`RESEARCH_CATEGORIES`),
    kategori-badgen på kortet og pill-raden `.cat-pills` øverst (fordeling + hopp-nav, vises
    kun ved 2+ kategorier). Anker-id `s<i>` settes **før** sorteringen (i = posisjon i
    `research_md`), ellers brekker lenkene fra nyhetssiden. «Resultat» har egen, nøytral
    fremheving (tallene StatsGuide finnes for); «Hva det betyr for deg» beholder accent-fargen.
    Kilde-metadata og URL kommer fra `studiesForDay()`, ikke `splitResearch()` alene.
    Forsiden og dagsiden har dagnavigasjon (`.daynav`, `researchNeighbors()` hopper over
    dager uten studier). `.research-grid--wide` brukes fortsatt av `/lagret`.
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
  - `TextileVerdict.astro` / `TextileStudy.astro` — dom-merket og studiekortet på
    tekstilsidene. Studiekortet splitter omtalen på de merkede etikettene (Metode / Funn /
    Hva det betyr for innkjøp / Forbehold) — «Hva det betyr for innkjøp» får accent-farge og
    full bredde, de tre andre går i to spalter over 1000px.
  - `ThemePicker.astro` — temavelger i headeren.
- **`src/lib/briefings.js`:** `listDates()`, `getBriefing(date)`, `renderMarkdown()` (marked),
  `getMarketHistory()`, `splitNewsSections(news_md)` → `[{ emoji, title, html, points }]`
  (per «## »-seksjon; håndterer flagg-emoji som 🇳🇴; `points` = seksjonens enkeltpunkter,
  se biblioteket) og `newsPoints(news_md)` (samme punkter flatet ut), `splitResearch(research_md)` →
  `[{ title, url, category, parts, html }]` (`parts` = de merkede avsnittene Metode/Resultat/
  Hva det betyr for deg/Forbehold — og Hva som ble gjort/Relevans i arkiverte briefinger;
  `category` løftes ut av **Kategori**-etiketten via `normalizeCategory()`; `html` er fallback),
  `studiesForDay(b)` (splitResearch + kilde-metadata fra `research_items` + `anchor`; se
  forskningsseksjonen), `researchNeighbors(date)` (forrige/neste dag med studier),
  `formatDateNo()`/`weekdayNo()` (lokaltid-trygg norsk dato).
- **`src/lib/supplements.js`:** speiler `textile.js` (`getKb()` med mtime+størrelse-cache,
  `topicsByKind()`, `getTopic()`, `topicStudies()`, `allStudies()`), pluss `doseGroups()`
  (protokollen, gruppert på **dom** og ikke evidensstyrke — den skal inn i et medisinskap,
  ikke ut til en leverandør), `skipList()` (stoffene med `dropp`/`risiko` — «ikke-lista»,
  som står FØRST på oversikten fordi den er den korteste veien til nytte),
  `interactionList()` og `verdictCounts()`. Sorteringen innad i en gruppe er på **dom**, ikke
  på antall studier som i tekstil: her er et stoff med null studier og målt bevis-gulv ofte
  det mest interessante på siden. Sti fra `SUPPLEMENT_KB` (`/data/supplement_kb.json` i prod).
  Mangler filen, returnerer alt tomt — ingenting kaster.
- **`src/lib/textile.js`:** `getKb()` (cache nøklet på mtime+størrelse — filen skrives
  atomisk, men mtime alene fanger ikke to skrivinger samme sekund), `topicsByKind()`,
  `getTopic()`, `topicStudies()`, `allStudies()`, `criteriaGroups()`, `supplierQuestions()`,
  `verdictCounts()`. Mangler `textile_kb.json` (før første kjøring), returnerer alt tomt —
  ingen kaster. Sti fra `TEXTILE_KB` (satt til `/data/textile_kb.json` i prod-Dockerfile).
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
- **`textile_kb.json` og `supplement_kb.json` kan ikke regenereres.** Køen bygges opp igjen av seg selv, men
  kunnskapsbasen er summen av alt Claude noen gang har skrevet i dette systemet. Den ligger
  på `briefing-data`, så den er dekket av backup-kommandoen — men vit hva du sletter.
- **Persistente data på volumet:** `briefings/<dato>.json`, `research_seen_dois.json`,
  `research_queue.json`, `quiz_seen.json`, `riddles_seen.json`, `learning_seen.json`,
  `textile_kb.json`, `textile_queue.json`, `textile_seen.json`, `supplement_kb.json`,
  `supplement_queue.json` og `supplement_seen.json` MÅ ligge
  i `/data` (`BRIEFING_DATA_DIR=/data`), ellers tomt arkiv + nullstilt dedup. Mister du
  `research_queue.json`, bygges den opp igjen ved neste kjøring — men de ferdigskrevne
  Claude-omtalene i den er betalt for og må skrives på nytt.
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
