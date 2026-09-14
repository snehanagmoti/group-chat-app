# PixelChat: Secure Group Quest

> **🌐 Load Balancer (primary entry point):** `http://10.1.75.51:4273`  
> **📁 Frontend UI (Optional / Local):** Served via `client/serve.py` (e.g. `https://localhost:3000` locally, or `https://10.1.75.51:3273` if hosted on Sys1)

A **real-time, secure, gamified group chat** built with **FastAPI** (Python backend) and **Vanilla HTML/CSS/JS** (no frameworks), styled with a retro 8-bit pixel aesthetic. All messages are **end-to-end encrypted** using AES-GCM via the browser's Web Crypto API, **digitally signed** with ECDSA-P256, and **persisted encrypted** in a Valkey (Redis-compatible) database with HMAC-SHA256 tamper detection.

The backend is deployed across **three systems** behind a custom **Go load balancer** using EWMA-based dynamic routing — automatically redistributing traffic when any backend becomes slow or unhealthy.

---

## Table of Contents

1. [Features](#features)
2. [Tech Stack](#tech-stack)
3. [Deployment Architecture & Port Mappings](#deployment-architecture--port-mappings)
4. [Load Balancer (Go)](#load-balancer-go)
5. [Load Generator & Testing Workflows](#load-generator--testing-workflows)
6. [Security & Encryption](#security--encryption)
7. [Database Design (Valkey)](#database-design-valkey)
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

### 🎮 Retro Pixel Theme & Gamification
- **8-Bit Aesthetic** — Dark theme featuring pixelated fonts (Press Start 2P / VT323), vibrant neon accents, scanline overlays, and sound effects.
- **XP & Leveling** — Earn XP for sending/receiving messages, streak bonuses, room creation, and active presence.
- **Ranks** — Progress through 6 ranks: `🌱 NEWBIE` → `🗡️ SQUIRE` → `🛡️ KNIGHT` → `🏆 CHAMPION` → `👑 WARLORD` → `⭐ LEGEND`.
- **Sound Effects** — Audio chimes for message events, user join/leave, and leveling up.

### 🔒 Security & Privacy
- **End-to-End Encryption (AES-GCM)** — Messages are encrypted client-side using 256-bit AES-GCM before transmission.
- **ECDSA-P256 Digital Signatures** — Every outgoing message is signed client-side with the user's private key; the server verifies every signature before persistence or broadcast.
- **Encrypted at Rest** — Valkey persists only ciphertexts and IVs, never plaintext.
- **HMAC-SHA256 Tamper Detection** — Message integrity is guarded by keyed HMAC digests re-verified on history retrieval.
- **Whisper / Direct Messages** — Private encrypted messages sent to a specific user inside a shared room.
- **Ephemerality** — 5-minute window for message edits; soft-delete support.

### ⚡ Distributed Scalability & High Throughput
- **Custom Go Load Balancer** — Layer 7 reverse proxy using Exponentially Weighted Moving Average (EWMA) response-time routing with active health probes.
- **Valkey Primary/Replica Architecture** — Sys2 acts as the write primary; Sys3 and Sys4 act as read replicas.
- **Async High-Throughput Endpoints** — Dedicated `/message` and `/feed` endpoints leveraging `redis.asyncio` pipeline batching and atomic deduplication.
- **Incremental Delta Caching** — Micro-cache (250 ms) with double-checked locking serves `/feed` in $O(\Delta)$ time by fetching and merging only new messages instead of re-reading entire lists.

---

## Tech Stack

| Layer | Technology | Purpose |
|---|---|---|
| **Load Balancer** | Go (standard library only) | Layer 7 reverse proxy with EWMA dynamic routing and connection pooling |
| **Backend** | Python 3.10+, FastAPI, Uvicorn, Gunicorn | REST API, WebSockets, auth, message validation |
| **Database** | Valkey (Redis-compatible) | In-memory datastore with primary-replica replication |
| **Async Redis** | `redis.asyncio` (`redis-py` 5.0+) | Non-blocking Redis I/O on FastAPI event loop |
| **Crypto (Client)** | Web Crypto API (`SubtleCrypto`) | AES-GCM-256 encryption, ECDSA-P256 signing |
| **Crypto (Server)** | Python `cryptography`, `bcrypt`, `hmac` | ECDSA verification, password hashing, HMAC tamper detection |
| **Frontend** | Vanilla HTML5, CSS3, JavaScript (ES2022) | Single-page app (Login → Lobby → Chat), no external UI frameworks |
| **Load Generator** | Go (goroutines, HTTP client) | Concurrent virtual user load generator |
| **Monitoring** | Python, `psutil`, `matplotlib` | Host resource monitoring and evaluation plot generation |

---

## Deployment Architecture & Port Mappings

### Port Mapping

| System | Host ID | SSH Port | Internal Port | External Port | Deployed Services |
|---|---|---|---|---|---|
| **Sys1** | 1 | `2273` | `4000`<br>`3000` | `4273`<br>`3273` | **Load Balancer** (External entry: `http://10.1.75.51:4273`)<br>*Optional Frontend Static Server* |
| **Sys2** | 2 | `2274` | `5000`<br>`6000` | `5274`<br>`6274` | **Backend 1** (`https://10.1.75.51:5274`)<br>**Valkey Primary** (Write client target: `6274`) |
| **Sys3** | 3 | `2275` | `5000`<br>`6000` | `5275`<br>`6275` | **Backend 2** (`https://10.1.75.51:5275`)<br>**Valkey Replica** (Replicates from Sys2 `:6274`) |
| **Sys4** | 4 | `2276` | `5000`<br>`6000` | `5276`<br>`6276` | **Backend 3** (`https://10.1.75.51:5276`)<br>**Valkey Replica** (Replicates from Sys2 `:6274`) |

```text
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                                LAB NETWORK (10.1.75.51)                                │
│                                                                                        │
│  ┌──────────────────────────────────────────────────────────────────────────────────┐  │
│  │ SYS1 (SSH :2273) — Ingress / Load Balancer                                       │  │
│  │                                                                                  │  │
│  │  ┌───────────────────────────────────────────────────┐                           │  │
│  │  │ Load Balancer (Go)       internal :4000 -> :4273  │                           │  │
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
│        │ :6274         ││ :6275         ││ :6276         │                             │
│        └───────┬───────┘└───────▲───────┘└───────▲───────┘                             │
│                │                │                │                                     │
│                └─ Replication ──┴────────────────┘                                     │
│                   (Sys3 & Sys4 replicate from Sys2 :6274)                              │
└───────────────────────┼────────────────────────────────────────────────────────────────┘
                        ▲                                               
               REST/WS  │ (HTTP :4273)                                  
                        │                                               
        ┌───────────────┴──────────────────────────────────────────────────────────────┐
        │                        Load Generator / Client                               │
        └──────────────────────────────────────────────────────────────────────────────┘
```

---

## Load Balancer (Go)

The load balancer (`load_balancer/main.go`) is a custom Layer 7 HTTP reverse proxy written in Go using only the standard library.

### Key Features
- **EWMA Response-Time Routing** — Balances requests by tracking moving average latency:
  $$\text{EWMA}_{\text{new}} = \alpha \cdot \text{latency} + (1 - \alpha) \cdot \text{EWMA}_{\text{prev}}$$
  Backends with lower latency receive proportionately more traffic.
- **Error Cooldown** — A backend that experiences a timeout or error enters a cooldown window to prevent cascading failures.
- **Connection Pooling** — Shared `http.Transport` keeping persistent keep-alive connections warm to minimize TCP/TLS handshake latency.
- **Live Health Probing** — Background health checker queries `/health` on all backends every 1s and updates routing status.
- **Observability** — `/lb/health`, `/lb/status`, and `/lb/metrics` endpoints expose real-time metrics, queue times, and backend scores.

### Startup Command
```bash
cd load_balancer
./lb \
  -addr :4000 \
  -backends "https://10.1.75.51:5274,https://10.1.75.51:5275,https://10.1.75.51:5276" \
  -threshold-ms 300 \
  -ewma-alpha 0.3 \
  -health-interval 1s \
  -backend-timeout 2500ms
```

---

## Load Generator & Testing Workflows

The load generator (`load_generator/`) simulates concurrent virtual users executing reads (`GET /feed`) and writes (`POST /message`):

### Running Load Tests
```bash
cd load_generator
./load_gen \
  -url http://10.1.75.51:4273 \
  -users 50 \
  -duration 120s \
  -min-interval 100ms -max-interval 500ms \
  -read-ratio 0.3 \
  -experiment stress_50u \
  -out results/
```

### Collecting System Metrics During Tests
On each monitored system (or remotely via SSH), run the resource monitor:
```bash
# On Sys1, Sys2, Sys3, Sys4
python3 monitor/monitor.py --duration 120 --interval 1 --out load_generator/results/sysN_monitor.csv
```

### Generating Report Plots & Reading Results
Once CSVs are generated in `load_generator/results/`, run `plot_results.py` to produce presentation-ready graphs:
```bash
python3 monitor/plot_results.py \
  --results-dir load_generator/results \
  --out load_generator/results/plots
```
Generated graphs in `load_generator/results/plots/`:
1. `response_time_cdf.png` — Response time Cumulative Distribution Function.
2. `response_time_timeseries.png` — Latency trend over test duration.
3. `throughput_timeseries.png` — Request throughput (RPS) over time.
4. `dropout_bar.png` — Error and timeout comparison.
5. `sys_cpu.png` & `sys_mem.png` & `sys_net.png` — Per-machine CPU, RAM, and network utilization.

---

## Security & Encryption

### End-to-End Encryption Flow (AES-GCM)
1. User receives 256-bit group key via authenticated `GET /group-key` endpoint.
2. Messages are encrypted in the browser with AES-GCM (generating a fresh 12-byte IV for every message).
3. Sender signs `ciphertext + iv` using ECDSA-P256 (`SubtleCrypto`).
4. Server validates ECDSA signature against the sender's registered public key.
5. Server saves encrypted payload to Valkey and computes `HMAC-SHA256(ciphertext + iv)` for tamper detection.
6. Recipients verify the ECDSA signature and decrypt the ciphertext in-browser.

---

## Database Design (Valkey)

The datastore is built on **Valkey** (Redis-compatible) configured in a primary-replica topology:

### Key Schema
| Key | Type | Description |
|---|---|---|
| `msg:{msg_id}` | Hash | Message record (`room_id`, `username`, `ciphertext`, `iv`, `signature`, `public_key`, `timestamp`, `hmac_digest`, `simple`, `json`) |
| `feed:list:{room_id}` | List | Pre-serialized JSON message entries for fast single-command `/feed` responses |
| `feed:room:{room_id}` | Sorted Set | Index of message IDs ordered by timestamp (`score = ts`, `member = msg_id`) |
| `feed:all` | Sorted Set | Global index of all messages ordered by timestamp |
| `user:{username}` | Hash | User profile (`password_hash`, `avatar`, `created_at`, `xp`) |
| `users:all` | Set | All registered usernames |
| `room:{room_id}` | Hash | Room metadata (`id`, `name`, `created_by`, `created_at`, `is_public`, `avatar`) |
| `rooms:all` | Set | All active room codes |
| `key:{username}` | String | User's registered ECDSA-P256 public key (JWK JSON) |
| `session:{token}` | String | Session tokens mapping to usernames with TTL |

### High-Throughput Optimizations
- **Atomic Deduplication**: Writes check `HSETNX msg:{msg_id} username <user>` to prevent duplicate processing.
- **Pipelined Storage**: `HSET` + `ZADD` $\times 2$ + `RPUSH` sent in a single round-trip pipeline.
- **Incremental Delta Caching**: Micro-cache (`_feed_cache`) with per-room `asyncio.Lock`. On miss, only newly appended items are fetched (`LRANGE feed:list:{room_id} prev_len -1`) and string-concatenated in $O(\Delta)$ time without re-serializing previous history.

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

### Ranks
`🌱 NEWBIE (0 XP)` → `🗡️ SQUIRE (200 XP)` → `🛡️ KNIGHT (600 XP)` → `🏆 CHAMPION (1,500 XP)` → `👑 WARLORD (4,000 XP)` → `⭐ LEGEND (10,000 XP)`

---

## WebSocket Message Protocol

### Client → Server
- `join`: `{ type: "join", token, public_key, room_id }`
- `message`: `{ type: "message", ciphertext, iv, signature, public_key, client_msg_id, attachment?, reply_to?, target_user? }`
- `edit_message`: `{ type: "edit_message", msg_id, ciphertext, iv, signature, public_key }`
- `delete_message`: `{ type: "delete_message", msg_id }`
- `typing`: `{ type: "typing" }`
- `heartbeat`: `{ type: "heartbeat" }`

### Server → Client
- `system`: Welcome message and room metadata
- `join` / `leave`: Player presence events
- `message`: Encrypted message broadcast
- `message_edited` / `message_deleted`: State modifications
- `receipt`: Delivery receipt (`sent` / `partial` / `delivered_all`)
- `userList`: Online user roster
- `history`: Replay of encrypted messages for newly joined client
- `xp_update`: Real-time XP status

---

## REST API Reference

### Load-Generator Endpoints
| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/message` | Ingest message (`msg_id`, `client-name`, `msg`). Pipelined, atomic dedup. |
| `GET` | `/feed` | Retrieve message feed (pre-serialized JSON list, delta micro-cached). |

### Auth & Management
| Method | Endpoint | Request Body | Response |
|---|---|---|---|
| `POST` | `/register` | `{username, password, avatar}` | `{token, username, avatar, xp}` |
| `POST` | `/login` | `{username, password}` | `{token, username, avatar, xp}` |
| `GET` | `/rooms` | — | `{rooms: [...]}` |
| `POST` | `/rooms` | `{name, is_public, avatar, created_by}` | `{room_id, name, ...}` |
| `POST` | `/upload` | Multipart file upload | `{url, fileName, fileType, fileSize}` |
| `GET` | `/group-key` | — | `{key: "<hex>"}` |
| `GET` | `/health` | — | `{"status": "ok"}` |
| `GET` | `/config.js` | — | Sets dynamic backend port config |

---

## Project Structure

```
group-chat-app/
├── .env                        # Runtime config (gitignored)
├── .env.example                # Config template
├── .gitignore
├── README.md
├── generate_certs.py           # RSA-2048 self-signed TLS cert generator
├── cert.pem                    # TLS certificate (gitignored)
├── key.pem                     # TLS private key (gitignored)
│
├── load_balancer/              # Custom Go EWMA load balancer
│   ├── main.go                 # Reverse proxy, health probes, EWMA logic
│   └── lb                      # Compiled executable
│
├── load_generator/             # Go load testing tool
│   ├── main.go                 # Concurrent virtual user generator
│   ├── load_gen                # Compiled executable
│   └── results/                # Experiment outputs (CSVs, JSONs)
│       └── plots/              # Generated performance charts
│
├── monitor/                    # Host metrics collector and plotter
│   ├── monitor.py              # psutil CPU/RAM/network sampler
│   ├── plot_results.py         # Matplotlib report chart generator
│   └── requirements.txt        # Monitor Python dependencies
│
├── valkey/                     # Valkey configuration files
│   ├── valkey-primary.conf     # Sys2 Primary configuration
│   ├── valkey-replica-sys3.conf # Sys3 Replica configuration
│   └── valkey-replica-sys4.conf # Sys4 Replica configuration
│
├── server/
│   ├── server.py               # FastAPI application (WebSockets, REST endpoints)
│   ├── db.py                   # Valkey database driver and caching logic
│   └── requirements.txt        # Backend dependencies
│
└── client/
    ├── index.html              # PixelChat SPA interface
    ├── style.css               # 8-bit retro theme styling
    ├── app.js                  # Client Web Crypto and WebSocket logic
    ├── serve.py                # Optional static file server
    └── sounds/                 # 8-bit audio effects
```

---

## Environment Configuration

Create `.env` from `.env.example`:
```bash
cp .env.example .env
```

Key environment variables:
```env
# Internal port on which FastAPI/Gunicorn binds (5000)
PORT=5000

# Reached by clients/frontend (e.g. 5274 on Sys2, 5275 on Sys3, 5276 on Sys4; or 4273 for LB)
BACKEND_PORT=5000

# 256-bit AES group key (64 hex chars)
AES_GROUP_KEY=<64-hex-chars>

# HMAC secret for message integrity (64 hex chars)
HMAC_SECRET=<64-hex-chars>

# Write client connects to Primary (Sys2 external 6274 or local 6000)
VALKEY_PRIMARY_URL=redis://10.1.75.51:6274/0

# Read client connects to local Valkey instance
VALKEY_REPLICA_URL=redis://127.0.0.1:6000/0
```

---

## Quick Start (Local)

### 1. Install Dependencies
```bash
cd server
pip install -r requirements.txt
```

### 2. Generate TLS Certificates
```bash
python3 generate_certs.py
```

### 3. Start Valkey (Terminal 1)
```bash
valkey-server --port 6000
```

### 4. Start Backend Server (Terminal 2)
```bash
cd server
gunicorn server:app \
  --workers 4 \
  --worker-class uvicorn.workers.UvicornWorker \
  --bind 0.0.0.0:5000 \
  --certfile ../cert.pem \
  --keyfile ../key.pem
```

### 5. Optional: Start Frontend Server (Terminal 3)
```bash
python3 client/serve.py
# Open https://localhost:3000 in browser
```

---

## Lab Deployment (Multi-Machine)

### 1. Backends (Sys2, Sys3, Sys4)
On each backend machine:
```bash

# Start Valkey (Sys2 runs as primary; Sys3/Sys4 run as replica)
valkey-server valkey/valkey-<role>.conf

# Start FastAPI application
cd server
gunicorn server:app \
  --workers 4 \
  --worker-class uvicorn.workers.UvicornWorker \
  --bind 0.0.0.0:5000 \
  --certfile ../cert.pem \
  --keyfile ../key.pem \
  --timeout 120
```

### 2. Load Balancer (Sys1)
On Sys1:
```bash
cd load_balancer
./lb \
  -addr :4000 \
  -backends "https://10.1.75.51:5274,https://10.1.75.51:5275,https://10.1.75.51:5276" \
  -threshold-ms 300 \
  -ewma-alpha 0.3 \
  -health-interval 1s \
  -backend-timeout 2500ms
```

### 3. Verify Deployment
```bash
# Check load balancer health & backends status
curl http://10.1.75.51:4273/lb/status | python3 -m json.tool
```
Expect all three backends to report `alive: true`.
