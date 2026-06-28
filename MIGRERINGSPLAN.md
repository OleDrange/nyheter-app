# Migreringsplan — Nyhets- og forskningsnettside på VPS (Astro)

> **Mål:**
> 1. Flytte appen til VPS-en.
> 2. En **generator** kjører **kl. 05:00 hver dag** (norsk tid) og lager dagens briefinger.
> 3. Generatoren **slutter med Notion** og lagrer i stedet dagens briefinger som data.
> 4. En **Astro-nettside** (`nyheter.modr.online`) viser dagens briefing + **arkiv**.
>
> **Prioritet:** først få det til å **kjøre på serveren** (Fase 1, ren tekst er
> godt nok). Pynt (design, figurer) kommer i Fase 2.

---

## 0. Arkitektur

To samvirkende tjenester i samme repo, som deler **ett volum** (`briefing-data`):

| Tjeneste | Type | Kjører | Rolle |
|---|---|---|---|
| **`generator`** | Batch (Python) | kl. 05:00, så avslutter | Henter RSS/forskning/vær/marked, oppsummerer med Claude, **skriver én JSON-fil per dag** til volumet. Ingen Notion. |
| **`web`** | Astro SSR (Node), alltid oppe | kontinuerlig | Leser JSON-filene fra volumet **ved hver forespørsel** og serverer nettsiden. Bak Caddy-proxyen. |

**Hvorfor JSON-fil per dag + Astro SSR (ikke statisk bygg):** da slipper vi et
daglig bygge-steg. Generatoren legger bare en fil; den kjørende Astro-serveren
viser den på neste forespørsel. Ingen koordinering mellom Python og Node i cron.

```
   kl. 05:00 (cron)                 ┌──────────────────────────────┐
   ┌───────────────┐  skriver       │ Volum briefing-data          │
   │  generator    │ ─────────────► │  /data/briefings/<dato>.json │
   │ (Python, exit)│                └───────────────┬──────────────┘
   └───────────────┘                    leser (ro)  │ ved hver request
                                                     ▼
   nettleser ─HTTPS─► modr-proxy (Caddy) ─►  web (Astro SSR, Node)
   nyheter.modr.online  TLS+hostname over    nyheter-web:8080
                        `web`-nettet          HOST=0.0.0.0
```

**Plattform-kontrakten gjelder nå fullt ut for `web`** (DNS, proxy, `web`-nett,
alias, intern port). For `generator` gjelder den **ikke** (ingen porter, ikke på
`web`-nettet). De tre tingene å rapportere til plattformen:

| | Verdi |
|---|---|
| Subdomene | `nyheter.modr.online` |
| Alias på `web`-nettet | `nyheter-web` |
| Intern port | `8080` |

---

## 1. Tech stack (valgt)

- **Frontend:** **Astro 5** i **SSR-modus** med **`@astrojs/node`** (standalone).
  Server-renderer ved request → nytt innhold vises uten rebuild.
- **Innhold/handoff:** **én JSON-fil per dag** på det delte volumet
  (`/data/briefings/<dato>.json`). Ingen database, ingen native Node-avhengigheter.
- **Markdown → HTML:** `marked` (npm). Husk å normalisere `• ` → `- `.
- **Generator:** eksisterende Python beholdes; bytter kun *output* fra Notion til
  JSON-fil.
- **Figurer (Fase 2):** Chart.js som leser markeds-/værdata embeddet i siden.
  Markedstrend bygges av akkumulerte daglige snapshots — gratis fordi vi lagrer
  strukturert data hver dag.
- **Styling (Fase 2):** Tailwind eller håndskrevet CSS. Fase 1 er uten styling.

---

## 2. JSON-formatet (kontrakten mellom generator og web)

Én fil per dag: `/data/briefings/2026-06-27.json`

