---
name: forskning-uke
description: Fyller forskningskøen for forskning.modr.no for én uke — henter og scorer studier fra Europe PMC, skriver 42 omtaler (6 kategorier × 7 dager) i Claude Code uten API-kall, og importerer dem i køen som cron publiserer fra. Bruk ved /forskning-uke, «ukens forskning», «fyll forskningskøen», eller når forskningssiden står tom.
---

# Ukentlig forskningsrunde

Målet er at `research_queue.json` på volumet har **≥ 42 ferdigskrevne omtaler** når du er
ferdig; cron publiserer 6 per dag. Generatoren kaller aldri Claude-API-et for forskning —
skrivingen skjer her.

Alle kommandoer kjøres fra `/root/nyheter-app`.

```
Fremdrift:
- [ ] 1 Hent og scor      --refill
- [ ] 2 Hent kandidater   --propose 9 → list_kandidater.py
- [ ] 3 Skriv omtaler     7 per kategori → omtaler.md
- [ ] 4 Importér          --import-writeups → null advarsler, ≥ 42 ferdigskrevne
- [ ] 5 Oppsummer         kun titler
```

## 1. Hent og scor

```bash
docker compose run --rm -T generator python research_briefing.py --refill
```

Pruner, scorer køen på nytt etter gjeldende regler og henter nytt fra Europe PMC (gratis).
Sluttlinja viser «venter på tekst» per kategori. Er en kategori under 9, si det til leseren:
det er tilsigssignalet, og fiksen er spørringen — ikke terskelen (se CLAUDE.md).

Det andre signalet kommer i steg 3: **vrakes mer enn 2 av de 9 øverste i en kategori**, er
toppen av køen støy. Fortell leseren hvilken type støy det var (yrkesgruppe, feil alder,
sykehusbehandling …) — fiksen er straffelistene i `_score_candidate()` (`_NARROW_POPULATION`,
`_METHOD_TITLE`, `_BARN_NARROW`, `_SCHOOL_AGE_TERMS`), aldri terskelen. Etter en regelendring:
`docker compose build generator`, `--refill` (omscorer hele køen) og sjekk toppen med
`--propose 12`.

## 2. Hent kandidater

```bash
docker compose run --rm -T generator python research_briefing.py --propose 9 > kandidater.json
python3 .claude/skills/forskning-uke/scripts/list_kandidater.py kandidater.json
```

9 per kategori: 7 skal skrives, 2 er reserve for det SKIP-reglene vraker. Ingen godkjenning
fra leseren — utvalget er reglenes ansvar.

Holder ikke reservene (flere enn 2 vrakes), hent dypere i stedet for å la dagen stå åpen:
`--propose 16 > kandidater16.json` og ta de neste i rekkefølge i den kategorien.
Løpenumrene i den fila er andre enn i `kandidater.json`, så bruk URL-en, ikke nummeret.

## 3. Skriv omtaler

Les leserprofil, SKIP-regler, FORMAT og REGLER fra kilden — de endres der, ikke her:

```bash
sed -n '/^SYSTEM_PROMPT = """/,/^_STUDY_SEPARATOR/p' research_briefing.py
```

Per kategori: de 7 høyest scorede som ikke vrakes. Resten blir liggende som `scored` til
neste uke — verken skriv eller vrak dem. **Skriv aldri SKIP for en reserve du ikke har
vurdert som ubrukelig**: SKIP er en varig gravstein.

Tellingen gjelder **kategorien i `**Kategori:**`-linja**, ikke kandidatfila — importen
synker kategorien derfra. Flytter du en studie (f.eks. et ashwagandha-forsøk fra Kosthold til
Søvn og stress), mangler giverkategorien én og mottakeren har én for mye. Mål: 7 i hver.

**Barn er smalere enn de andre kategoriene.** Leseren vil vite hvilke avgjørelser de som
foreldre kan ta for et friskt barn på 0–5 år. Hver Barn-omtale skal derfor ende i en konkret
avgjørelse (hva, når, hvor mye) og hvor stor gevinsten er for barnet. Studier om skolebarn og
ungdom, syke eller for tidlig fødte barn på sykehus, eller mors egen helse uten utfall hos
barnet vrakes etter regel 2. Er du i tvil om en studie, spør: *står en forelder til en
ettåring noen gang i dette valget?*

Europe PMC fjerner `<` fra abstractene, så «p < 0,001» blir bare «p» og tall kan mangle midt
i setninger. Skriv det som faktisk står, og gjett ikke på det som falt ut.

Skriv til `omtaler.md` i scratchpad, **6–7 omtaler per skriving** (en enkelt utskrift på 42
kappes). Én omtale = FORMAT-blokken: `## [tittel](URL)` med URL-en fra `kandidater.json`
uendret, så de fem `**…:**`-avsnittene. Skill blokkene med `\n\n---\n\n`. Grunnlaget er
abstractet i JSON-en; tall som ikke står der, finnes ikke.

En studie som er ubrukelig etter SKIP-reglene får én linje i samme fil, så den aldri kommer
tilbake, og den neste i kategorien skrives i stedet:

```
## SKIP <url> — <grunn fra SKIP-reglene>
```

## 4. Importér

```bash
docker compose run --rm -T generator python research_briefing.py --import-writeups - < omtaler.md
```

Bare blokker med kjent URL og alle fem avsnitt lagres; resten gis som `⚠`-linjer. Rett
filen og kjør igjen til det er null advarsler (lagrede hoppes over). Sluttlinja skal si
≥ 42 ferdigskrevne.

Oppdager du en feil i en omtale som allerede er lagret (et tall eller en påstand som ikke
står i abstractet), rett blokken og importer den på nytt med `--overwrite` lagt til
kommandoen. Det virker bare før omtalen er publisert — publiserte studier har forlatt køen.

## 5. Oppsummer

Kun titlene som ble skrevet, gruppert per kategori, og hvor mange dager køen dekker.

## Fallgruver

- `docker compose run` uten `-T` feiler med fil på stdin.
- `docker compose run generator` uten kommando kjører hele dagsbriefingen (koster kvote).
- Rediger aldri `research_queue.json` direkte — all skriving går via `--import-writeups`.
- Går en kategori tom, skriv de andre likevel; publiseringen fyller dagen mykt.
