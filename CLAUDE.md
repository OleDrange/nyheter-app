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

## Endre RSS-feeds

Rediger `RSS_FEEDS`-dict øverst i `news_briefing.py`. Format: `"Kildenavn": "https://..."`.

Standardkilder inkluderer Dagens Næringsliv og Oslo Børs nyheter (offisielle børsmeldinger — kvartalstall, utbytte, kapitalforhøyelser).

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
