"""
Tavern Bot - Yerel Yönetim Paneli (GUI)
=======================================
Sadece bu bilgisayardan (127.0.0.1) erişilebilen, şifre korumalı web arayüzü.

Özellikler:
  • Botu başlat / durdur / yeniden başlat
  • Canlı log takibi
  • Kanal listesi ve "bot buraya yazabiliyor mu?" testi
  • Kullanıcı bakiyelerini görüntüle / düzenle / sıfırla / sil
  • Seçilen kanala mesaj gönderme

Çalıştırma:  python gui.py   (Linux: ./start_gui.sh, Windows: gui.bat)
"""

import os
import sys
import json
import hmac
import time
import hashlib
import secrets
import asyncio
import subprocess
import threading
import webbrowser
import re
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs
from datetime import datetime

import env_loader  # .env dosyasını yükler
from platform_utils import ensure_utf8, get_data_dir, get_logs_dir, find_bot_pids, kill_pids, IS_WINDOWS, IS_LINUX, IS_RPI, load_env_file

# UTF-8 zorla
ensure_utf8()

# ─── Yollar & Ayarlar ──────────────────────────────────────────
BASE_DIR = Path(__file__).parent.resolve()
DATA_DIR = get_data_dir()
LOGS_DIR = get_logs_dir()
DB_FILE = DATA_DIR / "database.json"
ENV_FILE = BASE_DIR / ".env"
SECRET_FILE = DATA_DIR / ".gui_secret"
PID_FILE = DATA_DIR / "bot.pid"
STDOUT_LOG = DATA_DIR / "bot-stdout.log"

# GUI ayarları
# Güvenli varsayılan: LAN/Wi-Fi erişimi.
# 127.0.0.1 (sadece bu bilgisayar) için GUI_HOST=127.0.0.1 ayarla.
HOST = os.environ.get("GUI_HOST", "0.0.0.0").strip() or "0.0.0.0"
PORT = int(os.environ.get("GUI_PORT", "8787"))
SESSION_HOURS = 12

# Windows-specific constants (Linux'ta kullanılmaz)
if IS_WINDOWS:
    CREATE_NO_WINDOW = 0x08000000
    DETACHED_PROCESS = 0x00000008
    CREATE_NEW_PROCESS_GROUP = 0x00000200


# ─── Şifre & Oturum ────────────────────────────────────────────
def ensure_password() -> str:
    """GUI şifresini .env'den okur; yoksa güvenli bir şifre üretip kaydeder."""
    pw = os.environ.get("GUI_PASSWORD", "").strip()
    if pw:
        return pw

    pw = secrets.token_urlsafe(9)
    try:
        with open(ENV_FILE, "a", encoding="utf-8") as f:
            f.write("\n# Yerel GUI şifresi (otomatik oluşturuldu)\n")
            f.write(f"GUI_PASSWORD={pw}\n")
        os.environ["GUI_PASSWORD"] = pw
    except Exception as e:
        print(f"UYARI: .env dosyasına şifre yazılamadı: {e}")
    return pw


def load_secret() -> bytes:
    if SECRET_FILE.exists():
        try:
            return SECRET_FILE.read_bytes()
        except Exception:
            pass
    secret = secrets.token_bytes(32)
    try:
        SECRET_FILE.write_bytes(secret)
    except Exception:
        pass
    return secret


PASSWORD = ensure_password()
SECRET = load_secret()


def make_token() -> str:
    expires = int(time.time()) + SESSION_HOURS * 3600
    sig = hmac.new(SECRET, str(expires).encode(), hashlib.sha256).hexdigest()
    return f"{expires}.{sig}"


def token_valid(token: str | None) -> bool:
    if not token or "." not in token:
        return False
    exp_str, sig = token.rsplit(".", 1)
    try:
        expires = int(exp_str)
    except ValueError:
        return False
    if expires < time.time():
        return False
    expected = hmac.new(SECRET, exp_str.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(sig, expected)


# ─── Veritabanı ────────────────────────────────────────────────
_db_lock = threading.Lock()


def load_db() -> dict:
    with _db_lock:
        if DB_FILE.exists():
            try:
                with open(DB_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    data.setdefault("users", {})
                    return data
            except Exception:
                pass
        return {"users": {}}


def save_db(data: dict):
    with _db_lock:
        tmp = DB_FILE.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        tmp.replace(DB_FILE)


# ─── Bot Süreç Yönetimi ────────────────────────────────────────
_pid_cache = {"time": 0.0, "pids": []}


def find_bot_pids(force: bool = False) -> list[int]:
    """poller.py çalıştıran python süreçlerini bulur (3 sn önbellek)."""
    now = time.time()
    if not force and now - _pid_cache["time"] < 3:
        return list(_pid_cache["pids"])

    pids: list[int] = []
    try:
        ps = (
            "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
            "Where-Object { $_.CommandLine -like '*poller.py*' } | "
            "Select-Object -ExpandProperty ProcessId"
        )
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps],
            capture_output=True, text=True, timeout=10,
            creationflags=CREATE_NO_WINDOW,
        )
        pids = [int(x) for x in out.stdout.split() if x.strip().isdigit()]
    except Exception:
        # PowerShell yoksa pid dosyasına düş
        try:
            pid = int(PID_FILE.read_text().strip())
            pids = [pid] if pid else []
        except Exception:
            pids = []

    _pid_cache["time"] = now
    _pid_cache["pids"] = pids
    return list(pids)


