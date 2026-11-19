#!/usr/bin/env bash
# Tavern Bot - GUI Başlatıcı (Linux / Raspberry Pi OS)
# Kullanım: ./start_gui.sh
# Windows için gui.bat ayrı dosyadır.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Renkli çıktı
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
CYAN='\033[0;36m'
NC='\033[0m'

echo -e "${CYAN}========================================================${NC}"
echo -e "${CYAN}        TAVERN BOT - YÖNETİM PANELİ (Linux)${NC}"
echo -e "${CYAN}========================================================${NC}"

# .env dosyasını yükle
if [[ -f .env ]]; then
    export $(grep -v '^#' .env | xargs) 2>/dev/null || true
fi

# Python sürümü
PYTHON_CMD="${PYTHON_CMD:-python3}"
if ! command -v "$PYTHON_CMD" &> /dev/null; then
    echo -e "${RED}HATA: $PYTHON_CMD bulunamadı.${NC}"
    exit 1
fi

# Sanal ortam
if [[ -d "venv" && -f "venv/bin/activate" ]]; then
    source venv/bin/activate
    echo -e "Sanal ortam: ${GREEN}aktif${NC}"
fi

# Bağımlılıklar
if ! "$PYTHON_CMD" -c "import aiohttp, taverns" &>/dev/null; then
    echo -e "${YELLOW}Eksik paketler kuruluyor...${NC}"
    "$PYTHON_CMD" -m pip install --upgrade pip -q
    "$PYTHON_CMD" -m pip install -r requirements.txt
fi

# BOT_TOKEN kontrolü
if [[ -z "${BOT_TOKEN:-}" ]]; then
    echo -e "${RED}HATA: BOT_TOKEN .env dosyasında tanımlı değil!${NC}"
    exit 1
fi

# Çalışan GUI var mı?
if pgrep -f "gui\.py" > /dev/null; then
    echo -e "${YELLOW}GUI zaten çalışıyor.${NC}"
    exit 1
fi

# Dizinler
mkdir -p data logs

export PYTHONIOENCODING=utf-8
export PYTHONUTF8=1

echo -e "${GREEN}Yönetim paneli başlatılıyor...${NC}"
LAN_IP=$(hostname -I 2>/dev/null | awk '{print $1}')
LAN_IP=${LAN_IP:-127.0.0.1}
echo -e "Adres (LAN/Wi-Fi): ${CYAN}http://${LAN_IP}:${GUI_PORT:-8787}${NC}"
echo -e "Adres (bu cihaz):  ${CYAN}http://127.0.0.1:${GUI_PORT:-8787}${NC}"
echo -e "Şifre: .env dosyasındaki GUI_PASSWORD"
echo -e "Kapatmak için: CTRL+C"
echo ""

exec "$PYTHON_CMD" -u gui.py