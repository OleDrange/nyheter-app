#!/bin/sh
# Kjører begge briefingene etter hverandre. Myk feil: at den ene feiler stopper
# ikke den andre (samme oppførsel som run_briefing_scheduled.bat på Windows).
set -u
echo "=== Generator start $(date '+%Y-%m-%d %H:%M:%S %Z') ==="
python news_briefing.py --save     || echo "!! news_briefing.py feilet (exit $?)"
python research_briefing.py --save || echo "!! research_briefing.py feilet (exit $?)"
python healthcheck.py              || echo "!! healthcheck.py feilet (exit $?)"
echo "=== Generator ferdig $(date '+%Y-%m-%d %H:%M:%S %Z') ==="
