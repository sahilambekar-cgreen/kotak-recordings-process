@echo off
cd /d "%~dp0"
rem Stop any copy still running from an earlier launch, so the browser never
rem talks to a server holding old code.
powershell -NoProfile -Command "Get-NetTCPConnection -LocalPort 5000 -State Listen -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }"
python -m pip install -q -r requirements.txt
python app.py
pause
