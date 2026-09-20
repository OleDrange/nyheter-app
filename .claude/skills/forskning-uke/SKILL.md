---
name: forskning-uke
description: Ukentlig forskningsrunde for forskning.modr.no — hent og scor nye studier, la leseren godkjenne listen, skriv 42 omtaler (6 kategorier × 7 dager) her i Claude Code uten API-kostnad, og legg dem i køen som cron publiserer fra. Bruk når brukeren sier /forskning-uke, «ukens forskning», «fyll forskningskøen» eller lignende.
---

# Ukentlig forskningsrunde

Målet er at køen i `research_queue.json` (Docker-volumet) har **≥ 42 ferdigskrevne omtaler**
når du er ferdig — cron publiserer 6 per dag (én per kategori) i en uke. Alt Claude-arbeid
skjer **her i sesjonen**; generatoren kaller aldri API-et for forskning.

Kjør alle generator-kommandoer via `docker compose run --rm -T generator python
research_briefing.py …` fra `/root/nyheter-app`. Bruk scratchpad-mappa til alle mellomfiler.

## 1. Hent og scor (gratis)

```bash
docker compose run --rm -T generator python research_briefing.py --refill
```

Pruner, scorer køen på nytt med gjeldende regler og henter fra Europe PMC. Merk sluttlinja
«venter på tekst: trening N, kosthold N, …». Er en kategori under 9, si det til brukeren —
det er tilsigssignalet (se CLAUDE.md), og fiksen er spørringen, ikke terskelen.

## 2. Foreslå kandidater

```bash
docker compose run --rm -T generator python research_briefing.py --propose 9 > <scratchpad>/kandidater.json
```

9 per kategori (54) — 7 skal skrives, resten er slingringsmonn for det brukeren stryker.
Vis brukeren en **kompakt liste** gruppert per kategori: løpenummer, score, design,
tittel (kort) — én linje per studie, ingen abstracts. Spør hvilke som skal strykes (svar
med nummer). Ikke gå videre før brukeren har svart.

## 3. Skriv omtalene

Les leserprofil, vrakingsregler, FORMAT og REGLER **fra kilden**, ikke fra hukommelsen:

```bash
sed -n '/^SYSTEM_PROMPT = """/,/^_STUDY_SEPARATOR/p' research_briefing.py
```

Per kategori: skriv de 7 høyest scorede blant de godkjente. Godkjente utover 7 blir
liggende som `scored` til neste uke — ikke skriv dem, ikke vrak dem.

Skriv til én markdown-fil i scratchpad, **i batcher på 6–7 omtaler per Write/append** (42
omtaler i én utskrift kappes). Hver omtale er nøyaktig FORMAT-blokken: `## [tittel](URL)`
med URL-en fra `kandidater.json` **uendret**, deretter de fem `**…:**`-avsnittene. Skill
blokkene med `\n\n---\n\n`. Grunnlaget er abstractet i JSON-en — dikt aldri tall som ikke står der.

- Strøket av brukeren → én linje: `## SKIP <url> — strøket av leser`
- Ubrukelig etter vrakingsreglene → `## SKIP <url> — <kort grunn>`, og skriv neste
  godkjente i samme kategori i stedet, så kategorien fortsatt får 7.

## 4. Importér

```bash
docker compose run --rm -T generator python research_briefing.py --import-writeups - < <scratchpad>/omtaler.md
```

Importen lagrer bare blokker med kjent URL og alle fem avsnitt, og skriver ut advarsler
for resten. Er det advarsler: rett filen og kjør importen igjen (allerede lagrede hoppes
over). Sjekk at sluttlinja sier ≥ 42 ferdigskrevne / ≥ 7 dager.

## 5. Oppsummer

Til brukeren: **kun** en kort liste med titlene som ble skrevet, gruppert per kategori, og
antall dager køen dekker. Ingen omtaletekst, ingen prosessbeskrivelse.

## Fallgruver

- `docker compose run` uten `-T` feiler når stdin er en fil.
- Aldri `docker compose run generator` uten kommando — det kjører hele briefingen.
- Ikke rediger `research_queue.json` direkte; all skriving går via `--import-writeups`.
- Går en kategori tom, skriv de andre likevel — pop_for_today fyller dagen mykt.
