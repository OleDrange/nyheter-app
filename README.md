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

## Desktop-snarvei (Windows)

Gjør det mulig å kjøre briefingen med et dobbeltklikk fra skrivebordet.

**1. Oppdater stien i skriptet**

Åpne `create_shortcut.ps1` og endre de to hardkodede stiene til der du har klonet repoet:

```powershell
$bat = "C:\DIN\STI\nyheter-app\run_briefing.bat"
$sc.WorkingDirectory = "C:\DIN\STI\nyheter-app"
```

**2. Kjør skriptet**

```powershell
powershell -ExecutionPolicy Bypass -File create_shortcut.ps1
```

En snarvei kalt `Nyhetsbriefing.lnk` opprettes på skrivebordet. Dobbeltklikk for å kjøre.

## Konfigurasjon

| Hva | Hvor |
|---|---|
| RSS-feeds | `RSS_FEEDS`-dict øverst i `news_briefing.py` |
| Briefing-stil | `SYSTEM_PROMPT` i `news_briefing.py` |
| API-nøkler | `.env` |
