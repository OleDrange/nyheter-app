# Nyhetsbriefing — CLAUDE.md

Denne fila dokumenterer **det som ikke kan leses ut av koden**: målte tall bak et valg,
alternativer som er prøvd og forkastet, invarianter som er dyre å bryte, og drift som ikke
finnes i repoet. Alt annet — komponenter, feltlister, funksjonssignaturer, promptdetaljer —
leses direkte i kilden. Legger du til noe her, spør først: *ville neste utvikler funnet dette
ved å lese koden?* Er svaret ja, hører det ikke hjemme her.

## Hva appen er

Daglig briefing-app på VPS-en `MODR` (`/root/nyheter-app`, remote
`git@github.com:OleDrange/nyheter-app.git`, default-branch **`master`**). To deler, frikoblet
via JSON på et delt Docker-volum:

- **Generator** (Python, cron 05:00) — fire script: `news_briefing.py` (nyheter, vær, marked,
  quiz, gåter, inspirasjon, refleksjon, Brann), `research_briefing.py` (forskning — henter,
  scorer og publiserer fra kø; **omtalene skrives ukentlig i Claude Code via skillen
  `/forskning-uke`**, ingen API-kostnad), `supplement_briefing.py` og `textile_briefing.py`
  (to kunnskapsbaser, **ikke** dagsbaserte).
- **Nettside** (`web/`, Astro 5 SSR på Node) — leser JSON ved hver forespørsel. Nytt *innhold*
  vises uten rebuild; *kodeendringer* krever rebuild.

Samme app serverer **tre** nettsteder, host-rutet i `web/src/middleware.js`:
**nyheter.modr.no**, **forskning.modr.no** (tilskudds-basen bor under denne:
`forskning.modr.no/tilskudd`) og **tekstil.modr.no**.

## Utviklingsflyt

Utvikling skjer **direkte i dette repoet på serveren**. Claude skriver koden, committer og
pusher til `master`, og deployer selv. Testing skjer **live** — flyten er ikke ferdig før
nettsiden kjører den nye koden.

| Endring | Deploy |
|---|---|
| `web/` | `docker compose build web && docker compose up -d web` |
| `*.py` | `docker compose build generator` (neste cron-kjøring bruker ny kode) |
| Dokumentasjon | kun commit + push |

Rollback: `git revert <commit> && git push`, deretter rebuild + `up -d web`.

## Fallgruver

- **`master`, ikke `main`.**
- **`docker compose build` bygger IKKE generatoren** (`profiles: batch` → hoppes stille over).
  Bruk alltid `docker compose build web generator`. Klassisk symptom på gammel generator-kode:
  vær uten `hourly` → nettsiden faller tilbake til statisk stat-grid.
- **Generator-containeren har CMD, ikke ENTRYPOINT** — `docker compose run generator <cmd>`
  kjører `<cmd>` i stedet for briefingen; uten `<cmd>` kjøres hele briefingen (og bruker
  Claude-kvote). Inspiser data med `docker compose exec web …`, aldri `run generator`.
- **To volumer, ikke ett.** `briefing-data` (generator rw, web **ro**) og `saved-data` (kun
  web, rw). Backup må dekke begge.
- **Kunnskapsbasene kan ikke regenereres.** `textile_kb.json` og `supplement_kb.json` er summen
  av hver omtale og hver syntese Claude noen gang har skrevet. Køene bygges opp igjen av seg
  selv, men omtalene i dem er betalt for.
- **Persistent på volumet** (`BRIEFING_DATA_DIR=/data`) — ellers tomt arkiv + nullstilt dedup:
  `briefings/<dato>.json`, `research_seen_dois.json`, `research_queue.json`, `quiz_seen.json`,
  `riddles_seen.json`, `learning_seen.json`, `textile_kb.json`, `textile_queue.json`,
  `textile_seen.json`, `supplement_kb.json`, `supplement_queue.json`, `supplement_seen.json`.
- **Tidssone:** cron-tidspunkt = **verts**-TZ (`CRON_TZ` virker ikke på Debian); innholdets
  dato/værvinduer = container-TZ. Begge `Europe/Oslo`.
- **`docker-entrypoint.sh` må ha LF** (sikret av `.gitattributes`).
- **Caddy har `admin off`** → reload kun via `docker compose restart caddy`.

## Drift (VPS)

- **`docker-compose.yml`:** `web` alltid oppe, på eksternt `web`-nett med alias `nyheter-web`,
  intern port **8080** (`HOST=0.0.0.0` er plattformkrav). `generator` er batch
  (`profiles: ["batch"]`), kjøres av cron, `env_file: .env`.
