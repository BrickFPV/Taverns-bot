"""
Taverns (tav.gg) Tam Donanımlı Bot
==================================
OmniRoute (hermes.local:20128/v1 - Freecombo) yapay zeka entegrasyonu,
ekonomi, eğlence ve moderasyon modülleriyle hazır bot.
"""

import os
import sys
import json
import random
import asyncio
import logging
from datetime import datetime
from pathlib import Path

from taverns import (
    Client, Intents, Interaction, Message, Member,
    EmbedBuilder,
)
from taverns.commands import command, option
from taverns.types import CommandOptionType
from openai import AsyncOpenAI
from games import GameEngine
from names import member_names, pretty, find_user_id

# Windows konsolu emoji basamazsa çökmesin
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# ─── Log Yapılandırması ─────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("bot.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger("tavern-bot")

# ─── Çevre Değişkenleri & Ayarlar ──────────────────────────────
PREFIX = os.environ.get("BOT_PREFIX", "!")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()

# OmniRoute Ayarları
AI_BASE_URL = os.environ.get("AI_BASE_URL", "http://hermes.local:20128/v1")
AI_API_KEY = os.environ.get("AI_API_KEY", "omniroute")
AI_MODEL = os.environ.get("AI_MODEL", "Freecombo")

ai_client = AsyncOpenAI(
    base_url=AI_BASE_URL,
    api_key=AI_API_KEY,
)

DATA_DIR = Path(__file__).parent / "data"
DATA_DIR.mkdir(exist_ok=True)
DB_FILE = DATA_DIR / "database.json"

# Kullanıcı başına son sohbet geçmişi (hafıza)
# {user_id: [{"role": "user", "content": "..."}, ...]}
user_conversations: dict[str, list[dict]] = {}

# ─── Basit Kalıcı Veri Tabanı (Ekonomi / İstatistik / Notlar) ────
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

# Mini oyun motoru (cüzdan, market, oyunlar)
engine = GameEngine(db, lambda: save_db(db))

def get_user_profile(user_id: str) -> dict:
    if user_id not in db["users"]:
        db["users"][user_id] = {
            "coins": 100,
            "daily_last": None,
            "reputation": 0,
            "messages": 0
        }
        save_db(db)
    return db["users"][user_id]

# ─── Yapay Zeka Çağrısı (OmniRoute / Freecombo) ──────────────────
async def ask_ai(user_id: str, prompt: str) -> str:
    """OmniRoute üzerinden Freecombo modeline istek atar."""
    history = user_conversations.setdefault(user_id, [])

    # Sistem promptu
    messages = [
        {
            "role": "system",
            "content": (
                "Sen Taverns (tav.gg) topluluğu için tasarlanmış neşeli, cana yakın, "
                "yardımsever ve esprili bir taverna botusun. Adın Tavern AI. "
                "Cevaplarını net, samimi ve gereksiz uzatmadan ver. Türkçe yanıtla."
            ),
        }
    ]

    # Son 4 mesajı geçmiş olarak ekle
    messages.extend(history[-4:])
    messages.append({"role": "user", "content": prompt})

    try:
        response = await ai_client.chat.completions.create(
            model=AI_MODEL,
            messages=messages,
            max_tokens=600,
            temperature=0.7,
        )
        answer = response.choices[0].message.content.strip()

        # Geçmişe kaydet
        history.append({"role": "user", "content": prompt})
        history.append({"role": "assistant", "content": answer})
        if len(history) > 10:
            user_conversations[user_id] = history[-10:]

        return answer
    except Exception as e:
        logger.error(f"OmniRoute AI Hatası: {e}")
        return f"⚠️ OmniRoute modeline ulaşılamadı ({AI_MODEL}):\n`{e}`"

# ─── Client Tanımlama ──────────────────────────────────────────
client = Client(
    intents=(
        Intents.MESSAGES
        | Intents.MESSAGE_CONTENT
        | Intents.INTERACTIONS
        | Intents.MEMBERS
        | Intents.MODERATION
        | Intents.CHANNELS
        | Intents.REACTIONS
        | Intents.DIRECT_MESSAGES
    )
)

# ─── Embed Yardımcı Fonksiyonları ───────────────────────────────
def make_embed(title: str, description: str, color: str = "#4A90E2", fields=None, footer: str = None) -> dict:
    b = EmbedBuilder().set_title(pretty(title)).set_description(pretty(description)).set_color(color)
    if footer:
        b.set_footer(footer)
    if fields:
        for f in fields:
            b.add_field(f["name"], pretty(f["value"]), inline=f.get("inline", False))
    return b.build()

def success_embed(title: str, description: str) -> dict:
    return make_embed(f"✅ {title}", description, color="#2ECC71")

def error_embed(title: str, description: str) -> dict:
    return make_embed(f"❌ {title}", description, color="#E74C3C")

def warn_embed(title: str, description: str) -> dict:
    return make_embed(f"⚠️ {title}", description, color="#F39C12")


async def send_game(tavern_id, channel_id, result: dict, reply_to_id=None):
    """GameEngine sonucunu embed olarak kanala gönderir."""
    await client.send_message(tavern_id, channel_id, content="", embeds=[game_embed(result)], reply_to_id=reply_to_id)