def start_bot() -> dict:
    if find_bot_pids(force=True):
        return {"ok": False, "error": "Bot zaten çalışıyor."}

    token = os.environ.get("BOT_TOKEN", "").strip()
    if not token:
        return {"ok": False, "error": "BOT_TOKEN .env dosyasında tanımlı değil."}

    try:
        logf = open(STDOUT_LOG, "ab")
        python_cmd = [sys.executable, "-u", "poller.py"]
        if IS_WINDOWS:
            proc = subprocess.Popen(
                python_cmd,
                cwd=str(BASE_DIR),
                stdout=logf,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW,
                close_fds=True,
            )
        else:
            # Linux: start_new_session ile ayrı process group
            proc = subprocess.Popen(
                python_cmd,
                cwd=str(BASE_DIR),
                stdout=logf,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
                close_fds=True,
            )
        PID_FILE.write_text(str(proc.pid))
        _pid_cache["time"] = 0
        return {"ok": True, "pid": proc.pid}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def stop_bot() -> dict:
    pids = find_bot_pids(force=True)
    if not pids:
        return {"ok": True, "stopped": 0, "message": "Bot zaten çalışmıyor."}

    killed = kill_pids(pids)

    try:
        PID_FILE.unlink(missing_ok=True)
    except Exception:
        pass

    _pid_cache["time"] = 0
    return {"ok": True, "stopped": killed, "message": f"{killed} süreç durduruldu."}


def restart_bot() -> dict:
    stop_bot()
    time.sleep(1.5)
    return start_bot()


# ─── Taverns REST Yardımcıları ─────────────────────────────────
_channel_cache = {"time": 0.0, "data": []}
_name_cache = {"time": 0.0, "data": {}}


def get_token() -> str:
    return os.environ.get("BOT_TOKEN", "").strip()


async def _fetch_member_names_async() -> dict:
    """user_id -> görünen ad eşlemesi (takma ad > görünen ad > kullanıcı adı)."""
    from taverns.rest import RESTClient
    rest = RESTClient(get_token())
    try:
        me = await rest.get_self()
        names = {}
        for t in me.taverns:
            try:
                members = await rest.request("GET", f"/taverns/{t.id}/members")
            except Exception:
                continue
            for m in members or []:
                uid = m.get("userId") or (m.get("user") or {}).get("id")
                if not uid:
                    continue
                u = m.get("user") or {}
                name = (
                    m.get("nickname")
                    or u.get("displayName")
                    or u.get("username")
                    or u.get("aetherName")
                )
                if uid not in names or name:
                    names[uid] = {
                        "name": name or uid,
                        "avatar": u.get("avatarUrl"),
                    }
        return names
    finally:
        await rest.close()


def get_member_names(force: bool = False) -> dict:
    now = time.time()
    if not force and now - _name_cache["time"] < 60:
        return _name_cache["data"]
    try:
        data = asyncio.run(_fetch_member_names_async())
    except Exception:
        data = _name_cache["data"] or {}
    _name_cache["time"] = now
    _name_cache["data"] = data
    return data


async def _fetch_channels_async() -> list[dict]:
    from taverns.rest import RESTClient
    rest = RESTClient(get_token())
    try:
        me = await rest.get_self()
        result = []
        for t in me.taverns:
            channels = await rest.get_channels(t.id)
            for c in channels:
                result.append({
                    "tavern_id": t.id,
                    "tavern": t.name,
                    "id": c.id,
                    "name": c.name,
                    "type": c.type,
                    "writable": c.type in ("TEXT", "ANNOUNCEMENT"),
                })
        return result
    finally:
        await rest.close()


def get_channels(force: bool = False) -> list[dict]:
    now = time.time()
    if not force and now - _channel_cache["time"] < 30:
        return _channel_cache["data"]
    data = asyncio.run(_fetch_channels_async())
    _channel_cache["time"] = now
    _channel_cache["data"] = data
    return data


async def _test_channel_async(tavern_id: str, channel_id: str) -> dict:
    from taverns.rest import RESTClient
    rest = RESTClient(get_token())
    try:
        msg = await rest.send_message(
            tavern_id, channel_id,
            content="🧪 Yetki testi — bu mesaj otomatik silinecek.",
        )
        deleted = True
        try:
            await rest.delete_message(tavern_id, msg.id)
        except Exception:
            deleted = False
        return {"ok": True, "deleted": deleted}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    finally:
        await rest.close()


async def _send_message_async(tavern_id: str, channel_id: str, content: str) -> dict:
    from taverns.rest import RESTClient
    rest = RESTClient(get_token())
    try:
        msg = await rest.send_message(tavern_id, channel_id, content=content)
        return {"ok": True, "id": msg.id}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    finally:
        await rest.close()


# ─── Log ───────────────────────────────────────────────────────
def read_logs(lines: int = 250) -> str:
    """Bot logunu döner. bot.log varsa onu, yoksa süreç çıktısını kullanır."""
    bot_log = BASE_DIR / "bot.log"
    source = bot_log if bot_log.exists() and bot_log.stat().st_size > 0 else STDOUT_LOG
    try:
        with open(source, "r", encoding="utf-8", errors="replace") as f:
            text = "".join(f.readlines()[-lines:])
    except Exception:
        return ""
    return pretty_log(text)


_UUID_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)


def pretty_log(text: str) -> str:
    """Loglardaki UUID'leri kullanıcı adlarıyla değiştirir."""
    try:
        names = get_member_names()
    except Exception:
        names = {}
    if not names:
        return text

    def repl(m):
        info = names.get(m.group(0)) or {}
        return info.get("name") or m.group(0)

    return _UUID_RE.sub(repl, text)


# ─── HTTP Sunucu ───────────────────────────────────────────────
_login_attempts: dict[str, dict] = {}
_attempt_lock = threading.Lock()