- **Cron** — **én linje for alle generatorene**:
  ```cron
  0 5 * * * cd /root/nyheter-app && /usr/bin/docker compose run --rm generator >> /root/nyheter-cron.log 2>&1
  ```
  Kommandoen er tom, så CMD kjører: `docker-entrypoint.sh` tar nyheter → forskning → tilskudd
  → tekstil → healthcheck etter hverandre med myk feil mellom hvert steg. **En ny generator
  legges til DER, ikke som en ny crontab-linje** — to samtidige `docker compose run` mot samme
  volum er unødvendig risiko. Rekkefølgen er bevisst: nyhetsbriefingen er det leseren venter
  på kl. 05, og kunnskapsbasene akkumulerer og har ingen dagsfrist.
- **Proxy:** Caddy i `~/modr-proxy`. De tre domenene har identiske `reverse_proxy
  nyheter-web:8080`-blokker (samme container — appen ruter på host). **Korte alias må være
  301-redirect, ikke proxy:** en `reverse_proxy` på `t.modr.no` ville sendt Host-headeren
  `t.modr.no` inn i appen, som ikke matcher noe prefiks i `middleware.js` — og leseren hadde
  fått nyhetssiden. Nytt subdomene krever DNS A-post → serverens IP før Caddy kan hente
  sertifikat. Validér før reload:
  `docker compose exec -T caddy caddy validate --config /etc/caddy/Caddyfile`.
- **SSH — kun nøkkel.** `/etc/ssh/sshd_config.d/10-hardening.conf` setter
  `PasswordAuthentication no` + `AllowUsers root`. **Root er eneste konto med skall**, så en
  klient som gjetter brukernavn fra lokal PC (VS Code Remote-SSH gjør det) treffer en
  ikke-eksisterende bruker og får et passord-spørsmål som aldri kan lykkes — bruk `root@` i
  `~/.ssh/config`. Endrer du konfigen: valider med `sshd -t` FØR `systemctl restart ssh`, og
  bekreft nøkkelinnlogging fra en ANNEN forbindelse før du lukker den du sitter i.
  `ssh.service` er `disabled` — det er riktig, `ssh.socket` er den som starter ved boot.
  fail2ban kjører på `sshd`-jail; `ignoreip` inneholder en dynamisk hjemme-IP som kan bli
  foreldet. **Ingen ufw, bevisst:** Docker skriver iptables direkte og går utenom ufws
  INPUT-kjede, så en regel der ville gitt falsk trygghet for 80/443 og kunne stengt 22 ute.
- **Inspisere data:** `docker compose exec web ls -la /data/briefings`.
- **Logger:** generator → `/root/nyheter-cron.log`; web → `docker compose logs -f web`.
- **Backup — begge volumer:**
  ```bash
  docker run --rm -v nyheter-app_briefing-data:/d -v /root:/b alpine tar czf /b/nyheter-backup.tgz -C /d .
  docker run --rm -v nyheter-app_saved-data:/d    -v /root:/b alpine tar czf /b/lagret-backup.tgz  -C /d .
  ```
- **Feilvarsling:** `healthcheck.py` (sist i entrypoint) sjekker at dagens JSON har `news_md`.
  Feil → `ALERT_WEBHOOK_URL`; suksess → ping `HEARTBEAT_URL` (dead-man's-switch som fanger at
  cron aldri kjørte). Begge valgfrie.

## Miljøvariabler (`.env`)

`ANTHROPIC_API_KEY` påkrevd. `ALERT_WEBHOOK_URL`/`HEARTBEAT_URL` valgfrie.
`NOTION_API_KEY`/`NOTION_PARENT_PAGE_ID` er **legacy og holdes tomme** — en ugyldig ikke-tom
nøkkel gir en rød støylinje hver kjøring. `SAVE_PASSPHRASE` sendes til `web` som
**enkeltvariabel**, bevisst ikke `env_file` (som ville gitt web-containeren en API-nøkkel den
ikke trenger).

## Generator — felles designvalg

