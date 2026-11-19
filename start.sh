#!/usr/bin/env bash
# Tavern Bot - Cross-platform başlatıcı (Linux / Raspberry Pi OS)
# Kullanım: ./start.sh
# Windows için baslat.bat ayrı dosyadır.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Renkli çıktı
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

echo -e "${CYAN}========================================================${NC}"
echo -e "${CYAN}            TAVERN BOT - BASLATILIYOR (Linux)${NC}"
echo -e "${CYAN}========================================================${NC}"

# .env dosyasını yükle
if [[ -f .env ]]; then
    export $(grep -v '^#' .env | xargs) 2>/dev/null || true
fi

# Python sürümünü kontrol et
PYTHON_CMD="${PYTHON_CMD:-python3}"
if ! command -v "$PYTHON_CMD" &> /dev/null; then
    echo -e "${RED}HATA: $PYTHON_CMD bulunamadı. 'sudo apt install python3' ile kurun.${NC}"
    exit 1
fi

PY_VERSION=$("$PYTHON_CMD" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
echo -e "Python: ${GREEN}$PY_VERSION${NC}"

# Sanal ortam (venv) varsa kullan, yoksa sistem python
if [[ -d "venv" && -f "venv/bin/activate" ]]; then
    source venv/bin/activate
    echo -e "Sanal ortam: ${GREEN}aktif${NC}"
fi

# Bağımlılıkları kontrol et
if ! "$PYTHON_CMD" -c "import taverns, openai, aiohttp" &>/dev/null; then
    echo -e "${YELLOW}Eksik paketler kuruluyor...${NC}"
    "$PYTHON_CMD" -m pip install --upgrade pip -q
    "$PYTHON_CMD" -m pip install -r requirements.txt
fi

# BOT_TOKEN kontrolü
if [[ -z "${BOT_TOKEN:-}" ]]; then
    echo -e "${RED}HATA: BOT_TOKEN .env dosyasında tanımlı değil!${NC}"
    echo "Örnek .env:"
    cat .env.example
    exit 1
fi

# Çalışan bot var mı kontrol et (aynı dizindeki poller.py için)
if pgrep -f "poller\.py" > /dev/null; then
    echo -e "${YELLOW}Bot zaten çalışıyor. Önce 'pkill -f poller.py' ile durdurun.${NC}"
    exit 1
fi

# Log dizini
mkdir -p data logs

echo -e "${GREEN}Bot başlatılıyor... (Durdurmak için CTRL+C)${NC}"
echo ""

# UTF-8 zorla
export PYTHONIOENCODING=utf-8
export PYTHONUTF8=1

# Botu çalıştır (buffering olmadan -u)
exec "$PYTHON_CMD" -u poller.py