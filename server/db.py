"""
Database layer for the Secure Persistent Group Chat.

Uses Python's built-in sqlite3 module.
Handles:
  - Message persistence (encrypted, with HMAC for tamper detection)
  - User public key registry (for ECDSA signature verification)
  - Chat room management (rooms identified by unique 6-char codes)
"""

import sqlite3
import hmac
import hashlib
import os
import json
from pathlib import Path
from datetime import datetime

# ── Config ────────────────────────────────────────────────────────────────────

DB_PATH = Path(__file__).resolve().parent / "chat.db"

# HMAC_SECRET loaded from environment (set in .env, never hardcoded)
def _get_hmac_secret() -> bytes:
    secret = os.environ.get("HMAC_SECRET", "")
    if not secret:
        raise RuntimeError(
            "HMAC_SECRET is not set in .env — cannot start server safely."
        )
    return bytes.fromhex(secret)


# ── Schema ────────────────────────────────────────────────────────────────────

_SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    room_id     TEXT    NOT NULL DEFAULT 'default',
    msg_id      TEXT    NOT NULL DEFAULT '',       -- client-generated UUID for stable references
    username    TEXT    NOT NULL,
    avatar      TEXT    NOT NULL DEFAULT 'wizard',
    ciphertext  TEXT    NOT NULL,   -- base64 AES-GCM ciphertext
    iv          TEXT    NOT NULL,   -- base64 12-byte IV
    signature   TEXT    NOT NULL,   -- base64 ECDSA-P256 signature
    public_key  TEXT    NOT NULL,   -- JSON JWK of sender's ECDSA public key
    timestamp   TEXT    NOT NULL,
    hmac_digest TEXT    NOT NULL,   -- HMAC-SHA256(ciphertext || iv) for tamper detection
    sig_valid   INTEGER NOT NULL DEFAULT 1,  -- 1=valid, 0=invalid (recorded at receive time)
    reply_to      TEXT    DEFAULT NULL,          -- msg_id of parent message (threaded replies)
    is_deleted    INTEGER NOT NULL DEFAULT 0,   -- 1=tombstone (unsent/deleted)
    target_user   TEXT    DEFAULT NULL,          -- non-null = whisper to this username
    is_edited     INTEGER NOT NULL DEFAULT 0,   -- 1=edited message
    created_at_ts REAL    DEFAULT NULL,          -- unix epoch timestamp for edit window validation
    attachment    TEXT    DEFAULT NULL           -- JSON string of attachment data
);

CREATE TABLE IF NOT EXISTS user_keys (
    username    TEXT PRIMARY KEY,
    public_key  TEXT NOT NULL        -- JSON JWK
);

CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT    NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT    NOT NULL,   -- bcrypt hash
    avatar        TEXT    NOT NULL DEFAULT 'wizard',
    created_at    TEXT    NOT NULL,
    xp            INTEGER NOT NULL DEFAULT 0   -- persistent XP points
);