def game_embed(result: dict) -> dict:
    return make_embed(result["title"], result["text"], color=result.get("color", "#F39C12"))


async def _refresh_names_loop():
    """Kullanıcı adı önbelleğini 5 dakikada bir tazeler."""
    while True:
        await asyncio.sleep(300)
        try:
            await member_names(client.rest, force=True)
        except Exception:
            pass


# ═══════════════════════════════════════════════════════════════
#  BOT OLAYLARI (EVENTS)
# ═══════════════════════════════════════════════════════════════

@client.event
async def on_ready():
    logger.info(f"🚀 Bot başarıyla bağlandı: {client.user.display_name} (ID: {client.user.id})")
    logger.info(f"🤖 OmniRoute AI Aktif: {AI_BASE_URL} (Model: {AI_MODEL})")
    logger.info(f"🏰 Bağlı Tavern sayısı: {len(client.taverns)}")

    # Kullanıcı adları önbelleğini doldur ve 5 dakikada bir tazele
    try:
        await member_names(client.rest, force=True)
    except Exception as e:
        logger.warning(f"Üye adları alınamadı: {e}")
    asyncio.create_task(_refresh_names_loop())

    # Slash komutlarını kaydet
    await client.register_commands([
        # Yapay Zeka (OmniRoute - Freecombo)
        command("sor", "OmniRoute yapay zekasına (Freecombo) bir soru sor", options=[
            option("soru", "Yapay zekaya sormak istediğin soru", CommandOptionType.STRING, required=True),
        ]),
        command("unut", "Yapay zeka ile olan sohbet hafızanı sıfırlar"),

        # Genel & Bilgi
        command("ping", "Botun çalışma durumunu ve gecikmesini kontrol eder"),
        command("yardim", "Tüm komutların rehberini gösterir"),
        command("botbilgi", "Bot ve sistem istatistiklerini görüntüler"),
        command("saat", "Anlık tarih ve saat bilgisini verir"),

        # Eğlence & Mini Oyunlar
        command("zar", "1 ile 100 arasında veya 6'lık zar atar", options=[
            option("maksimum", "Zarın üst sınırı (varsayılan: 6)", CommandOptionType.INTEGER),
        ]),
        command("yazitura", "Yazı mı tura mı atışı yapar (bahisli oynanabilir)", options=[
            option("miktar", "Bahis miktarı (boş bırakırsan sadece yazı-tura atar)", CommandOptionType.INTEGER),
            option("secim", "Tahmininiz: yazi veya tura", CommandOptionType.STRING),
        ]),
        command("sec", "Verilen seçenekler arasından rastgele seçim yapar", options=[
            option("liste", "Virgülle ayrılmış seçenekler (ör: elma, armut, muz)", CommandOptionType.STRING, required=True),
        ]),
        command("fal", "Günün şans/tavsiye mesajını söyler"),
        command("soyle", "Bota istediğin bir metni söyletir", options=[
            option("metin", "Söylenecek metin", CommandOptionType.STRING, required=True),
        ]),

        # Ekonomi & Profil
        command("cuzdan", "Kendi veya başka birinin bakiyesini gösterir", options=[
            option("kullanici", "Hedef kullanıcı ID", CommandOptionType.USER),
        ]),
        command("gunluk", "Günlük 150 altın ödülünü alır"),
        command("kumar", "Belirli miktar parayı bahse koyar (Kazanma şansı 50%)", options=[
            option("miktar", "Bahis miktarı", CommandOptionType.INTEGER, required=True),
        ]),
        command("gonder", "Başka bir üyeye altın transfer eder", options=[
            option("kullanici", "Gönderilecek kullanıcı ID", CommandOptionType.USER, required=True),
            option("miktar", "Gönderilecek altın miktarı", CommandOptionType.INTEGER, required=True),
        ]),
        command("sirala", "En zengin kullanıcıların liderlik tablosunu gösterir"),

        # Mini Oyunlar (altın kazan / harca)
        command("slot", "Slot makinesi çevirir (üçlü = 4x-25x)", options=[
            option("miktar", "Bahis miktarı", CommandOptionType.INTEGER, required=True),
        ]),
        command("rulet", "Rulet oynar", options=[
            option("secim", "kirmizi, siyah, tek, cift veya 0-36 arası sayı", CommandOptionType.STRING, required=True),
            option("miktar", "Bahis miktarı", CommandOptionType.INTEGER, required=True),
        ]),
        command("tahmin", "1-5 arası sayı tahmin oyunu (4.5x ödeme)", options=[
            option("sayi", "Tahminin (1-5)", CommandOptionType.INTEGER, required=True),
            option("miktar", "Bahis miktarı", CommandOptionType.INTEGER, required=True),
        ]),
        command("calis", "30 dakikada bir altın kazanmak için çalışır"),
        command("balik", "Balık tutup altın kazanır (10 dk bekleme)"),
        command("soygun", "Başka bir üyeyi soymaya çalışır (riskli)", options=[
            option("kullanici", "Soyulacak kullanıcı ID", CommandOptionType.USER, required=True),
        ]),
        command("market", "Altınla alınabilecek ürünleri listeler"),
        command("satinal", "Marketten ürün satın alır", options=[
            option("urun", "Ürün anahtarı (sans_tilsimi, cifte_altin, olta, hirsiz_maskesi)", CommandOptionType.STRING, required=True),
        ]),
        command("envanter", "Envanterini ve aktif etkilerini gösterir"),

        # Moderasyon
        command("temizle", "Kanalda son N mesajı siler", options=[
            option("adet", "Silinecek mesaj sayısı (1-50)", CommandOptionType.INTEGER, required=True),
        ]),
        command("kick", "Belirtilen üyeyi tavern'dan atar", options=[
            option("kullanici", "Hedef kullanıcı ID", CommandOptionType.USER, required=True),
            option("sebep", "Atılma gerekçesi", CommandOptionType.STRING),
        ]),
        command("ban", "Belirtilen üyeyi tavern'dan yasaklar", options=[
            option("kullanici", "Hedef kullanıcı ID", CommandOptionType.USER, required=True),
            option("sebep", "Yasaklama gerekçesi", CommandOptionType.STRING),
        ]),
        command("unban", "Kullanıcının yasağını kaldırır", options=[
            option("kullanici", "Kullanıcı ID", CommandOptionType.USER, required=True),
        ]),
        command("mute", "Kullanıcıyı süreli/süresiz susturur", options=[
            option("kullanici", "Hedef kullanıcı ID", CommandOptionType.USER, required=True),
            option("dakika", "Susturma süresi (dakika)", CommandOptionType.INTEGER),
            option("sebep", "Gerekçe", CommandOptionType.STRING),
        ]),
        command("unmute", "Kullanıcının susturmasını kaldırır", options=[
            option("kullanici", "Hedef kullanıcı ID", CommandOptionType.USER, required=True),
        ]),
    ])
    logger.info("✨ Tüm Slash komutları başarıyla güncellendi!")