- **Modell: `claude-sonnet-5`** i alle fire generatorene (byttet 11. september 2026 av
  kostnadshensyn; `claude-opus-5` 1.–11. september, `claude-sonnet-4-6` før det). Estimert
  forbruk på Opus 5 var ~$1,65/dag, der syntesene i de to kunnskapsbasene alene sto for
  ~halvparten — tenketokens faktureres som output. **Sonnet 5 tenker som standard** (som
  Opus 5), og to ting følger av det:
  - **`max_tokens` er et felles tak for tenking OG svar.** Et for lavt tak gir **ingen
    feilmelding** — svaret kappes midt i, og JSON-parsingen faller til myk feil. Alle takene
    ble hevet i samme slengen da modellen ble byttet; hever du batch-størrelser, må taket følge.
  - **`content[0]` er ikke nødvendigvis tekst** — første blokk kan være en thinking-blokk.
    Slå sammen tekstblokkene (`_text_of()`).
  - **Unntak: refusal-probene** (`CLAUDE_PROBE_MAX_TOKENS = 16`) kjører med
    `thinking={"type": "disabled"}` — de spør kun ja/nei om sikkerhetsklassifikatoren slår til,
    og tenking ville spist hele budsjettet. (Opus 5 godtar avslått tenking kun ved `effort`
    `high` eller lavere — relevant om modellen byttes tilbake.)
  - **Pris:** $2/$10 per million (Opus 5: $5/$25), og tenketokens faktureres som output.
- **Forskning bruker aldri API-et fra cron** (fra 20. september 2026): entrypointet kjører
  alltid `research_briefing.py --no-claude`, som publiserer 6/dag fra ferdigskrevne i køen
  og utelater feltet (myk feil) når den er tom. Skriving skjer i **`/forskning-uke`** (se
  `.claude/skills/forskning-uke/SKILL.md`): `--refill` → `--propose 9` → Claude Code skriver 42
  omtaler → `--import-writeups`. Ingen godkjenning fra leseren (prøvd og valgt bort 20.
  september 2026). Køen + `seen` er hukommelsen, så ingen studie vurderes to ganger; vrakede
  studier blir `rejected`-gravsteiner.
- **Kostnadspause: `PAUSE_KNOWLEDGE=1` i `.env`** (satt 11. september 2026): tilskudd/tekstil
  hoppes over helt. Slå på igjen ved å fjerne linjen — leses ved hver `docker compose run`,
  ingen rebuild. Køene og `seen` står urørt i pausen.
- **Myke feil:** én RSS-feed, vær- eller markedsfeil stopper ikke resten av kjøringen. Hver
  seksjon som feiler, utelates fra dagsfila framfor å velte kjøringen.
- **Streaming** til terminal, ikke bufret.

## `news_briefing.py`

- **RSS hentes med `httpx`** (browser-UA + `follow_redirects`), så `feedparser.parse(resp.content)`.
  Mange norske aviser blokkerer feedparsers bot-UA — **ikke bytt tilbake til
  `feedparser.parse(url)`**. Bekreft HTTP 200 + gyldig XML før du legger til en feed.
- **Ikke prøv disse igjen:** Reuters (RSS stengt), Finansavisen (ingen RSS), Oslo Børs (kun
  NewsWeb API; dekkes via E24 Børs), forskning.no (JS-rendret), samt de utdaterte URLene
  `nrk.no/nyheter/rss.xml`, `e24.no/rss.xml`, `dn.no/rss.xml`.
- **Lokalt kutt-filter** (`_CUT_TITLE_RE`) kutter på tittel før MAX_PER_FEED-telling og sparer
  input-tokens. Listen er bevisst **konservativ** — feilkutt kan ikke reddes av Claude:
  «drapssiktet»/«siktet for» er med, bare «drept» er det IKKE (ville kuttet krigsnyheter).
- **Dedup mot tidligere dager:** `_load_recent_briefing_points()` leser `news_md` fra de siste
  dagsfilene (ingen egen state-fil) og gir både et mekanisk URL-/tittelfilter og en unngå-liste
  i prompten som fanger samme sak med ny overskrift.
- **`SYSTEM_PROMPT` er fasit for seksjoner, ordgrenser og fordelingskrav** — ikke gjenta dem
  her. To ting som ikke står i prompten: emojiene brukes av nettsidens parsing, og
  fordelingskravene inne i de sammenslåtte seksjonene («maks 1 AI/tech») er hele poenget med
  sammenslåingen — uten dem fortrenger AI-nyheter vitenskapen helt. Sammenslåingen (7 → 5
  seksjoner, august 2026) var **ren promptendring**: `splitNewsSections()` splitter generisk på
  «## », så arkiverte briefinger beholder sine gamle seksjoner og rendres uendret.