CREATE TABLE IF NOT EXISTS rooms (
    id          TEXT    PRIMARY KEY,              -- 6-char alphanumeric code e.g. "XKJ3P9"
    name        TEXT    NOT NULL,
    created_by  TEXT    NOT NULL,
    created_at  TEXT    NOT NULL,
    is_public   INTEGER NOT NULL DEFAULT 1,       -- 1=public (browsable), 0=private (code-only)
    avatar      TEXT    NOT NULL DEFAULT '🏰'
);
"""

# ── Migration helpers ─────────────────────────────────────────────────────────

def _apply_migrations(conn: sqlite3.Connection) -> None:
    """Add columns that may not exist in older DB versions."""
    cursor = conn.execute("PRAGMA table_info(messages)")
    columns = {row[1] for row in cursor.fetchall()}
    if "room_id" not in columns:
        conn.execute("ALTER TABLE messages ADD COLUMN room_id TEXT NOT NULL DEFAULT 'default'")
        print("[DB] Migration: added room_id column to messages")
    if "msg_id" not in columns:
        conn.execute("ALTER TABLE messages ADD COLUMN msg_id TEXT NOT NULL DEFAULT ''")
        print("[DB] Migration: added msg_id column to messages")
    if "reply_to" not in columns:
        conn.execute("ALTER TABLE messages ADD COLUMN reply_to TEXT DEFAULT NULL")
        print("[DB] Migration: added reply_to column to messages")
    if "is_deleted" not in columns:
        conn.execute("ALTER TABLE messages ADD COLUMN is_deleted INTEGER NOT NULL DEFAULT 0")
        print("[DB] Migration: added is_deleted column to messages")
    if "target_user" not in columns:
        conn.execute("ALTER TABLE messages ADD COLUMN target_user TEXT DEFAULT NULL")
        print("[DB] Migration: added target_user column to messages")
    if "is_edited" not in columns:
        conn.execute("ALTER TABLE messages ADD COLUMN is_edited INTEGER NOT NULL DEFAULT 0")
        print("[DB] Migration: added is_edited column to messages")
    if "created_at_ts" not in columns:
        conn.execute("ALTER TABLE messages ADD COLUMN created_at_ts REAL DEFAULT NULL")
        print("[DB] Migration: added created_at_ts column to messages")
    if "attachment" not in columns:
        conn.execute("ALTER TABLE messages ADD COLUMN attachment TEXT DEFAULT NULL")
        print("[DB] Migration: added attachment column to messages")

    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_msg_id ON messages(msg_id) WHERE msg_id != ''")
    print("[DB] Migration: ensured unique index on msg_id")

    cursor = conn.execute("PRAGMA table_info(users)")
    columns = {row[1] for row in cursor.fetchall()}
    if "xp" not in columns:
        conn.execute("ALTER TABLE users ADD COLUMN xp INTEGER NOT NULL DEFAULT 0")
        print("[DB] Migration: added xp column to users")

    cursor = conn.execute("PRAGMA table_info(rooms)")
    columns = {row[1] for row in cursor.fetchall()}
    if "avatar" not in columns:
        conn.execute("ALTER TABLE rooms ADD COLUMN avatar TEXT NOT NULL DEFAULT '🏰'")
        print("[DB] Migration: added avatar column to rooms")


# ── Persistent connection pool ────────────────────────────────────────────────

import threading

_pool_lock = threading.Lock()
_pool: list[sqlite3.Connection] = []
_POOL_SIZE = 8


def _configure_conn(conn: sqlite3.Connection) -> sqlite3.Connection:
    """Apply performance pragmas to a connection."""
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA cache_size=-64000")   # 64 MB
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA temp_store=MEMORY")
    return conn


def _get_conn() -> sqlite3.Connection:
    """Get a connection from the pool, or create a new one."""
    with _pool_lock:
        if _pool:
            return _pool.pop()
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    return _configure_conn(conn)


def _put_conn(conn: sqlite3.Connection) -> None:
    """Return a connection to the pool."""
    with _pool_lock:
        if len(_pool) < _POOL_SIZE:
            _pool.append(conn)
        else:
            conn.close()


# ── Initialisation ────────────────────────────────────────────────────────────

def init_db() -> None:
    """Create tables if they don't exist. Call once at server startup."""
    with _connect() as conn:
        conn.executescript(_SCHEMA)
        _apply_migrations(conn)
        # Enable WAL mode at init
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA cache_size=-64000")
        conn.execute("PRAGMA busy_timeout=5000")
    # Pre-fill the pool
    for _ in range(_POOL_SIZE):
        c = sqlite3.connect(str(DB_PATH), check_same_thread=False)
        _configure_conn(c)
        _pool.append(c)
    print(f"[DB] Initialised (WAL mode, pool={_POOL_SIZE}) — {DB_PATH}")


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


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
    Insert a new room into the rooms table.
    Raises sqlite3.IntegrityError if the room_id already exists.
    """
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO rooms (id, name, created_by, created_at, is_public, avatar)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (room_id, name, created_by, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), 1 if is_public else 0, avatar),
        )


def get_room(room_id: str) -> dict | None:
    """Retrieve a room by its code. Returns dict or None."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT id, name, created_by, created_at, is_public, avatar FROM rooms WHERE id = ?",
            (room_id,),
        ).fetchone()
    if row is None:
        return None
    return {
        "id":         row["id"],
        "name":       row["name"],
        "created_by": row["created_by"],
        "created_at": row["created_at"],
        "is_public":  bool(row["is_public"]),
        "avatar":     row["avatar"],
    }