```json
{
  "date": "2026-06-27",
  "created_at": "2026-06-27T05:01:12",
  "news_md": "## 🏥 Helse og medisin\n• ...",
  "research_md": "## [Tittel](url)\n**Hva som ble gjort** ...",
  "weather": { "summary": "14°C, lettskyet ...", "rain_hours": [], "...": "..." },
  "market": { "brent": 81.2, "brent_chg": 0.6, "...": "..." },
  "research_items": [ { "title": "...", "url": "..." } ]
}
```

`news_briefing.py` skriver `news_md` + `weather` + `market`; `research_briefing.py`
skriver `research_md` + `research_items`. Begge **slår sammen** inn i samme
dagsfil (UPSERT på filnivå). Skriving er **atomisk** (skriv `.tmp`, så `os.replace`)
så web aldri leser en halvskrevet fil.

---

# FASE 1 — Få det til å kjøre på serveren (MVP, ren tekst)

Mål: `https://nyheter.modr.online` viser dagens briefing som tekst, og et arkiv.
Ingen styling, ingen figurer.

## 1.1 Endringer i generatoren (Python)

### A) Legg til lagringsfunksjon

Legg denne i `news_briefing.py` (og importer den i `research_briefing.py`, slik
forskningsscriptet allerede importerer hjelpere derfra):

```python
import json

def store_briefing(date_str, *, news_md=None, research_md=None,
                   weather=None, market=None, research_items=None):
    """Skriv/merge dagens briefing til /data/briefings/<date>.json (atomisk)."""
    data_dir = os.environ.get("BRIEFING_DATA_DIR", ".")
    out_dir = os.path.join(data_dir, "briefings")
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"{date_str}.json")

    data = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            data = json.load(f)

    data["date"] = date_str
    data["created_at"] = datetime.now().isoformat()
    if news_md is not None:        data["news_md"] = news_md
    if research_md is not None:     data["research_md"] = research_md
    if weather is not None:         data["weather"] = weather
    if market is not None:          data["market"] = market
    if research_items is not None:  data["research_items"] = research_items

    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)   # atomisk publisering
```

### B) Kall den i `main()`

- `news_briefing.py`, etter at `briefing` er laget:
  ```python
  store_briefing(today_str, news_md=briefing, weather=weather, market=market)
  ```
- `research_briefing.py`, etter at forskningsbriefingen er laget:
  ```python
  store_briefing(today_str, research_md=briefing, research_items=valgte_studier)
  ```
  (Bruk listen over studier scriptet allerede har; tom liste er greit.)

### C) Slå av Notion

Notion er allerede env-gated (publiserer kun hvis `NOTION_*` er satt). **La
`NOTION_API_KEY`/`NOTION_PARENT_PAGE_ID` stå tomme i `.env` på VPS-en** → ingen
Notion. (Selve koden kan fjernes senere når nettsiden er bekreftet.)

### D) Persistent data — KRITISK

`research_seen_dois.json` skrives i dag ved siden av scriptet → flyktig i
container. Gjør stien konfigurerbar (`research_briefing.py`, `_seen_path`, ~linje 110):

```python
def _seen_path():
    base = os.environ.get("BRIEFING_DATA_DIR") or os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, SEEN_FILE)
```

### E) Tidssone — KRITISK

Scriptene bruker `datetime.now()` / `.astimezone()` (lokaltid). På en UTC-VPS blir
dato, værvinduer og 05:00 feil. Løses med `TZ=Europe/Oslo` i containeren (1.3) +
riktig cron-sone (1.6). Ingen kodeendring.

## 1.2 Generator-Docker

**`Dockerfile`** (repo-rot):

```dockerfile
FROM python:3.12-slim
ENV TZ=Europe/Oslo PYTHONUNBUFFERED=1 PYTHONUTF8=1 BRIEFING_DATA_DIR=/data
RUN apt-get update && apt-get install -y --no-install-recommends tzdata ca-certificates \
    && ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN mkdir -p /data && chmod +x docker-entrypoint.sh
ENTRYPOINT ["./docker-entrypoint.sh"]
```

**`docker-entrypoint.sh`** (må committes med **LF**-linjeskift):

