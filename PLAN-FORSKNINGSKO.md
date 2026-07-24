# Plan: prioritert forskningskø i stedet for karantene

Status: **implementert 24. juli 2026.** Se «Forskningsbriefing» i CLAUDE.md for hvordan det
faktisk endte. Avvik fra planen under implementasjon:

- `fetch_research()` ble til `refill_queue(queue, seen, today)` (skriver i kø, returnerer antall
  innsatte) og `summarize_research_with_claude()` til `write_up_with_claude()` + `write_up_batch()`.
- De tre åpne spørsmålene ble avgjort som planen anbefalte: batch 10, **mykt** kategoritak,
  ingen rescoring ved gjeninnsetting.
- `--dry-run` publiserer ikke og tømmer ikke køen (planen sa bare «hopper over Claude»), så den
  er trygg å kjøre mot produksjonsdata.

## Bakgrunn — hvorfor

Dagens `research_briefing.py` tar hele beslutningen på nytt hver morgen: henter ~473 studier,
scorer dem lokalt, sender de 40 beste til Claude, viser 5 — og kaster Claudes vurdering av de
35 øvrige. Fordi vurderingen ikke lagres, er eneste måte å slippe å betale for de samme
studiene i morgen å *nekte å se på dem*: `UNPICKED_COOLDOWN_DAYS = 14`.

Den lappen spiser sitt eget grunnlag. Vi karantenesetter ~34 studier per dag i 14 dager, altså
opptil ~475 samtidig — **flere enn hele 180-dagersvinduet inneholder (473)**. Og siden poolen på
40 alltid er de høyest scorede, er det systematisk de beste studiene som låses ute. Målt
24. juli 2026:

| I vinduet (473 studier) | Antall |
|---|---|
| Vist før (400-dagers blokkering) | 69 |
| **I 14-dagers karantene** | **199** — hvorav **160 scorer over 3,0**, toppscore 12,6 |
| Ferske | 205 — toppscore **2,9** |

Resultatet var at forskningsbriefingen uteble 23. og 24. juli: ingen fersk kandidat nådde
terskelen på 3,0, mens eliten satt innelåst. Symptomet i loggen er «0 over terskel» i alle fire
kategorier samtidig, med normal `hitCount`.

**Ideen:** erstatt karantenen med en varig, score-sortert kø over studier vi har vurdert men
ikke vist. Nye studier settes inn på riktig plass etter score. Er køen lang nok, henter vi ikke
nytt. Claudes ferdigskrevne tekst lagres sammen med studien, så ingen studie vurderes to ganger.

## Målbilde

1. **Ingen karantene.** `UNPICKED_COOLDOWN_DAYS` slettes. En studie som ble vurdert og ikke
   vist, ligger i køen — ikke i en sperreliste.
2. **Ingen tomme dager** så lenge køen har innhold. Går det tomt, degraderer det gradvis
   (3 studier i stedet for 5) i stedet for null.
3. **Hver studie koster Claude-tokens nøyaktig én gang, noensinne.**
4. **Publiseringsdager er gratis** — ren sammensetting av lagret tekst, uten API-kall.
5. `MIN_SCORE` tilbake til **3,0** (i dag midlertidig senket til 1,5 for å kompensere for
   karantenen — se commit «Forskning: senk terskel til 1,5 og tak til 5 studier»).

### Hva planen IKKE løser

Køen jevner ut svingninger, men skaper ikke tilbud. Vinduet tar inn ~2,6 nye studier i døgnet,
og bare en del av dem passerer terskelen. Viser vi 5 per dag, tømmes køen på sikt uansett.
Forskjellen er at det blir synlig og gradvis. Akkurat nå finnes et engangsetterslep på ~165
studier over 3,0 (de karantenesatte + ferske), altså **over en måned med forsyning** før
tilsiget blir den bindende begrensningen. Blir det stramt igjen, er `LOOKBACK_DAYS` knappen —
365 dager ga 272 kandidater over 3,0 ved måling.

## Datamodell

