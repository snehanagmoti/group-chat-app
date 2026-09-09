"""
Database layer for the Secure Persistent Group Chat.

Uses redis-py for Valkey (Redis-compatible).
Handles:
  - Message persistence (encrypted, with HMAC for tamper detection)
  - User public key registry (for ECDSA signature verification)
  - Chat room management (rooms identified by unique 6-char codes)
"""

import hmac
import hashlib
import os
import json
import uuid
import time
import redis
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

# ── Config ────────────────────────────────────────────────────────────────────

# Connect to Valkey using VALKEY_URL from .env
_valkey_url = os.environ.get("VALKEY_URL", "redis://localhost:6274/0")
r = redis.from_url(_valkey_url)

# HMAC_SECRET loaded from environment (set in .env, never hardcoded)
def _get_hmac_secret() -> bytes:
    secret = os.environ.get("HMAC_SECRET", "")
    if not secret:
        raise RuntimeError(
            "HMAC_SECRET is not set in .env — cannot start server safely."
        )
    return bytes.fromhex(secret)

# ── Initialisation ────────────────────────────────────────────────────────────

def init_db() -> None:
    """Ping Valkey to ensure connection and create room loadtest if not exists."""
    try:
        r.ping()
        print(f"[DB] Initialised — connected to Valkey at {_valkey_url}")
        if not r.exists("room:loadtest"):
            try:
                create_room(room_id="loadtest", name="Load Test", created_by="system", is_public=True, avatar="⚡")
            except Exception:
                pass
    except redis.ConnectionError:
        print(f"[DB] Error connecting to Valkey at {_valkey_url}")

# ── HMAC helpers ──────────────────────────────────────────────────────────────

def _compute_hmac(ciphertext: str, iv: str) -> str:
    """
    Compute HMAC-SHA256 over (ciphertext + iv) using HMAC_SECRET from .env.
    Returns the hex digest.
    """
    secret = _get_hmac_secret()
    payload = (ciphertext + iv).encode("utf-8")
    return hmac.new(secret, payload, hashlib.sha256).hexdigest()


def _verify_hmac(ciphertext: str, iv: str, stored_digest: str) -> bool:
    """Re-compute HMAC and compare with stored digest. Returns True if intact."""
    expected = _compute_hmac(ciphertext, iv)
    return hmac.compare_digest(expected, stored_digest)


# ── Room CRUD ─────────────────────────────────────────────────────────────────

def create_room(room_id: str, name: str, created_by: str, is_public: bool = True, avatar: str = "🏰") -> None:
    """
    Insert a new room into the rooms Hash.
    """
    key = f"room:{room_id}"
    inserted = r.hsetnx(key, "id", room_id)
    if not inserted:
        raise Exception("Room already exists")
    
    mapping = {
        "name": name,
        "created_by": created_by,
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "is_public": 1 if is_public else 0,
        "avatar": avatar
    }
    r.hset(key, mapping=mapping)
    if is_public:
        r.sadd("rooms:public", room_id)

def get_room(room_id: str) -> dict | None:
    """Retrieve a room by its code. Returns dict or None."""
    key = f"room:{room_id}"
    data = r.hgetall(key)
    if not data:
        return None
    
    return {
        "id": data[b"id"].decode(),
        "name": data[b"name"].decode(),
        "created_by": data[b"created_by"].decode(),
        "created_at": data.get(b"created_at", b"").decode(),
        "is_public": bool(int(data.get(b"is_public", b"1").decode())),
        "avatar": data.get(b"avatar", b"\xf0\x9f\x8f\xb0").decode() # default 🏰
    }

def update_room_creator_if_system(room_id: str, username: str) -> None:
    """If a room's created_by is 'system' or empty, set it to username."""
    key = f"room:{room_id}"
    created_by = r.hget(key, "created_by")
    if created_by:
        created_by_str = created_by.decode()
        if created_by_str == 'system' or created_by_str == '':
            r.hset(key, "created_by", username)

def list_rooms() -> list[dict]:
    """Return all public rooms (ordered newest first)."""
    room_ids = r.smembers("rooms:public")
    
    rooms = []
    for rid in room_ids:
        rid_str = rid.decode()
        data = get_room(rid_str)
        if data:
            owner_username = data["created_by"]
            owner_avatar = 'wizard'
            if owner_username:
                u_data = get_user(owner_username)
                if u_data:
                    owner_avatar = u_data["avatar"]
            data["owner_avatar"] = owner_avatar
            rooms.append(data)
    
    rooms.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    return rooms

# ── Message CRUD ──────────────────────────────────────────────────────────────

