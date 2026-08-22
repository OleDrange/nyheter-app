# Tekstil-kunnskapsbasen (tekstil.modr.no)

Bakgrunnen, valgene og det som gjenstår. Løpende drift er dokumentert i `CLAUDE.md`.

## Hva den skal svare på

Grunnlaget for en klesbutikk som skal selge plagg som er **bedre for kroppen** og **varer
lenger**. Ikke en nyhetsstrøm — et beslutningsdokument som vokser: hvilke fibre og
behandlinger vi tar inn, hvilke vi utelukker, hvilken dokumentasjon vi krever av en
leverandør, og hvilken forskning hver av de beslutningene hviler på.

Fire temaer, alle valgt inn: **hud og helse**, **prosess- og kjemikaliebruk**, **kvalitet og
holdbarhet**, **miljø og livsløp**.

## Hvorfor to kilder, ikke én

Forskningsbriefingen henter alt fra Europe PMC. Det går ikke her, og det er den viktigste
enkeltinnsikten i designet:

| Tema | Europe PMC | OpenAlex |
|---|---|---|
| Hud, allergi, toksikologi | **godt dekket** — 2 800+ artikler i vinduet | — |
| Prosesskjemi, restkjemikalier | delvis | supplerer |
| Slitestyrke, fargeekthet, levetid | **nesten ingenting** | **godt dekket** |
| LCA, mikrofiber, resirkulering | fragmentert | **godt dekket** |

Tekstilteknisk forskning publiseres i *Textile Research Journal*, *Journal of Cleaner
Production* og lignende — tidsskrifter som ikke er indeksert i MEDLINE. Med bare PMC ville
to av de fire valgte temaene stått permanent tomme. OpenAlex er åpent, krever ingen nøkkel,
og dekker alle fag.

Begge normaliseres til samme artikkel-dict og går gjennom samme kø, samme scoring og samme
Claude-prompt. Kilden er kun et felt (`source`) som vises på studiekortet.

## Hvorfor kravene om RCT er borte

`research_briefing.py` krever `PUB_TYPE:"Randomized Controlled Trial" OR "Meta-Analysis" OR
"Systematic Review"`. Det er riktig der og feil her. Tekstilallergi dokumenteres gjennom
**patch-test-serier**, kohorter og eksponeringsmålinger; det finnes knapt randomiserte
forsøk på om en polyestergenser gir eksem. Krever man RCT, står emnene tomme.

Kvalitetskravet er derfor flyttet to steder:
1. **Lokal scoring** — tallsignaler, utvalgsstørrelse, gjenkjent studiedesign.
2. **`**Forbehold:**`-avsnittet** i hver omtale, som eksplisitt skal si hva designet ikke
   kan vise («målt i laboratorium, ikke på hud», «patch-test-serie på hudpoliklinikk
   overrepresenterer allergikere»).

## Hvorfor kunnskapsbase og ikke daglig briefing

En daglig briefing hadde vært billigere å bygge (nesten ren kopi av `research_briefing.py`),
men innsikten ville ligget spredt utover et arkiv. Her akkumuleres den: hver studie rulles
inn under 1–3 **emner**, og emnet re-syntetiseres når det har fått nok ny evidens.
Resultatet er ~34 oppslag som blir bedre over tid, pluss én kravside som samler
konsekvensene på tvers.

Emnene speiler hvordan et plagg faktisk spesifiseres mot en leverandør: **fiber**
(bomull, viskose, polyester …) og **behandling** (PFAS, formaldehyd, azofarger …), pluss
**kvalitet** (slitestyrke, fargeekthet) og **miljø** (mikrofiber, LCA).

## Kostnadsmodellen

Tre knapper, og bare den siste vokser med basen:

| Steg | Koster | Skalerer med |
|---|---|---|
| Henting + scoring | ingenting | — |
| Omtaler (`WRITEUP_BATCH_SIZE = 8`) | tokens én gang per studie, noensinne | tilsig |
| Syntese (`MAX_SYNTH_PER_RUN = 3`) | tokens per emneoppdatering | **basens størrelse** |

Syntesen bremses av to ting: et emne står ikke for tur før det har fått
`SYNTH_PENDING_MIN = 2` nye studier, og inputen kappes til de `SYNTH_MAX_STUDIES = 14`
nyeste omtalene. Uten begge ville et emne med 40 studier under seg kostet 40 omtaler i
input hver gang én ny kom inn.

## Målt ved bygging (22. august 2026)

Første `--dry-run` etter at scoringen var på plass:

```
pmc/hud               9 nye i kø (av 26 hentet)
pmc/allergener       72 nye i kø (av 400)
pmc/kjemikalier      90 nye i kø (av 400)
pmc/mikrofiber_helse 29 nye i kø (av 195)
openalex/holdbarhet  73 nye i kø (av 244)
openalex/prosess    125 nye i kø (av 400)
openalex/miljo       82 nye i kø (av 400)
openalex/komfort     26 nye i kø (av 300)
→ 506 i kø, 1 714 forkastet av lokal scoring
```

506 i kø mot 5 innrullet per kjøring er ~100 kjøringer med materiale før køen må fylles
igjen — og påfyllet skjer automatisk under `QUEUE_REFILL_BELOW = 40`.

**To støytyper måtte scores bort først** (begge lå i topp 15 i første forsøk):
- *Ren materialsyntese* — «Solvent-Free Synthesis of a Phosphorus-Based Flame Retardant».
  Kjemi om et molekyl, ikke om et plagg. → `_LAB_NOISE`.
- *Miljøstudier om hvor forurensningen havner* — «Global patterns of lake microplastic
  pollution» kom på 2. plass fordi den treffer emnet `mikrofiberutslipp` og er full av tall.
  Faglig god, men svarer på et annet spørsmål enn vårt. → `_OFF_TARGET`.

## Gjenstår

- [x] **DNS + Caddy.** `tekstil.modr.no` og `t.modr.no` (301 → tekstil) er live med
      sertifikat fra 22. august 2026. Siden nås fortsatt også på `nyheter.modr.no/tekstil`.
- [x] **Cron.** Kjører 05:00 daglig som siste steg i `docker-entrypoint.sh`, etter nyheter
      og forskning.
- [ ] **Regulering som egen kilde.** REACH-restriksjonslista og RAPEX/Safety Gate
      (tekstiltilbakekallinger) var en del av det opprinnelige valget, men er ikke bygget:
      begge er hentbare, men verken kø eller scoring passer på dem — en restriksjon er
      ikke en studie som skal vurderes, den er et faktum som skal stå i emnet. Riktig form
      er trolig et eget felt per emne (`regulering: [...]`) som fylles fra ECHAs
      nedlastbare restriksjonsliste, ikke via Claude.
- [ ] **Pinning.** `SaveButton`/`/lagret` er ikke koblet på tekstilstudier. Biblioteket
      utledes i dag fra briefing-arkivet (`library.js`), og kunnskapsbasen er en annen
      datakilde — det krever en ny type i `library.js`, ikke bare en knapp.
