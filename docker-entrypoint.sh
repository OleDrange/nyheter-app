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
set -u
echo "=== Generator start $(date '+%Y-%m-%d %H:%M:%S %Z') ==="
python news_briefing.py --save     || echo "!! news_briefing.py feilet (exit $?)"
python research_briefing.py --save || echo "!! research_briefing.py feilet (exit $?)"
python supplement_briefing.py      || echo "!! supplement_briefing.py feilet (exit $?)"
python textile_briefing.py         || echo "!! textile_briefing.py feilet (exit $?)"
python healthcheck.py              || echo "!! healthcheck.py feilet (exit $?)"
echo "=== Generator ferdig $(date '+%Y-%m-%d %H:%M:%S %Z') ==="
