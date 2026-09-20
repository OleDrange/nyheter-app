---
name: quiz-runde
description: Fyller quizbanken (quiz_bank/) på nyheter.modr.no med nye spørsmål skrevet i Claude Code uten API-kall — viser tilsiget per kategori, håndhever kvalitetskriteriene og dedup mekanisk, importerer og deployer. Bruk ved /quiz-runde, «flere quizspørsmål», «quizen gjentar seg», eller når generatorloggen sier «er tømt».
---

# Quizrunde

Målet er at hver kategori i `quiz_bank/` har **≥ 40 ubrukte spørsmål** når du er ferdig.
Generatoren trekker 7 ferske per dag fra 12 kategorier (~0,6 per kategori per dag), og når
en kategori er tom, serverer den stille sette spørsmål på nytt — det er «gjentakelsene».

Formålet med quizen er **allmennkunnskap og verdensforståelse** for to lesere rundt 35 (én
lege, én som trener aktivt). Ikke pugg, ikke trivia — et spørsmål er godt når leseren forstår
litt mer om hvordan verden henger sammen etter å ha lest fasit og forklaring.

Alle kommandoer kjøres fra `/root/nyheter-app`. Ingen API-kall, ingen `run generator`.

```
Fremdrift:
- [ ] 1 Status        quiz_status.py → hvor mange per kategori
- [ ] 2 Skriv         én kandidatfil per kategori i scratchpad, ≤ 20 per skriving
- [ ] 3 Validér       quiz_import.py --check → null feil
- [ ] 4 Importér      quiz_import.py → commit, push, build generator
- [ ] 5 Oppsummer     antall per kategori, dager dekket
```

## 1. Status

```bash
python3 .claude/skills/quiz-runde/scripts/quiz_status.py
```

Sluttlisten sier hvor mange som skal skrives per kategori, fordelt på easy/medium/hard.
Skriv det tallet — ikke mer for de som er fulle, ikke mindre for de som er tomme.

## 2. Skriv

Før du skriver en kategori, les det som ligger der, så du ikke gjentar temaer:

```bash
python3 .claude/skills/quiz-runde/scripts/quiz_import.py --list <slug>
```

Skriv til `kandidater/<slug>.json` i scratchpad, **maks 20 spørsmål per skriving** (splitt
kategorien i `<slug>.json` + `<slug>.2.json` om det trengs — importen stripper `.N`). Formatet er en liste:

```json
[
  {
    "difficulty": "medium",
    "question": "Hvorfor er Bergen mye mildere om vinteren enn Fairbanks i Alaska, som ligger på nesten samme breddegrad?",
    "answer": "Golfstrømmen og vestavinden frakter varme fra Atlanterhavet",
    "options": [
      "Golfstrømmen og vestavinden frakter varme fra Atlanterhavet",
      "Bergen ligger lavere over havet",
      "Norge har mer skydekke som holder på varmen",
      "Alaska ligger nærmere Nordpolen"
    ],
    "explanation": "Havet lagrer enorme mengder varme fra sommeren og avgir den langsomt. Vestavinden blåser den varmen inn over Vest-Europa, mens Alaska får kald luft fra innlandet. Fjern Golfstrømmen, og Norge får klima som Sør-Grønland."
  }
]
```

### Kriterier — alle fem må holde

1. **Forståelse, ikke oppslag.** Spør om *hvorfor*, *hvordan*, *hvor mye større*, *hva
   skjedde som følge av*, *hva kom først*. Årstall, paragrafer og forkortelser stryker
   mekanisk. Et årstall kan stå i spørsmålet som kontekst, aldri være svaret. Test: kan
   leseren bruke dette til å forstå en nyhet, et menneske eller et sted bedre?
2. **Størrelsesordener og sammenligninger foretrekkes.** «Hvor mange ganger større er
   Russland enn Norge?» bygger intuisjon; «hva er hovedstaden i Russland» gjør det ikke.
3. **`explanation` skal gi noe NYTT** — en mekanisme, en konsekvens, et tall, en
   sammenligning. Minst 60 tegn. Den skal aldri bare gjenta svaret. Den vises uansett om
   leseren svarte riktig, så den bærer halve læringen.
4. **Distraktorene skal friste én som halvvet svaret.** Samme type, samme lengde, plausible.
   Ikke «Euro / Daler / Norske mark». Riktig svar må ikke være det lengste.
5. **Svaret skal være sant om fem år.** Ingen sittende personer, «nyeste», rekorder som
   slås, eller tall som endrer seg årlig. Etablert kunnskap, ikke nyheter.

### Nivå

- **easy:** kjent tema, ukjent detalj eller mekanisme. Begge leserne skal *lære* noe også
  her — «hva heter Norges valuta» er ikke easy, det er ubrukelig.
