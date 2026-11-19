"""
Tavern Polling Bot (REST Poller)
================================
WebSocket event beklemek yerine REST API ile kanalları sürekli dinler
ve mesaj geldiği anda anında cevap üretir. %100 garantili çalışır.
"""

import os
import sys
import json
import random
import asyncio
import logging
from datetime import datetime
from pathlib import Path

import env_loader
from taverns.rest import RESTClient
from taverns.types import Message
from taverns.embed import EmbedBuilder
from openai import AsyncOpenAI
from games import GameEngine
from names import member_names, pretty, find_user_id
from platform_utils import ensure_utf8, get_data_dir, get_logs_dir, find_bot_pids

# Windows konsolu emoji basamazsa çökmesin
ensure_utf8()

# ─── Loglama ───────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("bot.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger("tavern-poller")

# ─── Ayarlar ───────────────────────────────────────────────────
BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
PREFIX = os.environ.get("BOT_PREFIX", "!")

AI_BASE_URL = os.environ.get("AI_BASE_URL", "http://192.168.1.114:20128/v1")
AI_API_KEY = os.environ.get("AI_API_KEY", "omniroute")
AI_MODEL = os.environ.get("AI_MODEL", "Freecombo")

if not BOT_TOKEN:
    logger.error("BOT_TOKEN bulunamadı!")
    sys.exit(1)

rest = RESTClient(BOT_TOKEN)
ai_client = AsyncOpenAI(base_url=AI_BASE_URL, api_key=AI_API_KEY)

DATA_DIR = get_data_dir()
LOGS_DIR = get_logs_dir()
DB_FILE = DATA_DIR / "database.json"

