#!/bin/sh
# Kjører alle generatorene etter hverandre. Myk feil: at én feiler stopper ikke
# de andre (samme oppførsel som run_briefing_scheduled.bat på Windows).
#
# Rekkefølgen er ikke tilfeldig. Nyhetsbriefingen er det leseren faktisk venter på
# kl. 05, så den går først, deretter forskningsbriefingen. De to kunnskapsbasene
# (tilskudd og tekstil) går SIST fordi de er de eneste jobbene uten dagsfrist — de
# akkumulerer, så en dag uten kjøring koster ingenting, mens en hengende kjøring aldri
# skal kunne forsinke dagens briefing. Tilskudd før tekstil fordi den hører til
# forskningssiden og dermed er nærmere det leseren åpner om morgenen.
#
# Forskningsbriefingen kjører ALLTID med --no-claude: omtalene skrives ukentlig i Claude Code
# (skillen /forskning-uke, ingen API-kostnad) og cron publiserer kun fra det som ligger
# ferdigskrevet i køen. Går køen tom, utelates forskningsfeltet (myk feil) til neste ukeskjøring.
#
# PAUSE_KNOWLEDGE (i .env): kostnadspause for kunnskapsbasene — tilskudd og tekstil hoppes
# over helt. Fjern variabelen (eller sett den tom) for å starte opp igjen — ingen rebuild
# nødvendig, env_file leses ved hver `docker compose run`.
set -u
echo "=== Generator start $(date '+%Y-%m-%d %H:%M:%S %Z') ==="
python news_briefing.py --save     || echo "!! news_briefing.py feilet (exit $?)"
python research_briefing.py --save --no-claude || echo "!! research_briefing.py feilet (exit $?)"
if [ -n "${PAUSE_KNOWLEDGE:-}" ]; then
    echo "--- PAUSE_KNOWLEDGE er satt: tilskudd/tekstil hoppes over ---"
else
    python supplement_briefing.py      || echo "!! supplement_briefing.py feilet (exit $?)"
    python textile_briefing.py         || echo "!! textile_briefing.py feilet (exit $?)"
fi
python healthcheck.py              || echo "!! healthcheck.py feilet (exit $?)"
echo "=== Generator ferdig $(date '+%Y-%m-%d %H:%M:%S %Z') ==="
