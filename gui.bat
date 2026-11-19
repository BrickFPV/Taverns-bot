@echo off
chcp 65001 > nul
title Tavern Bot - Yonetim Paneli (Windows)
echo ========================================================
echo          TAVERN BOT - YONETIM PANELI (Windows)
echo ========================================================

cd /d "%~dp0"

REM .env dosyasini yukle
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

python -c "import sys; print('Python', sys.version.split()[0])" 2>nul
if errorlevel 1 (
    echo HATA: Python bulunamadi.
    pause
    exit /b
)

python -c "import aiohttp, taverns" 2>nul
if errorlevel 1 (
    echo Eksik paketler kuruluyor...
    python -m pip install -r requirements.txt
)

if not defined BOT_TOKEN (
    echo HATA: BOT_TOKEN .env dosyasinda tanimli degil!
    pause
    exit /b
)

REM Calisan GUI var mi?
powershell -NoProfile -Command "$p = Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object { $_.CommandLine -like '*gui.py*' }; if ($p) { exit 1 } else { exit 0 }"
if errorlevel 1 (
    echo.
    echo [!] GUI zaten calisiyor.
    echo.
    pause
    exit /b
)

echo Panel baslatiliyor... (Kapatmak icin bu pencereyi kapatabilirsin)
echo Adres: http://127.0.0.1:8787
echo Sifre: .env dosyasindaki GUI_PASSWORD
echo.

python -u gui.py

echo.
echo Panel durdu.
pause