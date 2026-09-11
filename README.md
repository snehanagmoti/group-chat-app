# PixelChat — Secure Group Quest v2.0

> **🌐 Load Balancer (primary entry point):** `http://10.1.75.51:4273`
> **📁 Frontend UI:** [https://10.1.75.51:3269/](https://10.1.75.51:3269/)

A **real-time, secure, gamified group chat** built with **FastAPI** (Python backend) and **Vanilla HTML/CSS/JS** (no frameworks), styled with a retro 8-bit pixel aesthetic. All messages are **end-to-end encrypted** using AES-GCM via the browser's Web Crypto API, **digitally signed** with ECDSA-P256, and **persisted encrypted** in a Valkey (Redis-compatible) database with HMAC-SHA256 tamper detection.

The backend is deployed across **three systems** behind a custom **Go load balancer** using EWMA-based dynamic routing — automatically redistributing traffic when any backend becomes slow or unhealthy.

---

## Table of Contents

1. [Features](#features)
2. [Tech Stack](#tech-stack)
3. [Deployment Architecture](#deployment-architecture)
4. [Load Balancer (Go)](#load-balancer-go)
5. [Load Generator](#load-generator)
6. [Security & Encryption](#security--encryption)
7. [Database Design](#database-design)
8. [Gamification System](#gamification-system)
9. [WebSocket Message Protocol](#websocket-message-protocol)
10. [REST API Reference](#rest-api-reference)
11. [File Attachment Support](#file-attachment-support)
12. [Message Receipt System](#message-receipt-system)
13. [Project Structure](#project-structure)
14. [Environment Configuration](#environment-configuration)
15. [Quick Start (Local)](#quick-start-local)
16. [Lab Deployment (Multi-Machine)](#lab-deployment-multi-machine)

---

## Features

### 🔐 Security & Encryption

- **AES-GCM 256-bit Encryption** — Every message (text, file, voice) is encrypted in the browser before being sent. The server never sees plaintext.
- **ECDSA-P256 Digital Signatures** — Each user generates a per-session key pair on login. Every outgoing message is signed with the private key. The server verifies the signature on every incoming message.
- **HMAC-SHA256 Database Tamper Detection** — Each stored record has an HMAC digest computed over `(ciphertext + iv)`. On history load, all records are re-verified and tampered rows are flagged `🚨 TAMPERED`.
- **Security Badge Per Message** — Each message bubble displays one of: `🔒✓ VERIFIED`, `⚠ SIG INVALID`, or `🚨 TAMPERED` based on server-side verification.
- **TLS/HTTPS + WSS** — Both frontend and backend run with self-signed SSL certificates (`cert.pem` / `key.pem`) so the Web Crypto API is available in all browsers (requires HTTPS context).
- **bcrypt Password Hashing** — User passwords are hashed with bcrypt (salted) before storage. The plaintext password is never stored.
- **One-Time Session Tokens** — After login or register, the server issues a single-use opaque token (`secrets.token_hex(32)`). The token is consumed when the WebSocket connection is established, preventing replay attacks.
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
- **Token Refresh** — Returning to lobby (after leaving a room) issues a new one-time token without requiring re-login.

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

| Layer              | Technology                                                                      |
|--------------------|---------------------------------------------------------------------------------|
| **Backend**        | Python 3.11+ · FastAPI · Gunicorn (4 workers) · Uvicorn workers · asyncio      |
| **Load Balancer**  | Go 1.21+ · `net/http` · `net/http/httputil` · EWMA dynamic routing             |
| **Shared Storage** | Valkey (Redis-compatible) · Primary on Sys2 · Replicas on Sys3, Sys4           |
| **Security**       | `cryptography` (ECDSA-P256) · `bcrypt` · `hmac` · `secrets` · `hashlib`        |
| **Frontend**       | HTML5 · Vanilla CSS · Vanilla JS (no frameworks, no build step)                 |
| **Crypto**         | Web Crypto API (`SubtleCrypto`) — AES-GCM 256-bit + ECDSA-P256                 |
| **Protocol**       | WebSockets (RFC 6455) `wss://` · REST HTTP/HTTPS                                |
| **Fonts**          | Press Start 2P · VT323 (Google Fonts)                                           |
| **TLS**            | Self-signed RSA-2048 certificate via Python `cryptography` library              |

---

## Deployment Architecture

### Lab System Layout

```text
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                                LAB NETWORK (10.1.75.51)                                │
│                                                                                        │
│  ┌──────────────────────────────────────────────────────────────────────────────────┐  │
│  │ SYS1 (SSH :2273) — Ingress & Static Hosting                                      │  │
│  │                                                                                  │  │
│  │  ┌───────────────────────────────────────────────────┐                           │  │
│  │  │ Load Balancer (Go)                   :4273 (HTTP) │                           │  │
│  │  │ · EWMA Dynamic Routing & Active Health Probes     │                           │  │
│  │  │ · Connection Pool (64 idle) & Live /lb/status     │                           │  │
│  │  └─────────────────────────┬─────────────────────────┘                           │  │
│  └────────────────────────────┼─────────────────────────────────────────────────────┘  │
│                               │                                                        │
│               ┌───────────────┼───────────────┐                                        │
│               │ HTTPS         │ HTTPS         │ HTTPS                                  │
│               ▼               ▼               ▼                                        │
│        ┌───────────────┐┌───────────────┐┌───────────────┐                             │
│        │ SYS2 (:2274)  ││ SYS3 (:2275)  ││ SYS4 (:2276)  │                             │
│        │ Backend 1     ││ Backend 2     ││ Backend 3     │                             │
│        │ :5274 (HTTPS) ││ :5275 (HTTPS) ││ :5276 (HTTPS) │                             │
│        │ Gunicorn (4w) ││ Gunicorn (4w) ││ Gunicorn (4w) │                             │
│        │               ││               ││               │                             │
│        │ Valkey        ││ Valkey        ││ Valkey        │                             │
│        │ PRIMARY (rw)  ││ REPLICA (ro)  ││ REPLICA (ro)  │                             │
│        │ :6274         ││ :6000         ││ :6000         │                             │
│        └───────┬───────┘└───────▲───────┘└───────▲───────┘                             │
│                │                │                │                                     │
│                └─ Replication ──┴────────────────┘                                     │
│                   (Async Snapshot & Stream Sync)                                       │
└───────────────────────┼────────────────────────────────────────────────────────────────┘
                        ▲                                               
               REST/WS  │ (HTTP :4273)                                  
                        │                                               
        ┌───────────────┴──────────────────────────────────────────────────────────────┐
        │                                Load Generator                                │
        └──────────────────────────────────────────────────────────────────────────────┘
```

### Port Mapping

Ports are derived from SSH port using: `App_N_Port = SSH_Port + (N × 1000)`

| System | SSH Port | Role | External Port | Internal Port |
|--------|----------|------|---------------|---------------|
| Sys1   | 2273     | Load Balancer | 4273 | 4000 |
| Sys2   | 2274     | Backend 1 / Valkey Primary | 5274 / 6274 | 5000 / 6000 |
| Sys3   | 2275     | Backend 2 / Valkey Replica | 5275 | 5000 |
| Sys4   | 2276     | Backend 3 / Valkey Replica | 5276 | 5000 |

### Component Responsibilities

| Component | File | Role |
|---|---|---|
| **Load Balancer** | `load_balancer/main.go` | EWMA routing, health probes, `/lb/status`, `/lb/metrics` |
| **Backend Server** | `server/server.py` | FastAPI app: REST API, WebSocket hub, auth, XP, ECDSA verification |
| **Database Layer** | `server/db.py` | Valkey CRUD, HMAC tamper detection, 200-message feed cap |
| **Frontend Client** | `client/app.js` | WebSocket client, SubtleCrypto encryption, gamification, UI |
| **UI** | `client/index.html` + `client/style.css` | Three-screen SPA (Login → Lobby → Chat), retro pixel theme |
| **Frontend Server** | `client/serve.py` | HTTPS static file server (FastAPI + uvicorn) |
| **Load Generator** | `load_generator/load_gen` | Go-based concurrent load tester with EWMA-aware metrics |
| **Monitor** | `monitor/monitor.py` | Per-system CPU/memory/network collector (psutil) |
| **Certificate Generator** | `generate_certs.py` | Generates RSA-2048 self-signed TLS cert + key |

---

## Load Balancer (Go)

The load balancer (`load_balancer/main.go`) is a Layer 7 HTTP reverse proxy written from scratch in Go using only the standard library.

### EWMA-Based Dynamic Routing

Traffic is distributed using an **Exponentially Weighted Moving Average** of per-backend response time:

```
score(b) = (InFlight(b) + 1) × EWMA(b)
```

The routing algorithm uses two passes:

1. **Pass 1 — Best non-overloaded backend:** Selects the alive, non-overloaded backend with the lowest score (lowest queue depth × latency product).
2. **Pass 2 — Fallback:** If all backends are overloaded, routes to the least busy alive backend to ensure progress.

A backend is marked **overloaded** when its EWMA exceeds `--threshold-ms` (default 300 ms). The EWMA updates after every request:

```
EWMA_t = α × latency_t + (1 − α) × EWMA_{t−1}    (α = 0.3)
```

### Features

- **Active health probes** — `GET /health` on each backend every 1 second; failed backends are automatically excluded
- **Connection pooling** — shared `http.Transport` with 64 idle connections per backend host
- **TLS passthrough** — `InsecureSkipVerify` for self-signed backend certificates
- **Per-request timeout** — 800 ms hard deadline via `context.WithTimeout`
- **Observability** — `/lb/health`, `/lb/status`, `/lb/metrics` endpoints

### Startup Command

```bash
cd load_balancer
./lb \
  -addr :4000 \
  -backends "https://10.1.75.51:5274,https://10.1.75.51:5275,https://10.1.75.51:5276" \
  -threshold-ms 300 \
  -ewma-alpha 0.3 \
  -health-interval 1s \
  -backend-timeout 800ms
```

### Build

```bash
cd load_balancer
go build -o lb .
```

---

## Load Generator

A custom concurrent load generator (`load_generator/`) was built in Go to stress-test the system:

```bash
cd load_generator
./load_gen \
  -url http://10.1.75.51:4273 \
  -users 50 -duration 120s \
  -min-interval 100ms -max-interval 500ms \
  -read-ratio 0.3 \
  -experiment stress_50u -out results/
```

**Key features:**
- Virtual users (goroutines), each with independent session state
- Configurable read/write ratio (`-read-ratio`)
- Randomised inter-request interval (avoids thundering herd)
- Outputs: JSON summary, timeseries CSV, latency CDF CSV
- Metrics: RPS, dropout %, p50/p95/p99 latency

**Build:**
```bash
cd load_generator
go build -o load_gen .
```

### Performance Results

| Experiment | Users | Duration | RPS | Dropout | p50 | p95 | p99 |
|---|---|---|---|---|---|---|---|
| `baseline_20u_v3` | 20 | 60 s | 50.00 | 0.00% | 33 ms | 381 ms | 914 ms |
| `stress_monitored` | 50 | 120 s | 74.04 | 0.00% | 28 ms | 81 ms | 112 ms |

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
  Server: active_sessions.pop(token)  ← token consumed (one-time use)
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
| `GET` | `/config.js` | Serve `window.PORT = <backend_port>;` for dynamic client config |
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
├── cert.pem                    # TLS certificate (generated, gitignored)
├── key.pem                     # TLS private key (generated, gitignored)
├── report_lab6.tex             # Lab 6 submission report (LaTeX)
│
├── load_balancer/              # Go EWMA load balancer
│   ├── main.go                 # LB implementation (EWMA routing, health, proxy)
│   └── lb                      # Compiled binary (gitignored)
│
├── load_generator/             # Go concurrent load testing tool
│   ├── main.go                 # Virtual user load generator
│   ├── load_gen                # Compiled binary (gitignored)
│   └── results/                # Experiment outputs
│       ├── *.json              # Per-experiment aggregate metrics
│       ├── *_timeseries.csv    # Per-second RPS/latency timeseries
│       ├── *_latencies.csv     # Per-request latency CDF data
│       └── plots/              # Generated performance charts
│           ├── response_time_cdf.png
│           ├── throughput_timeseries.png
│           ├── dropout_bar.png
│           ├── sys_cpu.png
│           ├── sys_mem.png
│           └── sys_net.png
│
├── monitor/                    # System resource monitoring
│   ├── monitor.py              # psutil collector (CPU, RAM, network)
│   └── plot_results.py         # Matplotlib chart generator
│
├── valkey/                     # Valkey (Redis-compatible) config
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
│   ├── db.py                   # Valkey database layer
│   │                           #   ├── HMAC-SHA256 tamper detection
│   │                           #   ├── 200-message feed cap (_FEED_LIMIT)
│   │                           #   └── All CRUD functions (messages, users, rooms, keys)
│   └── requirements.txt        # Python dependencies
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
# Backend (FastAPI WebSocket server) port
BACKEND_PORT=5000

# Frontend (static file server) port
FRONTEND_PORT=3269

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
cp .env.example .env
# Edit .env — generate AES_GROUP_KEY and HMAC_SECRET
```

### 3. Generate TLS Certificates

```bash
python generate_certs.py
# Outputs: cert.pem and key.pem
```

> Required because `SubtleCrypto` only works in HTTPS contexts.

### 4. Start Valkey (Terminal 1)

```bash
sudo service valkey-server start
# or: valkey-server --port 6000
```

### 5. Start the Backend (Terminal 2)

```bash
cd server
gunicorn server:app \
  --workers 4 \
  --worker-class uvicorn.workers.UvicornWorker \
  --bind 0.0.0.0:5000 \
  --certfile ../cert.pem \
  --keyfile ../key.pem \
  --timeout 120 \
  --graceful-timeout 30
```

### 6. Start the Frontend (Terminal 3)

```bash
cd client
python3 serve.py
```

### 7. Open in Browser

```
https://localhost:<FRONTEND_PORT>
```

> Accept the self-signed certificate for both frontend and backend ports on first load.

---

## Lab Deployment (Multi-Machine)

> **🌐 Load Balancer:** `http://10.1.75.51:4273`
> **🖥️ Frontend:** `https://10.1.75.51:3269`

### On Sys2, Sys3, Sys4 (each backend)

```bash
# 1. Start local Valkey replica (Sys3, Sys4 only — Sys2 is the primary)
sudo service valkey-server restart

# 2. Start backend with gunicorn
cd ~/group-chat-app/server
gunicorn server:app \
  --workers 4 \
  --worker-class uvicorn.workers.UvicornWorker \
  --bind 0.0.0.0:5000 \
  --certfile cert.pem \
  --keyfile key.pem \
  --timeout 120 \
  --graceful-timeout 30
```

### On Sys1 (load balancer + frontend)

```bash
# 1. Start load balancer
cd ~/group-chat-app/load_balancer
./lb \
  -addr :4000 \
  -backends "https://10.1.75.51:5274,https://10.1.75.51:5275,https://10.1.75.51:5276" \
  -threshold-ms 300 \
  -ewma-alpha 0.3 \
  -health-interval 1s \
  -backend-timeout 800ms

# 2. Start frontend
cd ~/group-chat-app/client
python3 serve.py
```

### Verify deployment

```bash
# Check all backends are healthy
curl http://10.1.75.51:4273/lb/status | python3 -m json.tool

# Expect: all three backends alive=true, overloaded=false
```

> **Tip:** Always restart the LB before an evaluation to reset EWMA state to zero for all backends.

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

---

*PixelChat — Group Quest v2.0 | CSD Lab 4 → Lab 6: Load Balanced Distributed Deployment*