class Handler(BaseHTTPRequestHandler):
    server_version = "TavernGUI/1.0"
    protocol_version = "HTTP/1.0"

    # Sessiz log
    def log_message(self, fmt, *args):
        pass

    # ── yardımcılar ──
    def _client_ip(self) -> str:
        return self.client_address[0]

    def _cookies(self) -> dict:
        raw = self.headers.get("Cookie", "")
        result = {}
        for part in raw.split(";"):
            if "=" in part:
                k, _, v = part.partition("=")
                result[k.strip()] = v.strip()
        return result

    def _authed(self) -> bool:
        return token_valid(self._cookies().get("tavern_gui"))

    def _json(self, obj, status: int = 200, headers: dict | None = None):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if headers:
            for k, v in headers.items():
                self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _html(self, html: str):
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if length <= 0 or length > 1_000_000:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception:
            return {}

    # ── GET ──
    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/":
            self._html(LOGIN_HTML if not self._authed() else APP_HTML)
            return

        if not path.startswith("/api/"):
            self.send_error(404)
            return

        if not self._authed():
            self._json({"error": "Oturum gerekli"}, 401)
            return

        try:
            if path == "/api/status":
                self._json(self.api_status())

            elif path == "/api/logs":
                qs = parse_qs(parsed.query)
                lines = min(max(int(qs.get("lines", ["250"])[0]), 10), 2000)
                self._json({"log": read_logs(lines)})

            elif path == "/api/channels":
                qs = parse_qs(parsed.query)
                force = qs.get("force", ["0"])[0] == "1"
                self._json({"channels": get_channels(force)})

            elif path == "/api/users":
                db = load_db()
                names = get_member_names()
                users = []
                for uid, p in db.get("users", {}).items():
                    if not isinstance(p, dict):
                        continue
                    st = p.get("stats") or {}
                    info = names.get(uid) or {}
                    users.append({
                        "id": uid,
                        "name": info.get("name") or uid,
                        "avatar": info.get("avatar"),
                        "coins": int(p.get("coins", 0) or 0),
                        "messages": int(p.get("messages", 0) or 0),
                        "games": int(st.get("games", 0) or 0),
                        "wins": int(st.get("wins", 0) or 0),
                        "daily_last": p.get("daily_last"),
                    })
                users.sort(key=lambda u: u["coins"], reverse=True)
                self._json({"users": users})

            else:
                self._json({"error": "Bulunamadı"}, 404)
        except Exception as e:
            self._json({"error": f"{type(e).__name__}: {e}"}, 500)

    # ── POST ──
    def do_POST(self):
        path = urlparse(self.path).path
        body = self._read_json()

        if path == "/api/login":
            self._handle_login(body)
            return

        if path == "/api/logout":
            self._json({"ok": True}, headers={
                "Set-Cookie": "tavern_gui=; Path=/; Max-Age=0; HttpOnly; SameSite=Strict",
            })
            return

        if not self._authed():
            self._json({"error": "Oturum gerekli"}, 401)
            return

        try:
            if path == "/api/bot/start":
                self._json(start_bot())

            elif path == "/api/bot/stop":
                self._json(stop_bot())

            elif path == "/api/bot/restart":
                self._json(restart_bot())

            elif path == "/api/channels/test":
                tid = str(body.get("tavern_id", "")).strip()
                cid = str(body.get("channel_id", "")).strip()
                if not tid or not cid:
                    self._json({"ok": False, "error": "Kanal bilgisi eksik."}, 400)
                else:
                    self._json(asyncio.run(_test_channel_async(tid, cid)))

            elif path == "/api/send":
                tid = str(body.get("tavern_id", "")).strip()
                cid = str(body.get("channel_id", "")).strip()
                content = str(body.get("content", "")).strip()
                if not tid or not cid or not content:
                    self._json({"ok": False, "error": "Kanal ve mesaj zorunlu."}, 400)
                elif len(content) > 2000:
                    self._json({"ok": False, "error": "Mesaj 2000 karakteri aşamaz."}, 400)
                else:
                    self._json(asyncio.run(_send_message_async(tid, cid, content)))

            elif path == "/api/users/update":
                self._update_user(body)

            elif path == "/api/users/delete":
                self._delete_user(body)

            else:
                self._json({"error": "Bulunamadı"}, 404)

        except Exception as e:
            self._json({"error": f"{type(e).__name__}: {e}"}, 500)

    # ── işlemler ──
    def _handle_login(self, body: dict):
        ip = self._client_ip()
        now = time.time()
        with _attempt_lock:
            state = _login_attempts.setdefault(ip, {"count": 0, "until": 0.0})
            if state["until"] > now:
                wait = int(state["until"] - now) + 1
                self._json({"ok": False, "error": f"Çok fazla deneme. {wait} saniye bekle."}, 429)
                return

        given = str(body.get("password", ""))
        if hmac.compare_digest(given, PASSWORD):
            with _attempt_lock:
                _login_attempts[ip] = {"count": 0, "until": 0.0}
            self._json({"ok": True}, headers={
                "Set-Cookie": (
                    f"tavern_gui={make_token()}; Path=/; Max-Age={SESSION_HOURS * 3600}; "
                    "HttpOnly; SameSite=Strict"
                ),
            })
        else:
            with _attempt_lock:
                state = _login_attempts.setdefault(ip, {"count": 0, "until": 0.0})
                state["count"] += 1
                if state["count"] >= 5:
                    state["until"] = now + 60
                    state["count"] = 0
            self._json({"ok": False, "error": "Şifre yanlış."}, 401)

    def _update_user(self, body: dict):
        uid = str(body.get("id", "")).strip()
        if not uid:
            self._json({"ok": False, "error": "Kullanıcı ID eksik."}, 400)
            return

        db = load_db()
        user = db.get("users", {}).get(uid)
        if not isinstance(user, dict):
            self._json({"ok": False, "error": "Kullanıcı bulunamadı."}, 404)
            return

        if "coins" in body:
            try:
                coins = int(body["coins"])
            except (TypeError, ValueError):
                self._json({"ok": False, "error": "Altın miktarı sayı olmalı."}, 400)
                return
            user["coins"] = max(0, min(coins, 1_000_000_000))

        if "messages" in body:
            try:
                user["messages"] = max(0, int(body["messages"]))
            except (TypeError, ValueError):
                pass

        if body.get("reset_daily"):
            user["daily_last"] = None

        save_db(db)
        self._json({"ok": True})

    def _delete_user(self, body: dict):
        uid = str(body.get("id", "")).strip()
        db = load_db()
        users = db.get("users", {})
        if uid in users:
            del users[uid]
            save_db(db)
            self._json({"ok": True})
        else:
            self._json({"ok": False, "error": "Kullanıcı bulunamadı."}, 404)

    # ── durum ──
    def api_status(self) -> dict:
        db = load_db()
        users = [p for p in db.get("users", {}).values() if isinstance(p, dict)]
        total_coins = sum(int(p.get("coins", 0) or 0) for p in users)
        total_messages = sum(int(p.get("messages", 0) or 0) for p in users)
        games = sum(int((p.get("stats") or {}).get("games", 0) or 0) for p in users)
        pids = find_bot_pids()

        started = None
        try:
            if pids and PID_FILE.exists():
                started = datetime.fromtimestamp(PID_FILE.stat().st_mtime).strftime("%d.%m.%Y %H:%M")
        except Exception:
            pass

        return {
            "running": bool(pids),
            "pids": pids,
            "started": started,
            "users": len(users),
            "total_coins": total_coins,
            "total_messages": total_messages,
            "games": games,
            "model": os.environ.get("AI_MODEL", "-"),
            "ai_url": os.environ.get("AI_BASE_URL", "-"),
            "prefix": os.environ.get("BOT_PREFIX", "!"),
            "server_time": datetime.now().strftime("%d.%m.%Y %H:%M:%S"),
        }


