@echo off
chcp 65001 >nul
title Nyhetsbriefing
cd /d "%~dp0"

if not exist ".env" (
    echo FEIL: .env-filen mangler.
    echo Kopier .env.example til .env og fyll inn API-noeyklene dine.
    pause
    exit /b 1
)

for /f "usebackq eol=# tokens=1,* delims==" %%A in (".env") do (
    if not "%%A"=="" set "%%A=%%B"
)

python news_briefing.py --save

echo.
pause