Ny fil `research_queue.json` i `BRIEFING_DATA_DIR` (**må persisteres** — ligger på
`briefing-data`-volumet, som allerede er dekket av backup-kommandoen i CLAUDE.md).

```json
{
  "version": 1,
  "updated": "2026-07-25",
  "entries": [
    {
      "doi": "10.1186/s12966-026-01902-3",
      "url": "https://doi.org/10.1186/...",
      "title": "Exercise and cognitive function in depression: ...",
      "journal": "Int J Behav Nutr Phys Act",
      "date": "2026-07-11",
      "category": "trening",
      "pub_types": ["Meta-Analysis"],
      "abstract": "…",
      "score": 8.4,
      "score_why": "meta-analysis, n≈2 324, 2 tallsignal, 2 utfall",
      "queued_at": "2026-07-25",
      "status": "scored",
      "writeup": null,
      "writeup_at": null
    }
  ]
}
```

- **`entries` holdes sortert synkende på `score`.** Innsetting skjer med `bisect.insort` på
  negert score (eller enkel `sort` etter innsetting — listen er noen hundre lang, kostnaden er
  irrelevant). Dette er kravet «ny studie kommer rett inn på den plassen i køen som tilsvarer
  hvor god den er».
- **`status`:**
  - `scored` — vurdert lokalt, venter på Claude-tekst.
  - `ready` — har `writeup`, klar til publisering.
  - `rejected` — Claude vurderte den som ubrukelig, eller den trigget
    sikkerhetsklassifikatoren. Blir aldri sendt inn igjen. (Beholdes i køen som gravstein så
    den ikke settes inn på nytt ved neste henting.)
- **`abstract` slettes når `status` blir `ready`** — teksten er skrevet, abstractet trengs ikke
  mer. Halverer filstørrelsen (~2 KB per oppføring).
- Køen er **ubegrenset i lengde**. Eneste beskjæring er alderspruning, se under.

### Forhold til `research_seen_dois.json`

Filen beholdes, men får én jobb: **«er denne allerede vist leseren?»**

- `picked` → blokkert `SEEN_RETENTION_DAYS = 400` (uendret — leseren skal aldri se samme
  studie to ganger).
- `refused` → blokkert like lenge (uendret).
- **`UNPICKED_COOLDOWN_DAYS` fjernes**, og `_is_blocked()` forenkles til
  `entry["picked"] or entry.get("refused")`. Eksisterende ikke-valgte oppføringer slutter da
  å blokkere av seg selv — **~199 studier frigjøres i det øyeblikket koden deployes**, uten
  migreringsskript.

## Flyt — én kjøring

```
main()
 ├─ 1. last kø + seen
 ├─ 2. prune kø
 ├─ 3. fyll på kø fra Europe PMC   (hoppes over hvis køen er lang nok)
 ├─ 4. skriv ut tekster med Claude (hoppes over hvis nok ready-oppføringer)
 └─ 5. publiser dagens studier      (aldri API-kall)
```

### 2. Pruning (`_prune_queue`)

Kjøres først, før alt annet:

- Fjern oppføringer der `date` er eldre enn `QUEUE_MAX_AGE_DAYS = 400` (en studie skal ikke
  ligge og bli gammel i køen for alltid).
- Fjern oppføringer hvis DOI er `picked` i `seen` (sikkerhetsnett mot dobbeltvisning hvis
  køen og seen kommer i utakt).
- Behold `rejected` som gravstein til `date` faller for aldersgrensen.

### 3. Påfylling fra Europe PMC (`_refill_queue`)

Gated på kølengde — dette er «om listen er lang nok, prøver vi ikke å hente mer»:

```python
scored_count = sum(1 for e in queue if e["status"] == "scored")
if scored_count >= QUEUE_REFILL_BELOW:      # 60
    print(f"  ⓘ  {scored_count} studier i kø — hopper over henting.")
else:
    ...fetch...
```

Selve hentingen gjenbruker dagens kode nesten uendret: `_fetch_all_pages()` per kategori,
`_score_candidate()` på **fullt abstract** (jf. den harde regelen i CLAUDE.md — kutt aldri før
scoring). Endringene er:

