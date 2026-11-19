"""
.env dosyasını yüklemek için yardımcı modül.
Bot'u başlatırken otomatik olarak .env dosyasını okur.
"""

import os
from pathlib import Path

def load_env():
    """Proje kök dizinindeki .env dosyasını yükler."""
    env_path = Path(__file__).parent / ".env"
    
    if not env_path.exists():
        return
    
    with open(env_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            # Boş satırları ve yorumları atla
            if not line or line.startswith("#"):
                continue
            
            if "=" in line:
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip()
                # Tırnak işaretlerini kaldır
                if value and value[0] in ('"', "'") and value[-1] == value[0]:
                    value = value[1:-1]
                os.environ.setdefault(key, value)

# Modül import edildiğinde otomatik yükle
load_env()