- **Markedsdata sendes IKKE til Claude** — Claude forklarer *hvorfor* markedet beveget seg.
- **Vær:** MET Locationforecast, **`complete`-endepunktet** (UV finnes ikke i `compact`).
- **Quiz og gåter** trekkes fra lokale banker i repoet — ingen Claude-bruk, ingen ekstern API.
  **Én quiz-fil = én kategori.** Dagen er 7 ferske (fra et kategorivindu som roterer med
  datoen) + inntil 3 forfalte repetisjoner. Begge har dedup + spaced repetition på volumet.
  **Tilsiget er den harde grensen, som i køgeneratorene:** 240 spørsmål ble tømt på under et
  år, og fallback-grenen (`pool = unseen or questions`) begynner da stille å trekke allerede
  sette spørsmål på nytt — uten varsel. Vokser dagsantallet, må banken vokse først.
  **Feltet `explanation` er valgfritt og skal aldri fjernes fra eldre spørsmål:**
  repetisjonsutvelgelsen prioriterer spørsmål som har det, siden en repetisjon uten den ekstra
  konteksten har liten læringsverdi. Nye spørsmål skal alltid ha feltet.
- **Inspirasjon:** Claude refererer episoder kun via **indeks-id** mot vår RSS-liste, så
  podcast/tittel/URL aldri kan hallusineres — kun rådsteksten og boktipsene er generert.

## Køarkitektur — felles for de tre kunnskapsgeneratorene

`research_briefing.py`, `textile_briefing.py` og `supplement_briefing.py` deler samme
maskineri. Forskjellene mellom dem står i hver sin seksjon under; dette gjelder alle tre.

**Systemet er købasert, ikke dagsbasert.** Studier vi har vurdert men ikke brukt, ligger i en
varig kø sortert synkende på score. En kjøring hopper over de tunge stegene når de ikke trengs:

1. **Last** kø + `seen`. 2. **Prun** (for gamle, allerede brukte). 3. **Påfyll** — kun når køen
er under terskel. 4. **Omtaler skrives** — kunnskapsbasene via API i batch når det er for få ferdigskrevne;
forskningen i Claude Code via `/forskning-uke`. 5. **Bruk** — publisering (research) eller innrulling + syntese (tekstil/tilskudd).

`--dry-run` kjører alt unntatt Claude-stegene. **Gratis, og eneste trygge måte å teste
spørringene på.** `--seed` er engangsknappen som gjør en base brukbar fra dag én; den koster
vesentlig mer enn en vanlig dag.

**Hvorfor køen finnes:** Claude **velger ikke, den skriver**. Lokal scoring bestemmer både hva
som kommer i kø og rekkefølgen, så Claude får kun studiene den skal skrive om. Input falt fra
~23 000 til ~6 000 tokens per kall, og **hver studie koster tokens nøyaktig én gang,
noensinne**. Kvalitetskontrollen beholdes ved at Claude kan vrake en studie eksplisitt; den
settes da `rejected` og blir liggende som gravstein så den ikke settes inn igjen.

**Invarianter som er dyre å bryte:**

- **Lagret tekst må parses likt for alltid.** Omtalene lagres i Claudes vanlige format og
  settes sammen ved bruk, slik at en omtale skrevet for tre måneder siden parses identisk med
  en skrevet i dag. **Endrer du FORMAT-delen av en `SYSTEM_PROMPT`, blir køens eksisterende
  omtaler stående i det gamle formatet** — begge må da parses.
- **Blokker mappes på URL, ikke rekkefølge.** Kan et svar ikke knyttes til en studie, lagres
  **ingenting**: feil tekst under riktig tittel er verre enn en tapt dag.
- **Scoringen kjører på FULLT abstract — kutt aldri før scoring.** `MAX_ABSTRACT_CHARS` gjelder
  kun når prompten bygges. Da abstractet ble kuttet til 1200 tegn *før* scoring, forsvant
  nøyaktig de signalene scoringen gir poeng for (HR/RR/CI/p og utvalgsstørrelse står typisk
  lenger ute): 20. juli 2026 falt 52 kvalifiserte kandidater til **0**, og forskningsbriefingen
  uteble helt. Besparelsen var ~1,5 øre/dag.
- **Dedup er to-nivås og varig** (`*_seen*.json`): brukt → blokkert; **avvist av
  sikkerhetsklassifikatoren** → `refused: true`, blokkert like lenge. En refusal er
  deterministisk, og uten flagget kommer samme abstract tilbake og betaler en ny
  prober-runde — derfor lagres refused-ID-er **også når kjøringen gir opp helt**.
- **Ikke gjeninnfør karantene av «vurdert, ikke valgt».** Fram til 24. juli 2026 blokkerte
  `UNPICKED_COOLDOWN_DAYS = 14` slike studier. Den fantes bare fordi Claudes vurdering ikke ble
  lagret. Med 40 kandidater/dag × 14 dager låste den ute opptil ~475 studier samtidig — mer enn
  hele vinduet — og systematisk de **høyest scorede**, siden poolen alltid var toppen av
  scoringen. Forskningsbriefingen uteble 23. og 24. juli.