- Kandidater med `score < MIN_SCORE` (3,0) settes **ikke** inn — køen skal ikke fylles av
  søppel som må siles ut igjen hver dag.
- DOI-er som allerede står i køen (uansett `status`) hoppes over — køen er sin egen dedup.
- `CANDIDATE_FLOOR_PER_CATEGORY` / `CANDIDATE_POOL` **utgår**. De fantes for å balansere ett
  enkelt dagsutvalg mot Claude; nå er køen full av alt, og balanseringen skjer ved uttak
  (steg 5). Kategoribredde ivaretas altså fortsatt, bare på riktig sted.

Merk: Europe PMC er gratis og uten nøkkel, så gatingen sparer ikke penger — den sparer tid
(~15 s per kjøring) og belastning på et åpent API. Den økonomiske gevinsten ligger i steg 4.

### 4. Claude-tekster i batch (`_write_up_batch`)

Gated på antall ferdige tekster:

```python
ready = [e for e in queue if e["status"] == "ready"]
if len(ready) >= WRITEUP_REFILL_BELOW:      # 10
    print(f"  ⓘ  {len(ready)} ferdigskrevne studier i kø — hopper over Claude-kallet.")
else:
    batch = top WRITEUP_BATCH_SIZE (10) entries with status == "scored"
```

**Dette er den store endringen i Claude-bruken.** I dag sender vi 40 kandidater for at Claude
skal *velge* 5 — og betaler for 35 abstracts vi kaster. Nå gjør den lokale scoringen
utvelgelsen, og Claude får bare de 10 den skal skrive om.

| | I dag | Etter |
|---|---|---|
| Input per kall | ~23 000 tokens (40 abstracts) | ~6 000 tokens (10 abstracts) |
| Output per kall | ~4 000 tokens (5 omtaler) | ~8 000 tokens (10 omtaler) |
| Kall per uke | 7 | ~3–4 |
| Kastet arbeid | 35 vurderinger per dag | 0 |

`MAX_TOKENS` må opp fra 8192 til **16000** for å få plass til 10 omtaler (~700 tokens hver
inkludert overhead). Sjekk at `MODEL = "claude-sonnet-4-6"` tåler det — det gjør den; ikke bytt
modell (designvalg i CLAUDE.md).

#### Endringer i `SYSTEM_PROMPT`

Utvelgelsesrollen fjernes, skriverollen beholdes ordrett. Konkret:

- Erstatt «Velg de OPPTIL 5 mest verdifulle …» med: *«Skriv en omtale av HVER av studiene
  nedenfor, i den rekkefølgen de står.»*
- **UTVALGSKRITERIER** beholdes, men omformuleres til en **vrakingsregel**: er en studie
  ubrukelig for leseren (ingen konkrete tall, eller ren klinisk behandling han aldri tar
  stilling til), skal Claude skrive nøyaktig `## SKIP [n] — <kort begrunnelse>` i stedet for en
  omtale. Da beholder vi kvalitetskontrollen uten å betale for kastet arbeid.
- **FORMAT** og **REGLER** står helt uendret — nettsidens `splitResearch()` parser disse
  etikettene, og formatet er kontrakten mot arkivet.

#### Parsing av svaret (`_parse_writeups`)

- Del på `\n## ` (behold `## ` i hver blokk). Hver blokk er selvstendig og starter med
  `## [tittel](url)` — det er nettopp derfor per-studie-lagring er trygt: `splitResearch()` i
  `web/src/lib/briefings.js` splitter på samme overskrift og merker ingen forskjell på om
  teksten ble skrevet i dag eller for tre dager siden.
- Map blokk → køoppføring **på URL** (`entry["url"] in block`). URL-en er unik og prompten
  krever at den gjengis uendret.