def save_message(
    room_id: str,
    username: str,
    avatar: str,
    ciphertext: str,
    iv: str,
    signature: str,
    public_key: dict,
    timestamp: str,
    sig_valid: bool,
    msg_id: str = "",
    reply_to: str | None = None,
    target_user: str | None = None,
    attachment: str | None = None,
) -> int:
    if not msg_id:
        msg_id = str(uuid.uuid4())
    key = f"msg:{msg_id}"
    ts = time.time()

    inserted = r.hsetnx(key, "username", username)
    if not inserted:
        return -1  # duplicate — silently ignored

    pipe = r.pipeline()
    pipe.hset(key, mapping={
        "room_id": room_id,
        "avatar": avatar,
        "ciphertext": ciphertext,
        "iv": iv,
        "signature": signature,
        "public_key": json.dumps(public_key),
        "timestamp": timestamp,
        "sig_valid": int(sig_valid),
        "reply_to": reply_to or "",
        "target_user": target_user or "",
        "is_deleted": 0,
        "is_edited": 0,
        "created_at_ts": ts,
        "attachment": attachment or "",
        "hmac_digest": _compute_hmac(ciphertext, iv),
    })
    pipe.zadd("feed:all", {msg_id: ts})
    pipe.zadd(f"feed:room:{room_id}", {msg_id: ts})
    pipe.execute()
    return 0

def get_history(room_id: str, limit: int | None = None, username: str | None = None) -> list[dict]:
    if limit is not None and limit > 0:
        ids = r.zrange(f"feed:room:{room_id}", -limit, -1)
    else:
        ids = r.zrange(f"feed:room:{room_id}", 0, -1)
        
    pipe = r.pipeline()
    for mid in ids:
        pipe.hgetall(f"msg:{mid.decode()}")
    rows = pipe.execute()
    
    messages = []
    for mid, fields in zip(ids, rows):
        if not fields:
            continue
            
        f = {k.decode(): v.decode() for k, v in fields.items()}
        tgt = f.get("target_user", "")
        if tgt and username:
            sender = f.get("username", "")
            if sender.lower() != username.lower() and tgt.lower() != username.lower():
                continue
                
        hmac_digest = f.get("hmac_digest", "")
        ciphertext = f.get("ciphertext", "")
        iv = f.get("iv", "")
        tampered = not _verify_hmac(ciphertext, iv, hmac_digest)
        
        messages.append({
            "type": "message",
            "msg_id": mid.decode(),
            "username": f.get("username", ""),
            "avatar": f.get("avatar", ""),
            "ciphertext": ciphertext,
            "iv": iv,
            "signature": f.get("signature", ""),
            "public_key": json.loads(f.get("public_key", "{}")),
            "timestamp": f.get("timestamp", ""),
            "sig_valid": bool(int(f.get("sig_valid", "0"))),
            "tampered": tampered,
            "reply_to": f.get("reply_to", None) if f.get("reply_to") else None,
            "is_deleted": bool(int(f.get("is_deleted", "0"))),
            "target_user": f.get("target_user", None) if f.get("target_user") else None,
            "is_edited": bool(int(f.get("is_edited", "0"))),
            "created_at_ts": float(f.get("created_at_ts", "0")),
            "attachment": json.loads(f.get("attachment")) if f.get("attachment") else None,
        })
    return messages

def get_message_by_id(msg_id: str) -> dict | None:
    key = f"msg:{msg_id}"
    data = r.hgetall(key)
    if not data:
        return None
    f = {k.decode(): v.decode() for k, v in data.items()}
    return {
        "msg_id": msg_id,
        "username": f.get("username", ""),
        "avatar": f.get("avatar", ""),
        "ciphertext": f.get("ciphertext", ""),
        "iv": f.get("iv", ""),
        "timestamp": f.get("timestamp", ""),
        "is_deleted": bool(int(f.get("is_deleted", "0"))),
        "target_user": f.get("target_user", None) if f.get("target_user") else None,
    }

def delete_message(msg_id: str, username: str) -> bool:
    key = f"msg:{msg_id}"
    data = r.hgetall(key)
    if not data:
        return False
    f = {k.decode(): v.decode() for k, v in data.items()}
    
    if bool(int(f.get("is_deleted", "0"))):
        return False
    if f.get("username", "").lower() != username.lower():
        return False
        
    r.hset(key, mapping={
        "is_deleted": 1,
        "ciphertext": "",
        "iv": "",
        "signature": ""
    })
    print(f"[DB] Message '{msg_id}' deleted by {username}")
    return True

