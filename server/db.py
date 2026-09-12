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

import asyncio
import hmac
import hashlib
import os
import json
import uuid
import time
import redis
import redis.asyncio as aioredis
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
# socket_timeout / socket_connect_timeout: without these, redis-py blocks
# INDEFINITELY on a stalled connection (dropped packet, momentarily
# unreachable replica, etc). Since db calls run inside asyncio.to_thread(),
# an indefinite block eats a threadpool slot permanently — enough of those
# under sustained load exhausts the pool and the whole process stops
# answering requests, including plain health checks, and never recovers
# even after traffic stops. A bounded timeout turns that into a fast,
# recoverable error instead.
_rw = redis.from_url(
    _primary_url,
    decode_responses=False,
    socket_timeout=10,         # large enough for LRANGE of 5000+ entries under load
    socket_connect_timeout=2,
    health_check_interval=30,
    retry_on_timeout=True,
)

# Read client — connected to local replica.
_ro = redis.from_url(
    _replica_url,
    decode_responses=False,
    socket_timeout=10,         # large enough for LRANGE of 5000+ entries under load
    socket_connect_timeout=2,
    health_check_interval=30,
    retry_on_timeout=True,
)

# ── Async Redis clients (used ONLY by /message and /feed hot paths) ───────────
# redis.asyncio uses the event loop for I/O instead of OS threads:
#   - sync client: 1 blocked OS thread per in-flight Redis call
#   - async client: 0 OS threads — I/O is multiplexed on the event loop
# At 500 concurrent users, the sync approach needed 167 thread slots per
# backend; async needs 0, so the thread-pool ceiling is no longer a factor.
_rw_async = aioredis.from_url(
    _primary_url,
    decode_responses=False,
    socket_timeout=10,
    socket_connect_timeout=2,
    health_check_interval=30,
    retry_on_timeout=True,
    max_connections=64,        # generous pool; async connections are cheap
)

# Async read client — local replica (kept for future /feed replica reads).
_ro_async = aioredis.from_url(
    _replica_url,
    decode_responses=False,
    socket_timeout=10,
    socket_connect_timeout=2,
    health_check_interval=30,
    retry_on_timeout=True,
    max_connections=64,
)

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

_feed_cache: dict[str, tuple[float, str, int]] = {}
_CACHE_TTL_SEC = 0.25  # 250ms micro-cache coalesces high-concurrency read spikes
# Per-room Lock used to single-flight cache misses in get_feed_json_async.
# When the cache expires under high concurrency, ONE coroutine acquires the
# lock and does the LRANGE+merge; all other waiters check the double-check
# inside the lock and return the freshly populated entry immediately.
#
# asyncio.Task (singleflight) was tried but has two failure modes in this
# workload:
#   1. CancelledError (BaseException) is not caught by `except Exception`,
#      so Gunicorn worker rotation causes a cascade of CancelledError to
#      every coroutine awaiting the shared Task simultaneously.
#   2. create_task() queues the Task at the BACK of the event-loop ready
#      queue; under 1000 concurrent coroutines the Task's scheduling delay
#      can exceed the LB's 2500ms timeout, failing all waiters at once.
# The Lock's FIFO convoy is the lesser cost.
_feed_lock: dict[str, asyncio.Lock] = {}

def _append_items(prev_json: str, new_items: list) -> str:
    """
    Append newly fetched JSON byte blobs or strings to an existing JSON array string.
    Does not parse or re-serialize existing JSON, turning O(total) work into O(delta).
    """
    if not new_items:
        return prev_json if prev_json else "[]"
    parts = []
    for item in new_items:
        if isinstance(item, (bytes, bytearray)):
            parts.append(item.decode("utf-8"))
        else:
            parts.append(str(item))
    new_str = ",".join(parts)
    if not prev_json or prev_json.strip() == "[]":
        return "[" + new_str + "]"
    prev_json = prev_json.strip()
    if prev_json.endswith("]"):
        return prev_json[:-1] + "," + new_str + "]"
    return "[" + new_str + "]"