- **Tilsiget er den harde grensen, og køen skjuler den ikke — den gjør den synlig.** Symptomet
  er at «ferdigskrevne igjen i kø» faller mot 0 flere dager på rad. Riktig fiks er å utvide
  vinduet, heve hentetaket eller senke antall per dag — **ikke** å senke score-terskelen.
  **Sjekk alltid spørringen først:** da køen blødde tom i august 2026 så det ut som naturlig
  uttørking, men årsaken var et ødelagt filter (se under). Mål `hitCount` per kategori før du
  justerer knapper.

## `research_briefing.py` — forskning

Målgruppe (fra 20. september 2026): et par rundt 35 — én som trener styrke/løping/padel, én
lege — med små barn i horisonten. Menneskestudier med tydelige tall de kan handle på selv.
**Seks kategorier** (trening, kosthold, søvn/stress, longevity, medisin, barn), én per dag
(`MAX_ITEMS = 6`, `MAX_PER_CATEGORY = 1` **mykt**) — har køen ikke nok kategorier, fylles
dagen opp likevel. Kryss-kategori-duplikater fjernes (første spørring vinner).
`CATEGORY_QUERIES` er en **liste** av (kategori, spørring) — trening har to.

**Designkravet er per kategori, bevisst:** RCT/MA/SR som standard; trening og barn tar også
`Clinical Trial` (crossover); prestasjonsspørringen (utøvere, VO2max, sener) har **ingen**
designkrav — der er kravet flyttet til scoringen (0 designpoeng → må ha n, tall og utfall).
Medisin er bundet til **tidsskrift** (NEJM, Lancet, JAMA, BMJ, Nat Med …), ikke tittel — et
gjennombrudd har ingen felles emneord — og der straffes verken pasienter eller medikamenter.
Målt hitCount per spørring står i kommentarene i koden.

**Europe PMC-spørringen — her håndheves kvalitetskravene, og her er de dyreste feilene gjort:**

- **Menneskefilteret må være `KW:"Humans"` — `MESH:` er dødt i Europe PMC og skal ALDRI
  brukes.** Fram til 11. august 2026 sto det `MESH:"Humans"`, som matcher en forsvinnende og
  vilkårlig delmengde. Målt på `TITLE:"exercise" AND SRC:MED`: RCT alene → **385** treff,
  RCT + `MESH:"Humans"` → **1**. Over ti år slapp 13 av 8 304 RCT-er gjennom (0,16 %) — filteret
  fjernet i praksis **hele RCT-tilfanget** og etterlot oversiktsartikler. (`MESH:"Animals"` → 0,
  `MESH:"Adult"` → 1: feltet virker ikke i det hele tatt.) `KW:` diskriminerer riktig og tok
  180-dagerspoolen fra 398 til **5 226** studier.
- **Emneordene er bundet til tittelen** (`TITLE:"exercise"`), ikke fritekst. Uten det matcher
  Europe PMC ordet hvor som helst i artikkelen, og poolen fylles av kreft og cellegift.
  **Ikke bytt tilbake til fritekst.** Fallgruver: `TITLE:"fiber"` matcher «Thulium **Fiber**
  Laser», `TITLE:"stress"` matcher «oxidative stress», `TITLE:"recovery"` matcher postoperativ
  restitusjon — de må stå som fraser.
- **`LOOKBACK_DAYS = 180`, ikke 2.** Forskning har ingen nyhetssyklus, og et kort vindu gjør
  kvalitetsfiltrene *utilgjengelige*: Europe PMC tildeler MeSH/PUB_TYPE uker etter publisering,
  så en to dager gammel artikkel er ennå ikke merket som RCT (målt: 2 dager → 0 treff,
  30 dager → 24).
- **Hele poolen hentes, ikke bare de nyeste** — `_fetch_all_pages()` paginerer via `cursorMark`.
  Tidligere hentet vi kun de 100 nyest indekserte per kategori, som utelot ~80 % av vinduet fra
  scoringen.

**Lokal scoring** gir poeng for studiedesign, utvalgsstørrelse, tydelige effektmål, relevante
utfall (også prestasjon: 1RM, tidskjøring, HRV, mikrobiom) og målgruppe («healthy adults»,
«athletes»), og **trekker fra** for smale pasientgrupper, medikamenter (ordliste **+
suffiks-regex**: -tide, -mab, -flozin …; «peptide» er unntatt), «older adults» (−1,5) og
observasjonelle titler («association», «prevalence», −1,5). Straffelistene er
**kategoriavhengige**: barn-termer straffes ikke i barn, ingenting av dette i medisin.
Kvinnehelse (menopause, svangerskap) er tatt ut av straffelisten. `--refill` **omscorer hele
køen**, så en regelendring slår gjennom på det som allerede ligger der. Toppen av hver
kategori er det eneste som teller — sjekk `--propose 9` etter en endring, ikke terskelen.

