"""
Tavern Bot - Mini Oyunlar & Ekonomi Motoru
==========================================
Cüzdandaki altınlarla oynanan mini oyunlar, meslekler, balık tutma,
soygun, market ve envanter sistemi.

Bu modül hem poller.py hem de bot.py tarafından kullanılır.
Tüm fonksiyonlar sözlük (dict) döndürür:
    {"ok": bool, "title": str, "text": str, "color": str, "win": bool}
"""

import random
import time
from names import name_of

# ─── Ayarlar ───────────────────────────────────────────────────
START_COINS = 100
DAILY_AMOUNT = 150
MAX_BET = 100_000

WORK_COOLDOWN = 30 * 60      # 30 dakika
FISH_COOLDOWN = 10 * 60      # 10 dakika (olta ile 5 dakika)
ROB_COOLDOWN = 15 * 60       # 15 dakika

# Slot makinesi: (sembol, ağırlık, üçlü ödeme çarpanı)
SYMBOLS = [
    ("🍒", 30, 4),
    ("🍋", 25, 5),
    ("🔔", 20, 6),
    ("⭐", 12, 10),
    ("💎", 8, 15),
    ("7️⃣", 5, 25),
]

# Market ürünleri
ITEMS = {
    "sans_tilsimi": {
        "name": "🍀 Şans Tılsımı",
        "price": 400,
        "desc": "Sonraki 3 oyunda kazanma şansını +%6 artırır.",
        "type": "buff",
        "charges": 3,
        "key": "luck",
    },
    "cifte_altin": {
        "name": "💎 Çifte Altın",
        "price": 700,
        "desc": "Bir sonraki kazandığın oyunun kârını 2'ye katlar.",
        "type": "buff",
        "charges": 1,
        "key": "double",
    },
    "olta": {
        "name": "🎣 Usta Olta",
        "price": 500,
        "desc": "Balık bekleme süresini 5 dakikaya indirir, ödülleri +%50 artırır. Kalıcıdır.",
        "type": "perm",
    },
    "hirsiz_maskesi": {
        "name": "🎭 Hırsız Maskesi",
        "price": 600,
        "desc": "Soygun başarı şansını +%10 artırır. 5 kullanım.",
        "type": "buff",
        "charges": 5,
        "key": "mask",
    },
}


def _new_profile() -> dict:
    return {
        "coins": START_COINS,
        "daily_last": None,
        "reputation": 0,
        "messages": 0,
        "inventory": {},
        "stats": {"games": 0, "wins": 0, "losses": 0, "biggest": 0, "jackpots": 0},
        "cooldowns": {},
        "buffs": {"luck": 0, "double": 0, "mask": 0},
    }