def save_message_simple(msg_id: str, room_id: str, username: str, text: str) -> bool:
    """
    Lightweight message store for the /message load-generator endpoint.
    Atomic dedup via HSETNX on the write client (primary).
    Stores pre-serialized JSON in Redis list for single-roundtrip /feed reads.
    """
    key = f"msg:{msg_id}"
    inserted = _rw.hsetnx(key, "username", username)
    if not inserted:
        return False  # duplicate
    ts = time.time()
    msg_dict = {
        "msg_id":      msg_id,
        "client-name": username,
        "msg":         text,
        "timestamp":   str(ts),
    }
    msg_json = json.dumps(msg_dict)

    pipe = _rw.pipeline()
    pipe.hset(key, mapping={
        "room_id":   room_id,
        "text":      text,
        "timestamp": ts,
        "simple":    1,
        "json":      msg_json,
    })
    pipe.zadd("feed:all", {msg_id: ts})
    pipe.zadd(f"feed:room:{room_id}", {msg_id: ts})
    pipe.rpush(f"feed:list:{room_id}", msg_json)
    results = pipe.execute(raise_on_error=True)  # surface OOM/errors instead of silently losing data
    _ = results  # noqa: F841 (suppress unused-variable warning)
    return True

def get_feed_json(room_id: str = "loadtest") -> str:
    """
    Return the complete /feed JSON string.
    1. Micro-cache (250ms) absorbs concurrent reader bursts.
    2. Fast path: 1 single Redis command (LRANGE) reading pre-serialized JSON.
    3. Auto-backfills feed:list if unpopulated.

    NOTE: reads from _rw (primary), not _ro (replica). This deployment runs
    the LB round-robining across 3 backend containers (1 fronting the primary,
    2 fronting replicas). A write on one container's primary connection can
    lag behind on another container's replica connection, so a POST /message
    immediately followed by a GET /feed against a different backend could miss
    the just-written message. /message and /feed are the exact load-tested
    endpoints, so they read-your-writes off primary; everything else in this
    file (room lookups, chat history, etc.) still reads from the replica.
    """
    now = time.time()
    cached = _feed_cache.get(room_id)
    if cached and (now - cached[0] < _CACHE_TTL_SEC):
        return cached[1]

    # Fast path: 1-command retrieval from Redis list
    raw_list = _rw.lrange(f"feed:list:{room_id}", 0, -1)
    if raw_list:
        body = b"[" + b",".join(raw_list) + b"]"
        json_str = body.decode("utf-8")
        _feed_cache[room_id] = (now, json_str, len(raw_list))
        return json_str

    # Fallback / Backfill path (for pre-existing unmigrated messages in Valkey)
    ids = _rw.zrange(f"feed:room:{room_id}", 0, -1)
    if not ids:
        empty_json = "[]"
        _feed_cache[room_id] = (now, empty_json, 0)
        return empty_json

    pipe = _rw.pipeline()
    for mid in ids:
        pipe.hgetall(f"msg:{mid.decode()}")
    rows = pipe.execute()

    items = []
    pipe_backfill = _rw.pipeline()
    for mid, fields in zip(ids, rows):
        if not fields:
            continue
        f = {k.decode(): v.decode() for k, v in fields.items()}
        if "json" in f:
            jstr = f["json"]
        else:
            jstr = json.dumps({
                "msg_id":      mid.decode(),
                "client-name": f.get("username", ""),
                "msg":         f.get("text", ""),
                "timestamp":   f.get("timestamp", ""),
            })
        items.append(jstr.encode("utf-8") if isinstance(jstr, str) else jstr)
        pipe_backfill.rpush(f"feed:list:{room_id}", jstr)

    try:
        pipe_backfill.execute()
    except Exception:
        pass

    body = b"[" + b",".join(items) + b"]"
    json_str = body.decode("utf-8")
    _feed_cache[room_id] = (now, json_str, len(items))
    return json_str

def get_all_messages_simple(room_id: str = "loadtest") -> list[dict]:
    """Compatibility helper returning parsed Python dicts."""
    return json.loads(get_feed_json(room_id))


# ── Async hot-path functions (event-loop native, 0 threads) ───────────────────

async def save_message_simple_async(
    msg_id: str, room_id: str, username: str, text: str
) -> bool:
    """
    Async version of save_message_simple for the /message load-test endpoint.

    Uses _rw_async (redis.asyncio) so every Redis call is a coroutine that
    suspends with `await` instead of blocking an OS thread. Under 500
    concurrent requests this means 500 coroutines interleaved on the event
    loop — not 500 threads stacked in the kernel.

    Logic is identical to the sync version:
      1. HSETNX for atomic dedup (returns False immediately if duplicate).
      2. 4-command pipeline: HSET + ZADD×2 + RPUSH — all sent in one
         round-trip, minimising latency per request.
    """
    key = f"msg:{msg_id}"
    inserted = await _rw_async.hsetnx(key, "username", username)
    if not inserted:
        return False  # duplicate — already stored

    ts = time.time()
    msg_dict = {
        "msg_id":      msg_id,
        "client-name": username,
        "msg":         text,
        "timestamp":   str(ts),
    }
    msg_json = json.dumps(msg_dict)

    # In redis.asyncio, pipeline commands are queued synchronously;
    # only execute() is awaited (sends the batch and reads all replies).
    pipe = _rw_async.pipeline(transaction=False)
    pipe.hset(key, mapping={
        "room_id":   room_id,
        "text":      text,
        "timestamp": ts,
        "simple":    1,
        "json":      msg_json,
    })
    pipe.zadd("feed:all", {msg_id: ts})
    pipe.zadd(f"feed:room:{room_id}", {msg_id: ts})
    pipe.rpush(f"feed:list:{room_id}", msg_json)
    await pipe.execute(raise_on_error=True)  # surface OOM errors, don't swallow them
    return True