```sh
#!/bin/sh
set -u
echo "=== Generator start $(date '+%Y-%m-%d %H:%M:%S %Z') ==="
python news_briefing.py --save     || echo "!! news_briefing.py feilet (exit $?)"
python research_briefing.py --save || echo "!! research_briefing.py feilet (exit $?)"
echo "=== Generator ferdig $(date '+%Y-%m-%d %H:%M:%S %Z') ==="
```

## 1.3 Astro-nettsiden (`web/`)

### Bootstrap (kjør lokalt, gir riktige versjoner)

```bash
cd nyheter-app
npm create astro@latest web -- --template minimal --install --no-git --yes
cd web
npx astro add node --yes      # legger til @astrojs/node + setter output: 'server'
npm install marked
```

### Sett konfig — `web/astro.config.mjs`

```js
import { defineConfig } from 'astro/config';
import node from '@astrojs/node';

export default defineConfig({
  output: 'server',
  adapter: node({ mode: 'standalone' }),
});
// HOST/PORT settes som miljøvariabler i containeren (HOST=0.0.0.0, PORT=8080)
```

### Datalesing — `web/src/lib/briefings.js`

```js
import { readdir, readFile } from 'node:fs/promises';
import path from 'node:path';

const DIR = process.env.BRIEFING_DIR || '/data/briefings';

export async function listDates() {
  try {
    const files = await readdir(DIR);
    return files.filter(f => f.endsWith('.json'))
                .map(f => f.replace('.json', ''))
                .sort().reverse();             // nyeste først
  } catch { return []; }
}

export async function getBriefing(date) {
  try {
    return JSON.parse(await readFile(path.join(DIR, `${date}.json`), 'utf8'));
  } catch { return null; }
}
```

### Layout — `web/src/layouts/Base.astro`

```astro
---
const { title } = Astro.props;
---
<html lang="no">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>{title}</title>
  </head>
  <body>
    <nav><a href="/">I dag</a> · <a href="/arkiv">Arkiv</a></nav>
    <main><slot /></main>
  </body>
</html>
```

### Forside — `web/src/pages/index.astro`

```astro
---
import Base from '../layouts/Base.astro';
import { listDates, getBriefing } from '../lib/briefings.js';
import { marked } from 'marked';

const dates = await listDates();
const b = dates.length ? await getBriefing(dates[0]) : null;
const md = (s) => marked.parse((s || '').replace(/^•\s+/gm, '- '));
---
<Base title={b ? `Briefing ${b.date}` : 'Nyhetsbriefing'}>
  {b ? (
    <article>
      <h1>Nyhetsbriefing — {b.date}</h1>
      <Fragment set:html={md(b.news_md)} />
      <h1>Forskningsbriefing</h1>
      <Fragment set:html={md(b.research_md)} />
    </article>
  ) : (
    <p>Ingen briefing generert ennå.</p>
  )}
</Base>
```

### Arkiv — `web/src/pages/arkiv.astro`

```astro
---
import Base from '../layouts/Base.astro';
import { listDates } from '../lib/briefings.js';
const dates = await listDates();
---
<Base title="Arkiv">
  <h1>Arkiv</h1>
  <ul>{dates.map(d => <li><a href={`/b/${d}`}>{d}</a></li>)}</ul>
</Base>
```

### Enkeltdag — `web/src/pages/b/[date].astro`

```astro
---
import Base from '../../layouts/Base.astro';
import { getBriefing } from '../../lib/briefings.js';
import { marked } from 'marked';

const { date } = Astro.params;
const b = await getBriefing(date);
const md = (s) => marked.parse((s || '').replace(/^•\s+/gm, '- '));
if (!b) return new Response('Ikke funnet', { status: 404 });
---
<Base title={`Briefing ${b.date}`}>
  <article>
    <h1>Nyhetsbriefing — {b.date}</h1>
    <Fragment set:html={md(b.news_md)} />
    <h1>Forskningsbriefing</h1>
    <Fragment set:html={md(b.research_md)} />
  </article>
</Base>
```

### Web-Docker — `web/Dockerfile`