class GameEngine:
    def __init__(self, db: dict, save):
        self.db = db
        self.save = save
        self.db.setdefault("users", {})

    # ─── Profil ────────────────────────────────────────────────
    def profile(self, user_id: str) -> dict:
        users = self.db["users"]
        p = users.get(user_id)
        if not isinstance(p, dict):
            p = _new_profile()
            users[user_id] = p
        p.setdefault("coins", START_COINS)
        p.setdefault("messages", 0)
        p.setdefault("reputation", 0)
        p.setdefault("daily_last", None)
        p.setdefault("inventory", {})
        stats = p.setdefault("stats", {})
        stats.setdefault("games", 0)
        stats.setdefault("wins", 0)
        stats.setdefault("losses", 0)
        stats.setdefault("biggest", 0)
        stats.setdefault("jackpots", 0)
        p.setdefault("cooldowns", {})
        buffs = p.setdefault("buffs", {})
        buffs.setdefault("luck", 0)
        buffs.setdefault("double", 0)
        buffs.setdefault("mask", 0)
        return p

    # ─── Yardımcılar ───────────────────────────────────────────
    @staticmethod
    def _err(title: str, text: str) -> dict:
        return {"ok": False, "title": f"❌ {title}", "text": text, "color": "#E74C3C", "win": False}

    @staticmethod
    def _res(title: str, text: str, win: bool = False, color: str = "#F39C12") -> dict:
        return {"ok": True, "title": title, "text": text, "color": color, "win": win}

    def _begin(self, user_id: str, bet):
        """Bahsi doğrular ve bakiyeden düşer."""
        p = self.profile(user_id)
        try:
            bet = int(bet)
        except (TypeError, ValueError):
            return None, self._err("Geçersiz Bahis", "Bahis miktarı bir tam sayı olmalı. Örnek: `!slot 50`")
        if bet <= 0:
            return None, self._err("Geçersiz Bahis", "Bahis miktarı 0'dan büyük olmalı.")
        if bet > MAX_BET:
            return None, self._err("Bahis Çok Yüksek", f"En fazla **{MAX_BET:,}** altın bahis koyabilirsin.")
        if p["coins"] < bet:
            return None, self._err(
                "Yetersiz Bakiye",
                f"Bu bahsi oynamak için yeterli altının yok.\nMevcut bakiyen: **{p['coins']}** 🪙",
            )
        p["coins"] -= bet
        return p, None

    def _payout(self, p: dict, bet: int, mult: float) -> int:
        """Bahsi geri öder, çifte altın varsa kârı ikiye katlar. Net kâr döner."""
        back = int(round(bet * mult))
        net = back - bet
        if net > 0 and p["buffs"].get("double", 0) > 0:
            p["buffs"]["double"] -= 1
            net *= 2
            back = bet + net
        p["coins"] += back
        return net

    def _finish(self, p: dict, win: bool, net: int, jackpot: bool = False):
        st = p["stats"]
        st["games"] += 1
        if win:
            st["wins"] += 1
        else:
            st["losses"] += 1
        if net > st["biggest"]:
            st["biggest"] = net
        if jackpot:
            st["jackpots"] += 1
        self.save()

    def _consume_luck(self, p: dict) -> float:
        """Şans tılsımı varsa bir kullanım harcayıp +%6 döner."""
        if p["buffs"].get("luck", 0) > 0:
            p["buffs"]["luck"] -= 1
            return 0.06
        return 0.0

    def _cooldown_left(self, p: dict, key: str, seconds: int) -> int:
        last = p["cooldowns"].get(key, 0)
        left = seconds - (time.time() - last)
        return max(0, int(left))

    @staticmethod
    def _fmt_time(seconds: int) -> str:
        m, s = divmod(seconds, 60)
        if m:
            return f"{m} dk {s} sn"
        return f"{s} sn"

    # ─── Günlük Ödül ───────────────────────────────────────────
    def daily(self, user_id: str) -> dict:
        p = self.profile(user_id)
        today = time.strftime("%Y-%m-%d")
        if p.get("daily_last") == today:
            return self._err("Günlük Ödül", "Bugünkü ödülünü zaten aldın! Yarın tekrar gel. 🕛")
        p["daily_last"] = today
        p["coins"] += DAILY_AMOUNT
        self.save()
        return self._res(
            "🎁 Günlük Ödül Alındı!",
            f"Hesabına **+{DAILY_AMOUNT} Altın** 🪙 eklendi!\nGüncel bakiyen: **{p['coins']}**",
            win=True,
            color="#2ECC71",
        )

    # ─── Yazı - Tura (bahisli) ─────────────────────────────────
    def coinflip(self, user_id: str, bet, choice: str = "yazi") -> dict:
        p, err = self._begin(user_id, bet)
        if err:
            return err
        bet = int(bet)
        choice = (choice or "yazi").lower().replace("ı", "i")
        secim = "tura" if choice == "tura" else "yazi"
        sonuc = random.choice(["yazi", "tura"])
        chance = 0.48 + self._consume_luck(p)

        # Yanlış tarafı seçtiyse kesin kaybeder; doğru seçtiyse %48 (+tılsım) ile kazanır.
        if sonuc == secim and random.random() < chance:
            net = self._payout(p, bet, 2.0)
            self._finish(p, True, net)
            return self._res(
                "🪙 Yazı - Tura",
                f"Para havaya atıldı... **{sonuc.upper()}**!\nTebrikler, **+{net} Altın** kazandın! 🎉\nGüncel bakiye: **{p['coins']}**",
                win=True,
                color="#2ECC71",
            )

        self._finish(p, False, -bet)
        return self._res(
            "🪙 Yazı - Tura",
            f"Para havaya atıldı... **{sonuc.upper()}**!\nMaalesef **-{bet} Altın** kaybettin. 😕\nGüncel bakiye: **{p['coins']}**",
            color="#E74C3C",
        )

    # ─── Slot Makinesi ─────────────────────────────────────────
    def slots(self, user_id: str, bet) -> dict:
        p, err = self._begin(user_id, bet)
        if err:
            return err
        bet = int(bet)

        weights = [s[1] for s in SYMBOLS]
        reels = random.choices(SYMBOLS, weights=weights, k=3)
        display = " | ".join(r[0] for r in reels)

        if reels[0][0] == reels[1][0] == reels[2][0]:
            mult = reels[0][2]
            net = self._payout(p, bet, mult)
            jackpot = reels[0][0] == "7️⃣"
            self._finish(p, True, net, jackpot=jackpot)
            baslik = "🎰 JACKPOT!" if jackpot else "🎰 Üçlü!"
            return self._res(
                baslik,
                f"```\n[ {display} ]\n```\n**{reels[0][0]} x3** → **{mult}x** ödeme!\n"
                f"Kazanç: **+{net} Altın** 🪙\nGüncel bakiye: **{p['coins']}**",
                win=True,
                color="#F1C40F",
            )

        semboller = [r[0] for r in reels]
        if len(set(semboller)) == 2:
            net = self._payout(p, bet, 1.5)
            self._finish(p, True, net)
            return self._res(
                "🎰 İkili!",
                f"```\n[ {display} ]\n```\nİki sembol tuttu → **1.5x** ödeme.\n"
                f"Kazanç: **{'%+d' % net} Altın** 🪙\nGüncel bakiye: **{p['coins']}**",
                win=net > 0,
                color="#2ECC71",
            )

        self._finish(p, False, -bet)
        return self._res(
            "🎰 Boş Geldi",
            f"```\n[ {display} ]\n```\nHiçbir şey tutmadı. **-{bet} Altın** kaybettin. 😕\n"
            f"Güncel bakiye: **{p['coins']}**",
            color="#E74C3C",
        )

    # ─── Rulet ─────────────────────────────────────────────────
    def roulette(self, user_id: str, bet, choice: str) -> dict:
        p, err = self._begin(user_id, bet)
        if err:
            return err
        bet = int(bet)
        secim = (choice or "").strip().lower()
        secim = secim.replace("ı", "i").replace("ü", "u").replace("ö", "o").replace("ç", "c").replace("ş", "s")

        renkler = {
            "kirmizi": "🔴 kırmızı",
            "siyah": "⚫ siyah",
            "yesil": "🟢 yeşil",
            "tek": "tek",
            "cift": "çift",
        }
        if secim not in renkler and not (secim.isdigit() and 0 <= int(secim) <= 36):
            return self._err(
                "Geçersiz Seçim",
                "Seçenekler: `kirmizi`, `siyah`, `tek`, `cift` veya `0-36` arası bir sayı.\n"
                "Örnek: `!rulet kirmizi 100`",
            )

        sayi = random.randint(0, 36)
        if sayi == 0:
            sonuc_renk = "🟢 yeşil"
        elif sayi % 2 == 0:
            sonuc_renk = "⚫ siyah"
        else:
            sonuc_renk = "🔴 kırmızı"

        kazandi = False
        mult = 0.0
        if secim.isdigit():
            if int(secim) == sayi:
                kazandi, mult = True, 36.0
        elif secim == "kirmizi":
            kazandi, mult = sonuc_renk == "🔴 kırmızı", 2.0
        elif secim == "siyah":
            kazandi, mult = sonuc_renk == "⚫ siyah", 2.0
        elif secim == "yesil":
            kazandi, mult = sonuc_renk == "🟢 yeşil", 14.0
        elif secim == "tek":
            kazandi, mult = sayi != 0 and sayi % 2 == 1, 2.0
        elif secim == "cift":
            kazandi, mult = sayi != 0 and sayi % 2 == 0, 2.0

        tahmin = f"**{secim}**" if secim.isdigit() else renkler.get(secim, secim)

        if kazandi:
            # Rulet şansı şans tılsımından etkilenmez, oyunun doğasına bırakıldı.
            net = self._payout(p, bet, mult)
            self._finish(p, True, net)
            return self._res(
                "🎡 Rulet Sonucu",
                f"Top döndü ve **{sayi} / {sonuc_renk}** geldi!\n"
                f"Tahminin: {tahmin} ✅\nKazanç: **+{net} Altın** 🪙\nGüncel bakiye: **{p['coins']}**",
                win=True,
                color="#F1C40F",
            )

        self._finish(p, False, -bet)
        return self._res(
            "🎡 Rulet Sonucu",
            f"Top döndü ve **{sayi} / {sonuc_renk}** geldi.\n"
            f"Tahminin: {tahmin} ❌\nKayıp: **-{bet} Altın**\nGüncel bakiye: **{p['coins']}**",
            color="#E74C3C",
        )

    # ─── Sayı Tahmini ──────────────────────────────────────────
    def guess(self, user_id: str, bet, guess) -> dict:
        p, err = self._begin(user_id, bet)
        if err:
            return err
        bet = int(bet)
        try:
            guess = int(guess)
        except (TypeError, ValueError):
            return self._err("Geçersiz Tahmin", "1 ile 5 arasında bir sayı tahmin etmelisin. Örnek: `!tahmin 3 50`")
        if not 1 <= guess <= 5:
            return self._err("Geçersiz Tahmin", "Tahmin **1 ile 5** arasında olmalı.")

        gizli = random.randint(1, 5)
        chance = 0.20 + self._consume_luck(p)
        # Şans tılsımı: tutmasa bile %6 ihtimalle ikinci bir şans tanır
        if gizli != guess and random.random() < chance:
            gizli = guess

        if gizli == guess:
            net = self._payout(p, bet, 4.5)
            self._finish(p, True, net)
            return self._res(
                "🔢 Sayı Tahmini",
                f"Tuttu! Gizli sayı **{gizli}** idi. 🎯\nKazanç: **+{net} Altın** 🪙\nGüncel bakiye: **{p['coins']}**",
                win=True,
                color="#2ECC71",
            )

        self._finish(p, False, -bet)
        return self._res(
            "🔢 Sayı Tahmini",
            f"Tutmadı! Gizli sayı **{gizli}** idi, sen **{guess}** dedin.\n"
            f"Kayıp: **-{bet} Altın**\nGüncel bakiye: **{p['coins']}**",
            color="#E74C3C",
        )

    # ─── Çalış (Para Kazan) ────────────────────────────────────
    def work(self, user_id: str) -> dict:
        p = self.profile(user_id)
        left = self._cooldown_left(p, "work", WORK_COOLDOWN)
        if left > 0:
            return self._err("Çok Yorgunsun", f"Biraz dinlenmen lazım. Tekrar çalışmak için **{self._fmt_time(left)}** bekle.")

        kazanc = random.randint(25, 70)
        p["coins"] += kazanc
        p["cooldowns"]["work"] = time.time()
        self.save()
        meslek = random.choice([
            "Tavern'da bulaşık yıkadın", "Meyhane için fıçı taşıdın", "Atları tımarladın",
            "Çiftlikte çalıştın", "Pazardan mal taşıdın", "Gece bekçiliği yaptın",
        ])
        return self._res(
            "💼 Çalıştın!",
            f"{meslek} ve **+{kazanc} Altın** kazandın! 🪙\nGüncel bakiye: **{p['coins']}**",
            win=True,
            color="#2ECC71",
        )

    # ─── Balık Tutma ───────────────────────────────────────────
    def fish(self, user_id: str) -> dict:
        p = self.profile(user_id)
        olta = p["inventory"].get("olta", 0) > 0
        cd = FISH_COOLDOWN // 2 if olta else FISH_COOLDOWN
        left = self._cooldown_left(p, "fish", cd)
        if left > 0:
            return self._err("Balıklar Kaçtı", f"Oltanı topla ve **{self._fmt_time(left)}** sonra tekrar dene.")

        roll = random.random()
        if roll < 0.03:
            isim, kazanc = "🐋 Dev Balina", random.randint(400, 700)
        elif roll < 0.12:
            isim, kazanc = "💎 İnci Dolu İstiridye", random.randint(250, 400)
        elif roll < 0.35:
            isim, kazanc = "🐟 Somon", random.randint(100, 200)
        elif roll < 0.65:
            isim, kazanc = "🐠 Levrek", random.randint(50, 110)
        else:
            isim, kazanc = "🥾 Eski Çizme", random.randint(5, 20)

        if olta:
            kazanc = int(kazanc * 1.5)
        p["coins"] += kazanc
        p["cooldowns"]["fish"] = time.time()
        self.save()

        return self._res(
            "🎣 Balık Tuttun!",
            f"Oltana **{isim}** takıldı!\nSattın ve **+{kazanc} Altın** kazandın 🪙"
            + ("\n🎣 Usta olta sayesinde ödül arttı." if olta else "")
            + f"\nGüncel bakiye: **{p['coins']}**",
            win=True,
            color="#3498DB",
        )

    # ─── Soygun ────────────────────────────────────────────────
    def rob(self, user_id: str, target_id: str) -> dict:
        if not target_id or target_id == user_id:
            return self._err("Soygun", "Kendini soyamazsın! Bir kullanıcı ID'si belirt. Örnek: `!soygun 123456 456789`")
        p = self.profile(user_id)
        left = self._cooldown_left(p, "rob", ROB_COOLDOWN)
        if left > 0:
            return self._err("Polis Peşinde", f"Bu kadar hızlı değil! Tekrar denemek için **{self._fmt_time(left)}** bekle.")

        if target_id not in self.db["users"]:
            return self._err("Hedef Bulunamadı", "Bu kullanıcı henüz botta kayıtlı değil. Sadece sunucuda konuşmuş kişileri soyabilirsin.")

        t = self.profile(target_id)
        hedef_isim = name_of(target_id)
        if t["coins"] <= 0:
            return self._err("Soygun", f"`{hedef_isim}` kullanıcısının cebi boş çıktı, soyacak bir şey yok.")

        chance = 0.35 + (0.10 if p["buffs"].get("mask", 0) > 0 else 0.0) + self._consume_luck(p)
        p["cooldowns"]["rob"] = time.time()

        if random.random() < chance:
            if p["buffs"].get("mask", 0) > 0:
                p["buffs"]["mask"] -= 1
            miktar = max(50, min(int(t["coins"] * 0.15), 500))
            miktar = min(miktar, t["coins"])
            t["coins"] -= miktar
            p["coins"] += miktar
            self.save()
            return self._res(
                "🕵️ Soygun Başarılı!",
                f"`{hedef_isim}` kullanıcısının cebinden **{miktar} Altın** aşırdın! 🪙\n"
                f"Güncel bakiyen: **{p['coins']}**",
                win=True,
                color="#9B59B6",
            )

        ceza = min(max(25, int(p["coins"] * 0.10)), 300)
        ceza = min(ceza, p["coins"])
        p["coins"] -= ceza
        self.save()
        return self._res(
            "🚔 Yakalandın!",
            f"`{hedef_isim}` seni yakaladı! Ceza olarak **{ceza} Altın** ödedin.\n"
            f"Güncel bakiyen: **{p['coins']}**",
            color="#E74C3C",
        )

    # ─── Market / Envanter ─────────────────────────────────────
    def market_text(self) -> str:
        lines = ["**🛒 Tavern Marketi**", ""]
        for key, item in ITEMS.items():
            lines.append(f"• `{key}` — **{item['name']}** — **{item['price']}** 🪙\n  _{item['desc']}_")
        lines.append("")
        lines.append("Satın almak için: `!satinal <ürün>`  •  Envanter: `!envanter`")
        return "\n".join(lines)

    def buy(self, user_id: str, item_key: str) -> dict:
        key = (item_key or "").strip().lower()
        item = ITEMS.get(key)
        if not item:
            return self._err("Ürün Bulunamadı", f"`{item_key}` diye bir ürün yok. `!market` yazarak listeye bakabilirsin.")

        p = self.profile(user_id)
        if item["type"] == "perm" and p["inventory"].get(key, 0) > 0:
            return self._err("Zaten Sende", f"**{item['name']}** zaten envanterinde var.")
        if p["coins"] < item["price"]:
            return self._err(
                "Yetersiz Bakiye",
                f"**{item['name']}** için **{item['price']} Altın** gerekiyor.\nMevcut bakiyen: **{p['coins']}** 🪙",
            )

        p["coins"] -= item["price"]
        p["inventory"][key] = p["inventory"].get(key, 0) + 1
        if item["type"] == "buff":
            p["buffs"][item["key"]] = p["buffs"].get(item["key"], 0) + item["charges"]

        self.save()
        return self._res(
            "🛒 Satın Alındı!",
            f"**{item['name']}** envanterine eklendi.\n"
            f"Ödenen: **-{item['price']} Altın**\nGüncel bakiye: **{p['coins']}** 🪙",
            win=True,
            color="#2ECC71",
        )

    def inventory_text(self, user_id: str) -> dict:
        p = self.profile(user_id)
        isim = name_of(user_id)
        lines = [f"**🎒 Envanter — `{isim}`**", ""]
        if p["inventory"]:
            for key, adet in p["inventory"].items():
                item = ITEMS.get(key)
                isim = item["name"] if item else key
                lines.append(f"• **{isim}** x{adet}")
        else:
            lines.append("_Envanterin boş. `!market` ile alışveriş yapabilirsin._")

        buffs = p["buffs"]
        aktif = []
        if buffs.get("luck", 0):
            aktif.append(f"🍀 Şans Tılsımı: {buffs['luck']} kullanım")
        if buffs.get("double", 0):
            aktif.append(f"💎 Çifte Altın: {buffs['double']} kullanım")
        if buffs.get("mask", 0):
            aktif.append(f"🎭 Hırsız Maskesi: {buffs['mask']} kullanım")
        if aktif:
            lines.append("")
            lines.append("**Aktif Etkiler:**")
            lines.extend(f"• {a}" for a in aktif)
        return self._res("🎒 Envanter", "\n".join(lines), color="#3498DB")

    # ─── Cüzdan & Sıralama ─────────────────────────────────────
    def wallet_text(self, user_id: str) -> dict:
        p = self.profile(user_id)
        st = p["stats"]
        oran = (st["wins"] / st["games"] * 100) if st["games"] else 0
        isim = name_of(user_id)
        text = (
            f"**Bakiye:** {p['coins']} 🪙\n"
            f"**Mesaj:** {p['messages']}  •  **İtibar:** {p['reputation']}\n"
            f"**Oyun:** {st['games']}  •  **Kazanma:** {st['wins']} (%{oran:.0f})\n"
            f"**En Büyük Kazanç:** {st['biggest']} 🪙  •  **Jackpot:** {st['jackpots']}\n\n"
            f"_Oyunlar: `!slot`, `!yt`, `!rulet`, `!tahmin`, `!calis`, `!balik`, `!soygun`_"
        )
        return self._res(f"💰 Cüzdan — `{isim}`", text, color="#F39C12")

    def leaderboard_text(self, limit: int = 10) -> str:
        rows = sorted(
            self.db["users"].items(),
            key=lambda x: x[1].get("coins", 0) if isinstance(x[1], dict) else 0,
            reverse=True,
        )[:limit]
        if not rows:
            return "Henüz kimse kayıtlı değil."
        madalya = ["🥇", "🥈", "🥉"]
        lines = []
        for i, (uid, data) in enumerate(rows, start=1):
            prefix = madalya[i - 1] if i <= 3 else f"**{i}.**"
            isim = name_of(uid)
            lines.append(f"{prefix} `{isim}` — **{data.get('coins', 0)}** 🪙")
        return "\n".join(lines)