- **medium:** krever at man kobler to ting, eller kjenner en mekanisme.
- **hard:** presis kunnskap eller kontraintuitivt svar. Ikke obskurt for obskurhetens skyld —
  det skal fortsatt være noe det er verdt å vite.
- **Medisin:** legen kan klinikken. Skriv om medisinhistorie, epidemiologi, mekanismer på
  befolkningsnivå, fysiologi i trening — ikke «hvilket organ produserer insulin».

### Kategorienes avgrensning — velg én, aldri to

| slug | hører hjemme | hører IKKE hjemme |
|---|---|---|
| `norsk_historie` | Norge før ca. 1990: hendelser, personer, årsaker | verdenskriger generelt (→ verdenshistorie) |
| `verdenshistorie` | verden utenfor Norge, alle epoker | kunstverk (→ kultur), oppfinnelser (→ teknologi/natur) |
| `geografi` | fysisk og human geografi: klima, elver, befolkning, hvorfor byer ligger der de ligger | pyramider/monumenter (→ verdenshistorie), BNP (→ økonomi) |
| `okonomi_og_verden` | hvordan økonomi, handel, energi, institusjoner (EU, WHO, sentralbanker) og geopolitikk virker | norske ordninger (→ norsk_samfunn) |
| `naturvitenskap` | fysikk, kjemi, biologi, astronomi, geologi | menneskekroppen (→ medisin), datateknikk (→ teknologi) |
| `norsk_samfunn` | Norge i dag: styresett, velferd, arbeidsliv, kultur, språk, hvordan ting *fungerer* | historie før 1990 (→ norsk_historie) |
| `filosofi_og_ideer` | filosofer, tankeretninger, religioner, etikk, vitenskapsteori, ideer som formet samfunn | psykologiske effekter (→ psykologi) |
| `teknologi_og_ai` | hvordan teknologi virker, AI, internett, teknologihistorie | forkortelser |
| `medisin_og_kropp` | fysiologi, sykdomsmekanismer, medisinhistorie, epidemiologi, trening og kropp | rene anatomispørsmål en lege kan i søvne |
| `psykologi_og_laering` | kognisjon, læring, atferd, skjevheter, sosialpsykologi | filosofi (→ filosofi_og_ideer) |
| `kultur_og_litteratur` | litteratur, kunst, musikk, film, arkitektur — verk og hvorfor de betyr noe | sport, teknologi |
| `sport` | idrettens fysiologi, historie, regler, taktikk, økonomi | rene resultater/årstall |

## 3. Validér

```bash
python3 .claude/skills/quiz-runde/scripts/quiz_import.py --check kandidater/*.json
```

Scriptet nekter alt som bryter det mekaniske: format, 4 alternativer, svar i alternativene,
årstall/paragraf/forkortelse, for kort forklaring, ustabile svar, duplikat eller nær-duplikat
mot banken og mot resten av runden. **Rett i kandidatfilen, aldri i banken.** Kjør til null
feil. Skjønnet i kriteriene (er dette faktisk forståelse?) er ditt — scriptet fanger bare
det som kan fanges.

## 4. Importér og deploy

```bash
python3 .claude/skills/quiz-runde/scripts/quiz_import.py kandidater/*.json
python3 .claude/skills/quiz-runde/scripts/quiz_status.py
git add quiz_bank && git commit -m "Quizrunde: +N spørsmål" && git push
docker compose build generator
```

Banken ligger i generator-imaget, så uten `build generator` bruker morgendagens cron den
gamle. `web` trenger ikke rebuild — den leser dagsfilen.

## 5. Oppsummer

Antall importert per kategori og nivå, og hvor mange dager den tynneste kategorien nå dekker.

## Fallgruver

- **Én import per fil, all-or-nothing.** Én feil holder hele filen tilbake, med vilje: en
  halvimportert runde gjør neste `--check` upålitelig.
- **Skriv ikke over 20 per skriving** — lengre utskrifter kappes, og en kappet JSON-fil
  importeres ikke.
- **Ny kategori** = ny fil `quiz_bank/<slug>.json` med `{"category": "Visningsnavn",
  "questions": []}` + linje i `_QUIZ_CATEGORY_ORDER` i `news_briefing.py` + rad i tabellen
  over. Slug endres aldri etterpå (dedup er på spørsmålstekst, så det er trygt, men
  rekkefølgen på siden endres).
- **Fjern aldri `explanation` fra gamle spørsmål** — repetisjonsutvelgelsen prioriterer dem.
- Slett svake gamle spørsmål fritt; `quiz_seen.json` ignorerer spørsmål som ikke finnes i
  banken lenger.
