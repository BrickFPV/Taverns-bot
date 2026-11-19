"""
Tavern Bot - Başlatıcı
"""
import os
import sys

# .env dosyasını yükle
import env_loader

token = os.environ.get("BOT_TOKEN", "").strip()
print(f"Token okundu: {token[:10]}... (uzunluk: {len(token)})")

if not token:
    print("HATA: BOT_TOKEN bulunamadı!")
    sys.exit(1)

import bot

print("bot.main() çağrılıyor...")
bot.main()
