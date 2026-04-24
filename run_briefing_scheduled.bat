@echo off
chcp 65001 >nul
cd /d "%~dp0"

for /f "usebackq eol=# tokens=1,* delims==" %%A in (".env") do (
    if not "%%A"=="" set "%%A=%%B"
)

"C:\Users\oledr\AppData\Local\Programs\Python\Python312\python.exe" news_briefing.py --save >> "%~dp0briefing_log.txt" 2>&1