# ─── Arayüz HTML ───────────────────────────────────────────────
LOGIN_HTML = r"""<!DOCTYPE html>
<html lang="tr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Tavern Bot — Giriş</title>
<style>
  :root { --gold:#e6b84c; --bg:#0e1015; --card:#171b24; --text:#e8e6e1; --muted:#8b93a3; }
  * { box-sizing:border-box; }
  body { margin:0; min-height:100vh; display:flex; align-items:center; justify-content:center;
         background:radial-gradient(circle at 50% 0%, #1d2333 0%, #0e1015 60%);
         font-family:'Segoe UI',system-ui,sans-serif; color:var(--text); }
  .box { width:360px; background:var(--card); border:1px solid #262c3a; border-radius:16px;
         padding:32px 28px; box-shadow:0 20px 60px rgba(0,0,0,.5); }
  h1 { margin:0 0 6px; font-size:22px; text-align:center; }
  h1 span { color:var(--gold); }
  p.sub { margin:0 0 24px; text-align:center; color:var(--muted); font-size:13px; }
  input { width:100%; padding:12px 14px; border-radius:10px; border:1px solid #303849;
          background:#0f131b; color:var(--text); font-size:15px; outline:none; }
  input:focus { border-color:var(--gold); }
  button { width:100%; margin-top:14px; padding:12px; border:0; border-radius:10px; cursor:pointer;
           background:linear-gradient(135deg,#e6b84c,#c9962f); color:#1a1206; font-weight:700; font-size:15px; }
  button:disabled { opacity:.6; cursor:default; }
  .err { margin-top:12px; color:#ff6b6b; font-size:13px; text-align:center; min-height:18px; }
  .foot { margin-top:20px; text-align:center; color:var(--muted); font-size:11px; }
</style>
</head>
<body>
  <form class="box" id="form">
    <h1>🍺 Tavern <span>Panel</span></h1>
    <p class="sub">Bu panel yalnızca bu bilgisayardan erişilebilir.</p>
    <input id="pw" type="password" placeholder="GUI şifresi" autocomplete="current-password" autofocus>
    <button id="btn" type="submit">Giriş Yap</button>
    <div class="err" id="err"></div>
    <div class="foot">Şifre <code>.env</code> dosyasındaki <code>GUI_PASSWORD</code> satırındadır.</div>
  </form>
<script>
const form = document.getElementById('form');
const pw = document.getElementById('pw');
const err = document.getElementById('err');
const btn = document.getElementById('btn');
form.addEventListener('submit', async (e) => {
  e.preventDefault();
  err.textContent = ''; btn.disabled = true; btn.textContent = 'Kontrol ediliyor...';
  try {
    const r = await fetch('/api/login', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({ password: pw.value })
    });
    const d = await r.json();
    if (r.ok && d.ok) { location.reload(); return; }
    err.textContent = d.error || 'Giriş başarısız.';
  } catch (ex) { err.textContent = 'Sunucuya ulaşılamadı.'; }
  btn.disabled = false; btn.textContent = 'Giriş Yap';
});
</script>
</body>
</html>
"""