- Blokk som starter med `## SKIP` → sett tilhørende oppføring til `status: "rejected"`.
- Oppføring i batchen uten treff i svaret → la den stå som `scored`, men tell opp
  `passes` (nytt felt); ved `passes >= 2` settes den til `rejected`. Hindrer at en studie
  Claude konsekvent hopper over blir sendt inn i evighet.
- **Guard:** hvis ingen blokker kunne mappes, behandles kallet som mislykket — køen skrives
  ikke, og vi går videre til publisering med det som allerede er `ready`.

#### Refusal-håndtering

`_find_refusing_articles()` (bisect med billige prober) beholdes uendret, men brukes nå mot
batchen på 10 i stedet for poolen på 40 — altså færre og billigere runder. Isolerte studier
settes til `status: "rejected"` i køen **og** merkes `refused` i `seen`, som i dag. Regelen om
at refused lagres selv når kjøringen gir opp helt, må bevares (kommentaren i `main()`
forklarer hvorfor).

### 5. Publisering (`_pop_for_today`)

Aldri et API-kall. Tar de beste `ready`-oppføringene, med kategoribalanse:

```python
picked, per_cat = [], Counter()
for e in ready:                                  # allerede sortert på score
    if per_cat[e["category"]] >= MAX_PER_CATEGORY:   # 2
        continue
    picked.append(e); per_cat[e["category"]] += 1
    if len(picked) == MAX_ITEMS:                  # 5
        break
```

Uten taket kan fem kostholdsstudier havne på samme dag fordi de tilfeldigvis scoret høyest;
nettsiden grupperer etter kategori og ville da vist én gruppe. Med tak 2 får du minst tre
kategorier representert.

Deretter:

- `research_md = "\n\n---\n\n".join(e["writeup"] for e in picked)` — samme separator som
  Claude produserer i dag.
- `research_items` bygges direkte fra oppføringene (`title`, `url`, `journal`, `date`,
  `category`) — mer robust enn dagens `a["url"] in briefing`-match.
- `store_briefing(today_str, research_md=..., research_items=...)` uendret.
- De publiserte DOI-ene markeres `picked` i `seen` og **fjernes fra køen**.
- Er `picked` tom → ingen `store_briefing`-kall (myk feil som i dag, feltet utelates), og
  loggen sier eksplisitt at køen er tom, ikke «ingen nye studier funnet».

## Konstanter

```python
# Fjernes
UNPICKED_COOLDOWN_DAYS = 14
CANDIDATE_FLOOR_PER_CATEGORY = 4
CANDIDATE_POOL = 40

# Endres
MIN_SCORE = 3.0     # tilbake fra 1.5 — køen fanger opp de gode i stedet for å låse dem ute
MAX_TOKENS = 16000  # fra 8192 — 10 omtaler i ett svar

# Nye
QUEUE_FILE = "research_queue.json"
QUEUE_REFILL_BELOW = 60    # under så mange scored-oppføringer: hent nytt fra Europe PMC
QUEUE_MAX_AGE_DAYS = 400   # prun oppføringer eldre enn dette (på studiens publiseringsdato)
WRITEUP_REFILL_BELOW = 10  # under så mange ready-oppføringer: kall Claude
WRITEUP_BATCH_SIZE = 10    # antall omtaler per Claude-kall
MAX_PER_CATEGORY = 2       # maks studier fra samme kategori i én dagsbriefing
MAX_ITEMS = 5              # uendret
```

Med `WRITEUP_BATCH_SIZE = 10` og 5 studier per dag kalles Claude omtrent **annenhver dag**.
Vil du sjeldnere kall, øk batchen — men da må `MAX_TOKENS` opp tilsvarende, og lange svar har
større risiko for at kvaliteten faller mot slutten. 10 er valgt som balansepunkt.

## Filer som endres