**Nettsiden tåler at Claude dropper lenken i overskriften** (målt: 2 av 5 studier 8. august
2026). `studiesForDay()` kobler `research_items` på studiene — først på URL, deretter
**posisjonsfallback**, som er trygt fordi `research_items` bygges i samme rekkefølge som
markdownen settes sammen.

## `textile_briefing.py` — tekstil-kunnskapsbase

Målgruppe: **innkjøp til en klesbutikk**. Bakgrunn i `PLAN-TEKSTIL.md`. Akkumulerende: hver
studie rulles inn i 1–3 **emner** som blir stående og vokser.

- **`textile_topics.py` er registeret og eneste sannhet.** Nettsiden har **ingen egen
  emneliste** — den leser gruppenavn, dommer og styrkenivåer ut av KB-en, så et nytt emne
  dukker opp på tekstil.modr.no ved neste generatorkjøring uten kodeendring i `web/`.
- **`slug` endres ALDRI etter publisering** — den er identiteten til all evidens som er rullet
  inn under emnet. Fjerner du et emne fra registeret, slettes ingenting: det merkes `retired`.
- **Ikke fjern OpenAlex.** Slitestyrke, fargeekthet, levetid og LCA publiseres i tidsskrifter
  som ikke er indeksert i MEDLINE; med bare Europe PMC står to av fire temaer permanent tomme.
  (OpenAlex leverer abstracts som **invertert indeks** som må settes sammen igjen.)
- **RCT-kravet fra `research_briefing.py` gjelder IKKE her, og det er bevisst.**
  Tekstilallergi dokumenteres gjennom patch-test-serier og eksponeringsmålinger — krever man
  RCT, står emnene tomme. Kvalitetskravet er flyttet til den lokale scoringen og til
  `**Forbehold:**`-avsnittet.
- **To støytyper MÅ straffes**, fordi kildene er brede med vilje (vi kan ikke spørre per emne):
  **`_LAB_NOISE`** — materialforskning om noe man aldri kan gå med (EMI-skjerming,
  superkondensatorer, sårbandasjer, ren syntese); **`_OFF_TARGET`** — miljøstudier om hvor
  forurensningen HAVNER (innsjø, sediment, fisk). Faglig gode, men de svarer på et annet
  spørsmål: vi skal velge et plagg, ikke kartlegge en innsjø.
- **Kostnaden vokser med basen, ikke med tilsiget.** Syntesen er eneste post som skalerer med
  størrelsen, og var **~halve API-regningen** fram til 11. september 2026 (57 synteser på 10
  dager, mot 22 omtale-batcher): med terskel 2 nye studier og 5 innrullinger/dag sto den på
  maks 3 + 3 hver dag, og hvert kall sendte de 14 nyeste omtalene (~9 000 tokens) for å
  oppdatere ett avsnitt. Tre bremser nå, i begge basene: terskel **4** nye studier og maks
  **2** synteser per kjøring; inputen er **de nye omtalene + 3 eldre som anker** (tak 8) —
  forrige oppslag sendes med og bærer resten av evidensen, og prompten sier eksplisitt at
  konklusjoner fra eldre studier skal bæres videre derfra; og `effort: low` (syntesen er
  omskriving av gitt input, tenking koster som output). Målt på «Bomull» (36 studier):
  ~3 600 tokens input mot ~9 000. Hever du `SYNTH_CONTEXT_STUDIES`, er det driften over mange
  omskrivinger (sammendrag av sammendrag) du kjøper deg ut av — ikke bedre enkeltoppslag.
- **Kan syntesesvaret ikke tolkes, står emnet uendret** — et halvt oppdatert oppslag er verre
  enn et gammelt. KB-en lagres etter hvert emne, så en feil på emne 3 ikke koster de to første.
- Studien lagres **én gang** i `kb["studies"]`; emnene refererer til den med id.

## `supplement_briefing.py` — tilskudds-kunnskapsbase

Målgruppe: samme leser som forskningsbriefingen. Bor på **forskning.modr.no/tilskudd**, ikke
eget subdomene: samme leser og samme spørsmål, så en fjerde side ville splittet lesingen.

**Formålet er å kunne si NEI raskt**, og det står i begge systempromptene. De fleste tilskudd
gjør ingenting målbart for en frisk person; verdien ligger i å slippe å prøve dem.
Tilskuddslitteraturen er systematisk skjevfordelt mot positive funn (små utvalg,
industrifinansiering) — **uten den eksplisitte instruksen vil Claude skrive positivt om alt.**