@client.event
async def on_member_join(member: Member):
    logger.info(f"👋 Yeni üye katıldı: {member.user_id} @ Tavern {member.tavern_id}")


@client.event
async def on_member_leave(member: Member):
    logger.info(f"🚪 Üye ayrıldı: {member.user_id} @ Tavern {member.tavern_id}")


# ═══════════════════════════════════════════════════════════════
#  MESAJ YÖNETİMİ & PREFIX KOMUTLARI
# ═══════════════════════════════════════════════════════════════

@client.event
async def on_message_create(message: Message):
    if message.sender_id == client.user.id:
        return

    await member_names(client.rest)

    # İstatistik sayacı
    prof = get_user_profile(message.sender_id)
    prof["messages"] += 1
    save_db(db)

    content = message.content.strip()
    tavern_id = message.tavern_id
    channel_id = message.channel_id

    # Bot etiketlenirse otomatik yapay zeka yanıtı versin
    if client.user.id in content:
        temiz_soru = content.replace(f"<@{client.user.id}>", "").replace(f"@{client.user.display_name}", "").strip()
        if temiz_soru:
            await client.start_typing(tavern_id, channel_id)
            cevap = await ask_ai(message.sender_id, temiz_soru)
            emb = make_embed(
                f"🤖 OmniRoute AI ({AI_MODEL})",
                cevap,
                color="#8E44AD",
                footer=f"Model: {AI_MODEL} | hermes.local"
            )
            await client.send_message(tavern_id, channel_id, content="", embeds=[emb], reply_to_id=message.id)
            return

    if not content.startswith(PREFIX):
        return

    parts = content[len(PREFIX):].split(maxsplit=1)
    cmd = parts[0].lower() if parts else ""
    args = parts[1].strip() if len(parts) > 1 else ""

    try:
        # !sor <soru> (OmniRoute - Freecombo)
        if cmd in ("sor", "ai", "gpt"):
            if not args:
                await client.send_message(tavern_id, channel_id, content="⚠️ Lütfen sormak istediğin soruyu yaz! Örnek: `!sor evrenin sırrı nedir?`")
                return
            await client.start_typing(tavern_id, channel_id)
            cevap = await ask_ai(message.sender_id, args)
            emb = make_embed(
                f"🧠 Soru: {args[:50]}...",
                cevap,
                color="#8E44AD",
                footer=f"Model: {AI_MODEL} | OmniRoute"
            )
            await client.send_message(tavern_id, channel_id, content="", embeds=[emb], reply_to_id=message.id)

        # !unut
        elif cmd in ("unut", "reset"):
            if message.sender_id in user_conversations:
                user_conversations[message.sender_id] = []
            emb = success_embed("Hafıza Sıfırlandı", "Yapay zeka ile olan sohbet geçmişin temizlendi!")
            await client.send_message(tavern_id, channel_id, content="", embeds=[emb])

        # !ping
        elif cmd == "ping":
            emb = success_embed("Pong!", f"🏓 Bot aktif! AI Motoru: `{AI_MODEL} @ {AI_BASE_URL}`")
            await client.send_message(tavern_id, channel_id, content="", embeds=[emb])

        # !yardim
        elif cmd in ("yardim", "help"):
            emb = make_embed(
                "📜 Bot Komut Kılavuzu",
                f"Tüm komutları hem `{PREFIX}komut` hem de `/komut` şeklinde kullanabilirsin.",
                color="#3498DB",
                fields=[
                    {"name": "🤖 Yapay Zeka (OmniRoute)", "value": f"`{PREFIX}sor <soru>` (Freecombo modeli ile konuş), `{PREFIX}unut` (hafızayı sil)"},
                    {"name": "⚙️ Genel", "value": f"`{PREFIX}ping`, `{PREFIX}yardim`, `{PREFIX}botbilgi`, `{PREFIX}saat`"},
                    {"name": "🎲 Eğlence", "value": f"`{PREFIX}zar [sayi]`, `{PREFIX}sec <a, b>`, `{PREFIX}fal`, `{PREFIX}soyle <mesaj>`"},
                    {"name": "🎮 Mini Oyunlar", "value": f"`{PREFIX}slot <miktar>`, `{PREFIX}yt <miktar>`, `{PREFIX}rulet <secim> <miktar>`, `{PREFIX}tahmin <1-5> <miktar>`, `{PREFIX}calis`, `{PREFIX}balik`, `{PREFIX}soygun <id>`"},
                    {"name": "💰 Ekonomi", "value": f"`{PREFIX}cuzdan`, `{PREFIX}gunluk`, `{PREFIX}kumar <miktar>`, `{PREFIX}gonder <id> <miktar>`, `{PREFIX}sirala`"},
                    {"name": "🛒 Market", "value": f"`{PREFIX}market`, `{PREFIX}satinal <urun>`, `{PREFIX}envanter`"},
                    {"name": "🛡️ Moderasyon", "value": "`/kick`, `/ban`, `/unban`, `/mute`, `/unmute`, `/temizle <sayi>`"},
                ],
                footer="OmniRoute & taverns.py ile güçlendirildi"
            )
            await client.send_message(tavern_id, channel_id, content="", embeds=[emb])

        # !botbilgi
        elif cmd == "botbilgi":
            emb = make_embed(
                "🤖 Tavern Bot Bilgisi",
                "Tav.gg için OmniRoute yerel yapay zeka destekli topluluk botu.",
                color="#9B59B6",
                fields=[
                    {"name": "Bot Adı", "value": client.user.display_name, "inline": True},
                    {"name": "Bot ID", "value": f"`{client.user.id}`", "inline": True},
                    {"name": "AI Model", "value": f"`{AI_MODEL}`", "inline": True},
                    {"name": "AI Endpoint", "value": f"`{AI_BASE_URL}`", "inline": True},
                    {"name": "Prefix", "value": f"`{PREFIX}`", "inline": True},
                    {"name": "Bağlı Tavern", "value": str(len(client.taverns)), "inline": True},
                ]
            )
            await client.send_message(tavern_id, channel_id, content="", embeds=[emb])

        # !saat
        elif cmd == "saat":
            now = datetime.now()
            emb = make_embed("🕐 Tarih & Saat", f"**{now.strftime('%H:%M:%S')}**\n{now.strftime('%d/%m/%Y - %A')}", color="#1ABC9C")
            await client.send_message(tavern_id, channel_id, content="", embeds=[emb])

        # !zar
        elif cmd == "zar":
            max_val = 6
            if args.isdigit() and int(args) > 1:
                max_val = min(int(args), 1000)
            sonuc = random.randint(1, max_val)
            emb = make_embed("🎲 Zar Atıldı", f"Atılan zar (1-{max_val}): **{sonuc}**", color="#E67E22")
            await client.send_message(tavern_id, channel_id, content="", embeds=[emb])

        # !yt [miktar] [yazi/tura]
        elif cmd in ("yt", "yazitura", "yazıtura"):
            tokens = args.split()
            secim = "yazi"
            miktar = None
            for tok in tokens:
                low = tok.lower()
                if low in ("tura", "yazi", "yazı"):
                    secim = "tura" if low == "tura" else "yazi"
                elif low.isdigit():
                    miktar = int(low)
            if miktar is None:
                sonuc = random.choice(["Yazı", "Tura"])
                emb = make_embed("🪙 Yazı-Tura", f"Para havaya atıldı... Sonuç: **{sonuc}**!", color="#F1C40F")
                await client.send_message(tavern_id, channel_id, content="", embeds=[emb], reply_to_id=message.id)
            else:
                await send_game(tavern_id, channel_id, engine.coinflip(message.sender_id, miktar, secim), reply_to_id=message.id)

        # !slot <miktar>
        elif cmd in ("slot", "slots", "slotlar"):
            if not args:
                await client.send_message(tavern_id, channel_id, content="🎰 Kullanım: `!slot <miktar>` — Örnek: `!slot 50`")
            else:
                await send_game(tavern_id, channel_id, engine.slots(message.sender_id, args.split()[0]), reply_to_id=message.id)

        # !rulet <secim> <miktar>
        elif cmd in ("rulet", "roulette"):
            tokens = args.split()
            number = next((t for t in tokens if t.isdigit()), None)
            choice = next((t for t in tokens if not t.isdigit()), None)
            if not number or not choice:
                await client.send_message(tavern_id, channel_id, content="🎡 Kullanım: `!rulet <kirmizi/siyah/tek/cift/0-36> <miktar>`")
            else:
                await send_game(tavern_id, channel_id, engine.roulette(message.sender_id, int(number), choice), reply_to_id=message.id)

        # !tahmin <1-5> <miktar>
        elif cmd in ("tahmin", "guess"):
            tokens = args.split()
            number = next((t for t in tokens if t.isdigit()), None)
            others = [t for t in tokens if not t.isdigit()]
            if not number or not others:
                await client.send_message(tavern_id, channel_id, content="🔢 Kullanım: `!tahmin <1-5> <miktar>` — Örnek: `!tahmin 3 50`")
            else:
                await send_game(tavern_id, channel_id, engine.guess(message.sender_id, int(others[0]), int(number)), reply_to_id=message.id)

        # !calis
        elif cmd in ("calis", "çalış", "work"):
            await send_game(tavern_id, channel_id, engine.work(message.sender_id), reply_to_id=message.id)

        # !balik
        elif cmd in ("balik", "balık", "fish"):
            await send_game(tavern_id, channel_id, engine.fish(message.sender_id), reply_to_id=message.id)

        # !soygun <kullanici>
        elif cmd in ("soygun", "soy", "rob"):
            if not args:
                await client.send_message(tavern_id, channel_id, content="🕵️ Kullanım: `!soygun <kullanıcı_id>`")
            else:
                hedef = find_user_id(args.split()[0])
                await send_game(tavern_id, channel_id, engine.rob(message.sender_id, hedef), reply_to_id=message.id)

        # !market
        elif cmd in ("market", "dukkan", "dükkan", "shop"):
            emb = make_embed("🛒 Tavern Marketi", engine.market_text(), color="#3498DB")
            await client.send_message(tavern_id, channel_id, content="", embeds=[emb], reply_to_id=message.id)

        # !satinal <urun>
        elif cmd in ("satinal", "satınal", "satin_al", "buy"):
            if not args:
                await client.send_message(tavern_id, channel_id, content="🛒 Kullanım: `!satinal <ürün>` — `!market` ile listeye bak.")
            else:
                await send_game(tavern_id, channel_id, engine.buy(message.sender_id, args.split()[0]), reply_to_id=message.id)

        # !envanter
        elif cmd in ("envanter", "çantam", "cantam", "inv"):
            await send_game(tavern_id, channel_id, engine.inventory_text(message.sender_id), reply_to_id=message.id)

        # !sec a, b, c
        elif cmd == "sec":
            secenekler = [s.strip() for s in args.split(",") if s.strip()]
            if len(secenekler) < 2:
                await client.send_message(tavern_id, channel_id, content="⚠️ En az 2 seçenek yazmalısın! Örnek: `!sec kebap, döner, pide`")
                return
            secim = random.choice(secenekler)
            emb = make_embed("🎯 Seçim Sonucu", f"Benim tercihim: **{secim}**", color="#9B59B6")
            await client.send_message(tavern_id, channel_id, content="", embeds=[emb])

        # !fal
        elif cmd == "fal":
            fallar = [
                "Bugün şansın yaver gidecek, yeni fırsatlara gözlerini aç!",
                "Bir dostun seni düşünüyor, ona mesaj atmanın tam vakti.",
                "Acele karar verme, biraz sabır her şeyi çözecek.",
                "Tavern'da bugün harika bir sohbet seni bekliyor!",
                "Cebindeki altınlara dikkat et, riskli bahislere girme :)",
            ]
            emb = make_embed("🔮 Günün Falı / Mesajı", random.choice(fallar), color="#8E44AD")
            await client.send_message(tavern_id, channel_id, content="", embeds=[emb])

        # !soyle
        elif cmd == "soyle" and args:
            await client.send_message(tavern_id, channel_id, content=args)

        # !cuzdan
        elif cmd in ("cuzdan", "cüzdan", "bakiye", "para"):
            target_id = find_user_id(args) if args else message.sender_id
            await send_game(tavern_id, channel_id, engine.wallet_text(target_id), reply_to_id=message.id)

        # !gunluk
        elif cmd in ("gunluk", "günlük", "daily"):
            await send_game(tavern_id, channel_id, engine.daily(message.sender_id), reply_to_id=message.id)

        # !kumar <miktar>
        elif cmd in ("kumar", "bahis"):
            if not args.isdigit() or int(args) <= 0:
                await client.send_message(tavern_id, channel_id, content="⚠️ Geçerli bir miktar girmelisin! Örnek: `!kumar 50`")
                return
            bet = int(args)
            prof = get_user_profile(message.sender_id)
            if prof["coins"] < bet:
                emb = error_embed("Yetersiz Bakiye", f"Bu bahsi oynamak için yeterli altının yok. Mevcut bakiyen: **{prof['coins']}**")
                await client.send_message(tavern_id, channel_id, content="", embeds=[emb])
                return

            kazandi = random.random() < 0.5
            if kazandi:
                prof["coins"] += bet
                save_db(db)
                emb = success_embed("🎉 Kazandın!", f"Tebrikler! **+{bet} Altın** kazandın. Güncel bakiyen: **{prof['coins']}**")
            else:
                prof["coins"] -= bet
                save_db(db)
                emb = error_embed(" Kaybettin!", f"Maalesef **-{bet} Altın** kaybettin. Güncel bakiyen: **{prof['coins']}**")
            await client.send_message(tavern_id, channel_id, content="", embeds=[emb])

        # !sirala
        elif cmd in ("sirala", "sırala", "top", "liderler"):
            emb = make_embed(
                "🏆 En Zenginler Sıralaması",
                engine.leaderboard_text(),
                color="#F1C40F"
            )
            await client.send_message(tavern_id, channel_id, content="", embeds=[emb])

    except Exception as e:
        logger.error(f"Prefix komut çalıştırma hatası ({cmd}): {e}")


