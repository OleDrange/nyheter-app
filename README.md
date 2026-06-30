# Nyhets- og forskningsbriefing

Henter RSS-nyheter (19 kilder), ny forskning (Europe PMC), Bergen-vær (MET Norway) og
markedsdata (yfinance), oppsummerer med Claude AI, og publiserer en daglig briefing på
**https://nyheter.modr.no** — dagens utgave pluss arkiv over tidligere dager.

> Tidligere ble briefingen publisert til Notion. Nå er nettsiden den primære kanalen;
> Notion er valgfri (under utfasing).

## Arkitektur

To samvirkende deler i samme repo, koblet via ett delt datalager (et Docker-volum):

- **Generator** — `news_briefing.py` + `research_briefing.py` (Python). Kjører én gang i
  døgnet (cron 05:00), oppsummerer med Claude og skriver dagens briefing som JSON.
- **Nettside** — `web/` (Astro i SSR-modus på Node). Leser JSON-filene ved hver
  forespørsel og viser dagens briefing + arkiv. Ligger bak den delte Caddy-proxyen.

```
generator (cron 05:00) ──► /data/briefings/<dato>.json (volum) ──► web (Astro SSR) ──► Caddy ──► nyheter.modr.no
```

- Bakgrunn og full migreringsoppskrift: [MIGRERINGSPLAN.md](MIGRERINGSPLAN.md)
- Drift på VPS, oppdateringsflyt og fallgruver: [CLAUDE.md](CLAUDE.md)

## Krav

- Python 3.10+ (generator), Node 20+ (nettside), Docker + Docker Compose (drift)
- Anthropic API-nøkkel
- (Valgfritt) Notion integration token + parent page ID

## Kjøre lokalt

**Generator:**
```bash
pip install -r requirements.txt
cp .env.example .env          # fyll inn ANTHROPIC_API_KEY
python news_briefing.py          # print til terminal
python news_briefing.py --save   # + JSON til ./briefings/ + markdown-backup
python research_briefing.py --save
```

**Nettside** (mot lokalt genererte data):
```bash
cd web && npm install
# PowerShell — pek på mappa generatoren skrev til:
$env:BRIEFING_DIR="..\briefings"; npm run dev      # http://localhost:4321
```

## Drift på VPS (oppsummert)

Kjører på VPS-en bak `*.modr.no`-proxyen. Full oppskrift i
[MIGRERINGSPLAN.md](MIGRERINGSPLAN.md) §1.6; detaljer + oppdateringsflyt i
[CLAUDE.md](CLAUDE.md).

| | |
|---|---|
| Subdomene | `nyheter.modr.no` (redirect fra `nyheter.modr.online` og `n.modr.no`) |
| Proxy-alias / intern port | `nyheter-web:8080` |
| Daglig kjøring | cron 05:00 (Europe/Oslo): `docker compose run --rm generator` |
| Datalager | Docker-volum `briefing-data` → `/data/briefings/<dato>.json` |
| Web-tjeneste | `docker compose up -d web` (alltid oppe) |

## Oppdatere appen

```bash
# lokalt: rediger + test, så:
git add -A && git commit -m "..." && git push
# på VPS:
cd ~/nyheter-app && git pull && docker compose build && docker compose up -d web
```

- **Generator-endring** (RSS, prompt, vær …) → ny logikk brukes ved neste cron-kjøring
  (eller test nå med `docker compose run --rm generator`).
- **Nettside-endring** (`web/`) → ny design vises umiddelbart på *all* historikk, fordi
  SSR re-rendrer eksisterende JSON — ingen regenerering.

## Konfigurasjon

| Hva | Hvor |
|---|---|
| RSS-feeds | `RSS_FEEDS`-dict øverst i `news_briefing.py` |
| Briefing-stil og seksjoner | `SYSTEM_PROMPT` i `news_briefing.py` |
| Forskningstema | `EUROPE_PMC_QUERY` i `research_briefing.py` |
| API-nøkler | `.env` (gitignored, kun på VPS) |
| Nettsidens utseende | `web/src/` (layout, sider, lib) |

Fullstendig kildeliste, seksjonsregler og tekniske valg: se [CLAUDE.md](CLAUDE.md).