Tre ting skiller den fra tekstil-generatoren:

1. **RCT-kravet GJELDER her.** Feltet er fullt av RCT-er *og* fullt av små sponsede studier.
   **Ikke senk filteret** — da fylles basen med nøyaktig det materialet som driver hypen, og
   verktøyet som skulle beskytte mot overselging blir leverandøren av den.
2. **Én spørring PER STOFF, ikke per gruppe.** `topic_query()` bygges av `probe`-feltet, så
   registeret er både stoffliste og søkespesifikasjon. Grunnen: slår man ni vitaminer sammen og
   kutter på hentetaket, overlever alltid det største stoffet. K2 (9 kvalifiserende studier på
   to år) ville aldri sett dagens lys ved siden av D-vitamin (417). Kostnaden er ~34
   HTTP-kall, og de er gratis.
3. **Bevis-gulvet er en FUNKSJON, ikke en mangel.** Stoffene i gruppen `uavklart` gir null
   treff gjennom kvalitetsfilteret: **BPC-157 har 193 publikasjoner og 0 kontrollerte
   menneskestudier.** `measure_evidence_floor()` teller begge tallene med gratis
   `hitCount`-kall og viser dem som selve oppslaget. **Ikke fjern disse stoffene fordi de er
   tomme** — fraværet av evidens er svaret for nettopp de stoffene som markedsføres hardest, og
   et stoff uten oppslag lar leseren tro at spørsmålet ikke er stilt.

**Registeret (`supplement_topics.py`)** — samme `slug`-invariant som tekstil. To felter som
ikke er pynt:

- **`claim`** er markedsføringspåstanden, og syntesen får den i prompten med instruks om å
  sette den opp mot hva studiene faktisk har målt (`measured`). For NAD-forløpere er påstanden
  «klarhet i hodet» mens utfallet i studiene er NAD+-konsentrasjon i helblod — å vise de to ved
  siden av hverandre er oppslagsverkets viktigste enkeltgrep.
- **`floor_verdict`** er dommen et stoff har så lenge det ikke finnes kvalifiserende evidens.
  Den utledes **ikke** av studier, men av regulatorisk status og av at stoffet injiseres uten
  humane sikkerhetsdata — derfor står den i registeret, ikke hos Claude. Uten feltet faller et
  tomt stoff til `ukjent`, som er riktig for et harmløst dårlig studert stoff (glysin) og feil
  for et gråmarkedspeptid: «ukjent» leses som «kanskje verdt et forsøk».

**Domsaksen: `ta` / `vurder` / `dropp` / `risiko` / `ukjent`.** `dropp` og `ukjent` er bevisst
forskjellige dommer — «godt undersøkt, effekten er omtrent null» (multivitamin) og «vi vet
ikke» er helt ulike svar, og et oppslagsverk som slår dem sammen er verdiløst. `dropp` er en
STERK konklusjon. `risiko` er skilt fra begge, og har egen rød farge mens `dropp` er nøytral
grå: å gi dem samme rødt ville gjort et virkningsløst multivitamin like alarmerende som et
gråmarkedspeptid. Prompten sier eksplisitt at `ukjent` skal brukes når evidensen er tynn.

**Lokal scoring — to støytyper MÅ straffes.** Presis spørring er ikke det samme som relevant:
D-vitamin gir 417 treff, og flertallet handler om graviditet, spedbarn, dialyse eller husdyr.
**`_OFF_POPULATION`** (feil populasjon — leseren er en frisk voksen; uten lista ville
D-vitamin-oppslaget blitt skrevet på svangerskapsstudier) og **`_NOT_A_SUPPLEMENT`** (stoffet
er en biomarkør eller infusjonsbehandling, ikke noe man kan kjøpe: «Serum zinc as a prognostic
marker in sepsis» treffer stoffet `sink` perfekt og er helt ubrukelig).

## Datalager — JSON-kontrakten

`store_briefing()` skriver/merger til `<BRIEFING_DATA_DIR>/briefings/<dato>.json`. **Alle
generatorene skriver inn i samme dagsfil, og oppdaterer kun sine egne felter** — skrivingen er
atomisk (`.tmp` + `os.replace`). Feltlista leses i `store_briefing()` og i
`web/src/lib/briefings.js`; den gjentas ikke her.

**Briefinger er immutable.** Det er forutsetningen for at biblioteket kan utledes fra arkivet,
at anker-lenker og lagrede indekser holder, og at gamle briefinger kan rendres av ny kode.
`weather`/`market` lagres strukturert hver dag, så historiske figurer bygges uten ny
datainnhenting.

## Nettsiden (`web/`)

