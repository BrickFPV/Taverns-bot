@echo off
chcp 65001 > nul
title Tavern Bot (Cross-Platform)
echo ========================================================
echo               TAVERN BOT BASLATILIYOR (Windows)
echo ========================================================

cd /d "%~dp0"

REM .env dosyasini yukle (PowerShell ile)
powershell -NoProfile -Command "
  if (Test-Path '.env') {
    Get-Content .env | ForEach-Object {
      if ($_ -match '^[^#].+=.+') {
        $parts = $_.Split('=', 2)
        [Environment]::SetEnvironmentVariable($parts[0].Trim(), $parts[1].Trim(), 'Process')
      }
    }
  }
"

REM Python kontrol
python -c "import sys; print('Python', sys.version.split()[0])" 2>nul
if errorlevel 1 (
    echo HATA: Python bulunamadi.
    pause
    exit /b
)

REM Bagimliliklar
python -c "import taverns, aiohttp, openai" 2>nul
if errorlevel 1 (
    echo Eksik paketler kuruluyor...
    python -m pip install -r requirements.txt
)

REM BOT_TOKEN kontrol
if not defined BOT_TOKEN (
    echo HATA: BOT_TOKEN .env dosyasinda tanimli degil!
    type .env.example
    pause
    exit /b
)

REM Calisan bot var mi?
powershell -NoProfile -Command "$p = Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object { $_.CommandLine -like '*poller.py*' }; if ($p) { exit 1 } else { exit 0 }"
if errorlevel 1 (
    echo.
    echo [!] Bot zaten calisiyor. Once acik olan bot penceresini kapatin.
    echo.
    pause
    exit /b
)

echo Bot baslatiliyor... (Kapatmak icin bu pencereyi kapatabilirsin)
echo.

python -u poller.py

echo.
echo Bot durdu. Hata mesaji varsa yukarida gorunur.
pause