def edit_message(
    msg_id: str,
    username: str,
    ciphertext: str,
    iv: str,
    signature: str,
    sig_valid: bool,
) -> tuple[bool, str]:
    key = f"msg:{msg_id}"
    data = r.hgetall(key)
    if not data:
        return False, "Message not found."
    f = {k.decode(): v.decode() for k, v in data.items()}
    
    if bool(int(f.get("is_deleted", "0"))):
        return False, "Cannot edit a deleted message."
    if f.get("username", "").lower() != username.lower():
        return False, "You can only edit your own messages."
        
    created_ts = float(f.get("created_at_ts", "0"))
    if created_ts and (time.time() - created_ts > 300):
        return False, "Message edit window (5 minutes) has expired."
        
    hmac_digest = _compute_hmac(ciphertext, iv)
    r.hset(key, mapping={
        "ciphertext": ciphertext,
        "iv": iv,
        "signature": signature,
        "hmac_digest": hmac_digest,
        "sig_valid": 1 if sig_valid else 0,
        "is_edited": 1
    })
    print(f"[DB] Message '{msg_id}' edited by {username}")
    return True, "Message updated successfully."


# ── Simple load-test endpoints ───────────────────────────────────────────────

def save_message_simple(msg_id, room_id, username, text):
    key = f"msg:{msg_id}"
    inserted = r.hsetnx(key, "username", username)
    if not inserted:
        return False   # duplicate
    ts = time.time()
    pipe = r.pipeline()
    pipe.hset(key, mapping={"room_id": room_id, "text": text,
                            "timestamp": ts, "simple": 1})
    pipe.zadd("feed:all", {msg_id: ts})
    pipe.zadd(f"feed:room:{room_id}", {msg_id: ts})
    pipe.execute()
    return True

def get_all_messages_simple(room_id="loadtest"):
    ids = r.zrange(f"feed:room:{room_id}", 0, -1)
    pipe = r.pipeline()
    for mid in ids:
        pipe.hgetall(f"msg:{mid.decode()}")
    rows = pipe.execute()
    result = []
    for mid, fields in zip(ids, rows):
        if not fields:
            continue
        f = {k.decode(): v.decode() for k, v in fields.items()}
        result.append({
            "msg_id": mid.decode(),
            "client-name": f.get("username", ""),
            "msg": f.get("text", ""),
            "timestamp": f.get("timestamp", ""),
        })
    return result

# ── User key registry ─────────────────────────────────────────────────────────

def register_user_key(username: str, public_key: dict) -> None:
    r.set(f"userkey:{username.lower()}", json.dumps(public_key))

def get_user_key(username: str) -> dict | None:
    data = r.get(f"userkey:{username.lower()}")
    return json.loads(data) if data else None

# ── Persistent user accounts ───────────────────────────────────────────────────

def create_user(username: str, password_hash: str, avatar: str) -> None:
    key = f"user:{username.lower()}"
    inserted = r.hsetnx(key, "username", username)
    if not inserted:
        raise Exception("Username already exists")
        
    r.hset(key, mapping={
        "password_hash": password_hash,
        "avatar": avatar,
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "xp": 0
    })

def get_user(username: str) -> dict | None:
    key = f"user:{username.lower()}"
    data = r.hgetall(key)
    if not data:
        return None
    f = {k.decode(): v.decode() for k, v in data.items()}
    return {
        "id": 0,
        "username": f.get("username", ""),
        "password_hash": f.get("password_hash", ""),
        "avatar": f.get("avatar", ""),
        "xp": int(f.get("xp", "0")),
    }

def add_xp(username: str, amount: int) -> int:
    key = f"user:{username.lower()}"
    if not r.exists(key):
        return 0
    return int(r.hincrby(key, "xp", amount))

def get_user_xp(username: str) -> int:
    key = f"user:{username.lower()}"
    if not r.exists(key):
        return 0
    xp = r.hget(key, "xp")
    return int(xp) if xp else 0

def clear_room_history(room_id: str) -> None:
    ids = r.zrange(f"feed:room:{room_id}", 0, -1)
    if not ids:
        return
    pipe = r.pipeline()
    for mid in ids:
        pipe.delete(f"msg:{mid.decode()}")
        pipe.zrem("feed:all", mid)
    pipe.delete(f"feed:room:{room_id}")
    pipe.execute()
    print(f"[DB] Room '{room_id}' message history cleared.")

def clear_room_history_by_creator(room_id: str, username: str) -> bool:
    room = get_room(room_id)
    if not room or room["created_by"].lower() != username.lower():
        return False
    clear_room_history(room_id)
    print(f"[DB] Message history for room '{room_id}' cleared by creator '{username}'.")
    return True

def delete_room(room_id: str, username: str) -> bool:
    room = get_room(room_id)
    if not room or room["created_by"].lower() != username.lower():
        return False
    clear_room_history(room_id)
    r.delete(f"room:{room_id}")
    r.srem("rooms:public", room_id)
    print(f"[DB] Room '{room_id}' and all messages deleted by creator '{username}'.")
    return True

def clear_history() -> None:
    r.flushdb()
    print("[DB] All message history and user keys cleared.")
