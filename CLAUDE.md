# Nyhetsbriefing — CLAUDE.md

## Hva prosjektet er

Et standalone Python-script (`news_briefing.py`) som henter RSS-nyheter, oppsummerer dem med Claude AI, og publiserer til Notion. Ingen web-app, ingen pakkestruktur — bare én fil.

## Kjøre scriptet

```bash
python news_briefing.py          # print til terminal
python news_briefing.py --save   # lagrer også briefing_YYYY-MM-DD.md
```

På Windows: dobbeltklikk `run_briefing.bat`.

## Miljøvariabler

Kopier `.env.example` til `.env` og fyll inn verdiene. Scriptet leser `.env` automatisk.

| Variabel | Påkrevd | Beskrivelse |
|---|---|---|
| `ANTHROPIC_API_KEY` | Ja | Claude API-nøkkel |
| `NOTION_API_KEY` | Nei | Notion integration token |
| `NOTION_PARENT_PAGE_ID` | Nei | ID på Notion-siden å opprette undersider under |

## Viktige designvalg — ikke endre uten grunn

- **Modell:** `claude-sonnet-4-20250514` — ikke bytt til opus eller haiku.
- **Streaming:** Claude-output streames direkte til terminal, ikke bufret.
- **Notion er valgfritt:** publiseres kun hvis begge Notion-variabler er satt.
- **RSS-feil er myke:** én feed som feiler stopper ikke resten av kjøringen.
- **Artikler uten dato:** inkluderes alltid (kan ikke fastslå alder).
- **`MAX_PER_FEED = 15`:** beskytter mot feeds med hundrevis av innlegg.

## RSS-henting — teknisk

Feeds hentes med `httpx` (browser-lignende User-Agent + `follow_redirects=True`) og parseres av `feedparser`. Dette er nødvendig fordi mange norske nyhetsnettsteder blokkerer `feedparser`s standard bot-identifikasjon. Ikke bytt tilbake til `feedparser.parse(url)`.

```python
resp = httpx.get(url, headers=_FETCH_HEADERS, timeout=10, follow_redirects=True)
resp.raise_for_status()
feed = feedparser.parse(resp.content)
```

`_FETCH_HEADERS` er definert som konstant rett under `MAX_DESC_CHARS`.

## Aktive RSS-feeds (10 stk)

| Kilde | URL |
|---|---|
| NRK Nyheter | `https://www.nrk.no/toppsaker.rss` |
| NRK Siste | `https://www.nrk.no/nyheter/siste.rss` |
| Bergens Tidende | `https://www.bt.no/rss.xml` |
| E24 | `https://e24.no/rss2/` |
| E24 Børs og finans | `https://e24.no/rss2/?seksjon=boers-og-finans` |
| The Guardian World | `https://www.theguardian.com/world/rss` |
| The Guardian Business | `https://www.theguardian.com/business/rss` |
| BBC World | `http://feeds.bbci.co.uk/news/world/rss.xml` |
| BBC Business | `http://feeds.bbci.co.uk/news/business/rss.xml` |
| Dagens Næringsliv | `https://services.dn.no/api/feed/rss/` |

**Fjernede kilder og årsak:**
- Reuters: offentlige RSS-feeds stengt 2020.
- Finansavisen: tilbyr ikke offentlige RSS-feeds.
- Oslo Børs: ingen offentlig RSS (børsmeldinger er tilgjengelig via NewsWeb API, ikke RSS). Børsnyheter dekkes via E24 Børs og finans.

Ikke bruk `https://www.nrk.no/nyheter/rss.xml` (404), `https://e24.no/rss.xml` (404) eller `https://www.dn.no/rss.xml` (ugyldig) — disse er utdaterte URLer.

## Endre RSS-feeds

Rediger `RSS_FEEDS`-dict øverst i `news_briefing.py`. Format: `"Kildenavn": "https://..."`.
Bekreft alltid at en ny URL faktisk returnerer gyldig RSS (HTTP 200 + XML) før du legger den til.

## Markedssnapshot

`fetch_market_snapshot()` henter Brent, S&P 500, OBX-indeksen (`OBX.OL`), EUR/NOK og USD/NOK via `yfinance`.
Dataene vises i terminal og Notion men sendes **ikke** til Claude — Claude skal forklare *hvorfor* markedet beveget seg, ikke gjenta prisene.
Feil i markedsdata stopper ikke resten av kjøringen (myk feil, som vær).

## Justere briefingen

`SYSTEM_PROMPT` i `news_briefing.py` styrer hva Claude skriver. Stil: Bloomberg-terminal — tall og fakta, ingen fyllord.

## Avhengigheter

```bash
pip install -r requirements.txt
```

Krever Python 3.10+.