def load_db() -> dict:
    if DB_FILE.exists():
        try:
            with open(DB_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def save_db(data: dict):
    with open(DB_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

db = load_db()
db.setdefault("users", {})
save_db(db)

def get_user_profile(user_id: str) -> dict:
    if user_id not in db["users"]:
        db["users"][user_id] = {"coins": 100, "daily_last": None, "messages": 0}
        save_db(db)
    return db["users"][user_id]

# Mini oyun motoru (cüzdan, market, oyunlar)
engine = GameEngine(db, lambda: save_db(db))


async def send_game_result(tavern_id, channel_id, message, result):
    """GameEngine sonucunu kanala gönderir."""
    text = pretty(f"{result['title']}\n{result['text']}")
    await rest.send_message(tavern_id, channel_id, content=text, reply_to_id=message.id)

# ─── Yapay Zeka ────────────────────────────────────────────────
user_conversations: dict[str, list[dict]] = {}

async def ask_ai(user_id: str, prompt: str) -> str:
    history = user_conversations.setdefault(user_id, [])
    messages = [
        {
            "role": "system",
            "content": (
                "Sen Ege'nin Tav.gg topluluk sunucusunda çalışan kanka ruhlu, esprili, samimi bir Discord/Tavern botusun. "
                "Cıvık, yılışık ya da flörtöz olma ('canım', 'tatlım', 'öpüyorum' gibi laflar KESİNLİKLE kullanma). "
                "Biri 'naber' dediğinde erkek erkeğe normal bir arkadaş gibi 'İyidir kanka senden naber?', 'Yuvarlanıp gidiyoruz valla' gibi doğal, rahat ve havalı konuş. "
                "Kısa ve net cevaplar ver, destan yazma."
            ),
        }
    ]
    messages.extend(history[-4:])
    messages.append({"role": "user", "content": prompt})

    try:
        res = await ai_client.chat.completions.create(
            model=AI_MODEL,
            messages=messages,
            max_tokens=500,
            temperature=0.7,
        )
        ans = res.choices[0].message.content.strip()
        history.append({"role": "user", "content": prompt})
        history.append({"role": "assistant", "content": ans})
        if len(history) > 10:
            user_conversations[user_id] = history[-10:]
        return ans
    except Exception as e:
        logger.error(f"AI Hatası: {e}")
        return f"⚠️ OmniRoute modeline ulaşılamadı ({AI_MODEL}):\n`{e}`"

# ─── Mesaj İşleme ──────────────────────────────────────────────
def normalize_messages(raw) -> list:
    """
    Taverns.py sürümlerinde get_messages farklı tip döndürebilir:
    - list[Message]
    - dict: {"messages": [...]}
    - list[dict]
    - beklenmedik string/list karışımı

    Hepsini poller'in anlayacağı dict benzeri Message'a dönüştürür.
    """
    if isinstance(raw, dict):
        raw = raw.get("messages", raw)

    if not isinstance(raw, (list, tuple)):
        return []

    normalized = []
    for item in raw:
        if isinstance(item, str):
            continue

        if isinstance(item, dict):
            sender = item.get("sender") or {}
            normalized.append(Message(
                id=item.get("id", ""),
                channel_id=item.get("channelId", item.get("channel_id", "")),
                content=item.get("content", "") or "",
                sender_id=(sender.get("id", "") if isinstance(sender, dict)
                           else item.get("senderId", "")),
                tavern_id=item.get("tavernId", item.get("tavern_id", "")),
                reply_to_id=item.get("replyToId"),
                metadata=item.get("metadata") or {},
                sender_is_bot=(sender.get("isBot", False) if isinstance(sender, dict)
                               else item.get("senderIsBot", False)),
            ))
        elif hasattr(item, "id"):
            normalized.append(item)

    return normalized


async def handle_message(msg: Message, tavern_id: str, bot_id: str, bot_name: str, channel_name: str = ""):
    if msg.sender_id == bot_id or msg.sender_is_bot:
        return

    content = msg.content.strip()
    channel_id = msg.channel_id

    prof = get_user_profile(msg.sender_id)
    prof["messages"] += 1
    save_db(db)

    # ID yerine görünen ad kullanmak için isim önbelleğini tazele (5 dk önbellek)
    await member_names(rest)

    logger.info(f"📩 Mesaj alındı: [{channel_name or channel_id}] [{msg.sender_id}] {content}")

    # Bot etiketlenmiş mi?
    is_mention = (bot_id in content) or (f"@{bot_name}" in content) or ("@Ege's-bot" in content)
    if is_mention:
        clean = (
            content.replace(f"<@{bot_id}>", "")
            .replace(f"@{bot_name}", "")
            .replace("@Ege's-bot", "")
            .strip()
        )
        if clean:
            ans = await ask_ai(msg.sender_id, clean)
            await rest.send_message(tavern_id, channel_id, content=ans, reply_to_id=msg.id)
            return

    # Selamlama
    if content.lower() in ("sa", "selam", "merhaba"):
        await rest.send_message(tavern_id, channel_id, content=f"Aleyküm Selam! Hoş geldin 🍺", reply_to_id=msg.id)
        return

    if not content.startswith(PREFIX):
        return

    parts = content[len(PREFIX):].split(maxsplit=1)
    cmd = parts[0].lower() if parts else ""
    args = parts[1].strip() if len(parts) > 1 else ""

    if cmd == "ping":
        await rest.send_message(tavern_id, channel_id, content=f"🏓 Pong! Bot anında çalışıyor! AI: `{AI_MODEL}`", reply_to_id=msg.id)

    elif cmd in ("sor", "ai", "gpt"):
        if not args:
            await rest.send_message(tavern_id, channel_id, content="⚠️ Lütfen sorunu yaz! Örnek: `!sor gökyüzü neden mavidir?`", reply_to_id=msg.id)
            return
        ans = await ask_ai(msg.sender_id, args)
        await rest.send_message(tavern_id, channel_id, content=ans, reply_to_id=msg.id)

    elif cmd in ("unut", "reset"):
        user_conversations.pop(msg.sender_id, None)
        await rest.send_message(tavern_id, channel_id, content="✅ Sohbet hafızan sıfırlandı!", reply_to_id=msg.id)

    elif cmd == "zar":
        val = random.randint(1, 6)
        await rest.send_message(tavern_id, channel_id, content=f"🎲 Zar atıldı: **{val}**", reply_to_id=msg.id)

    elif cmd in ("fal", "falcı", "falci"):
        fallar = [
            "Bugün şansın yaver gidecek, yeni fırsatlara gözlerini aç! 🔮",
            "Bir dostun seni düşünüyor, ona mesaj atmanın tam vakti. 💌",
            "Acele karar verme, biraz sabır her şeyi çözecek. ⏳",
            "Tavern'da bugün harika bir sohbet seni bekliyor! 🍺",
            "Cebindeki altınlara dikkat et, riskli bahislere girme. 🪙",
            "Yakında beklenmedik güzel bir haber alacaksın. ✨",
        ]
        await rest.send_message(tavern_id, channel_id, content=f"🔮 **Günün Falı**\n{random.choice(fallar)}", reply_to_id=msg.id)

    elif cmd == "sec":
        secenekler = [s.strip() for s in args.split(",") if s.strip()]
        if len(secenekler) < 2:
            await rest.send_message(tavern_id, channel_id, content="⚠️ En az 2 seçenek yaz! Örnek: `!sec kebap, döner, pide`", reply_to_id=msg.id)
        else:
            await rest.send_message(tavern_id, channel_id, content=f"🎯 Benim tercihim: **{random.choice(secenekler)}**", reply_to_id=msg.id)

    elif cmd in ("botbilgi", "botinfo"):
        txt = (
            f"🤖 **Bot Bilgisi**\n"
            f"• Ad: **{bot_name}**\n"
            f"• ID: `{bot_id}`\n"
            f"• AI Model: `{AI_MODEL}`\n"
            f"• AI Sunucu: `{AI_BASE_URL}`\n"
            f"• Prefix: `{PREFIX}`\n"
            f"• Durum: 🟢 Çevrimiçi (REST poller)"
        )
        await rest.send_message(tavern_id, channel_id, content=txt, reply_to_id=msg.id)

    elif cmd in ("yt", "yazitura", "yazıtura"):
        tokens = args.split()
        choice = "yazi"
        amount = None
        for tok in tokens:
            low = tok.lower()
            if low in ("tura", "yazi", "yazı"):
                choice = "tura" if low == "tura" else "yazi"
            elif low.isdigit():
                amount = int(low)
        if amount is None:
            sonuc = random.choice(["Yazı", "Tura"])
            await rest.send_message(tavern_id, channel_id, content=f"🪙 Sonuç: **{sonuc}**!", reply_to_id=msg.id)
        else:
            await send_game_result(tavern_id, channel_id, msg, engine.coinflip(msg.sender_id, amount, choice))

    elif cmd in ("slot", "slots", "slotlar"):
        if not args:
            await rest.send_message(tavern_id, channel_id, content="🎰 Kullanım: `!slot <miktar>` — Örnek: `!slot 50`", reply_to_id=msg.id)
        else:
            await send_game_result(tavern_id, channel_id, msg, engine.slots(msg.sender_id, args.split()[0]))

    elif cmd in ("rulet", "roulette"):
        tokens = args.split()
        number = next((t for t in tokens if t.isdigit()), None)
        choice = next((t for t in tokens if not t.isdigit()), None)
        if not number or not choice:
            await rest.send_message(
                tavern_id, channel_id,
                content="🎡 Kullanım: `!rulet <kirmizi/siyah/tek/cift/0-36> <miktar>`\nÖrnek: `!rulet kirmizi 100`",
                reply_to_id=msg.id,
            )
        else:
            await send_game_result(tavern_id, channel_id, msg, engine.roulette(msg.sender_id, int(number), choice))

    elif cmd in ("tahmin", "guess"):
        tokens = args.split()
        number = next((t for t in tokens if t.isdigit()), None)
        others = [t for t in tokens if not t.isdigit()]
        if not number or not others:
            await rest.send_message(
                tavern_id, channel_id,
                content="🔢 Kullanım: `!tahmin <1-5> <miktar>` — Örnek: `!tahmin 3 50`",
                reply_to_id=msg.id,
            )
        else:
            await send_game_result(tavern_id, channel_id, msg, engine.guess(msg.sender_id, int(others[0]), int(number)))

    elif cmd in ("calis", "çalış", "work"):
        await send_game_result(tavern_id, channel_id, msg, engine.work(msg.sender_id))

    elif cmd in ("balik", "balık", "fish"):
        await send_game_result(tavern_id, channel_id, msg, engine.fish(msg.sender_id))

    elif cmd in ("soygun", "soy", "rob"):
        if not args:
            await rest.send_message(
                tavern_id, channel_id,
                content="🕵️ Kullanım: `!soygun @kullanıcı`\nHedefi etiketleyerek yaz: `!soygun @dorukkdev`",
                reply_to_id=msg.id,
            )
        else:
            hedef = find_user_id(args.split()[0])
            await send_game_result(tavern_id, channel_id, msg, engine.rob(msg.sender_id, hedef))

    elif cmd in ("market", "dukkan", "dükkan", "shop"):
        await rest.send_message(tavern_id, channel_id, content=engine.market_text(), reply_to_id=msg.id)

    elif cmd in ("satinal", "satınal", "satin_al", "buy"):
        if not args:
            await rest.send_message(tavern_id, channel_id, content="🛒 Kullanım: `!satinal <ürün>` — `!market` ile ürünlere bak.", reply_to_id=msg.id)
        else:
            await send_game_result(tavern_id, channel_id, msg, engine.buy(msg.sender_id, args.split()[0]))

    elif cmd in ("envanter", "çantam", "cantam", "inv"):
        await send_game_result(tavern_id, channel_id, msg, engine.inventory_text(msg.sender_id))

    elif cmd == "saat":
        now = datetime.now().strftime("%H:%M:%S - %d/%m/%Y")
        await rest.send_message(tavern_id, channel_id, content=f"🕐 Saat: **{now}**", reply_to_id=msg.id)

    elif cmd == "soyle" and args:
        await rest.send_message(tavern_id, channel_id, content=args)

    elif cmd in ("cuzdan", "cüzdan", "bakiye", "para"):
        hedef = find_user_id(args) if args else msg.sender_id
        await send_game_result(tavern_id, channel_id, msg, engine.wallet_text(hedef))

    elif cmd in ("gunluk", "günlük", "daily"):
        await send_game_result(tavern_id, channel_id, msg, engine.daily(msg.sender_id))

    elif cmd in ("kumar", "bahis"):
        if not args.isdigit() or int(args) <= 0:
            await rest.send_message(tavern_id, channel_id, content="⚠️ Geçerli miktar gir! Örnek: `!kumar 50`", reply_to_id=msg.id)
            return
        await send_game_result(tavern_id, channel_id, msg, engine.coinflip(msg.sender_id, int(args), "yazi"))

    elif cmd in ("gonder", "gönder", "transfer"):
        tokens = args.split()
        if len(tokens) < 2 or not tokens[-1].isdigit():
            await rest.send_message(tavern_id, channel_id, content="💸 Kullanım: `!gonder @kullanıcı <miktar>`", reply_to_id=msg.id)
            return
        hedef = find_user_id(tokens[0])
        miktar = int(tokens[-1])
        if hedef == msg.sender_id:
            await rest.send_message(tavern_id, channel_id, content="Kendine altın gönderemezsin!", reply_to_id=msg.id)
            return
        src = engine.profile(msg.sender_id)
        dst = engine.profile(hedef)
        if miktar <= 0:
            await rest.send_message(tavern_id, channel_id, content="Miktar 0'dan büyük olmalı.", reply_to_id=msg.id)
            return
        if src["coins"] < miktar:
            await rest.send_message(tavern_id, channel_id, content=f"❌ Yetersiz bakiye! Mevcut: **{src['coins']}**", reply_to_id=msg.id)
            return
        src["coins"] -= miktar
        dst["coins"] += miktar
        save_db(db)
        await rest.send_message(
            tavern_id, channel_id,
            content=pretty(f"💸 `{hedef}` kullanıcısına **{miktar} Altın** gönderildi!\nGüncel bakiyen: **{src['coins']}**"),
            reply_to_id=msg.id,
        )

    elif cmd in ("sirala", "sırala", "top", "liderler", "zenginler"):
        await rest.send_message(
            tavern_id, channel_id,
            content=f"🏆 **En Zenginler**\n{engine.leaderboard_text()}",
            reply_to_id=msg.id,
        )

    elif cmd in ("yardim", "yardım", "help"):
        txt = (
            "📜 **Tavern Bot — Tüm Komutlar**\n"
            "Prefix: `!`  •  Botu etiketleyerek de konuşabilirsin\n\n"
            "🤖 **Yapay Zeka (OmniRoute / Freecombo)**\n"
            "• `!sor <soru>` — AI'ya soru sor (`!ai`, `!gpt` de çalışır)\n"
            "• `!unut` — AI sohbet hafızanı sıfırla\n\n"
            "🎮 **Mini Oyunlar (para kazan!)**\n"
            "• `!slot <miktar>` — Slot makinesi (ikili 1.5x, üçlü 4x-25x)\n"
            "• `!yt <miktar> [yazi/tura]` — Bahisli yazı-tura (2x)\n"
            "• `!rulet <kirmizi/siyah/tek/cift/0-36> <miktar>`\n"
            "• `!tahmin <1-5> <miktar>` — Sayı tahmini (4.5x)\n"
            "• `!calis` — 30 dk'da bir 25-70 altın\n"
            "• `!balik` — 10 dk'da bir balık tut\n"
            "• `!soygun @kullanici` — Altın çal (15 dk bekleme, riskli)\n\n"
            "💰 **Ekonomi**\n"
            "• `!cuzdan [@kullanici]` — Bakiye ve oyun istatistikleri\n"
            "• `!gunluk` — Günlük 150 altın\n"
            "• `!kumar <miktar>` — Hızlı bahis (yazı-tura)\n"
            "• `!gonder @kullanici <miktar>` — Altın transferi\n"
            "• `!sirala` — En zenginler tablosu\n\n"
            "🛒 **Market**\n"
            "• `!market` — Ürünleri listele\n"
            "• `!satinal <urun>` — sans_tilsimi, cifte_altin, olta, hirsiz_maskesi\n"
            "• `!envanter` — Çantanı ve aktif etkilerini gör\n\n"
            "⚙️ **Genel**\n"
            "• `!ping` — Bot çalışıyor mu?\n"
            "• `!botbilgi` — Bot, model ve bağlantı bilgisi\n"
            "• `!saat` — Tarih ve saat\n"
            "• `!zar` — Zar at\n"
            "• `!fal` — Günün falı\n"
            "• `!sec <a, b, c>` — Seçenekler arasından seç\n"
            "• `!soyle <mesaj>` — Botu konuştur\n"
            "• `!yardim` / `!help` — Bu listeyi göster"
        )
        await rest.send_message(tavern_id, channel_id, content=txt, reply_to_id=msg.id)

# ─── Ana Poller Döngüsü ────────────────────────────────────────
async def main_loop():
    logger.info("Bot kimliği kontrol ediliyor...")
    bot_self = await rest.get_self()
    bot_id = bot_self.user.id
    bot_name = bot_self.name
    logger.info(f"✅ Giriş yapıldı: {bot_name} (ID: {bot_id})")

    taverns = bot_self.taverns
    if not taverns:
        logger.error("Bot hiçbir tavern'a eklenmemiş!")
        return

    # Kanalları bul
    tracked_channels = []
    for t in taverns:
        try:
            raw_c = await rest.request("GET", f"/taverns/{t.id}/channels")
            c_list = raw_c.get("channels", raw_c) if isinstance(raw_c, dict) else raw_c
            if not isinstance(c_list, list):
                continue
            for ch in c_list:
                if not isinstance(ch, dict):
                    continue
                if ch.get("type") in ("TEXT", "ANNOUNCEMENT"):
                    tracked_channels.append((t.id, ch["id"], ch["name"]))
        except Exception as e:
            logger.warning(f"Kanal listesi alınamadı: {e}")

    logger.info(f"👀 Toplam {len(tracked_channels)} kanal dinleniyor...")

    # Başlangıçta son mesaj ID'lerini kaydet (eski mesajları tekrar çalıştırmasın)
    seen_message_ids = set()
    for t_id, c_id, c_name in tracked_channels:
        try:
            raw_msgs = await rest.request(
                "GET",
                f"/taverns/{t_id}/channels/{c_id}/messages",
                params={"limit": "5"},
            )
            msgs = normalize_messages(raw_msgs)
            for m in msgs:
                if getattr(m, "id", ""):
                    seen_message_ids.add(m.id)
        except Exception:
            pass

    logger.info("🚀 Poller aktif! Yeni mesajlar bekleniyor...")
    print(f"✅ Bot aktif ve {len(tracked_channels)} kanalı anlık dinliyor! (tav.gg)")

    turn = 0
    while True:
        try:
            # Rate limit'e takılmamak için kanalları sırayla (round-robin) kontrol et.
            # 10 kanal x 1.8 sn => her kanal ~18 saniyede bir taranır.
            t_id, c_id, c_name = tracked_channels[turn % len(tracked_channels)]
            turn += 1
            try:
                raw_msgs = await rest.request(
                    "GET",
                    f"/taverns/{t_id}/channels/{c_id}/messages",
                    params={"limit": "10"},
                )
                msgs = normalize_messages(raw_msgs)
                for m in reversed(msgs):
                    msg_id = getattr(m, "id", "")
                    if msg_id and msg_id not in seen_message_ids:
                        seen_message_ids.add(msg_id)
                        if len(seen_message_ids) > 5000:
                            seen_message_ids.clear()
                        try:
                            await handle_message(m, t_id, bot_id, bot_name, c_name)
                        except Exception as e:
                            logger.error(
                                f"❗ Komut işlenemedi (kanal: {c_name} / {c_id}): {type(e).__name__}: {e}",
                                exc_info=True,
                            )
            except Exception as e:
                logger.warning(f"Kanal okuma hatası {c_name}: {type(e).__name__}: {e}")
        except Exception as e:
            logger.error(f"Döngü hatası: {e}")

        await asyncio.sleep(1.8)

if __name__ == "__main__":
    asyncio.run(main_loop())
