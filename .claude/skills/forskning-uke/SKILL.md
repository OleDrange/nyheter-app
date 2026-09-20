---
name: forskning-uke
description: Fyller forskningskøen for forskning.modr.no for én uke — henter og scorer studier fra Europe PMC, lar leseren stryke kandidater, skriver 42 omtaler (6 kategorier × 7 dager) i Claude Code uten API-kall, og importerer dem i køen som cron publiserer fra. Bruk ved /forskning-uke, «ukens forskning», «fyll forskningskøen», eller når forskningssiden står tom.
---

# Ukentlig forskningsrunde

Målet er at `research_queue.json` på volumet har **≥ 42 ferdigskrevne omtaler** når du er
ferdig; cron publiserer 6 per dag. Generatoren kaller aldri Claude-API-et for forskning —
skrivingen skjer her.

Alle kommandoer kjøres fra `/root/nyheter-app`.

```
Fremdrift:
- [ ] 1 Hent og scor      --refill
- [ ] 2 Vis kandidater    --propose 9 → list_kandidater.py → leseren stryker
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

## 2. Vis kandidater

```bash
docker compose run --rm -T generator python research_briefing.py --propose 9 > kandidater.json
python .claude/skills/forskning-uke/scripts/list_kandidater.py kandidater.json
```

9 per kategori: 7 skal skrives, 2 er slingringsmonn for det leseren stryker. Vis listen
slik scriptet skriver den, og be leseren svare med numrene som skal strykes.

## 3. Skriv omtaler

Les leserprofil, SKIP-regler, FORMAT og REGLER fra kilden — de endres der, ikke her:

```bash
sed -n '/^SYSTEM_PROMPT = """/,/^_STUDY_SEPARATOR/p' research_briefing.py
```

Per kategori: de 7 høyest scorede som ikke er strøket. Godkjente utover 7 blir liggende
som `scored` til neste uke — verken skriv eller stryk dem.

Skriv til `omtaler.md` i scratchpad, **6–7 omtaler per skriving** (en enkelt utskrift på 42
kappes). Én omtale = FORMAT-blokken: `## [tittel](URL)` med URL-en fra `kandidater.json`
uendret, så de fem `**…:**`-avsnittene. Skill blokkene med `\n\n---\n\n`. Grunnlaget er
abstractet i JSON-en; tall som ikke står der, finnes ikke.

Strøkne og ubrukelige studier får én linje i samme fil, så de aldri kommer tilbake:

```
## SKIP <url> — strøket av leser
## SKIP <url> — <grunn fra SKIP-reglene>
```

Vraker du en selv, skriv den neste godkjente i kategorien i stedet, så den fortsatt får 7.

## 4. Importér

```bash
docker compose run --rm -T generator python research_briefing.py --import-writeups - < omtaler.md
```

Bare blokker med kjent URL og alle fem avsnitt lagres; resten gis som `⚠`-linjer. Rett
filen og kjør igjen til det er null advarsler (lagrede hoppes over). Sluttlinja skal si
≥ 42 ferdigskrevne.

## 5. Oppsummer

Kun titlene som ble skrevet, gruppert per kategori, og hvor mange dager køen dekker.

## Fallgruver

- `docker compose run` uten `-T` feiler med fil på stdin.
- `docker compose run generator` uten kommando kjører hele dagsbriefingen (koster kvote).
- Rediger aldri `research_queue.json` direkte — all skriving går via `--import-writeups`.
- Går en kategori tom, skriv de andre likevel; publiseringen fyller dagen mykt.
