# Implementasjonsplan — lagrede studier («pin»)

Status: **leveranse 1 og 2 ferdig og deployet**. Sist oppdatert 2026-07-20.

Mål: kunne pinne en studie (senere også gåter og quiz) fra nettsiden, slik at den huskes
permanent og vises på en egen side med filter og søk.

## Beslutninger som ligger fast

| Spørsmål | Valg | Hvorfor |
|---|---|---|
| Eierskap | Delt liste på serveren, skriving beskyttet av kodeord | Overlever nettleserbytte og deles mellom enheter/personer, uten å legge et åpent skrive-endepunkt ut på internett |
| Omfang | Studier først, deretter gåter + quiz | Studier har en stabil nøkkel (DOI) og størst varig verdi |
| Metadata | Valgfri notis + tagger | Notisen er det som gjør listen verdt noe om seks måneder |
| Lagringsform | **Fullt øyeblikksbilde**, ikke referanse | Briefinger er immutable, så det er ingenting å synkronisere. Snapshot fjerner en hel feilklasse (manglende arkivfil, parsing-drift) og koster ~2 KB per studie |

## Arkitektur

### Volum

Nettappen er den eneste prosessen eksponert mot internett. Den skal fortsatt **ikke** kunne
skrive ett byte inn i briefing-arkivet:

```yaml
web:
  environment:
    # KUN kodeordet — bevisst ikke `env_file`, som ville gitt den internettvendte
    # containeren ANTHROPIC_API_KEY den ikke trenger.
    SAVE_PASSPHRASE: ${SAVE_PASSPHRASE:-}
  volumes:
    - briefing-data:/data:ro   # uendret, read-only
    - saved-data:/state        # nytt, skrivbart
```

Fil: `/state/saved.json`. `SAVED_DIR` env-var, default `.` lokalt (repo-lokal `state/`,
gitignored) — samme mønster som `BRIEFING_DATA_DIR` i generatoren.

### Samtidighet — må bygges inn fra start

Node er én prosess, men to samtidige POST-er kan interleave read-modify-write og miste en
lagring. **Alle skrivinger går gjennom én seriell promise-kø.** Skriving er atomisk
(`.tmp` + `rename`), som `store_briefing()`.

### Datamodell (`/state/saved.json`)

```json
{
  "version": 1,
  "items": [{
    "id": "study:10.3390/nu18091450",
    "type": "study",
    "date": "2026-07-20",
    "url": "https://doi.org/10.3390/nu18091450",
    "title": "Intermitterende faste med 16:8-spisevindu …",
    "category": "kosthold",
    "journal": "Nutrients",
    "snapshot": { "parts": [{ "label": "Metode", "html": "…", "text": "…" }] },
    "note": "",
    "tags": ["faste", "vekt"],
    "savedAt": "2026-07-20T18:03:00+02:00"
  }]
}
```

**ID-strategi** (nøkkelen til at lagringen er idempotent — samme sak lagret to ganger blir
én oppføring):

| Type | ID | Kilde |
|---|---|---|
| `study` | `study:<doi>` | DOI-en fra `url`, normalisert til små bokstaver |
| `riddle` | `riddle:<sha1(spørsmål)[0:12]>` | Innholdshash — gåter har ingen ID |
| `quiz` | `quiz:<sha1(spørsmål)[0:12]>` | Samme; overlever quizens repetisjonsmekanikk |

`searchText` bygges ved lagring (tittel + all brødtekst + notis + tagger), lagres på
oppføringen, og brukes av fritekstsøket.

### Autentisering

- `SAVE_PASSPHRASE` i `.env`. Sammenlignes med `crypto.timingSafeEqual`.
- Ved riktig kodeord settes cookie `lagret_auth` = `<utløp>.<HMAC-SHA256(utløp, SAVE_PASSPHRASE)>`.
  HttpOnly, Secure, SameSite=Lax, `Max-Age` 1 år. Ingen sesjonslagring, kan ikke forfalskes.
- **Lesing er åpent for alle. Kun skriving krever cookie.**
- Rate-limiting på innlogging: maks 10 forsøk per 15 min per IP, in-memory.
- Mangler `SAVE_PASSPHRASE` i miljøet → skriveendepunktene svarer 503 og pin-knappene
  skjules. Da feiler funksjonen synlig i stedet for å stå åpen.

### Ruter

