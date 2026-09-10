"""
Database layer for the Secure Persistent Group Chat.

Uses redis-py for Valkey (Redis-compatible) with an active-active primary/replica
architecture:
  - _rw  →  VALKEY_PRIMARY_URL  (write client: all mutations go to the primary)
  - _ro  →  VALKEY_REPLICA_URL  (read client: reads served from the local replica)

When both env vars are the same (or only VALKEY_URL is set), both clients point to
the same node — single-node fallback requires zero further changes.

Handles:
  - Message persistence (encrypted, with HMAC for tamper detection)
  - User public key registry (for ECDSA signature verification)
  - Chat room management (rooms identified by unique 6-char codes)
  - Session token store (Valkey-backed, cross-server, TTL-guarded)
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

# Primary (write) URL — all mutations land here; replicates to replicas.
# Falls back to VALKEY_URL for single-node deployments.
_primary_url = os.environ.get(
    "VALKEY_PRIMARY_URL",
    os.environ.get("VALKEY_URL", "redis://localhost:6274/0"),
)

# Replica (read) URL — local Valkey replica on this system.
# Falls back to primary URL if not set (single-node mode).
_replica_url = os.environ.get("VALKEY_REPLICA_URL", _primary_url)

# Write client — connected to primary.
_rw = redis.from_url(_primary_url, decode_responses=False)

# Read client — connected to local replica.
_ro = redis.from_url(_replica_url, decode_responses=False)

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
    """Ping both Valkey nodes and ensure the loadtest room exists."""
    try:
        _rw.ping()
        print(f"[DB] Write client connected  → {_primary_url}")
    except redis.ConnectionError as e:
        print(f"[DB] ERROR: Cannot reach Valkey primary at {_primary_url}: {e}")

    try:
        _ro.ping()
        print(f"[DB] Read client connected   → {_replica_url}")
    except redis.ConnectionError as e:
        print(f"[DB] WARNING: Cannot reach Valkey replica at {_replica_url}: {e}")

    # Ensure the shared loadtest room exists
    if not _rw.exists("room:loadtest"):
        try:
            create_room(
                room_id="loadtest",
                name="Load Test",
                created_by="system",
                is_public=True,
                avatar="⚡",
            )
        except Exception:
            pass  # already exists (race on multi-server startup)

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


# ── Session token store (Valkey-backed, cross-server) ────────────────────────

_SESSION_KEY_PREFIX = "session:"

def r_session_set(token: str, data: dict, ttl: int = 90) -> None:
    """
    Store a one-time session token in Valkey with a TTL.
    Uses the write client so the token is visible to all servers
    (including those that only hold a read replica — they reach primary
    for session writes).
    """
    _rw.setex(f"{_SESSION_KEY_PREFIX}{token}", ttl, json.dumps(data))


def r_session_pop(token: str) -> dict | None:
    """
    Atomically retrieve and delete a session token.
    GETDEL is available in Valkey/Redis >= 6.2.
    Falls back to a GET + DEL pipeline for older versions.
    Returns the session dict or None if the token doesn't exist/expired.
    """
    key = f"{_SESSION_KEY_PREFIX}{token}"
    try:
        raw = _rw.getdel(key)
    except redis.ResponseError:
        # Older server that doesn't support GETDEL — use a pipeline
        pipe = _rw.pipeline(True)
        try:
            pipe.get(key)
            pipe.delete(key)
            results = pipe.execute()
            raw = results[0]
        except Exception:
            return None
    return json.loads(raw) if raw else None


# ── Room CRUD ─────────────────────────────────────────────────────────────────

def create_room(room_id: str, name: str, created_by: str, is_public: bool = True, avatar: str = "🏰") -> None:
    """
    Insert a new room into the rooms Hash.
    Uses write client — must land on primary.
    """
    key = f"room:{room_id}"
    inserted = _rw.hsetnx(key, "id", room_id)
    if not inserted:
        raise Exception("Room already exists")

    mapping = {
        "name": name,
        "created_by": created_by,
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "is_public": 1 if is_public else 0,
        "avatar": avatar,
    }
    _rw.hset(key, mapping=mapping)
    if is_public:
        _rw.sadd("rooms:public", room_id)

def get_room(room_id: str) -> dict | None:
    """Retrieve a room by its code. Uses read client (replica)."""
    key = f"room:{room_id}"
    data = _ro.hgetall(key)
    if not data:
        return None

    return {
        "id": data[b"id"].decode(),
        "name": data[b"name"].decode(),
        "created_by": data[b"created_by"].decode(),
        "created_at": data.get(b"created_at", b"").decode(),
        "is_public": bool(int(data.get(b"is_public", b"1").decode())),
        "avatar": data.get(b"avatar", b"\xf0\x9f\x8f\xb0").decode(),  # default 🏰
    }

def update_room_creator_if_system(room_id: str, username: str) -> None:
    """If a room's created_by is 'system' or empty, set it to username."""
    key = f"room:{room_id}"
    created_by = _rw.hget(key, "created_by")
    if created_by:
        created_by_str = created_by.decode()
        if created_by_str == "system" or created_by_str == "":
            _rw.hset(key, "created_by", username)

