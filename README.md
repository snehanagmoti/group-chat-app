# PixelChat — Secure Group Quest v2.0

> **🌐 Live Deployment:** [https://10.1.75.51:3269/](https://10.1.75.51:3269/)

A **real-time, secure, gamified group chat** built with **FastAPI WebSockets** (Python backend) and **Vanilla HTML/CSS/JS** (no frameworks), styled with a retro 8-bit pixel aesthetic. All messages are **end-to-end encrypted** using AES-GCM via the browser's Web Crypto API, **digitally signed** with ECDSA-P256, and **persisted encrypted** in an SQLite database with HMAC-SHA256 tamper detection.

---

## Table of Contents

1. [Features](#features)
2. [Tech Stack](#tech-stack)
3. [Architecture](#architecture)
4. [Security & Encryption](#security--encryption)
5. [Database Design](#database-design)
6. [Gamification System](#gamification-system)
7. [WebSocket Message Protocol](#websocket-message-protocol)
8. [REST API Reference](#rest-api-reference)
9. [File Attachment Support](#file-attachment-support)
10. [Message Receipt System](#message-receipt-system)
11. [Project Structure](#project-structure)
12. [Environment Configuration](#environment-configuration)
13. [Quick Start (Local)](#quick-start-local)
14. [Multi-Machine Deployment](#multi-machine-deployment)
15. [Lab 5 Load-Balanced Deployment](#lab-5-load-balanced-deployment)

---

## Features

### 🔐 Security & Encryption

- **AES-GCM 256-bit Encryption** — Every message (text, file, voice) is encrypted in the browser before being sent. The server never sees plaintext.
- **ECDSA-P256 Digital Signatures** — Each user generates a per-session key pair on login. Every outgoing message is signed with the private key. The server verifies the signature on every incoming message.
- **HMAC-SHA256 Database Tamper Detection** — Each stored record has an HMAC digest computed over `(ciphertext + iv)`. On history load, all records are re-verified and tampered rows are flagged `🚨 TAMPERED`.
- **Security Badge Per Message** — Each message bubble displays one of: `🔒✓ VERIFIED`, `⚠ SIG INVALID`, or `🚨 TAMPERED` based on server-side verification.
- **TLS/HTTPS + WSS** — Both frontend and backend run with self-signed SSL certificates (`cert.pem` / `key.pem`) so the Web Crypto API is available in all browsers (requires HTTPS context).
- **bcrypt Password Hashing** — User passwords are hashed with bcrypt (salted) before storage. The plaintext password is never stored.
- **Expiring Session Tokens** — Login and registration issue opaque, time-limited tokens. They remain valid across WebSocket reconnects and browser tabs until expiry or authenticated rotation.
- **Whisper (Private Message) Privacy** — Private messages sent via `/w @username` are stored in the DB but only returned to the sender and recipient in history queries.

### 💬 Messaging Features

- **Real-time WebSocket Broadcasting** — Messages are broadcast instantly to all connected users in a room.
- **Optimistic UI** — Sender's own message appears immediately without waiting for server echo.
- **Threaded Replies** — Reply to any specific message with a quoted preview bubble. Stored as `reply_to` (msg_id reference) in the database.
- **Whispers / Private Messages** — Type `/w @username <message>` to send an end-to-end encrypted private message only visible to the target user.
- **@Mentions** — Type `@username` in a message to highlight the mentioned user's bubble with a glow effect and play a mention sound effect.
- **Edit Messages** — Senders can edit their own messages within a 5-minute window. The edit is re-encrypted, re-signed, and broadcast to the room.
- **Delete / Unsend Messages** — Senders can permanently delete their own messages. A tombstone event is broadcast and the message is soft-deleted in the DB (ciphertext cleared).
- **Typing Indicator** — Shows "username is typing…" with an animated pixel-dot animation when another user is composing a message. Auto-clears after 2 seconds.
- **Emoji Picker** — A quick-react panel with 28 common emojis inserted at cursor position.
- **Voice Memos** — Hold-to-record in-app audio messages using the MediaRecorder API. Recordings are encrypted and sent as file attachments.

### 🏠 Room Management

- **Multi-Room Support** — Unlimited rooms, each identified by a unique 6-character alphanumeric code (e.g., `XKJ3P9`).
- **Create Public or Private Rooms** — Public rooms appear in the lobby browse list. Private rooms are accessible only via their code.
- **Room Avatar** — Each room has its own emoji avatar (Castle, Volcano, Arena, Arcade, Tavern, etc.).
- **Join by Code** — Users can join any room (including private ones) by entering the 6-character code directly.
- **Room Search** — Live search/filter the public room list in the lobby.
- **Creator Privileges** — The room creator (👑) can:
  - 🧹 **Clear History** — Delete all chat messages for the room from the database.
  - 🗑️ **Delete Room** — Permanently remove the room and all its messages.
- **Live Online Count** — Each room card shows the current number of online players.

### 👤 User Accounts & Authentication

- **Persistent User Accounts** — Users register with a username, password, and avatar. Accounts persist across sessions.
- **bcrypt Login / Register** — Passwords are hashed with bcrypt. Server validates against the stored hash.
- **Avatar Picker** — 12 pre-built pixel avatars: Wizard, Robot, Ninja, Astronaut, Dragon, Hero, Alien, Cyber, Fox, Owl, Bear, Lion.
- **Username Validation** — 1–20 characters, alphanumeric + underscore only. Case-insensitive uniqueness enforced.
- **Logout** — Cleanly returns user to login screen without losing the session state.
- **Authenticated Token Rotation** — Returning to the lobby rotates the current valid token without requiring the password again; a token cannot be refreshed for another user.

### 🎮 Gamification

- **XP System** — Earn XP for all in-app actions (see [Gamification System](#gamification-system)).
- **6 Rank Tiers** — Newbie → Squire → Knight → Champion → Warlord → Legend.
- **Message Streak Bonus** — Every 10th consecutive message earns +25 XP bonus.
- **Passive XP (Heartbeat)** — Earn +5 XP per minute spent in a room.
- **Level-Up Toast** — Animated retro "★ LEVEL UP! ★" toast popup on rank promotion.
- **XP Progress Bar** — Visual XP bar displayed in both the lobby header and the chat sidebar.
- **Floating XP Toasts** — Each XP gain triggers a floating "+10 XP" notification.
- **XP Persistence** — XP is stored in the database and persists across all sessions and rooms.

### 📎 Media & Files

- **File Attachments** — Attach images, videos, audio, PDFs, ZIP files, and any generic file.
- **Inline Rendering** — Images render as inline thumbnails; videos and audio play inline in the chat.
- **Voice Memos** — Record audio directly in-browser; encrypted and sent as `.webm` attachments.
- **Upload API** — Files are uploaded via `POST /upload` before the message is sent, and the resulting URL is embedded in the encrypted message payload.

### 🔄 Persistence & History

- **Unlimited Message History** — All messages are persisted in the SQLite database and delivered to new joiners on connect.
- **Encrypted at Rest** — Only the AES-GCM ciphertext is stored, never the plaintext.
- **HMAC Tamper Detection** — Every message has an HMAC digest that is re-verified when history is loaded.
- **Soft Delete** — Deleted messages keep their DB row but have ciphertext cleared and `is_deleted=1`.
- **Edit Tracking** — Edited messages store a new ciphertext and mark `is_edited=1`.
- **Whisper Filtering** — History queries filter whispers so each user only receives messages they are party to.

### 📡 Connection & UX

- **Connection Status Indicator** — Live `🟢 ONLINE` / `🔴 OFFLINE` / `🟡 RECONNECTING` badge in the header.
- **Auto-Reconnect with Exponential Backoff** — Disconnections are automatically retried with 1s → 10s delay.
- **Multi-Tab Support** — Multiple browser tabs with the same account are handled correctly (join/leave events deduplicated; user list shows one entry per unique username).
- **Delivery Receipts** — Each outgoing message gets a receipt emoji (😴 Sent / 😃 Partial / 😎 Delivered) from the server.
- **Security Certificate Onboarding Overlay** — If the browser blocks the backend's self-signed cert, a friendly overlay guides the user through accepting it without leaving the page.
- **Scanline + Pixel Background** — Retro CRT scanline overlay and animated pixel grid backgrounds.

---

## Tech Stack

| Layer        | Technology                                                                 |
|--------------|----------------------------------------------------------------------------|
| **Backend**  | Python 3.11+ · FastAPI · Uvicorn (ASGI) · SQLite3 · asyncio               |
| **Security** | `cryptography` (ECDSA-P256) · `bcrypt` · `hmac` · `secrets` · `hashlib`   |
| **Frontend** | HTML5 · Vanilla CSS · Vanilla JS (no frameworks, no build step)            |
| **Crypto**   | Web Crypto API (`SubtleCrypto`) — AES-GCM 256-bit + ECDSA-P256            |
| **Protocol** | WebSockets (RFC 6455) `wss://` · REST HTTP/HTTPS                           |
| **Database** | SQLite (via Python built-in `sqlite3`)                                     |
| **Fonts**    | Press Start 2P · VT323 (Google Fonts)                                      |
| **TLS**      | Self-signed RSA-2048 certificate via Python `cryptography` library         |

---

## Architecture

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                          Machine (Server Host)                               │
│                                                                              │
│  ┌───────────────────────────┐       ┌───────────────────────────────────┐  │
│  │   Frontend Server         │       │     Backend Server (FastAPI)      │  │
│  │   client/serve.py         │       │     server/server.py              │  │
│  │   https://0.0.0.0:3269    │       │     wss://0.0.0.0:PORT/ws         │  │
│  │                           │       │                                   │  │
│  │   Serves (HTTPS):         │       │   REST Endpoints:                 │  │
│  │   ├── index.html          │       │   ├── POST /register              │  │
│  │   ├── style.css           │       │   ├── POST /login                 │  │
│  │   ├── app.js              │       │   ├── POST /refresh-token         │  │
│  │   ├── config.js           │       │   ├── GET  /rooms                 │  │
│  │   └── sounds/             │       │   ├── POST /rooms                 │  │
│  │                           │       │   ├── GET  /rooms/{id}            │  │
│  └───────────────────────────┘       │   ├── DELETE /rooms/{id}          │  │
│                                      │   ├── DELETE /rooms/{id}/history  │  │
│                                      │   ├── POST /upload                │  │
│                                      │   ├── GET  /uploads/<file>        │  │
│                                      │   ├── GET  /group-key             │  │
│                                      │   ├── GET  /users/{name}/xp       │  │
│                                      │   └── GET  /health                │  │
│                                      │                                   │  │
│                                      │   WebSocket /ws:                  │  │
│                                      │   ├── join (token auth)           │  │
│                                      │   ├── message (encrypt+sign)      │  │
│                                      │   ├── typing                      │  │
│                                      │   ├── edit_message                │  │
│                                      │   ├── delete_message              │  │
│                                      │   ├── heartbeat (XP)              │  │
│                                      │   └── clear_room_history          │  │
│                                      │                                   │  │
│                                      │   ConnectionManager:              │  │
│                                      │   ├── Multi-room WS registry      │  │
│                                      │   ├── broadcast_to_room()         │  │
│                                      │   ├── send_to_user_in_room()      │  │
│                                      │   └── get_room_users()            │  │
│                                      │                                   │  │
│                                      │   Database (SQLite):              │  │
│                                      │   ├── messages (encrypted+HMAC)   │  │
│                                      │   ├── users (bcrypt hashes + XP)  │  │
│                                      │   ├── rooms                       │  │
│                                      │   └── user_keys (ECDSA JWKs)      │  │
│                                      └───────────────────────────────────┘  │
└──────────────────────────────┬───────────────────────────────────────────────┘
                               │ HTTPS / WSS (TLS)
              ┌────────────────┼───────────────────┐
              │                │                   │
       ┌──────┴──────┐  ┌──────┴──────┐   ┌───────┴──────┐
       │  Browser 1  │  │  Browser 2  │   │  Browser N   │
       │  (Player)   │  │  (Player)   │   │  (Player)    │
       └─────────────┘  └─────────────┘   └──────────────┘
```

### Component Responsibilities

| Component | File | Role |
|---|---|---|
| **Backend Server** | `server/server.py` | FastAPI app: WebSocket hub, auth, room management, XP, ECDSA verification |
| **Database Layer** | `server/db.py` | SQLite CRUD, HMAC computation/verification, schema migrations |
| **Frontend Client** | `client/app.js` | WebSocket client, SubtleCrypto encryption, gamification, UI rendering |
| **UI** | `client/index.html` + `client/style.css` | Three-screen SPA (Login → Lobby → Chat), retro pixel theme |
| **Frontend Server** | `client/serve.py` | HTTPS static file server (FastAPI + uvicorn) |
| **Certificate Generator** | `generate_certs.py` | Generates RSA-2048 self-signed TLS cert + key |

---

## Security & Encryption

### End-to-End Message Encryption (AES-GCM)

```
Client (Browser)                          Server
────────────────                          ──────
1. Fetch 256-bit key from GET /group-key
2. Import as CryptoKey (AES-GCM)
   (key is non-extractable in JS memory)

3. On send:
   plaintext = user's message text
   iv = crypto.getRandomValues(12 bytes)   ← fresh random IV per message
   ciphertext = AES-GCM.encrypt(key, iv, plaintext)
   → base64url encode both

4. Sign:
   material = ciphertext_b64 + iv_b64  (as UTF-8 bytes)
   signature = ECDSA-P256.sign(privateKey, material)
   → base64url encode (IEEE P1363 format, 64 bytes)

5. WS send → { ciphertext, iv, signature, public_key }
                                          ↓
                                6. Verify ECDSA signature (P1363→DER)
                                7. Save encrypted blob to SQLite
                                   (AES ciphertext stored, NOT plaintext)
                                8. Compute HMAC-SHA256(ciphertext+iv)
                                   store as hmac_digest
                                9. Broadcast to room

Client (Browser — all recipients)
──────────────────────────────────
10. Receive { ciphertext, iv, signature, public_key }
11. Verify ECDSA signature client-side (SubtleCrypto)
12. Decrypt AES-GCM ciphertext → plaintext
13. Render message bubble with security badge
```

### ECDSA Digital Signatures

- Each user generates a **new ECDSA-P256 key pair** on every login/session start using `SubtleCrypto.generateKey`.
- The **public key is exported as JWK** and sent to the server on WebSocket join and with every message.
- The server stores the latest JWK in the `user_keys` table and **verifies every incoming message's signature** before persisting or broadcasting.
- A signature covers the concatenation of `ciphertext_b64 + iv_b64` (as UTF-8 bytes), ensuring integrity of the encrypted blob.
- The Python `cryptography` library converts the IEEE P1363 format (r‖s, 64 bytes) to DER for server-side verification.

### HMAC-SHA256 Tamper Detection

- On every `save_message()` call, the DB layer computes `HMAC-SHA256(ciphertext + iv)` using `HMAC_SECRET` from `.env`.
- On history load (`get_history()`), every row re-computes the HMAC and compares with the stored `hmac_digest` using `hmac.compare_digest()` (constant-time comparison).
- Any mismatch marks the message as `tampered: True` and triggers a `🚨 TAMPERED` badge in the UI and a `[SECURITY ALERT]` server log.

### TLS / HTTPS

- Both frontend (`serve.py`) and backend (`server.py`) use `uvicorn` with `ssl_keyfile` and `ssl_certfile` pointing to `key.pem` / `cert.pem`.
- Self-signed RSA-2048 certificates are generated via `generate_certs.py` (Python `cryptography` library).
- HTTPS is **mandatory** — the Web Crypto API (`SubtleCrypto`) is only available in secure contexts.

### Authentication Flow

```
Register:
  POST /register { username, password, avatar }
  → bcrypt.hashpw(password, bcrypt.gensalt()) stored in DB
  ← { token, username, avatar, xp }
    token = secrets.token_hex(32)  ← stored in active_sessions dict

Login:
  POST /login { username, password }
  → bcrypt.checkpw(password, stored_hash)
  ← { token, username, avatar, xp }

WebSocket Join:
  WS send: { type: "join", token, public_key (JWK), room_id }
  Server: validates the token, owner, and expiry (the token remains reusable for reconnect)
  → validate room, register ECDSA key, send history + welcome
```

---

## Database Design

**File:** `server/chat.db` (SQLite, auto-created on first run)

### Tables

#### `messages`

| Column | Type | Description |
|---|---|---|
| `id` | INTEGER PK | Auto-increment row ID |
| `room_id` | TEXT | 6-char room code |
| `msg_id` | TEXT | Client-generated UUID (stable reference for edits/deletes/replies) |
| `username` | TEXT | Sender's username |
| `avatar` | TEXT | Sender's avatar ID |
| `ciphertext` | TEXT | Base64url AES-GCM ciphertext (**never plaintext**) |
| `iv` | TEXT | Base64url 12-byte GCM IV |
| `signature` | TEXT | Base64url ECDSA-P256 signature |
| `public_key` | TEXT | JSON JWK of sender's ECDSA public key |
| `timestamp` | TEXT | HH:MM:SS formatted time |
| `hmac_digest` | TEXT | HMAC-SHA256 hex digest for tamper detection |
| `sig_valid` | INTEGER | 1=valid, 0=invalid (recorded at receive time) |
| `reply_to` | TEXT | `msg_id` of parent message (threaded reply) |
| `is_deleted` | INTEGER | 1=soft-deleted (ciphertext cleared) |
| `target_user` | TEXT | Non-null = whisper to this username |
| `is_edited` | INTEGER | 1=message has been edited |
| `created_at_ts` | REAL | Unix epoch for 5-minute edit window validation |
| `attachment` | TEXT | JSON attachment metadata (url, fileName, fileType, fileSize) |

#### `users`

| Column | Type | Description |
|---|---|---|
| `id` | INTEGER PK | Auto-increment |
| `username` | TEXT UNIQUE | Case-insensitive unique username |
| `password_hash` | TEXT | bcrypt hash |
| `avatar` | TEXT | Avatar ID |
| `created_at` | TEXT | Registration timestamp |
| `xp` | INTEGER | Total accumulated XP |

#### `rooms`

| Column | Type | Description |
|---|---|---|
| `id` | TEXT PK | 6-char alphanumeric room code |
| `name` | TEXT | Room display name (max 40 chars) |
| `created_by` | TEXT | Username of creator |
| `created_at` | TEXT | Creation timestamp |
| `is_public` | INTEGER | 1=public (browsable), 0=private (code-only) |
| `avatar` | TEXT | Room emoji avatar |

#### `user_keys`

| Column | Type | Description |
|---|---|---|
| `username` | TEXT PK | Username |
| `public_key` | TEXT | Latest ECDSA-P256 JWK (JSON) |

### Key Database Functions (db.py)

| Function | Description |
|---|---|
| `init_db()` | Creates tables + runs migrations on startup |
| `save_message(...)` | Persists encrypted message with HMAC |
| `get_history(room_id, limit, username)` | Returns history with HMAC re-verification and whisper filtering |
| `delete_message(msg_id, username)` | Soft-delete — sender only |
| `edit_message(msg_id, username, ...)` | Re-encrypt + re-sign within 5-minute window |
| `create_user / get_user` | User CRUD |
| `add_xp / get_user_xp` | Atomic XP increment + read |
| `create_room / get_room / list_rooms / delete_room` | Room CRUD |
| `clear_room_history_by_creator` | Bulk-delete messages — creator only |
| `register_user_key / get_user_key` | ECDSA public key registry |

---

## Gamification System

| Action | XP Awarded |
|---|---|
| ✉️ Send a message | **+10 XP** |
| 📥 Receive a message | **+2 XP** |
| 🔥 Every 10th message sent (streak bonus) | **+25 XP** |
| 🏰 Create a room | **+20 XP** |
| 🚪 Someone joins your room | **+3 XP** |
| ⏱️ Per minute spent in a room (heartbeat) | **+5 XP** |

### Rank Progression

| Emoji | Rank | XP Required |
|---|---|---|
| 🌱 | NEWBIE | 0 |
| 🗡️ | SQUIRE | 200 |
| 🛡️ | KNIGHT | 600 |
| 🏆 | CHAMPION | 1,500 |
| 👑 | WARLORD | 4,000 |
| ⭐ | LEGEND | 10,000 |

- XP is **persistent** — stored in the `users` table, survives logout, room changes, and server restarts.
- Rank promotions trigger an **animated level-up toast** ("★ LEVEL UP! ★") and an 8-bit ascending chime.
- Both the **lobby header** and **chat sidebar** display the XP bar and current rank in real time.

---

## WebSocket Message Protocol

All messages are JSON with a `type` field. Transport is `wss://` (encrypted WebSocket over TLS).

### Client → Server

| Type | Key Fields | Description |
|---|---|---|
| `join` | `token`, `public_key` (JWK), `room_id` | Authenticate and join a room |
| `message` | `ciphertext`, `iv`, `signature`, `public_key`, `client_msg_id`, `attachment?`, `reply_to?`, `target_user?` | Send encrypted message (or whisper) |
| `edit_message` | `msg_id`, `ciphertext`, `iv`, `signature`, `public_key` | Edit own message (within 5 minutes) |
| `delete_message` | `msg_id` | Soft-delete own message |
| `typing` | — | Signal composing state to room |
| `heartbeat` | — | Sent every 60s for passive XP |
| `clear_room_history` | — | Creator clears all room messages |
| `delete_room` | — | Creator deletes the room |

### Server → Client

| Type | Key Fields | Description |
|---|---|---|
| `system` | `message`, `timestamp`, `room` | Welcome message on join |
| `join` | `username`, `avatar`, `message`, `timestamp` | User joined notification |
| `leave` | `username`, `message`, `timestamp` | User left notification |
| `message` | `msg_id`, `username`, `avatar`, `ciphertext`, `iv`, `signature`, `public_key`, `sig_valid`, `attachment`, `reply_to`, `target_user`, `timestamp` | Broadcast encrypted message |
| `message_deleted` | `msg_id`, `username` | Tombstone: message was deleted |
| `message_edited` | `msg_id`, `username`, `ciphertext`, `iv`, `signature`, `public_key`, `sig_valid`, `is_edited` | Edited message payload |
| `receipt` | `msg_id`, `status` | Delivery receipt (`sent` / `partial` / `delivered_all`) |
| `userList` | `users` (list of `{username, avatar}`) | Current online players (deduplicated by username) |
| `history` | `messages` | Full encrypted chat history for new joiner |
| `room_history_cleared` | `room_id`, `username` | History was cleared by creator |
| `room_deleted` | `room_id`, `username` | Room was deleted by creator |
| `xp_update` | `xp`, `gained`, `reason` | Real-time XP notification |
| `typing` | `username` | Another user is typing |
| `error` | `message` | Error notification |

---

## REST API Reference

### Authentication

| Method | Endpoint | Request Body | Response |
|---|---|---|---|
| `POST` | `/register` | `{username, password, avatar}` | `{token, username, avatar, xp}` |
| `POST` | `/login` | `{username, password}` | `{token, username, avatar, xp}` |
| `POST` | `/refresh-token` | `{username}` | `{token, username, avatar, xp}` |

### Room Management

| Method | Endpoint | Body / Params | Response |
|---|---|---|---|
| `GET` | `/rooms` | — | `{rooms: [...]}` with live `online` counts |
| `POST` | `/rooms` | `{name, is_public, avatar, created_by}` | `{room_id, name, xp_awarded, ...}` |
| `GET` | `/rooms/{room_id}` | — | Room metadata or 404 |
| `DELETE` | `/rooms/{room_id}` | `{username}` | `{ok: true}` (creator only) |
| `DELETE` | `/rooms/{room_id}/history` | `{username}` | `{ok: true}` (creator only) |

### Files & Utilities

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/upload` | Upload a file; returns `{url, fileName, fileType, fileSize}` |
| `GET` | `/uploads/<filename>` | Serve uploaded file (static) |
| `GET` | `/group-key` | Return AES-256 group key (hex) from `.env` |
| `GET` | `/users/{username}/xp` | Return user's current XP total |
| `GET` | `/config.js` | Serve `window.BACKEND_PORT = <load_balancer_port>;` for dynamic client config |
| `GET` | `/health` | Health check — `{"status": "ok"}` |

---

## File Attachment Support

Files are uploaded via `POST /upload` before the message is sent. The returned URL is included in the encrypted message payload as the `attachment` field.

| File Type | Rendering in Chat |
|---|---|
| `image/*` | Inline thumbnail; click to open full-size |
| `video/*` | Inline `<video>` player |
| `audio/*` | Inline `<audio>` player |
| Voice memo (`.webm`) | Inline `<audio>` player |
| PDF / Word / ZIP / other | Download card with file name, type icon, and file size |

---

## Message Receipt System

Each sent message gets an emoji receipt that updates when the server's `receipt` event arrives:

| Emoji | Status | Meaning |
|---|---|---|
| 😴 | `sent` | Reached server; no other players currently online |
| 😃 | `partial` | Delivered to some players, but not all |
| 😎 | `delivered_all` | All players in the room received it |

---

## Project Structure

```
group-chat-app/
├── .env                        # Runtime config (gitignored)
├── .env.example                # Config template — copy to .env
├── .gitignore
├── README.md
├── generate_certs.py           # RSA-2048 self-signed TLS cert generator
├── go.mod
├── cmd/                        # Go CLI entry points
│   ├── load-balancer/
│   └── load-generator/
├── internal/                   # Tested load-balancer and generator packages
│   ├── loadbalancer/
│   └── loadgenerator/
├── run_experiments.sh          # One-backend vs three-backend experiment
├── deploy_and_run.py           # Sys1-Sys4 deployment automation
├── cert.pem                    # TLS certificate (generated, gitignored)
├── key.pem                     # TLS private key (generated, gitignored)
├── architecture_diagram.jpg    # System architecture reference image
├── Lab 4.pdf                   # Assignment specification
│
├── server/
│   ├── server.py               # FastAPI WebSocket + REST API server
│   │                           #   ├── ConnectionManager (multi-room WS hub)
│   │                           #   ├── Auth endpoints (/register, /login, /refresh-token)
│   │                           #   ├── Room endpoints (/rooms CRUD)
│   │                           #   ├── WebSocket handler (/ws) — full message lifecycle
│   │                           #   ├── ECDSA-P256 signature verification
│   │                           #   ├── XP award logic (send/receive/join/heartbeat/streak)
│   │                           #   └── File upload handler (/upload)
│   ├── db.py                   # SQLite database layer
│   │                           #   ├── Schema definition + migration helpers
│   │                           #   ├── HMAC-SHA256 tamper detection
│   │                           #   └── All CRUD functions (messages, users, rooms, keys)
│   ├── requirements.txt        # Python dependencies
│   ├── chat.db                 # SQLite database file (auto-created)
│   └── uploads/                # Uploaded files served at /uploads/<uuid>.<ext>
│
└── client/
    ├── index.html              # Three-screen SPA (Login → Lobby → Chat)
    ├── style.css               # Retro 8-bit pixel dark theme + all component styles
    ├── app.js                  # All client-side logic:
    │                           #   ├── SubtleCrypto: AES-GCM encrypt/decrypt
    │                           #   ├── SubtleCrypto: ECDSA-P256 sign/verify
    │                           #   ├── WebSocket connect + all message type handlers
    │                           #   ├── Auth flows (register/login/logout/token refresh)
    │                           #   ├── Lobby (room creation, search, join by code)
    │                           #   ├── Chat (send, receive, edit, delete, reply, whisper, @mention)
    │                           #   ├── Gamification (XP tracking, rank calc, level-up toast)
    │                           #   ├── Media (voice memo recording, file upload preview)
    │                           #   ├── Typing indicator + emoji picker
    │                           #   └── Auto-reconnect with exponential backoff
    ├── serve.py                # HTTPS static file server (FastAPI + uvicorn)
    └── sounds/                 # 8-bit retro sound effects
        ├── coin.mp3            # New message sound
        ├── pipe.mp3            # User leave sound
        ├── mushroom.mp3        # User join sound
        └── mario_start.mp3    # App startup / level-up sound
```

---

## Environment Configuration

Copy `.env.example` to `.env` and fill in secrets:

```env
# Backend process port on Sys2, Sys3, and Sys4
PORT=5000

# Public load-balancer port advertised to the browser (Sys1 SSH 2237)
BACKEND_PORT=4237

# Load-balancer port inside the Sys1 container
LB_PORT=4000
PUBLIC_LB_PORT=4237

# Frontend (static file server) port
FRONTEND_PORT=3000

# Keep enabled outside local smoke tests
FRONTEND_TLS=1

# Authenticated session lifetime (seconds)
SESSION_TTL_SECONDS=43200

# Room cleanup timeout in seconds (default 300)
CLEANUP_TIMEOUT=300

# 256-bit AES-GCM group key — generate with:
# python3 -c "import secrets; print(secrets.token_hex(32))"
AES_GROUP_KEY=<64-hex-chars>

# HMAC-SHA256 secret for database tamper detection — generate with:
# python3 -c "import secrets; print(secrets.token_hex(32))"
HMAC_SECRET=<64-hex-chars>
```

> **Security Note:** Never commit `.env` to version control. It is already listed in `.gitignore`. Both `AES_GROUP_KEY` and `HMAC_SECRET` must be exactly 64 hex characters (32 bytes each).

---

## Quick Start (Local)

### 1. Install Dependencies

```bash
cd server
pip install -r requirements.txt
```

### 2. Set Up Environment

```bash
# From the project root
cp .env.example .env
# Edit .env — generate AES_GROUP_KEY and HMAC_SECRET as shown above
```

### 3. Generate TLS Certificates

```bash
# From the project root
python generate_certs.py
# Outputs: cert.pem and key.pem
```

> Required because `SubtleCrypto` only works in HTTPS contexts (secure origins).

### 4. Start the Backend (Terminal 1)

```bash
cd server
python3 server.py
```

Server starts on configured `PORT`:
- **WebSocket**: `wss://0.0.0.0:<PORT>/ws`
- **API**: `https://0.0.0.0:<PORT>/rooms`, `/login`, `/register`, etc.
- **Uploads**: `https://0.0.0.0:<PORT>/uploads/<file>`

### 5. Start the Frontend (Terminal 2)

```bash
cd client
python3 serve.py
```

Serves the client at `https://0.0.0.0:<FRONTEND_PORT>`.

### 6. Open in Browser

```
https://localhost:<FRONTEND_PORT>
```

> On first load, accept the self-signed certificate warning for **both** the frontend and backend ports. The app shows a guided overlay if the backend cert hasn't been accepted yet.

---

## Multi-Machine Deployment

> **🌐 Deployed at:** [https://10.1.75.51:3269/](https://10.1.75.51:3269/)

1. **Server Machine** — Run both `server/server.py` and `client/serve.py`. Note the machine's LAN IP (`ip addr` on Linux).
2. **All Client Machines** — Open `https://<SERVER_IP>:<FRONTEND_PORT>/` in any browser.
3. The frontend fetches `/config.js` which injects `window.BACKEND_PORT`. The client derives the WebSocket URL as `wss://<same-hostname>:<BACKEND_PORT>/ws`. **No client-side configuration needed.**

> New clients may need to accept the self-signed TLS cert for both ports on first visit. The app's certificate overlay guides users through this step automatically.

---

## Lab 5 Load-Balanced Deployment

The Lab 5 topology runs the Go reverse proxy on Sys1, identical FastAPI backends on Sys2-Sys4, and the static frontend on Sys1. Browser-affinity cookies keep a user's REST and WebSocket traffic on one backend while new sessions are distributed round-robin. Failed health checks remove a backend from rotation and successful checks restore it.

Build and test locally:

```bash
go test ./...
go vet ./...
go build ./...
go run ./cmd/load-balancer -backends https://sys2:5000,https://sys3:5000,https://sys4:5000 -port 4000 -backend-insecure-skip-verify
```

The lab maps Sys1 container port `3000` to public port `3237`, and container
port `4000` to public port `4237`. The frontend therefore advertises `4237`
to browsers while the load balancer listens on `4000`. Sys1 reaches the three
backends directly over the Docker bridge on their internal port `5000`; it
does not hairpin through public ports `5238`, `5239`, and `5240`.

Run the controlled comparison from the local workstation through the mapped
load-balancer port after configuring SSH credentials:

```bash
python tools/run_mapped_experiments.py
```

The older Sys1-local reproduction script remains available when required:

```bash
./run_experiments.sh
```

The script uses the same request count, concurrency, endpoint, and timeout for the one-backend and three-backend runs, waits for health readiness, and writes JSON/CSV results. `deploy_and_run.py` uploads the source, starts all three backends, builds the Go programs on Sys1, starts the load balancer, and serves the frontend through the load-balancer port.

---

## Python Dependencies

```
fastapi
uvicorn[standard]
python-dotenv
python-multipart
cryptography
bcrypt
```

---

*PixelChat — Group Quest v2.0 | CSD Lab 4*