| Rute | Metode | Auth | Beskrivelse |
|---|---|---|---|
| `/api/logg-inn` | POST | — | `{ passphrase }` → setter cookie |
| `/api/logg-inn` | DELETE | — | Sletter cookie |
| `/api/lagret` | POST | ✅ | Upsert på `id` (idempotent) |
| `/api/lagret` | DELETE | ✅ | `{ id }` |
| `/api/lagret` | PATCH | ✅ | `{ id, note?, tags? }` |
| `/lagret` | GET | — | Siden, med filter/søk via URL-parametre |

`/lagret` skal fungere på **begge** vertsnavn — `middleware.js` må slippe den gjennom uten
å prefikse med `/forskning`, siden siden spenner over begge sider.

### Siden `/lagret`

Filtrering **server-side via URL-parametre**: `?q=&type=&kategori=&tag=&sort=`.
Fungerer uten JS, URL-ene blir delbare/bokmerkbare, og det skalerer forbi noen tusen
oppføringer. På toppen et klient-side søkefelt som filtrerer det som allerede er rendret,
for umiddelbar respons.

- Sortering: nyest lagret først (default), eldst først, tittel.
- Tag-sky som klikkbare filtre.
- Tom tilstand som forklarer hvordan man pinner.

### Pinne-knappen

`SaveButton.astro` — ☆/★ på hvert studiekort. Serveren vet ved render hvilke ID-er som
allerede er lagret, så stjernen er fylt fra første paint (ingen blinking).

- Ikke innlogget → klikk åpner inline kodeord-felt, ikke stille feil.
- Notis/tagger **blokkerer ikke** pinningen: ett klikk lagrer, så folder det seg ut et felt
  som kan ignoreres.
- Avpinning er destruktivt (sletter notis + tagger) → toast med **angre**.
- Tagger normaliseres til små bokstaver, med autofullfør fra eksisterende tagger, ellers
  får vi «protein», «Protein» og «proteiner» som tre filtre innen en måned.

## Leveranse 1 — sjekkliste

- [x] `docker-compose.yml`: `saved-data`-volum + `SAVE_PASSPHRASE` på web
- [x] `.env`: `SAVE_PASSPHRASE`
- [x] `web/src/lib/saved.js`: lesing, seriell skrivekø, atomisk skriving, upsert/remove/patch, ID-bygging
- [x] `web/src/lib/auth.js`: HMAC-cookie, timingSafeEqual, rate-limit
- [x] `web/src/pages/api/logg-inn.js` (POST inn, DELETE ut)
- [x] `web/src/pages/api/lagret.js`: POST/DELETE/PATCH
- [x] `web/src/components/SaveButton.astro`
- [x] `web/src/components/ResearchList.astro`: pin-knapp per studie
- [x] `web/src/pages/lagret.astro`: liste, filter, søk, notis/tagg-redigering
- [x] `web/src/middleware.js`: `/lagret` på begge vertsnavn
- [x] `web/src/layouts/Base.astro`: navlenke
- [x] `web/src/styles/global.css`: stiler
- [x] `CLAUDE.md`: dokumentasjon + **backup-kommandoen må dekke `saved-data`**
- [x] Verifisert live på forskning.modr.no og nyheter.modr.no

## Leveranse 2 — ferdig

- [x] Gåter og quiz inn i samme modell (`RiddleCard`, `QuizCard`)
- [x] Eksport til markdown og JSON (gjør listen fri + ekstra backup)
- [x] «Dagens repetisjon»: løft én forfalt lagret studie tilbake på forsiden, etter samme
      mekanikk som `_QUIZ_REVIEW_INTERVALS`. En lagret-liste ingen leser er en kirkegård.

## Mulige videre steg

- Lagring av nyhetspunkter (krever egen identitetsløsning — de har ingen stabil nøkkel)
- Flere repetisjoner samtidig, eller egen repetisjonsside om listen vokser seg stor

## Risiko og fallgruver

| Risiko | Tiltak |
|---|---|
| Samtidige skrivinger mister data | Seriell skrivekø + atomisk rename |
| `saved.json` ikke i backup | Backup-kommandoen i CLAUDE.md **må** utvides — dette er de eneste dataene i systemet som ikke kan regenereres |
| Skriveendepunkt misbrukes | Kodeord + HMAC-cookie + rate-limiting; lesing åpen |
| Kodeord lekker til klienten | Kun serverside-sammenligning; cookien er HttpOnly og inneholder kun signatur |
| Volumet mangler ved deploy | Docker oppretter navngitte volumer automatisk ved `up -d` |
| Skriverettigheter i containeren | Verifiser at prosessbrukeren i `web/Dockerfile` kan skrive til `/state` |
