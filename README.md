# Nyhetsbriefing

Henter RSS-nyheter fra norske og internasjonale kilder, oppsummerer dem med Claude AI, og publiserer til Notion. Output er en Bloomberg-stil briefing delt inn i marked, norsk økonomi, Bergen og internasjonalt.

## Krav

- Python 3.10+
- Anthropic API-nøkkel
- (Valgfritt) Notion integration token + parent page ID

## Oppsett

```bash
# 1. Installer avhengigheter
pip install -r requirements.txt

# 2. Konfigurer API-nøkler
cp .env.example .env
# Rediger .env og fyll inn verdiene
```

## Kjøre

```bash
python news_briefing.py          # print til terminal
python news_briefing.py --save   # lagrer også briefing_YYYY-MM-DD.md
```

På Windows: dobbeltklikk `run_briefing.bat`.

## Desktop-snarvei (Windows)

Kjør følgende for å opprette en snarvei på skrivebordet:

```powershell
powershell -ExecutionPolicy Bypass -File create_shortcut.ps1
```

En snarvei kalt `Nyhetsbriefing.lnk` opprettes på skrivebordet. Dobbeltklikk for å kjøre.

> **Merk:** `create_shortcut.ps1` bruker hardkodede stier som peker til der repoet ble klonet. Åpne filen og oppdater stiene om du har klonet til en annen plassering.

## Kilder (10 RSS-feeds)

| Kilde | Seksjon |
|---|---|
| NRK Nyheter | Toppsaker |
| NRK Siste | Siste nytt |
| Bergens Tidende | Alle nyheter |
| E24 | Alle nyheter |
| E24 Børs og finans | Børs og finans |
| The Guardian World | Internasjonalt |
| The Guardian Business | Internasjonal økonomi |
| BBC World | Internasjonalt |
| BBC Business | Internasjonal økonomi |
| Dagens Næringsliv | Alle nyheter |

> Reuters ble fjernet (offentlige RSS-feeds stengt 2020). Finansavisen og Oslo Børs tilbyr ikke offentlige RSS-feeds.

## Konfigurasjon

| Hva | Hvor |
|---|---|
| RSS-feeds | `RSS_FEEDS`-dict øverst i `news_briefing.py` |
| Briefing-stil | `SYSTEM_PROMPT` i `news_briefing.py` |
| API-nøkler | `.env` |