| Fil | Endring |
|---|---|
| `research_briefing.py` | Ny køseksjon (`_queue_path`, `_load_queue`, `_save_queue`, `_insert_scored`, `_prune_queue`), `fetch_research()` skrives om til `_refill_queue()`, ny `_write_up_batch()` + `_parse_writeups()`, `main()` restruktureres til de fem stegene, `_is_blocked()` forenkles, `SYSTEM_PROMPT` justeres |
| `CLAUDE.md` | Seksjonen «Forskningsbriefing» skrives om: tre trinn → kømodellen. `research_queue.json` inn i listen over persistente data under «Fallgruver» |
| `PLAN-FORSKNINGSKO.md` | Denne fila — merkes implementert når den er i drift |

Nettsiden (`web/`) endres **ikke**. `splitResearch()`, `RESEARCH_CATEGORIES` og
JSON-kontrakten er uberørt — det er hele poenget med å lagre per studie i Claudes eksisterende
format. Ingen `docker compose build web` nødvendig.

## Testplan

1. **Tørrkjøring uten Claude.** Legg til `--dry-run` som kjører steg 1–3 og 5, men hopper over
   steg 4 og `store_briefing()`. Verifiser at køen fylles, sorteres synkende og deduper på DOI:
   ```bash
   docker compose run --rm generator python research_briefing.py --dry-run
   ```
2. **Sorteringsinvariant.** Etter kjøring: `scores == sorted(scores, reverse=True)`, og ingen
   DOI forekommer to ganger.
3. **Gating virker.** Kjør `--dry-run` to ganger. Andre kjøring skal skrive «hopper over
   henting» og ikke røre nettverket.
4. **Ekte kjøring** (bruker Claude-kvote):
   ```bash
   docker compose run --rm generator python research_briefing.py
   ```
   Forvent: ~165 studier inn i køen, 10 omtaler skrevet, 5 publisert, 5 ready igjen.
5. **Gratis dag.** Kjør på nytt umiddelbart. Forvent: ingen henting, **ingen Claude-kall**,
   5 nye studier publisert fra køen. Dette er den viktigste testen — den beviser at
   publisering er frikoblet fra API-et.
6. **Live-verifisering** på https://forskning.modr.no — studier gruppert i minst tre
   kategorier, alle lenker virker, `splitResearch()` har parset Metode/Resultat/osv.
7. **Regresjon på arkivet:** åpne en eldre dato under `/forskning/b/<dato>` og bekreft at
   gamle briefinger rendres uendret.

## Deploy og rollback

Per CLAUDE.md — generatoren må bygges **eksplisitt** (`profiles: batch` hoppes stille over av
bart `docker compose build`):

```bash
docker compose build web generator && docker compose up -d web
```

Rollback: `git revert <commit> && git push`, deretter rebuild. `research_queue.json` kan bli
liggende — gammel kode ignorerer den. Men merk: gjenopprettet gammel kode leser
`research_seen_dois.json`, der de publiserte studiene står som `picked`, så arkivet forblir
konsistent.

**Engangsrisiko ved første kjøring:** køen fylles med ~165 studier på én gang. Er noe galt i
innsettingslogikken, skjer det i stor skala. Ta en kopi først:

```bash
docker compose exec web cp /data/research_seen_dois.json /data/research_seen_dois.bak.json
```

## Åpne spørsmål

1. **Batchstørrelse 10 vs. 15.** 15 gir Claude-kall bare hver tredje dag, men krever
   `MAX_TOKENS ≈ 22000` og gir lengre svar der kvaliteten kan falle mot slutten. Planen
   foreslår 10; avklares før implementasjon.
2. **Skal `MAX_PER_CATEGORY = 2` være hardt?** Hvis køen en dag bare har trening igjen, betyr
   taket at vi viser 2 studier selv om 5 er klare. Alternativ: taket er mykt — fyll opp med
   overskytende når det ikke finnes nok kategorier. Planen foreslår **mykt tak** (fyll opp),
   siden tomme dager er verre enn en skjev dag.
3. **Bør `score` reberegnes ved gjeninnsetting?** En studie kan få MeSH-termer og PUB_TYPE
   tildelt av Europe PMC etter at vi først så den. Planen foreslår: nei — vi setter aldri inn
   en DOI som allerede står i køen, og reberegning ville flyttet ting rundt uten å endre hva
   leseren får.