def update_room_creator_if_system(room_id: str, username: str) -> None:
    """If a room's created_by is 'system' or empty, set it to username."""
    with _connect() as conn:
        conn.execute(
            "UPDATE rooms SET created_by = ? WHERE id = ? AND (created_by = 'system' OR created_by = '' OR created_by IS NULL)",
            (username, room_id),
        )



def list_rooms() -> list[dict]:
    """Return all public rooms (ordered newest first)."""
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT r.id, r.name, r.created_by, r.created_at, r.is_public, r.avatar,
                   COALESCE(u.avatar, 'wizard') as owner_avatar
            FROM rooms r
            LEFT JOIN users u ON u.username = r.created_by COLLATE NOCASE
            WHERE r.is_public = 1
            ORDER BY r.created_at DESC
            """,
        ).fetchall()
    return [
        {
            "id":           row["id"],
            "name":         row["name"],
            "created_by":   row["created_by"],
            "owner_avatar": row["owner_avatar"],
            "created_at":   row["created_at"],
            "is_public":    bool(row["is_public"]),
            "avatar":       row["avatar"],
        }
        for row in rows
    ]



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
    Persist an encrypted, signed message to the DB.
    Computes and stores HMAC for future tamper detection.
    Returns the new row id, or 0 if a duplicate msg_id was ignored.
    """
    hmac_digest = _compute_hmac(ciphertext, iv)
    pub_key_json = json.dumps(public_key)
    import time
    created_at_ts = time.time()

    with _connect() as conn:
        try:
            cur = conn.execute(
                """
                INSERT INTO messages
                    (room_id, msg_id, username, avatar, ciphertext, iv, signature, public_key,
                     timestamp, hmac_digest, sig_valid, reply_to, target_user, is_edited, created_at_ts, attachment)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
                """,
                (
                    room_id,
                    msg_id,
                    username,
                    avatar,
                    ciphertext,
                    iv,
                    signature,
                    pub_key_json,
                    timestamp,
                    hmac_digest,
                    1 if sig_valid else 0,
                    reply_to,
                    target_user,
                    created_at_ts,
                    attachment,
                ),
            )
            return cur.lastrowid
        except sqlite3.IntegrityError:
            print(f"[DB] Ignored duplicate message insertion for msg_id: {msg_id}")
            return 0


def get_history(room_id: str, limit: int | None = None, username: str | None = None) -> list[dict]:
    """
    Return history messages for a given room (unlimited if limit is None).
    Whispers (target_user IS NOT NULL) are only returned if the requesting
    user is either the sender or the target.
    Each row is enriched with a `tampered` flag (True if HMAC mismatch).
    """
    sql = """
        SELECT msg_id, username, avatar, ciphertext, iv, signature, public_key,
               timestamp, hmac_digest, sig_valid, reply_to, is_deleted, target_user,
               is_edited, created_at_ts, attachment
        FROM messages
        WHERE room_id = ?
        ORDER BY id DESC
    """
    params = [room_id]
    if limit is not None and limit > 0:
        sql += " LIMIT ?"
        params.append(limit)

    with _connect() as conn:
        rows = conn.execute(sql, params).fetchall()

    messages = []
    for row in reversed(rows):  # chronological order
        # Filter whispers: only include if user is sender or target
        tgt = row["target_user"]
        if tgt and username:
            if row["username"].lower() != username.lower() and tgt.lower() != username.lower():
                continue  # skip — this whisper isn't for us

        tampered = not _verify_hmac(row["ciphertext"], row["iv"], row["hmac_digest"])
        messages.append(
            {
                "type": "message",
                "msg_id": row["msg_id"],
                "username": row["username"],
                "avatar": row["avatar"],
                "ciphertext": row["ciphertext"],
                "iv": row["iv"],
                "signature": row["signature"],
                "public_key": json.loads(row["public_key"]),
                "timestamp": row["timestamp"],
                "sig_valid": bool(row["sig_valid"]),
                "tampered": tampered,
                "reply_to": row["reply_to"],
                "is_deleted": bool(row["is_deleted"]),
                "target_user": row["target_user"],
                "is_edited": bool(row["is_edited"]),
                "created_at_ts": row["created_at_ts"],
                "attachment": json.loads(row["attachment"]) if row["attachment"] else None,
            }
        )
    return messages