# ═══════════════════════════════════════════════════════════════
#  SLASH KOMUTLARI (INTERACTIONS)
# ═══════════════════════════════════════════════════════════════

@client.event
async def on_interaction_create(interaction: Interaction):
    cmd = interaction.command_name

    await member_names(client.rest)

    try:
        # /sor (OmniRoute Freecombo)
        if cmd == "sor":
            soru = interaction.get_option("soru")
            await interaction.defer_reply()
            cevap = await ask_ai(interaction.user_id, soru)
            emb = make_embed(
                f"🧠 Soru: {soru[:50]}...",
                cevap,
                color="#8E44AD",
                footer=f"Model: {AI_MODEL} | OmniRoute"
            )
            await interaction.follow_up("", embeds=[emb])

        # /unut
        elif cmd == "unut":
            if interaction.user_id in user_conversations:
                user_conversations[interaction.user_id] = []
            emb = success_embed("Hafıza Sıfırlandı", "Yapay zeka ile olan geçmiş konuşmaların temizlendi.")
            await interaction.reply("", embeds=[emb], ephemeral=True)

        # /ping
        elif cmd == "ping":
            emb = success_embed("Pong!", f"Bot aktif! OmniRoute: `{AI_MODEL}`")
            await interaction.reply("", embeds=[emb])

        # /yardim
        elif cmd == "yardim":
            emb = make_embed(
                "📜 Bot Komut Kılavuzu",
                "Kullanabileceğin tüm komutlar aşağıda listelenmiştir:",
                color="#3498DB",
                fields=[
                    {"name": "🤖 Yapay Zeka (OmniRoute)", "value": "`/sor <soru>` (Freecombo ile konuş), `/unut` (hafızayı temizle)"},
                    {"name": "⚙️ Genel Komutlar", "value": "`/ping`, `/yardim`, `/botbilgi`, `/saat`"},
                    {"name": "🎲 Eğlence", "value": "`/zar`, `/sec`, `/fal`, `/soyle`"},
                    {"name": "🎮 Mini Oyunlar", "value": "`/slot`, `/yazitura`, `/rulet`, `/tahmin`, `/calis`, `/balik`, `/soygun`"},
                    {"name": "💰 Ekonomi", "value": "`/cuzdan`, `/gunluk`, `/kumar`, `/gonder`, `/sirala`"},
                    {"name": "🛒 Market", "value": "`/market`, `/satinal`, `/envanter`"},
                    {"name": "🛡️ Moderasyon", "value": "`/kick`, `/ban`, `/unban`, `/mute`, `/unmute`, `/temizle`"},
                ]
            )
            await interaction.reply("", embeds=[emb], ephemeral=True)

        # /botbilgi
        elif cmd == "botbilgi":
            emb = make_embed(
                "🤖 Tavern Bot Bilgisi",
                "Taverns platformu için tasarlanmış OmniRoute destekli topluluk botu.",
                color="#9B59B6",
                fields=[
                    {"name": "Bot Adı", "value": client.user.display_name, "inline": True},
                    {"name": "Bot ID", "value": f"`{client.user.id}`", "inline": True},
                    {"name": "AI Model", "value": f"`{AI_MODEL}`", "inline": True},
                    {"name": "AI URL", "value": f"`{AI_BASE_URL}`", "inline": True},
                    {"name": "Prefix", "value": f"`{PREFIX}`", "inline": True},
                    {"name": "Bağlı Tavern", "value": str(len(client.taverns)), "inline": True},
                ]
            )
            await interaction.reply("", embeds=[emb])

        # /saat
        elif cmd == "saat":
            now = datetime.now()
            emb = make_embed("🕐 Anlık Saat", f"**{now.strftime('%H:%M:%S')}**\n{now.strftime('%d/%m/%Y, %A')}", color="#1ABC9C")
            await interaction.reply("", embeds=[emb])

        # /zar
        elif cmd == "zar":
            limit = interaction.get_option("maksimum", 6)
            limit = max(2, min(limit, 1000))
            res = random.randint(1, limit)
            emb = make_embed("🎲 Zar Atıldı", f"Zar sonucu (1-{limit}): **{res}**", color="#E67E22")
            await interaction.reply("", embeds=[emb])

        # /yazitura
        elif cmd == "yazitura":
            miktar = interaction.get_option("miktar")
            secim = interaction.get_option("secim", "yazi")
            if miktar:
                res = engine.coinflip(interaction.user_id, miktar, secim)
                await interaction.reply("", embeds=[game_embed(res)])
            else:
                res = random.choice(["Yazı", "Tura"])
                emb = make_embed("🪙 Yazı-Tura", f"Para atıldı! Sonuç: **{res}**", color="#F1C40F")
                await interaction.reply("", embeds=[emb])

        # /slot
        elif cmd == "slot":
            res = engine.slots(interaction.user_id, interaction.get_option("miktar"))
            await interaction.reply("", embeds=[game_embed(res)])

        # /rulet
        elif cmd == "rulet":
            res = engine.roulette(
                interaction.user_id,
                interaction.get_option("miktar"),
                interaction.get_option("secim", ""),
            )
            await interaction.reply("", embeds=[game_embed(res)])

        # /tahmin
        elif cmd == "tahmin":
            res = engine.guess(
                interaction.user_id,
                interaction.get_option("miktar"),
                interaction.get_option("sayi"),
            )
            await interaction.reply("", embeds=[game_embed(res)])

        # /calis
        elif cmd == "calis":
            res = engine.work(interaction.user_id)
            await interaction.reply("", embeds=[game_embed(res)])

        # /balik
        elif cmd == "balik":
            res = engine.fish(interaction.user_id)
            await interaction.reply("", embeds=[game_embed(res)])

        # /soygun
        elif cmd == "soygun":
            hedef = interaction.get_option("kullanici")
            res = engine.rob(interaction.user_id, hedef)
            await interaction.reply("", embeds=[game_embed(res)])

        # /market
        elif cmd == "market":
            emb = make_embed("🛒 Tavern Marketi", engine.market_text(), color="#3498DB")
            await interaction.reply("", embeds=[emb])

        # /satinal
        elif cmd == "satinal":
            res = engine.buy(interaction.user_id, interaction.get_option("urun", ""))
            await interaction.reply("", embeds=[game_embed(res)])

        # /envanter
        elif cmd == "envanter":
            res = engine.inventory_text(interaction.user_id)
            await interaction.reply("", embeds=[game_embed(res)])

        # /sec
        elif cmd == "sec":
            liste_str = interaction.get_option("liste", "")
            items = [s.strip() for s in liste_str.split(",") if s.strip()]
            if len(items) < 2:
                await interaction.reply("⚠️ Lütfen en az 2 seçenek belirtin (virgülle ayırın).", ephemeral=True)
            else:
                secim = random.choice(items)
                emb = make_embed("🎯 Seçim Yapıldı", f"Seçilen: **{secim}**", color="#9B59B6")
                await interaction.reply("", embeds=[emb])

        # /fal
        elif cmd == "fal":
            fallar = [
                "Bugün şansın çok açık! Güzel bir haber kapında.",
                "Tavern'da yeni arkadaşlar edineceğin keyifli bir gün.",
                "Riskli hamleler yapmaktan kaçın, sağlam adımlar at.",
            ]
            emb = make_embed("🔮 Günün Falı", random.choice(fallar), color="#8E44AD")
            await interaction.reply("", embeds=[emb])

        # /soyle
        elif cmd == "soyle":
            txt = interaction.get_option("metin", "...")
            await interaction.reply(txt)

        # /cuzdan
        elif cmd == "cuzdan":
            user_id = interaction.get_option("kullanici", interaction.user_id)
            res = engine.wallet_text(user_id)
            await interaction.reply("", embeds=[game_embed(res)])

        # /gunluk
        elif cmd == "gunluk":
            res = engine.daily(interaction.user_id)
            await interaction.reply("", embeds=[game_embed(res)])

        # /kumar
        elif cmd == "kumar":
            miktar = interaction.get_option("miktar", 10)
            if miktar <= 0:
                await interaction.reply("Pozitif bir miktar belirtmelisin.", ephemeral=True)
                return
            prof = get_user_profile(interaction.user_id)
            if prof["coins"] < miktar:
                emb = error_embed("Yetersiz Bakiye", f"Bu miktarda altının yok. Mevcut bakiyen: **{prof['coins']}**")
                await interaction.reply("", embeds=[emb], ephemeral=True)
                return

            kazandi = random.random() < 0.5
            if kazandi:
                prof["coins"] += miktar
                save_db(db)
                emb = success_embed("Kazandın!", f"Tebrikler! **+{miktar} Altın** kazandın. Güncel bakiye: **{prof['coins']}**")
            else:
                prof["coins"] -= miktar
                save_db(db)
                emb = error_embed("Kaybettin!", f"Maalesef **-{miktar} Altın** kaybettin. Güncel bakiye: **{prof['coins']}**")
            await interaction.reply("", embeds=[emb])

        # /gonder
        elif cmd == "gonder":
            hedef = interaction.get_option("kullanici")
            miktar = interaction.get_option("miktar")
            if not hedef or miktar <= 0:
                await interaction.reply("Geçersiz hedef veya miktar.", ephemeral=True)
                return
            if hedef == interaction.user_id:
                await interaction.reply("Kendine altın gönderemezsin!", ephemeral=True)
                return

            sender_prof = get_user_profile(interaction.user_id)
            if sender_prof["coins"] < miktar:
                emb = error_embed("Yetersiz Bakiye", "Göndermek istediğin kadar altının yok!")
                await interaction.reply("", embeds=[emb], ephemeral=True)
                return

            target_prof = get_user_profile(hedef)
            sender_prof["coins"] -= miktar
            target_prof["coins"] += miktar
            save_db(db)
            emb = success_embed("Transfer Başarılı", f"`{hedef}` kullanıcısına **{miktar} Altın** gönderildi!")
            await interaction.reply("", embeds=[emb])

        # /sirala
        elif cmd == "sirala":
            emb = make_embed("🏆 Liderlik Tablosu (En Zenginler)", engine.leaderboard_text(), color="#F1C40F")
            await interaction.reply("", embeds=[emb])

        # /kick
        elif cmd == "kick":
            hedef = interaction.get_option("kullanici")
            sebep = interaction.get_option("sebep", "Belirtilmedi")
            await client.kick_member(interaction.tavern_id, hedef, reason=sebep)
            emb = success_embed("Üye Atıldı", f"`{hedef}` tavern'dan atıldı.\n**Sebep:** {sebep}")
            await interaction.reply("", embeds=[emb])

        # /ban
        elif cmd == "ban":
            hedef = interaction.get_option("kullanici")
            sebep = interaction.get_option("sebep", "Belirtilmedi")
            await client.ban_member(interaction.tavern_id, hedef, reason=sebep)
            emb = success_embed("Üye Yasaklandı", f"`{hedef}` tavern'dan yasaklandı.\n**Sebep:** {sebep}")
            await interaction.reply("", embeds=[emb])

        # /unban
        elif cmd == "unban":
            hedef = interaction.get_option("kullanici")
            await client.unban_member(interaction.tavern_id, hedef)
            emb = success_embed("Yasak Kaldırıldı", f"`{hedef}` yasağı kaldırıldı.")
            await interaction.reply("", embeds=[emb])

        # /mute
        elif cmd == "mute":
            hedef = interaction.get_option("kullanici")
            dakika = interaction.get_option("dakika")
            sebep = interaction.get_option("sebep", "Belirtilmedi")
            await client.mute_member(interaction.tavern_id, hedef, duration_minutes=dakika, reason=sebep)
            sure_str = f"{dakika} dakika" if dakika else "Süresiz"
            emb = success_embed("Üye Susturuldu", f"`{hedef}` susturuldu.\n**Süre:** {sure_str}\n**Sebep:** {sebep}")
            await interaction.reply("", embeds=[emb])

        # /unmute
        elif cmd == "unmute":
            hedef = interaction.get_option("kullanici")
            await client.unmute_member(interaction.tavern_id, hedef)
            emb = success_embed("Susturma Kaldırıldı", f"`{hedef}` susturması kaldırıldı.")
            await interaction.reply("", embeds=[emb])

        # /temizle
        elif cmd == "temizle":
            adet = interaction.get_option("adet", 5)
            adet = max(1, min(50, adet))
            await interaction.defer_reply(ephemeral=True)
            msgs = await client.rest.get_messages(interaction.tavern_id, interaction.channel_id, limit=adet)
            silinen = 0
            for m in msgs:
                try:
                    await client.rest.delete_message(interaction.tavern_id, m.id)
                    silinen += 1
                except Exception:
                    pass
            emb = success_embed("Kanal Temizlendi", f"Son **{silinen}** adet mesaj başarıyla silindi.")
            await interaction.follow_up("", embeds=[emb], ephemeral=True)

    except Exception as e:
        logger.error(f"Slash komut hatası ({cmd}): {e}")
        try:
            emb = error_embed("İşlem Başarısız", f"Komut işlenirken bir sorun oluştu:\n`{e}`")
            await interaction.reply("", embeds=[emb], ephemeral=True)
        except Exception:
            pass


# ═══════════════════════════════════════════════════════════════
#  ÖZEL MESAJ (DM) YÖNETİMİ
# ═══════════════════════════════════════════════════════════════

@client.event
async def on_dm_message_create(dm):
    if dm.sender.is_bot:
        return
    # DM'den yazıldığında doğrudan OmniRoute AI cevap versin
    try:
        cevap = await ask_ai(dm.sender_id, dm.content)
        await client.send_dm_message(dm.channel_id, content=cevap)
    except Exception as e:
        logger.warning(f"DM AI yanıt hatası: {e}")


# ═══════════════════════════════════════════════════════════════
#  BAŞLATMA
# ═══════════════════════════════════════════════════════════════

def main():
    token = os.environ.get("BOT_TOKEN", "").strip() or BOT_TOKEN
    if not token:
        logger.error("HATA: BOT_TOKEN tanımlı değil! Lütfen .env dosyasına token'ınızı girin.")
        sys.exit(1)

    logger.info("Bot bağlantısı başlatılıyor...")
    print("Bot bağlantısı başlatılıyor...")
    client.run(token)

if __name__ == "__main__":
    main()