APP_HTML = r"""<!DOCTYPE html>
<html lang="tr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Tavern Bot — Panel</title>
<style>
  :root {
    --gold:#e6b84c; --bg:#0e1015; --card:#171b24; --card2:#1d222d;
    --text:#e8e6e1; --muted:#8b93a3; --line:#262c3a;
    --green:#38d39f; --red:#ff6b6b; --blue:#5aa9ff;
  }
  * { box-sizing:border-box; }
  body { margin:0; background:var(--bg); color:var(--text);
         font-family:'Segoe UI',system-ui,sans-serif; }
  header { position:sticky; top:0; z-index:10; display:flex; align-items:center; gap:16px;
           padding:14px 22px; background:#12151dcc; backdrop-filter:blur(10px);
           border-bottom:1px solid var(--line); }
  header h1 { font-size:17px; margin:0; white-space:nowrap; }
  header h1 span { color:var(--gold); }
  .status { display:flex; align-items:center; gap:8px; font-size:13px; color:var(--muted); }
  .dot { width:10px; height:10px; border-radius:50%; background:#555; }
  .dot.on { background:var(--green); box-shadow:0 0 10px var(--green); }
  .dot.off { background:var(--red); }
  .spacer { flex:1; }
  button { cursor:pointer; border:1px solid var(--line); background:var(--card2); color:var(--text);
           padding:8px 14px; border-radius:9px; font-size:13px; transition:.15s; }
  button:hover { border-color:var(--gold); color:var(--gold); }
  button.primary { background:linear-gradient(135deg,#e6b84c,#c9962f); border:0; color:#1a1206; font-weight:700; }
  button.primary:hover { color:#1a1206; filter:brightness(1.07); }
  button.danger { border-color:#5a2a2a; color:#ff8a8a; }
  button.danger:hover { background:#3a1d1d; color:#ffb3b3; }
  button.small { padding:5px 10px; font-size:12px; }
  button:disabled { opacity:.45; cursor:default; }
  nav { display:flex; gap:6px; padding:16px 22px 0; flex-wrap:wrap; }
  nav button { border:0; background:transparent; color:var(--muted); font-size:14px; padding:9px 14px; }
  nav button.active { color:var(--gold); background:var(--card); border-radius:9px; }
  main { padding:18px 22px 48px; max-width:1200px; margin:0 auto; }
  .grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(210px,1fr)); gap:14px; }
  .card { background:var(--card); border:1px solid var(--line); border-radius:14px; padding:18px; }
  .card .label { color:var(--muted); font-size:12px; text-transform:uppercase; letter-spacing:.06em; }
  .card .value { font-size:26px; font-weight:700; margin-top:6px; }
  .card .value.gold { color:var(--gold); }
  .card .hint { color:var(--muted); font-size:12px; margin-top:4px; }
  .row { display:flex; gap:10px; flex-wrap:wrap; align-items:center; }
  table { width:100%; border-collapse:collapse; font-size:13px; }
  th, td { text-align:left; padding:10px 8px; border-bottom:1px solid var(--line); vertical-align:middle; }
  th { color:var(--muted); font-weight:600; font-size:11px; text-transform:uppercase; letter-spacing:.05em; }
  tr:hover td { background:#1b202a; }
  .mono { font-family:Consolas,monospace; font-size:12px; color:var(--muted); }
  .user { display:flex; align-items:center; gap:10px; min-width:190px; }
  .avatar { width:30px; height:30px; border-radius:50%; object-fit:cover; background:#2a3140;
            display:flex; align-items:center; justify-content:center; font-size:12px;
            color:var(--gold); flex:0 0 30px; }
  .uname { font-weight:600; font-size:13px; }
  .uid { font-family:Consolas,monospace; font-size:10px; color:var(--muted); margin-top:1px; }
  input[type=number], input[type=text], select, textarea {
    background:#0f131b; color:var(--text); border:1px solid #303849; border-radius:8px;
    padding:8px 10px; font-size:13px; outline:none; }
  input:focus, select:focus, textarea:focus { border-color:var(--gold); }
  textarea { width:100%; min-height:120px; resize:vertical; font-family:inherit; }
  .w80 { width:80px; }
  .badge { display:inline-block; padding:2px 8px; border-radius:20px; font-size:11px; }
  .badge.ok { background:#123a2e; color:var(--green); }
  .badge.no { background:#3a1d1d; color:#ff8a8a; }
  .muted { color:var(--muted); }
  .hidden { display:none !important; }
  pre.log { background:#0b0e13; border:1px solid var(--line); border-radius:12px; padding:14px;
            height:64vh; overflow:auto; font-family:Consolas,monospace; font-size:12px;
            line-height:1.55; white-space:pre-wrap; word-break:break-word; }
  .toast { position:fixed; right:20px; bottom:20px; background:var(--card2); border:1px solid var(--line);
           border-left:3px solid var(--gold); padding:12px 16px; border-radius:10px; font-size:13px;
           box-shadow:0 10px 30px rgba(0,0,0,.4); opacity:0; transform:translateY(10px);
           transition:.25s; pointer-events:none; max-width:360px; }
  .toast.show { opacity:1; transform:translateY(0); }
  .section-title { font-size:15px; margin:0 0 12px; }
  .mt { margin-top:16px; }
  .search { max-width:280px; }
  @media (max-width:640px) {
    header { flex-wrap:wrap; gap:10px; padding:12px 14px; }
    main, nav { padding-left:14px; padding-right:14px; }
    .card .value { font-size:22px; }
    table { font-size:12px; }
  }
</style>
</head>
<body>
<header>
  <h1>🍺 Tavern <span>Panel</span></h1>
  <div class="status"><span class="dot" id="dot"></span><span id="statusText">Kontrol ediliyor...</span></div>
  <div class="spacer"></div>
  <button class="primary" id="btnStart">▶ Başlat</button>
  <button id="btnRestart">⟳ Yeniden Başlat</button>
  <button class="danger" id="btnStop">■ Durdur</button>
  <button class="small" id="btnLogout">Çıkış</button>
</header>

<nav>
  <button class="active" data-tab="dashboard">Genel</button>
  <button data-tab="channels">Kanallar</button>
  <button data-tab="economy">Ekonomi</button>
  <button data-tab="send">Mesaj Gönder</button>
  <button data-tab="logs">Loglar</button>
</nav>

<main>
  <!-- GENEL -->
  <section id="tab-dashboard">
    <div class="grid">
      <div class="card"><div class="label">Bot Durumu</div><div class="value" id="sRunning">—</div><div class="hint" id="sStarted">—</div></div>
      <div class="card"><div class="label">Kayıtlı Kullanıcı</div><div class="value" id="sUsers">—</div><div class="hint" id="sMessages">—</div></div>
      <div class="card"><div class="label">Toplam Altın</div><div class="value gold" id="sCoins">—</div><div class="hint" id="sGames">—</div></div>
      <div class="card"><div class="label">AI Modeli</div><div class="value" style="font-size:17px" id="sModel">—</div><div class="hint" id="sAiUrl">—</div></div>
    </div>
    <div class="card mt">
      <h3 class="section-title">Hızlı Bilgi</h3>
      <div class="row muted" style="font-size:13px">
        <span>Prefix: <b id="sPrefix">—</b></span>
        <span>•</span>
        <span>Sunucu saati: <b id="sTime">—</b></span>
        <span>•</span>
        <span>PID: <b id="sPids">—</b></span>
      </div>
      <p class="muted" style="font-size:13px;line-height:1.7">
        Bu panel aynı Wi-Fi/LAN ağıdaki cihazlardan erişilebilir. <code>http://&lt;bilgisayar-ip&gt;:8787</code>
        üzerinden açabilirsin. Botu buradan başlatıp durdurabilirsin. Değişiklikler anında uygulanır.
      </p>
    </div>
  </section>

  <!-- KANALLAR -->
  <section id="tab-channels" class="hidden">
    <div class="card">
      <div class="row">
        <h3 class="section-title" style="margin:0">Kanallar ve Yazma İzni</h3>
        <div class="spacer"></div>
        <button class="small" id="chRefresh">⟳ Yenile</button>
      </div>
      <p class="muted" style="font-size:12px">"Test Et" butonu kanala kısa bir mesaj gönderip siler; botun o kanala yazma izni olup olmadığını gösterir.</p>
      <div style="overflow:auto">
        <table>
          <thead><tr><th>Kanal</th><th>Tür</th><th>Tavern</th><th>Sonuç</th><th></th></tr></thead>
          <tbody id="chBody"><tr><td colspan="5" class="muted">Yükleniyor...</td></tr></tbody>
        </table>
      </div>
    </div>
  </section>

  <!-- EKONOMİ -->
  <section id="tab-economy" class="hidden">
    <div class="card">
      <div class="row">
        <h3 class="section-title" style="margin:0">Ekonomi Yönetimi</h3>
        <div class="spacer"></div>
        <input class="search" type="text" id="userSearch" placeholder="Kullanıcı adı veya ID ara...">
        <button class="small" id="usersRefresh">⟳ Yenile</button>
      </div>
      <p class="muted" style="font-size:12px">Altın değerini değiştirip "Kaydet"e bas. "Günlük" butonu o kullanıcının günlük ödül kilidini açar.</p>
      <div style="overflow:auto">
        <table>
          <thead><tr><th>Kullanıcı</th><th>Altın</th><th>Mesaj</th><th>Oyun</th><th>Günlük</th><th></th></tr></thead>
          <tbody id="userBody"><tr><td colspan="6" class="muted">Yükleniyor...</td></tr></tbody>
        </table>
      </div>
    </div>
  </section>

  <!-- MESAJ -->
  <section id="tab-send" class="hidden">
    <div class="card">
      <h3 class="section-title">Kanal Seç ve Mesaj Gönder</h3>
      <div class="row">
        <select id="sendChannel" style="min-width:280px"><option value="">Kanal seç...</option></select>
        <button class="small" id="sendChRefresh">⟳ Kanalları Yenile</button>
      </div>
      <div class="mt">
        <textarea id="sendText" maxlength="2000" placeholder="Mesajını yaz... (en fazla 2000 karakter)"></textarea>
      </div>
      <div class="row mt">
        <button class="primary" id="btnSend">📨 Gönder</button>
        <span class="muted" id="sendInfo" style="font-size:12px"></span>
      </div>
    </div>
  </section>

  <!-- LOGLAR -->
  <section id="tab-logs" class="hidden">
    <div class="card">
      <div class="row">
        <h3 class="section-title" style="margin:0">Canlı Loglar</h3>
        <div class="spacer"></div>
        <label class="muted" style="font-size:12px;display:flex;gap:6px;align-items:center">
          <input type="checkbox" id="logAuto" checked> Otomatik yenile
        </label>
        <button class="small" id="logRefresh">⟳ Yenile</button>
        <button class="small" id="logBottom">⬇ En alta</button>
      </div>
      <pre class="log mt" id="logBox">Yükleniyor...</pre>
    </div>
  </section>
</main>

<div class="toast" id="toast"></div>

<script>
// ─── yardımcılar ───────────────────────────────────────────────
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

let toastTimer = null;
function toast(msg, ok = true) {
  const t = $('toast');
  t.textContent = msg;
  t.style.borderLeftColor = ok ? '#38d39f' : '#ff6b6b';
  t.classList.add('show');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove('show'), 3200);
}

async function api(path, opts = {}) {
  const res = await fetch(path, {
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json' },
    ...opts,
  });
  if (res.status === 401) { location.reload(); throw new Error('Oturum sonlandı'); }
  const data = await res.json().catch(() => ({}));
  if (!res.ok || (data.ok === false && data.error)) throw new Error(data.error || 'İşlem başarısız');
  return data;
}

// ─── sekmeler ──────────────────────────────────────────────────
let currentTab = 'dashboard';
document.querySelectorAll('nav button').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('nav button').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    currentTab = btn.dataset.tab;
    document.querySelectorAll('main section').forEach(s => s.classList.add('hidden'));
    $('tab-' + currentTab).classList.remove('hidden');
    if (currentTab === 'channels') loadChannels();
    if (currentTab === 'economy') loadUsers();
    if (currentTab === 'send') loadChannelSelect();
    if (currentTab === 'logs') loadLogs();
  });
});

// ─── durum ─────────────────────────────────────────────────────
async function loadStatus() {
  try {
    const s = await api('/api/status');
    $('dot').className = 'dot ' + (s.running ? 'on' : 'off');
    $('statusText').textContent = s.running ? 'Çalışıyor' : 'Kapalı';
    $('sRunning').textContent = s.running ? 'Çalışıyor' : 'Kapalı';
    $('sRunning').style.color = s.running ? 'var(--green)' : 'var(--red)';
    $('sStarted').textContent = s.running ? ('Başlangıç: ' + (s.started || '—')) : 'Bot şu an kapalı';
    $('sUsers').textContent = s.users;
    $('sMessages').textContent = s.total_messages + ' toplam mesaj';
    $('sCoins').textContent = s.total_coins.toLocaleString('tr-TR');
    $('sGames').textContent = s.games + ' oynanan oyun';
    $('sModel').textContent = s.model;
    $('sAiUrl').textContent = s.ai_url;
    $('sPrefix').textContent = s.prefix;
    $('sTime').textContent = s.server_time;
    $('sPids').textContent = s.pids.length ? s.pids.join(', ') : '—';
    $('btnStart').disabled = s.running;
    $('btnStop').disabled = !s.running;
  } catch (e) { /* sessiz */ }
}

$('btnStart').addEventListener('click', async () => {
  $('btnStart').disabled = true;
  try { const r = await api('/api/bot/start', {method:'POST'}); toast(r.message || 'Bot başlatıldı (PID ' + r.pid + ')'); }
  catch (e) { toast(e.message, false); }
  setTimeout(loadStatus, 1200);
});
$('btnStop').addEventListener('click', async () => {
  if (!confirm('Botu durdurmak istediğine emin misin?')) return;
  try { const r = await api('/api/bot/stop', {method:'POST'}); toast('Bot durduruldu (' + (r.stopped || 0) + ' süreç)'); }
  catch (e) { toast(e.message, false); }
  setTimeout(loadStatus, 800);
});
$('btnRestart').addEventListener('click', async () => {
  try { await api('/api/bot/restart', {method:'POST'}); toast('Bot yeniden başlatıldı'); }
  catch (e) { toast(e.message, false); }
  setTimeout(loadStatus, 2500);
});
$('btnLogout').addEventListener('click', async () => {
  await fetch('/api/logout', {method:'POST'});
  location.reload();
});

// ─── kanallar ──────────────────────────────────────────────────
let channels = [];

async function loadChannels(force = false) {
  $('chBody').innerHTML = '<tr><td colspan="5" class="muted">Yükleniyor...</td></tr>';
  try {
    const d = await api('/api/channels' + (force ? '?force=1' : ''));
    channels = d.channels || [];
    if (!channels.length) { $('chBody').innerHTML = '<tr><td colspan="5" class="muted">Kanal bulunamadı.</td></tr>'; return; }
    $('chBody').innerHTML = channels.map((c, i) => `
      <tr>
        <td><b>${esc(c.name)}</b><div class="mono">${esc(c.id)}</div></td>
        <td><span class="badge ${c.writable ? 'ok' : 'no'}">${esc(c.type)}</span></td>
        <td>${esc(c.tavern)}</td>
        <td id="chres-${i}" class="muted">—</td>
        <td><button class="small" data-chtest="${i}">Test Et</button></td>
      </tr>`).join('');
    document.querySelectorAll('[data-chtest]').forEach(b => {
      b.addEventListener('click', () => testChannel(parseInt(b.dataset.chtest), b));
    });
  } catch (e) {
    $('chBody').innerHTML = `<tr><td colspan="5" style="color:var(--red)">${esc(e.message)}</td></tr>`;
  }
}

async function testChannel(i, btn) {
  const c = channels[i];
  const cell = $('chres-' + i);
  btn.disabled = true; cell.className = 'muted'; cell.textContent = 'Test ediliyor...';
  try {
    const r = await api('/api/channels/test', { method:'POST', body: JSON.stringify({ tavern_id:c.tavern_id, channel_id:c.id }) });
    if (r.ok) {
      cell.className = ''; cell.innerHTML = '<span class="badge ok">Yazabiliyor</span>' + (r.deleted ? '' : ' <span class="muted">(test silinemedi)</span>');
    } else {
      cell.className = ''; cell.innerHTML = '<span class="badge no">İzin yok</span> <span class="muted" style="font-size:11px">' + esc(r.error || '') + '</span>';
    }
  } catch (e) {
    cell.className = ''; cell.innerHTML = '<span class="badge no">Hata</span> ' + esc(e.message);
  }
  btn.disabled = false;
}
$('chRefresh').addEventListener('click', () => loadChannels(true));

// ─── ekonomi ───────────────────────────────────────────────────
let users = [];

async function loadUsers() {
  $('userBody').innerHTML = '<tr><td colspan="6" class="muted">Yükleniyor...</td></tr>';
  try {
    const d = await api('/api/users');
    users = d.users || [];
    renderUsers();
  } catch (e) {
    $('userBody').innerHTML = `<tr><td colspan="6" style="color:var(--red)">${esc(e.message)}</td></tr>`;
  }
}

function renderUsers() {
  const q = $('userSearch').value.trim().toLowerCase();
  const list = q
    ? users.filter(u => u.id.toLowerCase().includes(q) || String(u.name || '').toLowerCase().includes(q))
    : users;
  if (!list.length) { $('userBody').innerHTML = '<tr><td colspan="6" class="muted">Kullanıcı yok.</td></tr>'; return; }
  $('userBody').innerHTML = list.map(u => {
    const initial = esc(String(u.name || '?').trim().charAt(0).toUpperCase() || '?');
    const avatar = u.avatar
      ? `<img class="avatar" src="${esc(u.avatar)}" alt="" onerror="this.style.display='none'">`
      : `<div class="avatar">${initial}</div>`;
    return `
    <tr>
      <td>
        <div class="user">
          ${avatar}
          <div>
            <div class="uname">${esc(u.name || u.id)}</div>
            <div class="uid">${esc(u.id)}</div>
          </div>
        </div>
      </td>
      <td><input type="number" class="w80" min="0" value="${u.coins}" data-coins="${esc(u.id)}"></td>
      <td>${u.messages}</td>
      <td>${u.games}</td>
      <td class="muted">${u.daily_last ? esc(u.daily_last) : '—'}</td>
      <td class="row" style="gap:6px">
        <button class="small primary" data-save="${esc(u.id)}">Kaydet</button>
        <button class="small" data-daily="${esc(u.id)}">Günlük</button>
        <button class="small danger" data-del="${esc(u.id)}">Sil</button>
      </td>
    </tr>`;
  }).join('');

  document.querySelectorAll('[data-save]').forEach(b => b.addEventListener('click', () => saveUser(b.dataset.save)));
  document.querySelectorAll('[data-daily]').forEach(b => b.addEventListener('click', () => resetDaily(b.dataset.daily)));
  document.querySelectorAll('[data-del]').forEach(b => b.addEventListener('click', () => deleteUser(b.dataset.del)));
}

async function saveUser(id) {
  const input = document.querySelector(`[data-coins="${CSS.escape(id)}"]`);
  try {
    await api('/api/users/update', { method:'POST', body: JSON.stringify({ id, coins: parseInt(input.value || '0') }) });
    toast('Bakiye güncellendi');
    loadUsers(); loadStatus();
  } catch (e) { toast(e.message, false); }
}
async function resetDaily(id) {
  try {
    await api('/api/users/update', { method:'POST', body: JSON.stringify({ id, reset_daily: true }) });
    toast('Günlük ödül kilidi açıldı');
    loadUsers();
  } catch (e) { toast(e.message, false); }
}
async function deleteUser(id) {
  if (!confirm('Bu kullanıcıyı veritabanından silmek istediğine emin misin?')) return;
  try {
    await api('/api/users/delete', { method:'POST', body: JSON.stringify({ id }) });
    toast('Kullanıcı silindi');
    loadUsers(); loadStatus();
  } catch (e) { toast(e.message, false); }
}

$('userSearch').addEventListener('input', renderUsers);
$('usersRefresh').addEventListener('click', loadUsers);

// ─── mesaj gönder ──────────────────────────────────────────────
async function loadChannelSelect(force = false) {
  try {
    const d = await api('/api/channels' + (force ? '?force=1' : ''));
    channels = d.channels || [];
    const sel = $('sendChannel');
    const current = sel.value;
    sel.innerHTML = '<option value="">Kanal seç...</option>' + channels.map((c, i) =>
      `<option value="${i}">${esc(c.name)} — ${esc(c.tavern)} (${esc(c.type)})</option>`).join('');
    if (current) sel.value = current;
  } catch (e) { toast(e.message, false); }
}
$('sendChRefresh').addEventListener('click', () => loadChannelSelect(true));

$('sendText').addEventListener('input', () => {
  $('sendInfo').textContent = $('sendText').value.length + ' / 2000';
});

$('btnSend').addEventListener('click', async () => {
  const idx = $('sendChannel').value;
  const content = $('sendText').value.trim();
  if (idx === '') return toast('Bir kanal seç.', false);
  if (!content) return toast('Mesaj boş olamaz.', false);
  const c = channels[parseInt(idx)];
  $('btnSend').disabled = true;
  try {
    const r = await api('/api/send', { method:'POST', body: JSON.stringify({ tavern_id:c.tavern_id, channel_id:c.id, content }) });
    toast('Mesaj gönderildi ✓');
    $('sendText').value = ''; $('sendInfo').textContent = '';
  } catch (e) { toast(e.message, false); }
  $('btnSend').disabled = false;
});

// ─── loglar ────────────────────────────────────────────────────
async function loadLogs(scroll = false) {
  try {
    const d = await api('/api/logs?lines=400');
    const box = $('logBox');
    const wasBottom = box.scrollTop + box.clientHeight >= box.scrollHeight - 40;
    box.textContent = d.log || '(log boş)';
    if (scroll || wasBottom) box.scrollTop = box.scrollHeight;
  } catch (e) { $('logBox').textContent = 'Log alınamadı: ' + e.message; }
}
$('logRefresh').addEventListener('click', () => loadLogs());
$('logBottom').addEventListener('click', () => { $('logBox').scrollTop = $('logBox').scrollHeight; });

// ─── döngüler ──────────────────────────────────────────────────
loadStatus();
setInterval(loadStatus, 5000);
setInterval(() => {
  if (currentTab === 'logs' && $('logAuto').checked) loadLogs();
}, 3000);
</script>
</body>
</html>
"""