def get_history_fast(room_id: str) -> list[dict]:
    """
    Lightweight history retrieval for load-gen /feed endpoint.
    Skips HMAC verification and whisper filtering for maximum throughput.
    """
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT msg_id, username, ciphertext, timestamp FROM messages WHERE room_id = ? ORDER BY id DESC",
            (room_id,),
        ).fetchall()
    finally:
        _put_conn(conn)

    return [
        {
            "msg_id": row["msg_id"],
            "username": row["username"],
            "ciphertext": row["ciphertext"],
            "timestamp": row["timestamp"],
        }
        for row in rows
    ]


def save_message_fast(room_id: str, msg_id: str, username: str, msg: str, timestamp: str) -> int:
    """
    Lightweight message save for load-gen /message endpoint.
    Skips HMAC computation, signature storage, and attachment handling.
    Returns row id or 0 if duplicate.
    """
    conn = _get_conn()
    try:
        cur = conn.execute(
            "INSERT INTO messages (room_id, msg_id, username, avatar, ciphertext, iv, signature, public_key, timestamp, hmac_digest, sig_valid) VALUES (?, ?, ?, 'wizard', ?, 'x', 'x', '{}', ?, 'x', 1)",
            (room_id, msg_id, username, msg, timestamp),
        )
        conn.commit()
        return cur.lastrowid
    except sqlite3.IntegrityError:
        return 0
    finally:
        _put_conn(conn)


def get_message_by_id(msg_id: str) -> dict | None:
    """Retrieve a single message by its client-generated msg_id."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT msg_id, username, avatar, ciphertext, iv, timestamp, is_deleted, target_user FROM messages WHERE msg_id = ?",
            (msg_id,),
        ).fetchone()
    if row is None:
        return None
    return {
        "msg_id":      row["msg_id"],
        "username":    row["username"],
        "avatar":      row["avatar"],
        "ciphertext":  row["ciphertext"],
        "iv":          row["iv"],
        "timestamp":   row["timestamp"],
        "is_deleted":  bool(row["is_deleted"]),
        "target_user": row["target_user"],
    }


def delete_message(msg_id: str, username: str) -> bool:
    """
    Soft-delete a message by setting is_deleted = 1 and clearing ciphertext.
    Only succeeds if the requesting user is the original sender.
    Returns True if a row was updated, False otherwise.
    """
    with _connect() as conn:
        cur = conn.execute(
            """
            UPDATE messages
            SET is_deleted = 1, ciphertext = '', iv = '', signature = ''
            WHERE msg_id = ? AND username = ? COLLATE NOCASE AND is_deleted = 0
            """,
            (msg_id, username),
        )
        updated = cur.rowcount > 0
    if updated:
        print(f"[DB] Message '{msg_id}' deleted by {username}")
    return updated


def edit_message(
    msg_id: str,
    username: str,
    ciphertext: str,
    iv: str,
    signature: str,
    sig_valid: bool,
) -> tuple[bool, str]:
    """
    Edit an existing message's ciphertext, iv, and signature.
    Validates that:
      1. Message exists and is not deleted.
      2. Requesting user matches the original sender.
      3. Less than 5 minutes (300 seconds) have elapsed since creation.
    """
    import time
    with _connect() as conn:
        row = conn.execute(
            "SELECT username, created_at_ts, is_deleted FROM messages WHERE msg_id = ?",
            (msg_id,),
        ).fetchone()

        if not row:
            return False, "Message not found."
        if bool(row["is_deleted"]):
            return False, "Cannot edit a deleted message."
        if row["username"].lower() != username.lower():
            return False, "You can only edit your own messages."

        created_ts = row["created_at_ts"]
        if created_ts and (time.time() - created_ts > 300):
            return False, "Message edit window (5 minutes) has expired."

        hmac_digest = _compute_hmac(ciphertext, iv)
        conn.execute(
            """
            UPDATE messages
            SET ciphertext = ?, iv = ?, signature = ?, hmac_digest = ?, sig_valid = ?, is_edited = 1
            WHERE msg_id = ?
            """,
            (ciphertext, iv, signature, hmac_digest, 1 if sig_valid else 0, msg_id),
        )
    print(f"[DB] Message '{msg_id}' edited by {username}")
    return True, "Message updated successfully."


# ── User key registry ─────────────────────────────────────────────────────────

def register_user_key(username: str, public_key: dict) -> None:
    """Store or update a user's ECDSA public key (JWK dict)."""
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO user_keys (username, public_key)
            VALUES (?, ?)
            ON CONFLICT(username) DO UPDATE SET public_key = excluded.public_key
            """,
            (username, json.dumps(public_key)),
        )


def get_user_key(username: str) -> dict | None:
    """Retrieve a user's registered ECDSA public key, or None if not found."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT public_key FROM user_keys WHERE username = ?", (username,)
        ).fetchone()
    return json.loads(row["public_key"]) if row else None


