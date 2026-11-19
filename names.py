"""
Tavern Bot - Kullanıcı Adı Çözümleyici
======================================
ID yerine görünen adları kullanmak için ortak önbellek.

Kullanım:
    from names import member_names, pretty
    await member_names(rest)      # önbelleği tazeler (5 dk)
    print(pretty("Bakiye: <uuid>"))  # UUID'leri adlara çevirir
"""

import re
import time

UUID_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)

MAX_AGE = 300  # saniye

_cache = {"time": 0.0, "data": {}}


async def _fetch(rest) -> dict:
    """Tüm tavernlardaki üyelerin görünen adlarını toplar."""
    me = await rest.get_self()
    names: dict[str, str] = {}
    for t in getattr(me, "taverns", []) or []:
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
            if name:
                names[uid] = name
    return names


async def member_names(rest, force: bool = False, max_age: int = MAX_AGE) -> dict:
    """Ad önbelleğini döner; eskiyse yeniler. Hata olursa mevcut önbelleği korur."""
    now = time.time()
    if not force and _cache["data"] and now - _cache["time"] < max_age:
        return _cache["data"]
    try:
        data = await _fetch(rest)
        if data:
            _cache["data"] = data
            _cache["time"] = now
    except Exception:
        pass
    return _cache["data"]


def name_of(user_id: str) -> str:
    return _cache["data"].get(user_id, user_id)


def find_user_id(query: str) -> str:
    """Bir UUID, etiket veya kullanıcı adını kullanıcı ID'sine çevirir."""
    q = (query or "").strip().replace("<@", "").replace(">", "")
    data = _cache["data"]
    if q in data:
        return q
    low = q.lower().lstrip("@")
    if not low:
        return q
    for uid, name in data.items():
        if str(name).lower() == low:
            return uid
    for uid, name in data.items():
        if str(name).lower().startswith(low):
            return uid
    return q


def pretty(text: str) -> str:
    """Metindeki bilinen UUID'leri kullanıcı adlarıyla değiştirir."""
    if not text:
        return text
    data = _cache["data"]
    if not data:
        return text
    return UUID_RE.sub(lambda m: data.get(m.group(0), m.group(0)), text)