```dockerfile
# ---- bygg ----
FROM node:22-slim AS build
WORKDIR /app
COPY web/package.json ./
RUN npm install
COPY web/ .
RUN npm run build

# ---- kjør ----
FROM node:22-slim
ENV TZ=Europe/Oslo NODE_ENV=production HOST=0.0.0.0 PORT=8080 \
    BRIEFING_DIR=/data/briefings
WORKDIR /app
COPY --from=build /app/dist ./dist
COPY --from=build /app/node_modules ./node_modules
COPY --from=build /app/package.json ./package.json
EXPOSE 8080
CMD ["node", "./dist/server/entry.mjs"]
```

> `HOST=0.0.0.0` er et plattformkrav (ellers er den ikke nåbar fra proxyen). Det
> standalone Node-serveren respekterer `HOST`/`PORT`.

## 1.4 Compose og ignore-filer

**`docker-compose.yml`**:

```yaml
services:
  web:
    build:
      context: .
      dockerfile: web/Dockerfile
    restart: unless-stopped
    environment:
      TZ: Europe/Oslo
      HOST: 0.0.0.0
      PORT: "8080"
      BRIEFING_DIR: /data/briefings
    volumes:
      - briefing-data:/data:ro      # web leser kun
    networks:
      web:
        aliases:
          - nyheter-web             # navnet proxyen bruker
    # INGEN "ports:" — proxyen eier 80/443.

  generator:
    build: .
    profiles: ["batch"]             # `up -d` starter den IKKE; cron kjører `run`
    env_file: .env                  # ANTHROPIC_API_KEY (+ NOTION_* tomt = av)
    environment:
      TZ: Europe/Oslo
      BRIEFING_DATA_DIR: /data
    volumes:
      - briefing-data:/data         # generator skriver

networks:
  web:
    external: true                  # på verten: docker network create web

volumes:
  briefing-data:
```

**`.dockerignore`**:
```
.git
.env
.venv
venv/
__pycache__/
*.pyc
briefing_*.md
forskningsbrief_*.md
research_seen_dois.json
briefing_log.txt
*.bat
create_shortcut.ps1
web/node_modules
web/dist
```

**`.gitattributes`**:
```
* text=auto
*.sh text eol=lf
```

## 1.5 (Valgfritt) Importer eksisterende historikk

For at arkivet ikke skal starte tomt, lag `import_history.py` i repo-rot som leser
hver `briefing_*.md`, henter dato fra filnavnet og kaller `store_briefing(dato,
news_md=tekst)`. Kjør én gang: `docker compose run --rm generator python import_history.py`.

## 1.6 Deploy til VPS (ordnet rekkefølge)

```bash
# 1. Klon
git clone <repo-url> ~/nyheter-app && cd ~/nyheter-app

# 2. Hemmeligheter — ANTHROPIC_API_KEY påkrevd; la NOTION_* stå tomme
cp .env.example .env && nano .env

# 3. Sørg for det delte proxy-nettet
docker network create web 2>/dev/null || true

# 4. Bygg begge images
docker compose build

# 5. Første generator-kjøring NÅ (lager første JSON-fil)
docker compose run --rm generator

# 6. Start web-tjenesten
docker compose up -d web

# 7. Daglig cron kl. 05:00 (crontab -e):
#    CRON_TZ=Europe/Oslo
#    0 5 * * * cd /home/<bruker>/nyheter-app && /usr/bin/docker compose run --rm generator >> /home/<bruker>/nyheter-cron.log 2>&1
crontab -e

# 8. DNS: A-record nyheter.modr.online -> VPS-IP

# 9. Proxy: i modr-proxy/Caddyfile:
#      nyheter.modr.online {
#          encode gzip
#          reverse_proxy nyheter-web:8080
#      }
#    Last uten nedetid:
cd ~/modr-proxy && docker compose exec caddy caddy reload --config /etc/caddy/Caddyfile
```

> `CRON_TZ=Europe/Oslo` gjør at `0 5` = 05:00 norsk tid året rundt (DST). Krever
> Vixie-cron (Debian/Ubuntu). Alternativt `sudo timedatectl set-timezone Europe/Oslo`.