# ─── Başlatma ──────────────────────────────────────────────────
def find_free_port(start: int) -> int:
    import socket
    for port in range(start, start + 25):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind((HOST, port))
                return port
            except OSError:
                continue
    return start


def main():
    global PORT
    PORT = find_free_port(PORT)

    if not get_token():
        print("HATA: BOT_TOKEN .env dosyasında tanımlı değil.")
        sys.exit(1)

    server = ThreadingHTTPServer((HOST, PORT), Handler)
    # LAN IP adresini otomatik bul; kullanıcıya gerçek erişim adresini göster.
    lan_ip = "127.0.0.1"
    try:
        import socket
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        probe.connect(("8.8.8.8", 80))
        lan_ip = probe.getsockname()[0]
        probe.close()
    except Exception:
        pass

    url = f"http://{lan_ip}:{PORT}"
    print("=" * 62)
    print("            TAVERN BOT — YEREL YÖNETİM PANELİ")
    print("=" * 62)
    print(f"  Adres    : {url}")
    print(f"  Yerel    : http://127.0.0.1:{PORT}")
    print(f"  Şifre    : {PASSWORD}")
    print(f"  Kaynak   : .env -> GUI_PASSWORD")
    print(f"  Erişim   : Wi-Fi/LAN: {lan_ip} ({HOST})")
    print("  Kapatmak : Bu pencereyi kapat veya CTRL+C")
    print("=" * 62)

    if "--no-browser" not in sys.argv:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nPanel kapatılıyor...")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