# ── Persistent user accounts ───────────────────────────────────────────────────

def create_user(username: str, password_hash: str, avatar: str) -> None:
    """
    Insert a new user into the users table.
    Raises sqlite3.IntegrityError if the username is already taken (UNIQUE constraint).
    """
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO users (username, password_hash, avatar, created_at, xp)
            VALUES (?, ?, ?, ?, 0)
            """,
            (username, password_hash, avatar, datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
        )


def get_user(username: str) -> dict | None:
    """
    Retrieve a user row by username (case-insensitive).
    Returns { id, username, password_hash, avatar, xp } or None if not found.
    """
    with _connect() as conn:
        row = conn.execute(
            "SELECT id, username, password_hash, avatar, xp FROM users WHERE username = ? COLLATE NOCASE",
            (username,),
        ).fetchone()
    if row is None:
        return None
    return {
        "id":            row["id"],
        "username":      row["username"],
        "password_hash": row["password_hash"],
        "avatar":        row["avatar"],
        "xp":            row["xp"] or 0,
    }


def add_xp(username: str, amount: int) -> int:
    """
    Atomically add `amount` XP to a user's total.
    Returns the new XP total.
    """
    with _connect() as conn:
        conn.execute(
            "UPDATE users SET xp = xp + ? WHERE username = ? COLLATE NOCASE",
            (amount, username),
        )
        row = conn.execute(
            "SELECT xp FROM users WHERE username = ? COLLATE NOCASE",
            (username,),
        ).fetchone()
    return row["xp"] if row else 0


def get_user_xp(username: str) -> int:
    """Return the current XP total for a user."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT xp FROM users WHERE username = ? COLLATE NOCASE",
            (username,),
        ).fetchone()
    return row["xp"] if row else 0


def clear_room_history(room_id: str) -> None:
    """Delete all messages for a specific room. Called when the room has been empty for a while."""
    with _connect() as conn:
        conn.execute("DELETE FROM messages WHERE room_id = ?", (room_id,))
    print(f"[DB] Room '{room_id}' message history cleared.")


def clear_room_history_by_creator(room_id: str, username: str) -> bool:
    """
    Clear all stored messages for a specific room.
    Succeeds ONLY if the requesting user is the creator of the room.
    """
    with _connect() as conn:
        room = conn.execute(
            "SELECT created_by FROM rooms WHERE id = ?", (room_id,)
        ).fetchone()
        if not room or room["created_by"].lower() != username.lower():
            return False
        conn.execute("DELETE FROM messages WHERE room_id = ?", (room_id,))
    print(f"[DB] Message history for room '{room_id}' cleared by creator '{username}'.")
    return True


def delete_room(room_id: str, username: str) -> bool:
    """
    Permanently delete a chat room and all its messages.
    Succeeds ONLY if the requesting user is the creator of the room.
    """
    with _connect() as conn:
        room = conn.execute(
            "SELECT created_by FROM rooms WHERE id = ?", (room_id,)
        ).fetchone()
        if not room or room["created_by"].lower() != username.lower():
            return False
        conn.execute("DELETE FROM messages WHERE room_id = ?", (room_id,))
        conn.execute("DELETE FROM rooms WHERE id = ? AND created_by = ? COLLATE NOCASE", (room_id, username))
    print(f"[DB] Room '{room_id}' and all messages deleted by creator '{username}'.")
    return True


def clear_history() -> None:
    """Delete ALL messages and user keys. Legacy fallback."""
    with _connect() as conn:
        conn.execute("DELETE FROM messages")
        conn.execute("DELETE FROM user_keys")
    print("[DB] All message history and user keys cleared.")