def list_rooms() -> list[dict]:
    """Return all public rooms (ordered newest first). Reads from replica."""
    room_ids = _ro.smembers("rooms:public")

    rooms = []
    for rid in room_ids:
        rid_str = rid.decode()
        data = get_room(rid_str)
        if data:
            owner_username = data["created_by"]
            owner_avatar = "wizard"
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
    """
    Persist a fully encrypted chat message.
    Dedup guard: HSETNX on msg:{id} → username is atomic; returns -1 on duplicate.
    All writes go to primary (_rw).
    """
    if not msg_id:
        msg_id = str(uuid.uuid4())
    key = f"msg:{msg_id}"
    ts = time.time()

    # Atomic dedup: only the first writer succeeds
    inserted = _rw.hsetnx(key, "username", username)
    if not inserted:
        return -1  # duplicate — silently ignored

    pipe = _rw.pipeline()
    pipe.hset(key, mapping={
        "room_id":      room_id,
        "avatar":       avatar,
        "ciphertext":   ciphertext,
        "iv":           iv,
        "signature":    signature,
        "public_key":   json.dumps(public_key),
        "timestamp":    timestamp,
        "sig_valid":    int(sig_valid),
        "reply_to":     reply_to or "",
        "target_user":  target_user or "",
        "is_deleted":   0,
        "is_edited":    0,
        "created_at_ts": ts,
        "attachment":   attachment or "",
        "hmac_digest":  _compute_hmac(ciphertext, iv),
    })
    pipe.zadd("feed:all", {msg_id: ts})
    pipe.zadd(f"feed:room:{room_id}", {msg_id: ts})
    pipe.execute()
    return 0

def get_history(room_id: str, limit: int | None = None, username: str | None = None) -> list[dict]:
    """Fetch message history for a room. Reads from the local replica."""
    if limit is not None and limit > 0:
        ids = _ro.zrange(f"feed:room:{room_id}", -limit, -1)
    else:
        ids = _ro.zrange(f"feed:room:{room_id}", 0, -1)

    pipe = _ro.pipeline()
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
        ciphertext  = f.get("ciphertext", "")
        iv          = f.get("iv", "")
        tampered    = not _verify_hmac(ciphertext, iv, hmac_digest)

        messages.append({
            "type":         "message",
            "msg_id":       mid.decode(),
            "username":     f.get("username", ""),
            "avatar":       f.get("avatar", ""),
            "ciphertext":   ciphertext,
            "iv":           iv,
            "signature":    f.get("signature", ""),
            "public_key":   json.loads(f.get("public_key", "{}")),
            "timestamp":    f.get("timestamp", ""),
            "sig_valid":    bool(int(f.get("sig_valid", "0"))),
            "tampered":     tampered,
            "reply_to":     f.get("reply_to", None) if f.get("reply_to") else None,
            "is_deleted":   bool(int(f.get("is_deleted", "0"))),
            "target_user":  f.get("target_user", None) if f.get("target_user") else None,
            "is_edited":    bool(int(f.get("is_edited", "0"))),
            "created_at_ts": float(f.get("created_at_ts", "0")),
            "attachment":   json.loads(f.get("attachment")) if f.get("attachment") else None,
        })
    return messages