## 1.7 Verifisering (Fase 1 ferdig når alt er grønt)

- [ ] `docker compose run --rm generator` kjører grønt (vær, marked, artikler,
      Claude-stream i loggen).
- [ ] JSON-fila finnes:
      `docker compose run --rm generator ls -la /data/briefings`
- [ ] **Dedup persisterer:** kjør generatoren to ganger; forskningsbriefen
      gjentar ikke gårsdagens studier.
- [ ] Web svarer internt:
      `docker compose exec web node -e "fetch('http://localhost:8080/').then(r=>r.text()).then(t=>console.log(t.slice(0,300)))"`
- [ ] Etter DNS+proxy: `https://nyheter.modr.online` viser dagens tekst.
- [ ] `/arkiv` lister datoer; `/b/<dato>` åpner én dag.
- [ ] Dato er norsk (ikke UTC-forskjøvet rundt midnatt).
- [ ] Neste morgen: ny fil dukker opp 05:00 og forsiden oppdateres automatisk.

---

# FASE 2 — Pynt (etter at Fase 1 kjører)

Gjøres trinnvis uten å røre generatoren eller deploy-oppsettet:

1. **Styling:** `npx astro add tailwind` (eller egen CSS i `Base.astro`). Layout
   med seksjoner, typografi, mobilvennlig.
2. **Figurer (Chart.js):**
   - Markedstrend over tid: ny Astro-endepunkt/loader som leser alle
     `/data/briefings/*.json`, plukker `market`, og mater en Chart.js-graf.
   - Dagens vær: visualiser UV/temp/nedbør fra `weather`-objektet.
   - Vil du ha temp-/UV-kurve *gjennom dagen*: utvid `fetch_bergen_weather` til
     også å lagre timesserien i `weather`-objektet (liten generator-endring).
3. **Seksjonsvisning:** parse `news_md` i de syv seksjonene og vis dem som kort.
4. **Ytelse (valgfritt):** legg `Cache-Control` på SSR-svar, eller vurder
   inkrementell statisk generering senere. Sannsynligvis unødvendig.
5. **Notion-opprydding:** fjern Notion-koden fra scriptene når nettsiden er
   bekreftet stabil.

---

## Drift

- **Logger:** generator → `~/nyheter-cron.log`; web → `docker compose logs -f web`.
- **Oppdatering:** `git pull && docker compose build && docker compose up -d web`
  (generator-imaget bygges samtidig; cron bruker nytt image neste kjøring).
- **Backup av arkivet:** ta vare på `briefing-data`-volumet —
  `docker run --rm -v nyheter-app_briefing-data:/d -v $PWD:/b alpine tar czf /b/backup.tgz -C /d .`
- **Stille feil** er største risiko for en daglig jobb. Valgfri herding senere:
  varsle (e-post/webhook) hvis generatoren feiler eller ingen fil dukker opp innen 06:00.

## Sjekkliste (kort)

**Generator:** `store_briefing()` lagt til + kalt i begge `main()` · Notion av ·
`_seen_path()` bruker `BRIEFING_DATA_DIR` · `Dockerfile` + `docker-entrypoint.sh` (LF).

**Web (Astro):** bootstrappet i `web/` · `output:'server'` + node-adapter ·
`marked` installert · `lib/briefings.js` + `index/arkiv/[date]` + `Base.astro` ·
`web/Dockerfile` med `HOST=0.0.0.0 PORT=8080`.

**Docker/VPS:** `docker-compose.yml` (web på `web`-nett, alias `nyheter-web`,
intern port 8080; generator profil `batch`; volum `briefing-data`) · `.dockerignore`
· `.gitattributes` · `docker network create web` · cron 05:00 (`CRON_TZ=Europe/Oslo`)
· DNS + proxy site-blokk + `caddy reload`.

**Rapporter til plattform:** subdomene `nyheter.modr.online`, alias `nyheter-web`,
intern port `8080`.
```
