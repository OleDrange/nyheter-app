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

## Aktive RSS-feeds (15 stk)

| Kilde | Kategori |
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
| VentureBeat AI | `https://venturebeat.com/category/ai/feed/` |
| MIT Technology Review | `https://www.technologyreview.com/feed/` |
| ScienceDaily | `https://www.sciencedaily.com/rss/top/science.xml` |
| ScienceDaily Helse | `https://www.sciencedaily.com/rss/health_medicine.xml` |
| STAT News | `https://www.statnews.com/feed/` |

**Fjernede kilder og årsak:**
- Reuters: offentlige RSS-feeds stengt 2020.
- Finansavisen: tilbyr ikke offentlige RSS-feeds.
- Oslo Børs: ingen offentlig RSS (børsmeldinger er tilgjengelig via NewsWeb API, ikke RSS). Børsnyheter dekkes via E24 Børs og finans.

Ikke bruk `https://www.nrk.no/nyheter/rss.xml` (404), `https://e24.no/rss.xml` (404) eller `https://www.dn.no/rss.xml` (ugyldig) — disse er utdaterte URLer.

## Endre RSS-feeds

Rediger `RSS_FEEDS`-dict øverst i `news_briefing.py`. Format: `"Kildenavn": "https://..."`.
Bekreft alltid at en ny URL faktisk returnerer gyldig RSS (HTTP 200 + XML) før du legger den til.

## Briefing-seksjoner (8 stk)

`SYSTEM_PROMPT` styrer hva Claude skriver. Stil: Bloomberg-terminal — tall og fakta, ingen fyllord. Maks 490 ord totalt.

| Seksjon | Innhold |
|---|---|
| 🏥 Helse og medisin | Legemiddelgodkjenninger, klinisk evidens, folkehelsevarsler |
| 🤖 AI og forskning | Nye modeller, gjennombrudd, AI-regulering |
| 🎯 Dagens intensjon | Én setning — viktigste observasjon fra dagens nyheter |
| 🧠 Visste du at | Historisk/faglig kontekst til én av dagens nyheter |
| 🌍 Internasjonalt | Geopolitikk, naturkatastrofer, G20-valg |
| 🇳🇴 Norsk økonomi | Norges Bank, oljesektor, kronekurs, norske børsselskaper |
| 📈 Marked og makro | Rentevedtak, inflasjon, handelskrig, kvartalstall |
| 🏙️ Bergen og Vestland | Kun direkte hverdagskonsekvens (kollektiv, vedtak, helse) |

`Dagens intensjon` og `Visste du at` bruker ikke kulepunkter — tekst skrives direkte under heading.

## Værvarsling Bergen

`fetch_bergen_weather()` bruker MET Norway Locationforecast API (`complete`-endepunktet — ikke `compact`, da UV-data kun er tilgjengelig i `complete`).

Returnerer:
- `summary` — nåværende vær + eventuell ettermiddagsendring
- `rain_hours` — tidsspenn der nedbør >= 1 mm/t resten av i dag
- `sun_periods` — sammenhengende klarvær-perioder (dagstid kl. 05–21)
- `max_uv` — høyeste UV-indeks (clear-sky) i dag
- `max_uv_hour` — timen med maks UV
- `max_temp` — høyeste temperatur i dag
- `max_temp_hour` — timen med maks temp
- `temp_0700` — temperatur kl. 07:00

Feil i værhenting stopper ikke resten av kjøringen (myk feil).

## Markedssnapshot

`fetch_market_snapshot()` henter Brent, S&P 500, OBX-indeksen (`OBX.OL`), EUR/NOK og USD/NOK via `yfinance`.
Dataene vises i terminal og Notion men sendes **ikke** til Claude — Claude skal forklare *hvorfor* markedet beveget seg, ikke gjenta prisene.
Feil i markedsdata stopper ikke resten av kjøringen (myk feil).

## Notion-struktur

Siden bygges opp i denne rekkefølgen:
1. Værseksjon (heading_1 + værsummary, klarvær-perioder, nedbørstimer, UV/temp-stats)
2. Markedssnapshot (Brent, S&P 500, OBX, EUR/NOK, USD/NOK)
3. Nyhetsinnhold fra Claude (8 seksjoner)

Notion godtar maks 100 blokker per API-kall — lange briefinger splittes automatisk.

## Avhengigheter

```bash
pip install -r requirements.txt
```

Krever Python 3.10+.