def get_message_by_id(msg_id: str) -> dict | None:
    """Fetch a single message by ID. Reads from replica."""
    key = f"msg:{msg_id}"
    data = _ro.hgetall(key)
    if not data:
        return None
    f = {k.decode(): v.decode() for k, v in data.items()}
    return {
        "msg_id":      msg_id,
        "username":    f.get("username", ""),
        "avatar":      f.get("avatar", ""),
        "ciphertext":  f.get("ciphertext", ""),
        "iv":          f.get("iv", ""),
        "timestamp":   f.get("timestamp", ""),
        "is_deleted":  bool(int(f.get("is_deleted", "0"))),
        "target_user": f.get("target_user", None) if f.get("target_user") else None,
    }

def delete_message(msg_id: str, username: str) -> bool:
    """Soft-delete a message (owner only). Writes to primary."""
    key = f"msg:{msg_id}"
    data = _rw.hgetall(key)
    if not data:
        return False
    f = {k.decode(): v.decode() for k, v in data.items()}

    if bool(int(f.get("is_deleted", "0"))):
        return False
    if f.get("username", "").lower() != username.lower():
        return False

    _rw.hset(key, mapping={
        "is_deleted": 1,
        "ciphertext": "",
        "iv":         "",
        "signature":  "",
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
    """Edit a message within the 5-minute window. Writes to primary."""
    key = f"msg:{msg_id}"
    data = _rw.hgetall(key)
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
    _rw.hset(key, mapping={
        "ciphertext":  ciphertext,
        "iv":          iv,
        "signature":   signature,
        "hmac_digest": hmac_digest,
        "sig_valid":   1 if sig_valid else 0,
        "is_edited":   1,
    })
    print(f"[DB] Message '{msg_id}' edited by {username}")
    return True, "Message updated successfully."


# ── Simple load-test endpoints ────────────────────────────────────────────────

def save_message_simple(msg_id: str, room_id: str, username: str, text: str) -> bool:
    """
    Lightweight message store for the /message load-generator endpoint.
    Atomic dedup via HSETNX on the write client (primary).
    Returns True on success, False on duplicate.
    """
    key = f"msg:{msg_id}"
    inserted = _rw.hsetnx(key, "username", username)
    if not inserted:
        return False  # duplicate
    ts = time.time()
    pipe = _rw.pipeline()
    pipe.hset(key, mapping={
        "room_id":   room_id,
        "text":      text,
        "timestamp": ts,
        "simple":    1,
    })
    pipe.zadd("feed:all", {msg_id: ts})
    pipe.zadd(f"feed:room:{room_id}", {msg_id: ts})
    pipe.execute()
    return True

_FEED_LIMIT = 200  # return only the most recent N messages to keep /feed latency constant

def get_all_messages_simple(room_id: str = "loadtest") -> list[dict]:
    """
    Return the most recent FEED_LIMIT messages in a room for the /feed endpoint.
    Reads from the local replica — this is the hot read path that scales.
    Using -FEED_LIMIT:-1 ensures response size stays constant regardless of
    how many messages have accumulated (prevents latency growth over test time).
    """
    ids = _ro.zrange(f"feed:room:{room_id}", -_FEED_LIMIT, -1)
    pipe = _ro.pipeline()
    for mid in ids:
        pipe.hgetall(f"msg:{mid.decode()}")
    rows = pipe.execute()
    result = []
    for mid, fields in zip(ids, rows):
        if not fields:
            continue
        f = {k.decode(): v.decode() for k, v in fields.items()}
        result.append({
            "msg_id":      mid.decode(),
            "client-name": f.get("username", ""),
            "msg":         f.get("text", ""),
            "timestamp":   f.get("timestamp", ""),
        })
    return result

# ── User key registry ─────────────────────────────────────────────────────────

def register_user_key(username: str, public_key: dict) -> None:
    """Persist a user's ECDSA public key. Writes to primary."""
    _rw.set(f"userkey:{username.lower()}", json.dumps(public_key))

def get_user_key(username: str) -> dict | None:
    """Fetch a user's ECDSA public key. Reads from replica."""
    data = _ro.get(f"userkey:{username.lower()}")
    return json.loads(data) if data else None

# ── Persistent user accounts ──────────────────────────────────────────────────

def create_user(username: str, password_hash: str, avatar: str) -> None:
    """Create a new user account. Writes to primary."""
    key = f"user:{username.lower()}"
    inserted = _rw.hsetnx(key, "username", username)
    if not inserted:
        raise Exception("Username already exists")

    _rw.hset(key, mapping={
        "password_hash": password_hash,
        "avatar":        avatar,
        "created_at":    datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "xp":            0,
    })

def get_user(username: str) -> dict | None:
    """Fetch a user account. Reads from replica."""
    key = f"user:{username.lower()}"
    data = _ro.hgetall(key)
    if not data:
        return None
    f = {k.decode(): v.decode() for k, v in data.items()}
    return {
        "id":            0,
        "username":      f.get("username", ""),
        "password_hash": f.get("password_hash", ""),
        "avatar":        f.get("avatar", ""),
        "xp":            int(f.get("xp", "0")),
    }

def add_xp(username: str, amount: int) -> int:
    """Increment XP for a user atomically. Writes to primary."""
    key = f"user:{username.lower()}"
    if not _rw.exists(key):
        return 0
    return int(_rw.hincrby(key, "xp", amount))

def get_user_xp(username: str) -> int:
    """Read a user's current XP. Reads from replica."""
    key = f"user:{username.lower()}"
    if not _ro.exists(key):
        return 0
    xp = _ro.hget(key, "xp")
    return int(xp) if xp else 0

def clear_room_history(room_id: str) -> None:
    """Delete all messages in a room. Writes to primary."""
    ids = _rw.zrange(f"feed:room:{room_id}", 0, -1)
    if not ids:
        return
    pipe = _rw.pipeline()
    for mid in ids:
        pipe.delete(f"msg:{mid.decode()}")
        pipe.zrem("feed:all", mid)
    pipe.delete(f"feed:room:{room_id}")
    pipe.execute()
    print(f"[DB] Room '{room_id}' message history cleared.")

def clear_room_history_by_creator(room_id: str, username: str) -> bool:
    """Clear a room's history if the caller is the creator."""
    room = get_room(room_id)
    if not room or room["created_by"].lower() != username.lower():
        return False
    clear_room_history(room_id)
    print(f"[DB] Message history for room '{room_id}' cleared by creator '{username}'.")
    return True

def delete_room(room_id: str, username: str) -> bool:
    """Delete a room and all its messages if the caller is the creator."""
    room = get_room(room_id)
    if not room or room["created_by"].lower() != username.lower():
        return False
    clear_room_history(room_id)
    _rw.delete(f"room:{room_id}")
    _rw.srem("rooms:public", room_id)
    print(f"[DB] Room '{room_id}' and all messages deleted by creator '{username}'.")
    return True

def clear_history() -> None:
    """Flush the entire Valkey database. DANGEROUS — dev use only."""
    _rw.flushdb()
    print("[DB] All message history and user keys cleared.")