async def get_feed_json_async(room_id: str = "loadtest") -> str:
    """
    Async version of get_feed_json for the /feed load-test endpoint.

    Uses incremental delta caching + per-room asyncio.Lock:
      - Warm cache (< 250ms): returns immediately with zero Redis I/O.
      - Cache miss: acquires per-room lock so only ONE coroutine queries Redis.
      - All concurrent waiters re-check inside the lock and return the freshly
        populated entry — no redundant LRANGE calls.
      - The lock winner fetches ONLY the delta since the last cached length,
        turning per-request cost from O(total) into O(new messages).
    """
    now = time.time()
    cached = _feed_cache.get(room_id)
    if cached and (now - cached[0] < _CACHE_TTL_SEC):
        return cached[1]  # fast path: warm cache, no I/O

    lock = _feed_lock.get(room_id)
    if lock is None:
        lock = asyncio.Lock()
        _feed_lock[room_id] = lock

    async with lock:
        # Double-check: another coroutine may have refreshed the cache
        # while we were waiting for the lock.
        cached = _feed_cache.get(room_id)
        if cached and (time.time() - cached[0] < _CACHE_TTL_SEC):
            return cached[1]

        prev_ts, prev_json, prev_len = cached if (cached and len(cached) >= 3) else (0.0, "[]", 0)

        try:
            # Incremental fetch: only new items since the last cached length.
            new_items = await _rw_async.lrange(f"feed:list:{room_id}", prev_len, -1)

            # Detect external flush (DEL between test runs) — reset and reload.
            if not new_items and prev_len > 0:
                current_len = await _rw_async.llen(f"feed:list:{room_id}")
                if current_len < prev_len:
                    prev_len = 0
                    prev_json = "[]"
                    new_items = await _rw_async.lrange(f"feed:list:{room_id}", 0, -1)

            if new_items or prev_len > 0:
                merged_json = _append_items(prev_json, new_items)
                new_len = prev_len + len(new_items)
                _feed_cache[room_id] = (time.time(), merged_json, new_len)
                return merged_json

        except Exception as exc:
            # Redis error: serve stale cache so the request still succeeds.
            print(f"[feed] get_feed_json_async error for room {room_id!r}: {exc}")
            if cached:
                return cached[1]
            return "[]"

        # Fallback / backfill: handles messages written by the sync version or
        # pre-existing data that lacks feed:list entries.
        try:
            ids = await _rw_async.zrange(f"feed:room:{room_id}", 0, -1)
            if not ids:
                empty = "[]"
                _feed_cache[room_id] = (time.time(), empty, 0)
                return empty

            pipe = _rw_async.pipeline(transaction=False)
            for mid in ids:
                pipe.hgetall(f"msg:{mid.decode()}")
            rows = await pipe.execute()

            items = []
            for mid, fields in zip(ids, rows):
                if not fields:
                    continue
                f = {k.decode(): v.decode() for k, v in fields.items()}
                jstr = f.get("json") or json.dumps({
                    "msg_id":      mid.decode(),
                    "client-name": f.get("username", ""),
                    "msg":         f.get("text", ""),
                    "timestamp":   f.get("timestamp", ""),
                })
                items.append(jstr.encode("utf-8") if isinstance(jstr, str) else jstr)

            if items:
                try:
                    pipe_backfill = _rw_async.pipeline(transaction=False)
                    for item in items:
                        pipe_backfill.rpush(f"feed:list:{room_id}", item)
                    await pipe_backfill.execute()
                except Exception:
                    pass  # backfill is best-effort

            body = b"[" + b",".join(items) + b"]"
            json_str = body.decode("utf-8")
            _feed_cache[room_id] = (time.time(), json_str, len(items))
            return json_str

        except Exception as exc:
            print(f"[feed] backfill error for room {room_id!r}: {exc}")
            if cached:
                return cached[1]
            return "[]"


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