Astro 5, `output: 'server'`, `@astrojs/node`. Ruter finnes under `web/src/pages/`, komponentene
i `web/src/components/` har toppkommentarer som forklarer formål og interaksjon — les dem der.
`middleware.js` er **datadrevet** (`SITES`-lista): nytt subdomene = én linje. Den ruter host →
intern sti og setter lenkeprefikset i `locals`, så sidene virker både på subdomene og under
sti-prefiks i dev. `/lagret` og `/api/*` skrives **ikke** om — de er felles for alle vertsnavn.

Det som ikke står i koden:

- **Visningskode må tåle gamle dagsfiler.** Briefinger er immutable og rendres av dagens kode.
  `WeatherCard.astro` er derfor en dispatcher over tre generasjoner værdata, og
  `splitNewsSections()` splitter generisk så arkiverte briefinger med gamle seksjoner består.
- **Forsidens rekkefølge ligger i `BriefingView.astro` og speiles i `.jumpnav` i `Base.astro`**
  — endres den ett sted, må det andre følge med, ellers hopper leseren i utakt med siden.
- **`ResearchList.astro`: ikke gå tilbake til én grid per kategori.** 5 studier på 3–4
  kategorier ga 1–2 kort per grid, altså permanente hull i hver rad. Anker-id `s<i>` settes
  **før** sorteringen, ellers brekker lenkene fra nyhetssiden.
- **Registerdrevet UI:** ny node i `src/lib/statsGuide.js` gir ny seksjon i `StatsGuide` uten
  UI-endring; nytt tema = `[data-theme]`-blokk i `global.css` + én linje i `themes.js`.
- **`Whiteboard.astro`** (tegnetavle for gåtene) holder strøk i verdenskoordinater tegnet via
  canvas-transform, så minnebruken følger antall strøk og ikke lerretstørrelsen.

### Bibliotek (`/lagret`)

Opprinnelig plan i `PLAN-LAGREDE-STUDIER.md`.

- **To lag, ikke ett.** Biblioteket **utledes fra briefing-arkivet ved forespørsel**
  (`library.js`) — ingenting kopieres inn. Arkivet er fasiten, så biblioteket kan ikke komme i
  utakt, og web trenger ikke skrivetilgang til `/data`. `saved.json` er **kun favorittene**.
- **Cachen nøkles på `briefingStamp()`** (antall filer + sum av mtime), ikke filnavn: flere
  generatorer skriver inn i samme dagsfil, så dagens fil endres etter at den først er lest.
  Samme grunn til at `getKb()` nøkler på mtime **+ størrelse** — mtime alene fanger ikke to
  skrivinger samme sekund.
- **Nyheter er ikke med i repetisjonspoolen.** En tre uker gammel nyhet er ikke noe å repetere,
  og med ~19 punkter/dag ville de fortrengt studiene kortet er til for.
- **«Dagens repetisjon» velges deterministisk per dato** (hash av `<dagnummer>:<id>`) over hele
  arkivet — ingen state-fil å persistere. **Ikke gå tilbake til «mest forfalt vinner» over kun
  `saved.json`:** siden `reps` bare bumpes når man trykker «Repetert», var tilstanden
  absorberende — den eldste favoritten ble vist hver eneste dag, og poolen var 5 oppføringer
  i stedet for ~550.

### Sikkerhet — invarianter

- **Innholdet utledes SERVER-SIDE** fra arkivet; klienten sender kun `{date, url}` eller
  `{date, index}`. Ellers kunne enhver med kodeordet plantet vilkårlig HTML som senere rendres
  med `set:html`. **Må ikke svekkes.**
- **Eget volum:** `saved-data:/state` (rw) ved siden av `briefing-data:/data:ro`. Nettappen er
  eneste prosess eksponert mot internett og skal **aldri** kunne skrive i briefing-arkivet.
- **Lesing åpent, skriving krever kodeord** (`auth.js`): HMAC-signert cookie, ingen
  sesjonslagring — bytter du kodeord, blir alle utstedte cookies ugyldige automatisk.
  **Uten `SAVE_PASSPHRASE` svarer skriveendepunktene 503 og pin-knappene skjules** — funksjonen
  feiler synlig, ikke åpent.
- **Alle mutasjoner går gjennom én seriell promise-kø** (`saved.js`): samtidige POST-er kan
  ellers interleave read-modify-write og miste en lagring. Skriving er atomisk.

## Avhengigheter

```bash
pip install -r requirements.txt    # httpx, feedparser, anthropic, notion-client, yfinance
cd web && npm install              # astro, @astrojs/node, marked
```

Python 3.10+ (image: 3.12). Node 20+ (image: 22).
