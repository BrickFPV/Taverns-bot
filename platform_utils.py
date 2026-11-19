"""
Tavern Bot - Platform Yardımcıları
==================================
Windows ve Linux (Raspberry Pi) uyumluluğu için ortak fonksiyonlar.
"""

import os
import sys
import subprocess
import signal
import platform
from pathlib import Path
from typing import List, Optional


IS_WINDOWS = platform.system() == "Windows"
IS_LINUX = platform.system() == "Linux"
IS_RPI = IS_LINUX and Path("/proc/device-tree/model").read_text(errors="ignore").startswith("Raspberry Pi")


def get_python_cmd() -> str:
    """Platforma göre python komutunu döner."""
    if IS_WINDOWS:
        return sys.executable
    # Linux: önce venv, sonra python3
    venv = Path(__file__).parent / "venv" / "bin" / "python"
    if venv.exists():
        return str(venv)
    return "python3"


def find_bot_pids(script_name: str = "poller.py") -> List[int]:
    """Belirtilen script'i çalıştıran süreçleri bulur."""
    pids: List[int] = []
    try:
        if IS_WINDOWS:
            # Windows: WMI sorgusu
            ps = (
                "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
                f"Where-Object {{ $_.CommandLine -like '*{script_name}*' }} | "
                "Select-Object -ExpandProperty ProcessId"
            )
            out = subprocess.run(
                ["powershell", "-NoProfile", "-Command", ps],
                capture_output=True, text=True, timeout=10,
                creationflags=0x08000000,  # CREATE_NO_WINDOW
            )
            pids = [int(x) for x in out.stdout.split() if x.strip().isdigit()]
        else:
            # Linux: pgrep
            out = subprocess.run(
                ["pgrep", "-f", script_name],
                capture_output=True, text=True, timeout=5,
            )
            pids = [int(x) for x in out.stdout.split() if x.strip().isdigit()]
    except Exception:
        pass
    return pids


def kill_pids(pids: List[int]) -> int:
    """Süreçleri sonlandırır. Dönen değer: sonlandırılan sayı."""
    if not pids:
        return 0
    count = 0
    for pid in pids:
        try:
            if IS_WINDOWS:
                subprocess.run(
                    ["taskkill", "/PID", str(pid), "/F"],
                    capture_output=True, timeout=10,
                    creationflags=0x08000000,
                )
            else:
                os.kill(pid, signal.SIGTERM)
            count += 1
        except Exception:
            pass
    return count


def ensure_utf8():
    """Stdout/stderr encoding'i UTF-8'e zorlar (Windows için kritik)."""
    if IS_WINDOWS:
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    # Linux'ta genelde zaten UTF-8
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    os.environ.setdefault("PYTHONUTF8", "1")


def get_data_dir() -> Path:
    """Veri dizinini döner (cross-platform)."""
    base = Path(__file__).parent
    data = base / "data"
    data.mkdir(exist_ok=True)
    return data


def get_logs_dir() -> Path:
    """Log dizinini döner."""
    base = Path(__file__).parent
    logs = base / "logs"
    logs.mkdir(exist_ok=True)
    return logs


def load_env_file(env_path: Optional[Path] = None) -> dict:
    """.env dosyasını yükler, dict döner."""
    if env_path is None:
        env_path = Path(__file__).parent / ".env"
    result = {}
    if not env_path.exists():
        return result
    try:
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    key, _, value = line.partition("=")
                    key = key.strip()
                    value = value.strip()
                    if value and value[0] in ('"', "'") and value[-1] == value[0]:
                        value = value[1:-1]
                    result[key] = value
                    os.environ.setdefault(key, value)
    except Exception:
        pass
    return result


def is_running_in_service() -> bool:
    """systemd/service altında çalışıyor mu?"""
    return IS_LINUX and (
        os.environ.get("INVOCATION_ID") is not None
        or os.environ.get("JOURNAL_STREAM") is not None
    )


def setup_signal_handlers(on_shutdown):
    """Sinyal yakalayıcıları kurar (Linux için SIGTERM, Windows için CTRL_C)."""
    if IS_WINDOWS:
        try:
            import win32api
            def handler(ctrl_type):
                if ctrl_type in (0, 2):  # CTRL_C_EVENT, CTRL_BREAK_EVENT
                    on_shutdown()
                    return True
                return False
            win32api.SetConsoleCtrlHandler(handler, True)
        except Exception:
            pass
    else:
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                signal.signal(sig, lambda s, f: on_shutdown())
            except Exception:
                pass


# Modül yüklendiğinde UTF-8'i zorla
ensure_utf8()