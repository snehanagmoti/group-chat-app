/**
 * PixelChat — Secure WebSocket Client
 * Adds AES-GCM encryption (key from server /group-key endpoint)
 * and ECDSA-P256 per-user signing via the browser's SubtleCrypto API.
 */

// ── Config ────────────────────────────────────────────────────────────────────
const WS_PROTOCOL   = window.location.protocol === "https:" ? "wss:" : "ws:";
const HTTP_PROTOCOL = window.location.protocol === "https:" ? "https:" : "http:";
const DYNAMIC_PORT  = window.BACKEND_PORT;
const CURRENT_PORT  = window.location.port ? parseInt(window.location.port) : (window.location.protocol === "https:" ? 443 : 80);

// Uses dynamic BACKEND_PORT for local dev (distinct ports) or same-origin window.location.host for production/proxy
const BACKEND_HOST  = (DYNAMIC_PORT && DYNAMIC_PORT !== CURRENT_PORT)
    ? `${window.location.hostname}:${DYNAMIC_PORT}`
    : window.location.host;

const SERVER_URL    = `${WS_PROTOCOL}//${BACKEND_HOST}/ws`;
const UPLOAD_URL    = `${HTTP_PROTOCOL}//${BACKEND_HOST}/upload`;
const GROUP_KEY_URL = `${HTTP_PROTOCOL}//${BACKEND_HOST}/group-key`;

// The frontend and load balancer normally use different ports. Include
// credentials so the browser stores/sends the load balancer's affinity cookie
// across those same-site, cross-origin requests.
function backendFetch(resource, options = {}) {
    return fetch(resource, { ...options, credentials: "include" });
}


// ── Sounds ────────────────────────────────────────────────────────────────────
const SOUNDS = {
    join:    new Audio("/static/sounds/mushroom.mp3"),
    message: new Audio("/static/sounds/coin.mp3"),
    leave:   new Audio("/static/sounds/pipe.mp3"),
    start:   new Audio("/static/sounds/mario_start.mp3"),
};
Object.values(SOUNDS).forEach(a => { a.preload = "auto"; a.volume = 0.5; });

function playSound(name) {
    const clip = SOUNDS[name];
    if (!clip) return;
    clip.currentTime = 0;
    clip.play().catch(() => {});
}

const RECONNECT_BASE_DELAY = 1000;
const RECONNECT_MAX_DELAY  = 10000;

// ── Avatars ───────────────────────────────────────────────────────────────────
const AVATAR_COLORS = [
    "#778873","#A1BC98","#546058","#8aab88",
    "#6a8068","#c8dcc5","#4a6050","#9abca0",
    "#667860","#b0ccb0","#506858","#7a9878",
];
const AVATARS = [
    { id:"wizard",    name:"WIZARD",   icon:"🧙‍♂️", bg:"#4a6050", border:"#A1BC98" },
    { id:"robot",     name:"ROBOT",    icon:"🤖",   bg:"#384d54", border:"#729fa8" },
    { id:"ninja",     name:"NINJA",    icon:"🥷",   bg:"#2d3330", border:"#586660" },
    { id:"astronaut", name:"ASTRO",    icon:"👨‍🚀", bg:"#423854", border:"#8f75b8" },
    { id:"dragon",    name:"DRAGON",   icon:"🐉",   bg:"#5c2a2a", border:"#b85c5c" },
    { id:"hero",      name:"HERO",     icon:"🦸",   bg:"#2a425c", border:"#5c8eb8" },
    { id:"alien",     name:"ALIEN",    icon:"👽",   bg:"#2a5c3b", border:"#5cb87d" },
    { id:"cyber",     name:"CYBER",    icon:"👾",   bg:"#542a5c", border:"#b85cb0" },
    { id:"fox",       name:"FOX",      icon:"🦊",   bg:"#5c3d2a", border:"#b87c5c" },
    { id:"owl",       name:"OWL",      icon:"🦉",   bg:"#473f32", border:"#8a7d67" },
    { id:"bear",      name:"BEAR",     icon:"🐻",   bg:"#3b2f28", border:"#786154" },
    { id:"lion",      name:"LION",     icon:"🦁",   bg:"#594924", border:"#ad914e" },
];

const ROOM_AVATARS = [
    { icon: "🏰", name: "CASTLE" },
    { icon: "🌋", name: "VOLCANO" },
    { icon: "🗺️", name: "MAP" },
    { icon: "⚔️", name: "ARENA" },
    { icon: "🎮", name: "ARCADE" },
    { icon: "🔮", name: "TAVERN" },
    { icon: "🛡️", name: "ARMORY" },
    { icon: "🌙", name: "NIGHT" }
];

let selectedAvatar = AVATARS[0].id;
let selectedRoomAvatar = ROOM_AVATARS[0].icon;

function getAvatarData(avatarId, username = "") {
    const found = AVATARS.find(a => a.id === avatarId);
    if (found) return found;
    return {
        id:     avatarId || "default",
        name:   username || "HERO",
        icon:   (username || "?").charAt(0).toUpperCase(),
        bg:     getAvatarColor(username || avatarId || "user"),
        border: "#A1BC98",
    };
}

function renderAvatarPicker() {
    const grid = document.getElementById("avatar-picker-grid");
    if (!grid) return;
    grid.innerHTML = "";
    AVATARS.forEach(av => {
        const btn = document.createElement("div");
        btn.className = `avatar-option ${av.id === selectedAvatar ? "selected" : ""}`;
        btn.style.backgroundColor = av.bg;
        btn.style.borderColor = av.border;
        btn.title = av.name;
        btn.innerHTML = `
            <span class="avatar-option-icon">${av.icon}</span>
            <span class="avatar-option-name">${av.name}</span>
        `;
        btn.addEventListener("click", () => {
            selectedAvatar = av.id;
            Array.from(grid.children).forEach(c => c.classList.remove("selected"));
            btn.classList.add("selected");
        });
        grid.appendChild(btn);
    });
}

function renderRoomAvatarPicker() {
    if (!roomAvatarPickerGrid) return;
    roomAvatarPickerGrid.innerHTML = "";
    ROOM_AVATARS.forEach((a, i) => {
        const btn = document.createElement("div");
        btn.className = `avatar-option ${a.icon === selectedRoomAvatar ? "selected" : ""}`;
        btn.innerHTML = `
            <div class="avatar-option-icon">${a.icon}</div>
        `;
        btn.addEventListener("click", () => {
            selectedRoomAvatar = a.icon;
            Array.from(roomAvatarPickerGrid.children).forEach(c => c.classList.remove("selected"));
            btn.classList.add("selected");
        });
        roomAvatarPickerGrid.appendChild(btn);
    });
}

// ── Ranks ─────────────────────────────────────────────────────────────────────
const RANKS = [
    { level:1, xp:0,     name:"NEWBIE",   emoji:"🌱", next:200   },
    { level:2, xp:200,   name:"SQUIRE",   emoji:"🗡️", next:600   },
    { level:3, xp:600,   name:"KNIGHT",   emoji:"🛡️", next:1500  },
    { level:4, xp:1500,  name:"CHAMPION", emoji:"🏆", next:4000  },
    { level:5, xp:4000,  name:"WARLORD",  emoji:"👑", next:10000 },
    { level:6, xp:10000, name:"LEGEND",   emoji:"⭐", next:10000 },
];

// ── State ─────────────────────────────────────────────────────────────────────
let ws                = null;
let currentUsername   = "";
let reconnectAttempts = 0;
let reconnectTimer    = null;
let isIntentionalClose = false;
let isJoined          = false;
let sessionToken      = null;
let typingTimeout     = null;
let lastTypingSent    = 0;
let isTabFocused      = true;

// Room state
let currentRoomId      = null;
let currentRoomName    = "";
let currentRoomAvatar  = "🏰";
let currentRoomCreator = "";
let roomListCache      = [];
let roomPollTimer      = null;
let isPublicRoom       = true;

// Gamification
let totalXP      = 0;
let streakCount  = 0;
let messagesSent = 0;
let sessionStart = null;
let sessionTimer = null;
let currentLevel = 1;

// Receipt tracking
const pendingReceipts = new Map();

// Attachment state
let attachmentData = null;
let pendingFile    = null;

// Reply state
let replyToMsgId   = null;
let replyToUsername = "";
let replyToText    = "";

// Voice memo state
let mediaRecorder  = null;
let audioChunks    = [];
let isRecording    = false;
let recordingTimer = null;
let recordingStart = 0;

// Message cache for reply lookup
const messageCache = new Map(); // msg_id → { username, text }

let pendingFilePayload = null;

function clearPendingAttachment() {
    attachmentData = null;
    pendingFile    = null;
    pendingFilePayload = null;
    if (attachmentPreviewBar) attachmentPreviewBar.classList.add("hidden");
    if (attachmentFilename)  attachmentFilename.textContent  = "";
    if (attachmentFilesize)  attachmentFilesize.textContent  = "";
    if (fileInput)           fileInput.value = "";
}

function clearReply() {
    replyToMsgId   = null;
    replyToUsername = "";
    replyToText    = "";
    const bar = document.getElementById("reply-preview-bar");
    if (bar) bar.classList.add("hidden");
}

// ── Crypto State ──────────────────────────────────────────────────────────────
let aesKey         = null;   // CryptoKey (AES-GCM 256-bit, imported from /group-key)
let ecdsaKeyPair   = null;   // { privateKey, publicKey } CryptoKey
let myPublicKeyJwk = null;   // JWK of my ECDSA public key (sent in join + every message)
let cryptoReady    = false;

// ── Crypto Helpers ────────────────────────────────────────────────────────────

/** Hex string → Uint8Array */
function hexToBytes(hex) {
    const arr = new Uint8Array(hex.length / 2);
    for (let i = 0; i < arr.length; i++) {
        arr[i] = parseInt(hex.slice(i * 2, i * 2 + 2), 16);
    }
    return arr;
}

/** ArrayBuffer → base64url string (chunk-safe for large buffers like audio) */
function bufToB64(buf) {
    const bytes = new Uint8Array(buf);
    let binary = "";
    const chunkSize = 8192; // process 8KB at a time to avoid call stack overflow
    for (let i = 0; i < bytes.length; i += chunkSize) {
        binary += String.fromCharCode(...bytes.subarray(i, i + chunkSize));
    }
    return btoa(binary)
        .replace(/\+/g, "-").replace(/\//g, "_").replace(/=/g, "");
}

/** base64url string → Uint8Array */
function b64ToBuf(b64) {
    const padded = b64.replace(/-/g, "+").replace(/_/g, "/");
    const bin = atob(padded);
    return Uint8Array.from(bin, c => c.charCodeAt(0));
}

/**
 * Initialise crypto:
 *  1. Fetch AES group key from server /group-key (hex in .env)
 *  2. Import it as AES-GCM 256-bit CryptoKey
 *  3. Generate per-user ECDSA-P256 signing key pair
 */
async function initCrypto() {
    try {
        // 1. Fetch key from server
        const resp = await backendFetch(GROUP_KEY_URL);
        if (!resp.ok) throw new Error(`/group-key returned ${resp.status}`);
        const { key: keyHex } = await resp.json();
        if (!keyHex || keyHex.length !== 64) throw new Error("Invalid AES key length from server");

        // 2. Import AES-GCM key
        aesKey = await crypto.subtle.importKey(
            "raw",
            hexToBytes(keyHex),
            { name: "AES-GCM" },
            false,           // not extractable
            ["encrypt", "decrypt"]
        );

        // 3. Generate ECDSA P-256 key pair for this session
        ecdsaKeyPair = await crypto.subtle.generateKey(
            { name: "ECDSA", namedCurve: "P-256" },
            true,
            ["sign", "verify"]
        );
        myPublicKeyJwk = await crypto.subtle.exportKey("jwk", ecdsaKeyPair.publicKey);

        cryptoReady = true;
        setCryptoStatusUI(true);
        console.log("[Crypto] Initialised — AES-GCM + ECDSA-P256 ready");
    } catch (err) {
        cryptoReady = false;
        setCryptoStatusUI(false);
        console.error("[Crypto] Init failed:", err);
        throw err;
    }
}

/**
 * Encrypt a plaintext string with AES-GCM.
 * Returns { ciphertext: base64url, iv: base64url }
 */
async function encryptMessage(plaintext) {
    const iv = crypto.getRandomValues(new Uint8Array(12)); // 96-bit IV
    const encodedText = new TextEncoder().encode(plaintext);
    const cipherBuf = await crypto.subtle.encrypt(
        { name: "AES-GCM", iv },
        aesKey,
        encodedText
    );
    return {
        ciphertext: bufToB64(cipherBuf),
        iv:         bufToB64(iv),
    };
}

/**
 * Decrypt a base64url-encoded AES-GCM ciphertext.
 * Returns the plaintext string, or null on failure (tampered/wrong key).
 */
async function decryptMessage(ciphertextB64, ivB64) {
    try {
        const cipherBuf = b64ToBuf(ciphertextB64);
        const iv        = b64ToBuf(ivB64);
        const plainBuf  = await crypto.subtle.decrypt(
            { name: "AES-GCM", iv },
            aesKey,
            cipherBuf
        );
        return new TextDecoder().decode(plainBuf);
    } catch {
        return null; // decryption failure = ciphertext modified or wrong key
    }
}

/**
 * Sign material (ciphertext + iv concatenated as UTF-8) with ECDSA-P256/SHA-256.
 * Returns base64url-encoded IEEE P1363 signature (r||s, 64 bytes).
 */
async function signMaterial(material) {
    const encoded = new TextEncoder().encode(material);
    const sigBuf  = await crypto.subtle.sign(
        { name: "ECDSA", hash: "SHA-256" },
        ecdsaKeyPair.privateKey,
        encoded
    );
    return bufToB64(sigBuf);
}

/**
 * Verify a base64url ECDSA-P256/SHA-256 signature.
 * `senderJwk` — the JWK public key dict of the sender.
 * Returns true if valid.
 */
async function verifySignature(material, sigB64, senderJwk) {
    try {
        const pubKey = await crypto.subtle.importKey(
            "jwk",
            senderJwk,
            { name: "ECDSA", namedCurve: "P-256" },
            false,
            ["verify"]
        );
        const encoded = new TextEncoder().encode(material);
        const sigBuf  = b64ToBuf(sigB64);
        return await crypto.subtle.verify(
            { name: "ECDSA", hash: "SHA-256" },
            pubKey,
            sigBuf,
            encoded
        );
    } catch {
        return false;
    }
}

function setCryptoStatusUI(ready) {
    const el = document.getElementById("crypto-status");
    if (!el) return;
    if (ready) {
        el.textContent  = "🔒 SECURE";
        el.className    = "crypto-status secure";
        el.title        = "AES-GCM encrypted · ECDSA signed";
    } else {
        el.textContent  = "⚠ NO CRYPTO";
        el.className    = "crypto-status insecure";
        el.title        = "Crypto failed to initialise";
    }
}

// ── DOM ───────────────────────────────────────────────────────────────────────
const loginScreen       = document.getElementById("login-screen");
const lobbyScreen       = document.getElementById("lobby-screen");
const chatScreen        = document.getElementById("chat-screen");

// Auth DOM
const tabRegister       = document.getElementById("tab-register");
const tabLogin          = document.getElementById("tab-login");
const panelRegister     = document.getElementById("panel-register");
const panelLogin        = document.getElementById("panel-login");

const regUsername       = document.getElementById("reg-username");
const regPassword       = document.getElementById("reg-password");
const registerBtn       = document.getElementById("register-btn");
const registerBtnText   = document.getElementById("register-btn-text");

const loginUsername     = document.getElementById("login-username");
const loginPassword     = document.getElementById("login-password");
const loginBtn          = document.getElementById("login-btn");
const loginBtnText      = document.getElementById("login-btn-text");

const loginError        = document.getElementById("login-error");

// Lobby DOM
const lobbyUsernameDisplay = document.getElementById("lobby-username-display");
const lobbyLogoutBtn       = document.getElementById("lobby-logout-btn");
const ltabCreate           = document.getElementById("ltab-create");
const ltabJoin             = document.getElementById("ltab-join");
const lpanelCreate         = document.getElementById("lpanel-create");
const lpanelJoin           = document.getElementById("lpanel-join");
const roomNameInput        = document.getElementById("room-name-input");
const roomAvatarPickerGrid = document.getElementById("room-avatar-picker-grid");
const visPublicBtn         = document.getElementById("vis-public");
const visPrivateBtn        = document.getElementById("vis-private");
const visibilityHint       = document.getElementById("visibility-hint");
const createRoomBtn        = document.getElementById("create-room-btn");
const createRoomBtnText    = document.getElementById("create-room-btn-text");
const lobbyError           = document.getElementById("lobby-error");
const roomCodeInput        = document.getElementById("room-code-input");
const joinCodeBtn          = document.getElementById("join-code-btn");
const joinCodeBtnText      = document.getElementById("join-code-btn-text");
const joinCodeError        = document.getElementById("join-code-error");
const roomListScroll       = document.getElementById("room-list-scroll");
const roomListEmpty        = document.getElementById("room-list-empty");
const roomSearchInput      = document.getElementById("room-search-input");
const refreshRoomsBtn      = document.getElementById("refresh-rooms-btn");

function switchTab(tab) {
    if (loginErrorTimer) { clearTimeout(loginErrorTimer); loginErrorTimer = null; }
    if (loginError) loginError.textContent = "";
    if (tab === "register") {

        tabRegister.classList.add("active");
        tabLogin.classList.remove("active");
        panelRegister.classList.remove("hidden");
        panelLogin.classList.add("hidden");
        regUsername.focus();
    } else {
        tabLogin.classList.add("active");
        tabRegister.classList.remove("active");
        panelLogin.classList.remove("hidden");
        panelRegister.classList.add("hidden");
        loginUsername.focus();
    }
}

// Global scope for onclick
window.switchTab = switchTab;

const messagesScroll    = document.getElementById("messages-scroll");
const messageInput      = document.getElementById("message-input");
const sendBtn           = document.getElementById("send-btn");
const userList          = document.getElementById("user-list");
const headerSubtitle    = document.getElementById("header-subtitle");
const typingIndicator   = document.getElementById("typing-indicator");
const typingTextEl      = document.getElementById("typing-text");
const emojiPicker       = document.getElementById("emoji-picker");
const emojiToggleBtn    = document.getElementById("emoji-toggle-btn");
const fileInput         = document.getElementById("file-input");
const attachmentToggleBtn  = document.getElementById("attachment-toggle-btn");
const attachmentPreviewBar = document.getElementById("attachment-preview-bar");
const attachmentFilename   = document.getElementById("attachment-filename");
const attachmentFilesize   = document.getElementById("attachment-filesize");
const attachmentRemoveBtn  = document.getElementById("attachment-remove-btn");
const statusDot         = document.getElementById("status-dot");
const statusText        = document.getElementById("status-text");

// Gamification DOM
const xpBarFill   = document.getElementById("xp-bar-fill");
const xpValue     = document.getElementById("xp-value");
const rankNameEl  = document.getElementById("rank-name");
const onlineBadge = document.getElementById("online-count-badge");

const levelupToast   = document.getElementById("levelup-toast");
const levelupSub     = document.getElementById("levelup-sub");

const infoBtn          = document.getElementById("info-btn");
const leaveBtn         = document.getElementById("leave-btn");
const infoModalOverlay = document.getElementById("info-modal-overlay");
const infoModalClose   = document.getElementById("info-modal-close");
const voiceMemoBtn     = document.getElementById("voice-memo-btn");
const replyPreviewBar  = document.getElementById("reply-preview-bar");
const replyPreviewUsername = document.getElementById("reply-preview-username");
const replyPreviewText = document.getElementById("reply-preview-text");
const replyPreviewClose = document.getElementById("reply-preview-close");

// ── WebSocket ─────────────────────────────────────────────────────────────────

function connect() {
    if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) return;
    ws = new WebSocket(SERVER_URL);

    ws.onopen = () => {
        reconnectAttempts = 0;
        updateConnectionStatus("connected");
        // Send the authenticated session token, ECDSA public key, and room_id
        ws.send(JSON.stringify({
            type:       "join",
            token:      sessionToken,
            public_key: myPublicKeyJwk,
            room_id:    currentRoomId,
        }));
    };

    ws.onmessage = (event) => {
        try { handleMessage(JSON.parse(event.data)); }
        catch (err) { console.error("[WS] parse error:", err); }
    };

    ws.onclose = () => {
        updateConnectionStatus("disconnected");
        if (loginScreen && !loginScreen.classList.contains("hidden")) return;
        if (lobbyScreen && !lobbyScreen.classList.contains("hidden")) return;
        if (!isIntentionalClose && currentUsername) scheduleReconnect();
    };

    ws.onerror = (e) => console.error("[WS] error:", e);
}

function scheduleReconnect() {
    if (reconnectTimer) clearTimeout(reconnectTimer);
    const delay = Math.min(RECONNECT_BASE_DELAY * Math.pow(2, reconnectAttempts), RECONNECT_MAX_DELAY);
    reconnectAttempts++;
    updateConnectionStatus("reconnecting");
    reconnectTimer = setTimeout(connect, delay);
}

function disconnect() {
    isIntentionalClose = true;
    if (reconnectTimer) clearTimeout(reconnectTimer);
    if (ws) ws.close();
    pendingReceipts.clear();
}

// ── Message Handler ───────────────────────────────────────────────────────────

function handleMessage(data) {
    switch (data.type) {
        case "system":
            if (!isJoined && data.message && data.message.includes("Welcome")) {
                isJoined = true;
                // Update room info from server welcome message
                if (data.room) {
                    currentRoomId      = data.room.id;
                    currentRoomName    = data.room.name;
                    currentRoomAvatar  = data.room.avatar || "🏰";
                    currentRoomCreator = data.room.created_by || "";
                }
                showChatScreen();
                setCryptoStatusUI(cryptoReady);
            }
            addSystemMessage(data.message, data.timestamp);
            break;

        case "room_history_cleared":
            messagesScroll.innerHTML = "";
            addSystemMessage(`--- CHAT HISTORY CLEARED BY CREATOR (@${data.username}) ---`, data.timestamp);
            break;

        case "room_deleted":
            showInAppAlert("ROOM DELETED", `Room #${currentRoomName} was permanently deleted by creator @${data.username}.`, () => {
                leaveChat();
            });
            break;

        case "join":
            addSystemMessage(data.message, data.timestamp, "join");
            playSound("join");
            // XP for others joining is now awarded server-side via xp_update
            break;

        case "leave":
            addSystemMessage(data.message, data.timestamp, "leave");
            hideTypingIndicator(data.username);
            playSound("leave");
            break;

        case "message":
            if (data.username === currentUsername && !data.target_user) break;
            // For whispers sent by us, we already have optimistic UI
            if (data.username === currentUsername && data.target_user) break;
            handleIncomingEncryptedMessage(data);
            hideTypingIndicator(data.username);
            playSound("message");
            if (!isTabFocused) playNotificationSound();
            break;

        case "message_deleted":
            handleMessageDeleted(data.msg_id, data.username);
            break;

        case "message_edited":
            handleIncomingEditedMessage(data);
            break;

        case "receipt":
            updateReceipt(data.msg_id, data.status);
            break;

        case "userList":
            updateUserList(data.users);
            break;

        case "typing":
            showTypingIndicator(data.username);
            break;

        case "history":
            renderHistory(data.messages);
            break;

        case "xp_update":
            handleXpUpdate(data.xp, data.gained, data.reason);
            break;

        case "error":
            if (!isJoined) {
                // If we're in the lobby trying to join, show error there
                if (lobbyError && lobbyScreen && !lobbyScreen.classList.contains("hidden")) {
                    lobbyError.textContent = "! " + (data.message || "Connection error").toUpperCase();
                    createRoomBtn.disabled = false;
                    createRoomBtnText.textContent = "✦ CREATE ROOM";
                    joinCodeBtn.disabled = false;
                    joinCodeBtnText.textContent = "⌖ JOIN ROOM";
                } else {
                    showLoginError(data.message);
                }
            }
            break;


        default:
            console.warn("[WS] unknown type:", data.type);
    }
}

/**
 * Decrypt and verify an incoming encrypted message, then render it.
 */
async function handleIncomingEncryptedMessage(data) {
    const { username, avatar, ciphertext, iv, signature, public_key, timestamp,
            sig_valid: serverSigValid, tampered, attachment,
            msg_id, reply_to, is_deleted, target_user } = data;

    // Deleted messages render as tombstones
    if (is_deleted) {
        addDeletedMessage(username, timestamp, avatar, msg_id);
        return;
    }

    // Decrypt
    const plaintext = await decryptMessage(ciphertext, iv);
    if (plaintext === null) {
        addChatMessage(username, "[⚠ DECRYPTION FAILED — MESSAGE TAMPERED]", timestamp, avatar,
            null, false, true, msg_id, null, target_user);
        return;
    }

    let finalAttachment = attachment;
    let text = plaintext;
    if (plaintext && (plaintext.startsWith('{"type":"voice_memo"') || plaintext.startsWith('{"type": "voice_memo"'))) {
        try {
            const voiceObj = JSON.parse(plaintext);
            if (voiceObj && voiceObj.audio) {
                finalAttachment = { type: "voice_memo", audio: voiceObj.audio };
                text = "";
            }
        } catch (e) {}
    } else if (plaintext && (plaintext.startsWith('{"type":"encrypted_file"') || plaintext.startsWith('{"type": "encrypted_file"'))) {
        try {
            const fileObj = JSON.parse(plaintext);
            if (fileObj && fileObj.data) {
                finalAttachment = {
                    type: "encrypted_file",
                    fileName: fileObj.fileName,
                    fileType: fileObj.fileType,
                    fileSize: fileObj.fileSize,
                    data: fileObj.data,
                };
                text = "";
            }
        } catch (e) {}
    }

    // Cache for reply lookups and editing
    const createdTs = data.created_at_ts || (Date.now() / 1000);
    if (msg_id) messageCache.set(msg_id, { username, text, created_at_ts: createdTs, is_edited: data.is_edited, ciphertext, iv });

    // Client-side ECDSA verification
    const material  = ciphertext + iv;
    const sigValid  = public_key ? await verifySignature(material, signature, public_key) : false;

    addChatMessage(username, text, timestamp, avatar, finalAttachment, sigValid,
        tampered === true, msg_id, reply_to, target_user, data.is_edited === true, createdTs);
}

// ── Render ────────────────────────────────────────────────────────────────────

function addSystemMessage(text, time, subtype = "") {
    const el = document.createElement("div");
    el.className = `message system ${subtype ? subtype + "-msg" : ""}`;
    el.innerHTML = `
        <div class="message-bubble">
            <p class="message-text">${escapeHtml(text)}</p>
            ${time ? `<span class="message-time">${escapeHtml(time)}</span>` : ""}
        </div>`;
    messagesScroll.appendChild(el);
    scrollToBottom();
}

/**
 * Parse @mentions in text: wraps @username in <span class="mention-tag">
 * Returns { html, mentionsMe } where mentionsMe is true if @currentUsername found.
 */
function parseMentions(escapedText) {
    let mentionsMe = false;
    const html = escapedText.replace(/@(\w+)/g, (match, name) => {
        if (name.toLowerCase() === currentUsername.toLowerCase()) mentionsMe = true;
        return `<span class="mention-tag">${match}</span>`;
    });
    return { html, mentionsMe };
}

/**
 * Render a chat message bubble.
 * sigValid   — true = ECDSA verified, false = invalid/unknown
 * tampered   — true = server flagged HMAC mismatch (DB was modified)
 */
function addChatMessage(username, text, time, avatarId = "wizard", attachment = null,
                        sigValid = true, tampered = false, msgId = null, replyTo = null,
                        targetUser = null, isEdited = false, createdAtTs = null) {
    const isOwn      = username === currentUsername;
    const avatarData = getAvatarData(avatarId, username);
    const el         = document.createElement("div");

    // Build class list
    let classes = `message ${isOwn ? "own" : "other"}`;
    if (targetUser) classes += " whisper";

    // Parse mentions
    let textHtml = "";
    let mentionsMe = false;
    if (text) {
        const parsed = parseMentions(escapeHtml(text));
        textHtml = parsed.html;
        mentionsMe = parsed.mentionsMe;
    }
    if (mentionsMe && !isOwn) classes += " mention-highlight";

    el.className = classes;
    if (msgId) el.dataset.msgId = msgId;

    const avatarHtml = `
        <div class="chat-msg-avatar" style="background:${avatarData.bg}; border-color:${avatarData.border}" title="${escapeHtml(avatarData.name)}">
            <span>${avatarData.icon}</span>
        </div>`;

    // Security badges
    let securityBadge = "";
    if (tampered) {
        securityBadge = `<span class="sec-badge tampered" title="Database tamper detected!">🚨 TAMPERED</span>`;
    } else if (sigValid) {
        securityBadge = `<span class="sec-badge verified" title="ECDSA signature verified">🔒 ✓</span>`;
    } else {
        securityBadge = `<span class="sec-badge invalid" title="Signature invalid or missing">⚠ SIG?</span>`;
    }

    // Whisper label
    let whisperHtml = "";
    if (targetUser) {
        const label = isOwn ? `WHISPER TO @${escapeHtml(targetUser).toUpperCase()}` : `WHISPER FROM @${escapeHtml(username).toUpperCase()}`;
        whisperHtml = `<div class="whisper-label">🔮 ${label}</div>`;
    }

    // Reply context (show parent message preview)
    let replyContextHtml = "";
    if (replyTo) {
        const parent = messageCache.get(replyTo);
        const parentUser = parent ? parent.username : "...";
        const parentText = parent ? parent.text.substring(0, 80) : "...";
        replyContextHtml = `
            <div class="reply-context" data-reply-to="${escapeHtml(replyTo)}" title="Click to scroll to original">
                <span class="reply-context-user">↳ ${escapeHtml(parentUser)}</span>
                <span class="reply-context-text">${escapeHtml(parentText)}</span>
            </div>`;
    }

    let attachmentHtml = buildAttachmentHtml(attachment);

    const nowTs = Date.now() / 1000;
    const msgAge = createdAtTs ? (nowTs - createdAtTs) : 0;
    const canEdit = isOwn && (msgAge <= 300);

    // Message action buttons (reply for everyone, delete/edit for own)
    let actionsHtml = `
        <div class="msg-actions">
            <button class="msg-action-btn reply-btn" title="Reply" data-msg-id="${escapeHtml(msgId || '')}" data-msg-user="${escapeHtml(username)}" data-msg-text="${escapeHtml(text || '')}">↩</button>
            ${canEdit ? `<button class="msg-action-btn edit-btn" title="Edit message (5 min)" data-msg-id="${escapeHtml(msgId || '')}">✏️</button>` : ""}
            ${isOwn ? `<button class="msg-action-btn delete-btn" title="Delete" data-msg-id="${escapeHtml(msgId || '')}">🗑</button>` : ""}
        </div>`;

    el.innerHTML = `
        ${!isOwn ? avatarHtml : ""}
        <div class="message-bubble ${tampered ? "tampered-bubble" : ""}">
            ${actionsHtml}
            ${whisperHtml}
            ${replyContextHtml}
            <div class="message-meta">
                <span class="message-username">${escapeHtml(username)}</span>
                ${time ? `<span class="message-time">${escapeHtml(time)}</span>` : ""}
                ${isEdited ? `<span class="edited-tag" title="This message was edited">(edited)</span>` : ""}
                ${securityBadge}
            </div>
            ${text ? `<p class="message-text">${textHtml}</p>` : ""}
            ${attachmentHtml}
        </div>
        ${isOwn ? avatarHtml : ""}
    `;
    messagesScroll.appendChild(el);
    scrollToBottom();

    // Play mention ping
    if (mentionsMe && !isOwn) playMentionSound();
}

function addOwnMessageOptimistic(text, msgId, attachment = null, replyTo = null, targetUser = null) {
    const avatarData = getAvatarData(selectedAvatar, currentUsername);
    const el = document.createElement("div");

    let classes = "message own";
    if (targetUser) classes += " whisper";
    el.className = classes;
    if (msgId) el.dataset.msgId = msgId;

    const avatarHtml = `
        <div class="chat-msg-avatar" style="background:${avatarData.bg}; border-color:${avatarData.border}" title="${escapeHtml(avatarData.name)}">
            <span>${avatarData.icon}</span>
        </div>`;

    // Whisper label
    let whisperHtml = "";
    if (targetUser) {
        whisperHtml = `<div class="whisper-label">🔮 WHISPER TO @${escapeHtml(targetUser).toUpperCase()}</div>`;
    }

    // Reply context
    let replyContextHtml = "";
    if (replyTo) {
        const parent = messageCache.get(replyTo);
        const parentUser = parent ? parent.username : "...";
        const parentText = parent ? parent.text.substring(0, 80) : "...";
        replyContextHtml = `
            <div class="reply-context" data-reply-to="${escapeHtml(replyTo)}">
                <span class="reply-context-user">↳ ${escapeHtml(parentUser)}</span>
                <span class="reply-context-text">${escapeHtml(parentText)}</span>
            </div>`;
    }

    // Parse mentions in own message
    let textHtml = "";
    if (text) {
        const parsed = parseMentions(escapeHtml(text));
        textHtml = parsed.html;
    }

    // Action buttons for own message
    let actionsHtml = `
        <div class="msg-actions">
            <button class="msg-action-btn reply-btn" title="Reply" data-msg-id="${escapeHtml(msgId || '')}" data-msg-user="${escapeHtml(currentUsername)}" data-msg-text="${escapeHtml(text || '')}">↩</button>
            <button class="msg-action-btn edit-btn" title="Edit message (5 min)" data-msg-id="${escapeHtml(msgId || '')}">✏️</button>
            <button class="msg-action-btn delete-btn" title="Delete" data-msg-id="${escapeHtml(msgId || '')}">🗑</button>
        </div>`;

    const receiptId = `receipt-${msgId}`;
    el.innerHTML = `
        <div class="message-bubble">
            ${actionsHtml}
            ${whisperHtml}
            ${replyContextHtml}
            <div class="message-meta">
                <span class="message-username">${escapeHtml(currentUsername)}</span>
                <span class="message-time">${currentTime()}</span>
                <span class="sec-badge verified" title="Sent encrypted & signed">🔒 ✓</span>
            </div>
            ${text ? `<p class="message-text">${textHtml}</p>` : ""}
            ${buildAttachmentHtml(attachment)}
            <span class="receipt-icon" id="${receiptId}" title="Sent">😴</span>
        </div>
        ${avatarHtml}
    `;
    messagesScroll.appendChild(el);
    scrollToBottom();
    pendingReceipts.set(msgId, document.getElementById(receiptId));

    // Cache own message
    if (msgId) {
        messageCache.set(msgId, {
            username: currentUsername,
            text: text,
            created_at_ts: Date.now() / 1000,
            is_edited: false
        });
    }
}

function buildAttachmentHtml(attachment) {
    if (!attachment) return "";
    if (attachment.type === "voice_memo" && attachment.audio) {
        const audioUrl = escapeHtml(attachment.audio);
        return `<div class="chat-attachment chat-attachment-voice"><audio controls src="${audioUrl}"></audio></div>`;
    }
    if (attachment.type === "encrypted_file" && attachment.data) {
        const fileName = escapeHtml(attachment.fileName || "file");
        const fileType = (attachment.fileType || "").toLowerCase();
        const fileSize = formatBytes(attachment.fileSize || 0);

        if (fileType.startsWith("image/")) {
            return `<div class="chat-attachment chat-attachment-image"><a href="${attachment.data}" target="_blank" download="${fileName}"><img src="${attachment.data}" alt="${fileName}"></a></div>`;
        } else if (fileType.startsWith("video/")) {
            return `<div class="chat-attachment chat-attachment-video"><video controls src="${attachment.data}"></video></div>`;
        } else if (fileType.startsWith("audio/")) {
            return `<div class="chat-attachment chat-attachment-audio"><audio controls src="${attachment.data}"></audio></div>`;
        } else {
            const icon = getFileIcon(fileType, fileName);
            return `<div class="chat-attachment"><a href="${attachment.data}" download="${fileName}" class="chat-attachment-file"><span class="file-card-icon">${icon}</span><div class="file-card-details"><span class="file-card-name">${fileName}</span><span class="file-card-size">${fileSize}</span></div><span class="file-card-dl-btn">💾 DOWNLOAD</span></a></div>`;
        }
    }
    if (!attachment.url) return "";
    const fileUrl  = escapeHtml(attachment.url);
    const fileName = escapeHtml(attachment.fileName || "attachment");
    const fileType = (attachment.fileType || "").toLowerCase();
    const fileSize = formatBytes(attachment.fileSize || 0);

    if (fileType.startsWith("image/")) {
        return `<div class="chat-attachment chat-attachment-image"><a href="${fileUrl}" target="_blank"><img src="${fileUrl}" alt="${fileName}"></a></div>`;
    } else if (fileType.startsWith("video/")) {
        return `<div class="chat-attachment chat-attachment-video"><video controls src="${fileUrl}"></video></div>`;
    } else if (fileType.startsWith("audio/")) {
        return `<div class="chat-attachment chat-attachment-audio"><audio controls src="${fileUrl}"></audio></div>`;
    } else {
        const icon = getFileIcon(fileType, fileName);
        return `<div class="chat-attachment"><a href="${fileUrl}" download="${fileName}" class="chat-attachment-file" target="_blank">
            <span class="file-card-icon">${icon}</span>
            <div class="file-card-details"><span class="file-card-name">${fileName}</span><span class="file-card-size">${fileSize}</span></div>
            <span class="file-card-dl-btn">💾 DOWNLOAD</span></a></div>`;
    }
}

function updateReceipt(msgId, status) {
    const el = pendingReceipts.get(msgId);
    if (!el) return;
    if (status === "partial") {
        el.textContent = "😃"; el.title = "Delivered to some"; el.classList.add("receipt-partial");
    } else if (status === "delivered_all") {
        el.textContent = "😎"; el.title = "Delivered to all"; el.classList.add("receipt-delivered");
    }
    pendingReceipts.delete(msgId);
}

function updateUserList(users) {
    userList.innerHTML = "";
    const count = users.length;
    if (headerSubtitle) headerSubtitle.textContent = `${count} PLAYER${count !== 1 ? "S" : ""} ONLINE`;
    if (onlineBadge)    onlineBadge.textContent = count;

    users.forEach(item => {
        const username  = typeof item === "object" ? item.username : item;
        const avatarId  = typeof item === "object" ? item.avatar   : "wizard";
        const avatarData = getAvatarData(avatarId, username);
        const li = document.createElement("li");
        const isYou = username === currentUsername;
        if (isYou) li.classList.add("is-you");
        li.innerHTML = `
            <div class="user-avatar" style="background:${avatarData.bg}; border-color:${avatarData.border}">${avatarData.icon}</div>
            <span class="user-name">${escapeHtml(username)}</span>
            ${isYou ? '<span class="user-you-tag">YOU</span>' : ""}`;
        userList.appendChild(li);
    });
}

function updateConnectionStatus(status) {
    if (!statusDot || !statusText) return;
    statusDot.className = "status-dot " + status;
    const labels = { connected:"ONLINE", disconnected:"OFFLINE", reconnecting:"WAIT..." };
    statusText.textContent = labels[status] || status.toUpperCase();
}

let loginErrorTimer = null;

function showLoginError(message) {
    if (loginErrorTimer) {
        clearTimeout(loginErrorTimer);
        loginErrorTimer = null;
    }

    if (loginError) {
        loginError.textContent = "! " + message.toUpperCase();
    }

    // Re-enable auth buttons & restore button text
    if (registerBtn) { registerBtn.disabled = false; if (registerBtnText) registerBtnText.textContent = "► CREATE ACCOUNT"; }
    if (loginBtn)    { loginBtn.disabled = false;    if (loginBtnText)    loginBtnText.textContent    = "► LOGIN"; }
    if (typeof joinBtn !== 'undefined' && joinBtn) { joinBtn.disabled = false; if (typeof joinBtnText !== 'undefined' && joinBtnText) joinBtnText.textContent = "► START QUEST"; }

    // Auto-revert error message after 5 seconds
    loginErrorTimer = setTimeout(() => {
        if (loginError) loginError.textContent = "";
        loginErrorTimer = null;
    }, 5000);
}


function saveSessionState() {
    const activeScr = sessionStorage.getItem("pixel_active_screen") || "lobby";

    if (sessionToken) sessionStorage.setItem("pixel_session_token", sessionToken);
    if (currentUsername) sessionStorage.setItem("pixel_username", currentUsername);
    if (selectedAvatar) sessionStorage.setItem("pixel_avatar", selectedAvatar);
    if (currentRoomId) sessionStorage.setItem("pixel_room_id", currentRoomId);
    else sessionStorage.removeItem("pixel_room_id");
    if (currentRoomName) sessionStorage.setItem("pixel_room_name", currentRoomName);
    else sessionStorage.removeItem("pixel_room_name");

    if (sessionToken && currentUsername) {
        const sessionObj = {
            token: sessionToken,
            username: currentUsername,
            avatar: selectedAvatar,
            activeScreen: activeScr,
            roomId: currentRoomId,
            roomName: currentRoomName,
            screenX: window.screenX,
            screenY: window.screenY,
            outerWidth: window.outerWidth,
            outerHeight: window.outerHeight,
            timestamp: Date.now(),
        };
        localStorage.setItem("pixel_active_session", JSON.stringify(sessionObj));
    }
}

function clearSavedSession() {
    sessionStorage.clear();
    localStorage.removeItem("pixel_active_session");
}

function showChatScreen() {
    loginScreen.classList.add("hidden");
    lobbyScreen.classList.add("hidden");
    chatScreen.classList.remove("hidden");
    sessionStorage.setItem("pixel_active_screen", "chat");
    saveSessionState();
    // Update room info in header and sidebar
    const hdrRoomName  = document.getElementById("hdr-room-name");
    const hdrRoomCode  = document.getElementById("hdr-room-code");
    const hdrSprite    = document.querySelector(".hdr-sprite");
    const badgeCode    = document.getElementById("room-badge-code");
    const badgeName    = document.getElementById("room-badge-name");
    if (hdrRoomName) hdrRoomName.textContent = `# ${currentRoomName.toUpperCase()}`;
    if (hdrRoomCode) hdrRoomCode.textContent = currentRoomId || "";
    if (hdrSprite)   hdrSprite.textContent   = currentRoomAvatar;
    if (badgeCode)   badgeCode.textContent   = currentRoomId || "------";
    if (badgeName)   badgeName.textContent   = currentRoomAvatar + " " + (currentRoomName || "ROOM");
    updateOwnerActionsVisibility();
    messageInput.focus();
    startSessionTimer();
    playSound("join");
}

function updateOwnerActionsVisibility() {
    const ownerWrap = document.getElementById("owner-actions-wrap");
    if (ownerWrap) {
        if (currentUsername && currentRoomCreator && currentUsername.toLowerCase() === currentRoomCreator.toLowerCase()) {
            ownerWrap.classList.remove("hidden");
        } else {
            ownerWrap.classList.add("hidden");
        }
    }
}

function scrollToBottom() {
    messagesScroll.scrollTop = messagesScroll.scrollHeight;
}

async function renderHistory(messages) {
    if (!messages || !messages.length) return;

    const sep = document.createElement("div");
    sep.className = "message system";
    sep.innerHTML = `<div class="message-bubble"><p class="message-text">--- PREV MESSAGES ---</p></div>`;
    messagesScroll.appendChild(sep);

    for (const msg of messages) {
        if (msg.type === "message") {
            // Deleted messages render as tombstones
            if (msg.is_deleted) {
                addDeletedMessage(msg.username, msg.timestamp, msg.avatar, msg.msg_id);
                continue;
            }
            // Decrypt history messages
            const plaintext = await decryptMessage(msg.ciphertext, msg.iv);
            if (plaintext === null) {
                addChatMessage(msg.username, "[⚠ DECRYPTION FAILED — MESSAGE TAMPERED]",
                    msg.timestamp, msg.avatar, null, false, true, msg.msg_id, null, msg.target_user);
                continue;
            }

            let finalAttachment = msg.attachment;
            let text = plaintext;
            if (plaintext && (plaintext.startsWith('{"type":"voice_memo"') || plaintext.startsWith('{"type": "voice_memo"'))) {
                try {
                    const voiceObj = JSON.parse(plaintext);
                    if (voiceObj && voiceObj.audio) {
                        finalAttachment = { type: "voice_memo", audio: voiceObj.audio };
                        text = "";
                    }
                } catch (e) {}
            } else if (plaintext && (plaintext.startsWith('{"type":"encrypted_file"') || plaintext.startsWith('{"type": "encrypted_file"'))) {
                try {
                    const fileObj = JSON.parse(plaintext);
                    if (fileObj && fileObj.data) {
                        finalAttachment = {
                            type: "encrypted_file",
                            fileName: fileObj.fileName,
                            fileType: fileObj.fileType,
                            fileSize: fileObj.fileSize,
                            data: fileObj.data,
                        };
                        text = "";
                    }
                } catch (e) {}
            }

            // Cache for reply lookups and editing
            if (msg.msg_id) {
                messageCache.set(msg.msg_id, {
                    username: msg.username,
                    text: text,
                    created_at_ts: msg.created_at_ts,
                    is_edited: msg.is_edited,
                    ciphertext: msg.ciphertext,
                    iv: msg.iv
                });
            }

            const material = msg.ciphertext + msg.iv;
            const sigValid  = msg.public_key
                ? await verifySignature(material, msg.signature, msg.public_key)
                : false;
            addChatMessage(msg.username, text, msg.timestamp, msg.avatar,
                finalAttachment, sigValid, msg.tampered === true,
                msg.msg_id, msg.reply_to, msg.target_user, msg.is_edited === true, msg.created_at_ts);
        }
    }

    const sep2 = document.createElement("div");
    sep2.className = "message system";
    sep2.innerHTML = `<div class="message-bubble"><p class="message-text">--- NEW MESSAGES ---</p></div>`;
    messagesScroll.appendChild(sep2);
    scrollToBottom();
}

// ── Typing ────────────────────────────────────────────────────────────────────

function showTypingIndicator(username) {
    typingTextEl.textContent = `${username.toUpperCase()} TYPING`;
    typingIndicator.classList.add("active");
    if (typingTimeout) clearTimeout(typingTimeout);
    typingTimeout = setTimeout(() => typingIndicator.classList.remove("active"), 3000);
}

function hideTypingIndicator(username) {
    if (typingTextEl.textContent.includes(username.toUpperCase())) {
        typingIndicator.classList.remove("active");
        if (typingTimeout) clearTimeout(typingTimeout);
    }
}

function sendTypingEvent() {
    const now = Date.now();
    if (now - lastTypingSent < 2000) return;
    lastTypingSent = now;
    if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: "typing" }));
    }
}

// ── Gamification ──────────────────────────────────────────────────────────────

function gainXP(amount) { totalXP += amount; updateXPBar(); checkLevelUp(); }

// Set absolute XP from server (used on login and xp_update)
function setXP(newTotal) {
    totalXP = newTotal;
    currentLevel = getCurrentRank().level;
    updateXPBar();
}

function handleXpUpdate(newTotal, gained, reason) {
    totalXP = newTotal;
    updateXPBar();
    checkLevelUp();
    if (gained > 0) showXpGainToast(gained, reason);
}

function updateXPBar() {
    const rank      = getCurrentRank();
    const xpInLevel = totalXP - rank.xp;
    const xpNeeded  = rank.next - rank.xp;
    const pct       = rank.level === 6 ? 100 : Math.min(100, (xpInLevel / xpNeeded) * 100);
    if (xpBarFill) xpBarFill.style.width = `${pct.toFixed(1)}%`;
    if (xpValue)   xpValue.textContent   = `${totalXP} / ${rank.next}`;
    if (rankNameEl) rankNameEl.textContent = `${rank.emoji} ${rank.name}`;
}

function getCurrentRank() {
    let current = RANKS[0];
    for (const r of RANKS) { if (totalXP >= r.xp) current = r; else break; }
    return current;
}

function checkLevelUp() {
    const rank = getCurrentRank();
    if (rank.level > currentLevel) {
        currentLevel = rank.level;
        if (levelupSub) levelupSub.textContent = `${rank.emoji} NEW RANK: ${rank.name}`;
        levelupToast.classList.add("show");
        playLevelUpSound();
        setTimeout(() => levelupToast.classList.remove("show"), 3500);
    }
}

function incrementStreak()      { streakCount++; }
function incrementMessageCount(){ messagesSent++; incrementStreak(); }

function startSessionTimer() {
    sessionStart = Date.now();
    sessionTimer = setInterval(() => {
        const min = Math.floor((Date.now() - sessionStart) / 60000);
        // Send heartbeat — server awards +5 XP/min and sends back xp_update
        if (ws && ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({ type: "heartbeat" }));
        }
    }, 60000);
}

// ── XP Gain Toast ─────────────────────────────────────────────────────────────
function showXpGainToast(amount, reason) {
    const container = document.getElementById("xp-toast-container");
    if (!container) return;
    const toast = document.createElement("div");
    toast.className = "xp-gain-toast";
    const isStreak = reason && (reason.includes("streak") || reason.includes("🔥"));
    toast.textContent = isStreak ? `+${amount} XP 🔥` : `+${amount} XP`;
    if (isStreak) toast.classList.add("streak");
    container.appendChild(toast);
    requestAnimationFrame(() => {
        requestAnimationFrame(() => { toast.classList.add("show"); });
    });
    setTimeout(() => {
        toast.classList.remove("show");
        setTimeout(() => toast.remove(), 400);
    }, 1800);
}

// ── Utils ─────────────────────────────────────────────────────────────────────

function escapeHtml(text) {
    const d = document.createElement("div");
    d.textContent = text;
    return d.innerHTML;
}

function getAvatarColor(username) {
    let hash = 0;
    for (let i = 0; i < username.length; i++) hash = username.charCodeAt(i) + ((hash << 5) - hash);
    return AVATAR_COLORS[Math.abs(hash) % AVATAR_COLORS.length];
}

function currentTime() { return new Date().toLocaleTimeString("en-GB"); }

function formatBytes(bytes) {
    if (bytes === 0) return "0 B";
    const k = 1024, sizes = ["B","KB","MB","GB"];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return `${parseFloat((bytes / Math.pow(k, i)).toFixed(1))} ${sizes[i]}`;
}

function getFileIcon(fileType, fileName) {
    if (fileType.includes("pdf"))                                                   return "📄";
    if (fileType.includes("word") || fileName.endsWith(".docx"))                   return "📝";
    if (fileType.includes("zip") || fileType.includes("rar"))                      return "🗜️";
    if (fileType.includes("text") || fileName.endsWith(".txt"))                    return "🗒️";
    if (fileType.includes("spreadsheet") || fileName.endsWith(".xlsx"))            return "📊";
    return "📁";
}

// ── Send Message (encrypt + sign) ────────────────────────────────────────────

async function sendMessage() {
    let text         = messageInput.value.trim();
    const hasAttachment = attachmentData !== null;
    if (!text && !hasAttachment) return;
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    if (!cryptoReady) { console.error("[Send] Crypto not ready"); return; }

    const msgId = crypto.randomUUID ? crypto.randomUUID() : `msg-${Date.now()}-${Math.random().toString(36).slice(2, 9)}`;

    // Parse whisper command: /w @username message
    let targetUser = null;
    const whisperMatch = text.match(/^\/w\s+@(\w+)\s+(.+)$/is);
    if (whisperMatch) {
        targetUser = whisperMatch[1];
        text       = whisperMatch[2];
    }

    // Cache own message for reply lookups
    messageCache.set(msgId, { username: currentUsername, text });

    // 1. Encrypt
    const { ciphertext, iv } = await encryptMessage(text);

    // 2. Sign (over ciphertext + iv — same material the server verifies)
    const material  = ciphertext + iv;
    const signature = await signMaterial(material);

    // 3. Send
    ws.send(JSON.stringify({
        type:         "message",
        ciphertext,
        iv,
        signature,
        public_key:   myPublicKeyJwk,
        client_msg_id: msgId,
        attachment:   attachmentData || null,
        reply_to:    replyToMsgId || null,
        target_user: targetUser,
    }));

    playSound("message");
    const attachmentSnapshot = attachmentData;
    addOwnMessageOptimistic(text, msgId, attachmentSnapshot, replyToMsgId, targetUser);
    messageInput.value = "";
    clearPendingAttachment();
    clearReply();
    messageInput.focus();
    emojiPicker.classList.remove("open");
    emojiToggleBtn.classList.remove("active");
    incrementMessageCount();
}

// ── Audio ─────────────────────────────────────────────────────────────────────

function playNotificationSound() {
    try {
        const ctx = new (window.AudioContext || window.webkitAudioContext)();
        const osc = ctx.createOscillator();
        const gain = ctx.createGain();
        osc.connect(gain); gain.connect(ctx.destination);
        osc.type = "square"; osc.frequency.value = 440;
        gain.gain.setValueAtTime(0.1, ctx.currentTime);
        gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.3);
        osc.start(ctx.currentTime); osc.stop(ctx.currentTime + 0.3);
    } catch (e) {}
}

function playLevelUpSound() {
    try {
        const ctx = new (window.AudioContext || window.webkitAudioContext)();
        [262,330,392,523].forEach((freq, i) => {
            const osc = ctx.createOscillator(), gain = ctx.createGain();
            osc.connect(gain); gain.connect(ctx.destination);
            osc.type = "square"; osc.frequency.value = freq;
            const t = ctx.currentTime + i * 0.12;
            gain.gain.setValueAtTime(0.12, t);
            gain.gain.exponentialRampToValueAtTime(0.001, t + 0.12);
            osc.start(t); osc.stop(t + 0.12);
        });
    } catch (e) {}
}

function playMentionSound() {
    try {
        const ctx = new (window.AudioContext || window.webkitAudioContext)();
        [660, 880, 1100].forEach((freq, i) => {
            const osc = ctx.createOscillator(), gain = ctx.createGain();
            osc.connect(gain); gain.connect(ctx.destination);
            osc.type = "square"; osc.frequency.value = freq;
            const t = ctx.currentTime + i * 0.08;
            gain.gain.setValueAtTime(0.08, t);
            gain.gain.exponentialRampToValueAtTime(0.001, t + 0.1);
            osc.start(t); osc.stop(t + 0.1);
        });
    } catch (e) {}
}

// ── Delete / Unsend ───────────────────────────────────────────────────────────

function handleMessageDeleted(msgId, deletedBy) {
    // Find the message DOM element
    const el = messagesScroll.querySelector(`[data-msg-id="${CSS.escape(msgId)}"]`);
    if (el) {
        el.classList.remove("whisper", "mention-highlight");
        el.classList.add("deleted");
        const bubble = el.querySelector(".message-bubble");
        if (bubble) {
            bubble.innerHTML = `
                <div class="deleted-tombstone">
                    <span class="deleted-tombstone-icon">🚫</span>
                    <span>This message was deleted</span>
                </div>`;
        }
    }
}

function addDeletedMessage(username, time, avatarId, msgId) {
    const isOwn = username === currentUsername;
    const avatarData = getAvatarData(avatarId, username);
    const el = document.createElement("div");
    el.className = `message ${isOwn ? "own" : "other"} deleted`;
    if (msgId) el.dataset.msgId = msgId;

    const avatarHtml = `
        <div class="chat-msg-avatar" style="background:${avatarData.bg}; border-color:${avatarData.border}" title="${escapeHtml(avatarData.name)}">
            <span>${avatarData.icon}</span>
        </div>`;

    el.innerHTML = `
        ${!isOwn ? avatarHtml : ""}
        <div class="message-bubble">
            <div class="message-meta">
                <span class="message-username">${escapeHtml(username)}</span>
                ${time ? `<span class="message-time">${escapeHtml(time)}</span>` : ""}
            </div>
            <div class="deleted-tombstone">
                <span class="deleted-tombstone-icon">🚫</span>
                <span>This message was deleted</span>
            </div>
        </div>
        ${isOwn ? avatarHtml : ""}
    `;
    messagesScroll.appendChild(el);
    scrollToBottom();
}

// ── Reply system ──────────────────────────────────────────────────────────────

function setReply(msgId, username, text) {
    replyToMsgId   = msgId;
    replyToUsername = username;
    replyToText    = text;
    if (replyPreviewUsername) replyPreviewUsername.textContent = username.toUpperCase();
    if (replyPreviewText)     replyPreviewText.textContent = text.substring(0, 100);
    if (replyPreviewBar)      replyPreviewBar.classList.remove("hidden");
    messageInput.focus();
}

// ── Voice Memo ────────────────────────────────────────────────────────────────

async function startVoiceRecording() {
    try {
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        audioChunks = [];
        mediaRecorder = new MediaRecorder(stream, { mimeType: getMimeType() });

        mediaRecorder.ondataavailable = (e) => {
            if (e.data.size > 0) audioChunks.push(e.data);
        };

        mediaRecorder.onstop = async () => {
            stream.getTracks().forEach(t => t.stop());
            clearInterval(recordingTimer);
            isRecording = false;
            voiceMemoBtn.classList.remove("recording");
            voiceMemoBtn.textContent = "🎤";

            if (audioChunks.length === 0) return;

            const mime = mediaRecorder.mimeType || getMimeType();
            const blob = new Blob(audioChunks, { type: mime });

            const reader = new FileReader();
            reader.readAsDataURL(blob);
            reader.onloadend = async () => {
                const audioDataUrl = reader.result;
                const voicePayload = JSON.stringify({ type: "voice_memo", mime: mime, audio: audioDataUrl });
                const msgId = crypto.randomUUID ? crypto.randomUUID() : `msg-${Date.now()}-${Math.random().toString(36).slice(2, 9)}`;

                // 1. Encrypt voice payload with AES-GCM
                const { ciphertext, iv } = await encryptMessage(voicePayload);

                // 2. Sign over material (ciphertext + iv)
                const material  = ciphertext + iv;
                const signature = await signMaterial(material);

                // 3. Send over WebSocket
                if (ws && ws.readyState === WebSocket.OPEN) {
                    ws.send(JSON.stringify({
                        type:          "message",
                        ciphertext:    ciphertext,
                        iv:            iv,
                        signature:     signature,
                        public_key:    myPublicKeyJwk,
                        client_msg_id: msgId,
                        attachment:    { type: "voice_memo" },
                        reply_to:      replyToMsgId || null,
                        target_user:   null,  // voice memos are always room broadcasts
                    }));

                    playSound("message");
                    addOwnMessageOptimistic("", msgId, { type: "voice_memo", audio: audioDataUrl }, replyToMsgId, null);
                    clearReply();
                    incrementMessageCount();
                }
            };
        };

        mediaRecorder.start();
        isRecording = true;
        recordingStart = Date.now();
        voiceMemoBtn.classList.add("recording");
        voiceMemoBtn.textContent = "⏹";

        // Auto-stop after 60 seconds
        recordingTimer = setTimeout(() => {
            if (isRecording && mediaRecorder && mediaRecorder.state === "recording") {
                mediaRecorder.stop();
            }
        }, 60000);

    } catch (err) {
        console.error("[VoiceMemo] Microphone access denied:", err);
        alert("Microphone access is required for voice memos.");
    }
}

function stopVoiceRecording() {
    if (mediaRecorder && mediaRecorder.state === "recording") {
        mediaRecorder.stop();
    }
}

function getMimeType() {
    if (MediaRecorder.isTypeSupported("audio/webm;codecs=opus")) return "audio/webm;codecs=opus";
    if (MediaRecorder.isTypeSupported("audio/webm")) return "audio/webm";
    if (MediaRecorder.isTypeSupported("audio/ogg;codecs=opus")) return "audio/ogg;codecs=opus";
    return "audio/webm";
}

// ── Event Listeners ───────────────────────────────────────────────────────────

async function authenticate(mode) {
    let username, password, btn, btnText, originalText, endpoint, payload;

    if (mode === "register") {
        username = regUsername.value.trim();
        password = regPassword.value;
        btn = registerBtn;
        btnText = registerBtnText;
        originalText = "► CREATE ACCOUNT";
        endpoint = "/register";
        payload = { username, password, avatar: selectedAvatar };
        if (!username) { showLoginError("ENTER A NAME!"); return; }
        if (password.length < 6) { showLoginError("PASSWORD TOO SHORT!"); return; }
    } else {
        username = loginUsername.value.trim();
        password = loginPassword.value;
        btn = loginBtn;
        btnText = loginBtnText;
        originalText = "► LOGIN";
        endpoint = "/login";
        payload = { username, password };
        if (!username || !password) { showLoginError("ENTER CREDENTIALS!"); return; }
    }

    loginError.textContent = "";
    btn.disabled = true;
    btnText.textContent = "AUTHENTICATING...";

    try {
        const res = await backendFetch(`${HTTP_PROTOCOL}//${BACKEND_HOST}${endpoint}`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload)
        });

        const data = await res.json();
        if (!res.ok) {
            throw new Error(data.detail || "Authentication failed");
        }

        sessionToken = data.token;
        currentUsername = data.username || username;
        selectedAvatar = data.avatar;
        // Initialize XP from server — persisted across sessions
        if (typeof data.xp === "number") {
            totalXP = data.xp;
            currentLevel = getCurrentRank().level;
        }
        sessionStorage.setItem("pixel_active_screen", "lobby");
        saveSessionState();

        btnText.textContent = "LOADING CRYPTO...";
        await initCrypto();

        btnText.textContent = "ENTERING LOBBY...";
        playSound("start");
        showLobbyScreen();
        btn.disabled = false;
        btnText.textContent = originalText;

    } catch (err) {
        showLoginError(err.message);
        btnText.textContent = originalText;
        btn.disabled = false;
    }
}

registerBtn.addEventListener("click", () => authenticate("register"));
loginBtn.addEventListener("click", () => authenticate("login"));

regUsername.addEventListener("keydown", (e) => { if (e.key === "Enter") regPassword.focus(); });
regPassword.addEventListener("keydown", (e) => { if (e.key === "Enter") registerBtn.click(); });
loginUsername.addEventListener("keydown", (e) => { if (e.key === "Enter") loginPassword.focus(); });
loginPassword.addEventListener("keydown", (e) => { if (e.key === "Enter") loginBtn.click(); });
sendBtn.addEventListener("click", sendMessage);

messageInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendMessage(); }
});

messageInput.addEventListener("input", () => {
    if (messageInput.value.trim().length > 0) sendTypingEvent();
});

emojiToggleBtn.addEventListener("click", (e) => {
    e.stopPropagation();
    emojiPicker.classList.toggle("open");
    emojiToggleBtn.classList.toggle("active");
});

document.getElementById("emoji-grid").addEventListener("click", (e) => {
    const target = e.target.closest(".emoji");
    if (target) { messageInput.value += target.textContent; messageInput.focus(); }
});

if (attachmentToggleBtn && fileInput) {
    attachmentToggleBtn.addEventListener("click", () => fileInput.click());

    fileInput.addEventListener("change", async () => {
        if (!fileInput.files || !fileInput.files[0]) return;
        pendingFile = fileInput.files[0];
        attachmentFilename.textContent = pendingFile.name;
        attachmentFilesize.textContent = "(UPLOADING...)";
        attachmentPreviewBar.classList.remove("hidden");
        attachmentData = null;

        try {
            const formData = new FormData();
            formData.append("file", pendingFile);
            const res = await backendFetch(UPLOAD_URL, { method: "POST", body: formData });
            if (!res.ok) throw new Error(`HTTP ${res.status}`);
            attachmentData = await res.json();
            if (attachmentData.url && attachmentData.url.startsWith("/")) {
                attachmentData.url = `${HTTP_PROTOCOL}//${BACKEND_HOST}${attachmentData.url}`;
            }
            attachmentFilesize.textContent = `(DONE - ${formatBytes(attachmentData.fileSize)})`;
        } catch (err) {
            console.error("[Upload] failed:", err);
            attachmentFilename.textContent = "UPLOAD FAILED";
            attachmentFilesize.textContent = "";
            attachmentData = null; pendingFile = null;
        }
    });

    if (attachmentRemoveBtn) {
        attachmentRemoveBtn.addEventListener("click", clearPendingAttachment);
    }
}

document.addEventListener("click", (e) => {
    if (!emojiPicker.contains(e.target) && e.target !== emojiToggleBtn) {
        emojiPicker.classList.remove("open");
        emojiToggleBtn.classList.remove("active");
    }
});

window.addEventListener("focus", () => { isTabFocused = true;  });
window.addEventListener("blur",  () => { isTabFocused = false; });

// ── Info / Feature Guide modal ────────────────────────────────────────────────
function openInfoModal() { infoModalOverlay.classList.remove("hidden"); }
function closeInfoModal() { infoModalOverlay.classList.add("hidden"); }

infoBtn.addEventListener("click", openInfoModal);
// Lobby help button (available before joining a room)
const lobbyHelpBtn = document.getElementById("lobby-help-btn");
if (lobbyHelpBtn) lobbyHelpBtn.addEventListener("click", openInfoModal);

infoModalClose.addEventListener("click", closeInfoModal);
infoModalOverlay.addEventListener("click", (e) => {
    if (e.target === infoModalOverlay) closeInfoModal();
});
document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
        closeInfoModal();
        clearReply();
    }
});

// ── Voice memo button ─────────────────────────────────────────────────────────
if (voiceMemoBtn) {
    voiceMemoBtn.addEventListener("click", () => {
        if (isRecording) {
            stopVoiceRecording();
        } else {
            startVoiceRecording();
        }
    });
}

// ── Reply preview close ───────────────────────────────────────────────────────
if (replyPreviewClose) {
    replyPreviewClose.addEventListener("click", clearReply);
}

// ── Message action delegation (Reply & Delete) ───────────────────────────────
messagesScroll.addEventListener("click", (e) => {
    // Reply button
    const replyBtn = e.target.closest(".reply-btn");
    if (replyBtn) {
        const msgId = replyBtn.dataset.msgId;
        const user  = replyBtn.dataset.msgUser;
        const text  = replyBtn.dataset.msgText;
        if (msgId) setReply(msgId, user, text);
        return;
    }

    // Edit button
    const editBtn = e.target.closest(".edit-btn");
    if (editBtn) {
        const msgId = editBtn.dataset.msgId;
        if (!msgId) return;
        const msgData = messageCache.get(msgId);
        if (!msgData || !msgData.text) return;

        const createdTs = msgData.created_at_ts || (Date.now() / 1000);
        if ((Date.now() / 1000) - createdTs > 300) {
            showInAppAlert("EDIT EXPIRED", "This message is older than 5 minutes and can no longer be edited.");
            return;
        }

        currentEditingMsgId = msgId;
        const inputEl = document.getElementById("edit-msg-input");
        const overlay = document.getElementById("edit-modal-overlay");
        if (inputEl && overlay) {
            inputEl.value = msgData.text;
            overlay.classList.remove("hidden");
            inputEl.focus();
        }
        return;
    }

    // Delete button
    const deleteBtn = e.target.closest(".delete-btn");
    if (deleteBtn) {
        const msgId = deleteBtn.dataset.msgId;
        if (msgId && ws && ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({ type: "delete_message", msg_id: msgId }));
            // Optimistic delete on sender side
            handleMessageDeleted(msgId, currentUsername);
        }
        return;
    }

    // Reply context click (scroll to original)
    const replyCtx = e.target.closest(".reply-context");
    if (replyCtx) {
        const parentId = replyCtx.dataset.replyTo;
        if (parentId) {
            const parentEl = messagesScroll.querySelector(`[data-msg-id="${CSS.escape(parentId)}"]`);
            if (parentEl) {
                parentEl.scrollIntoView({ behavior: "smooth", block: "center" });
                parentEl.style.outline = "2px solid var(--sage)";
                setTimeout(() => { parentEl.style.outline = ""; }, 2000);
            }
        }
    }
});

// ── Leave button ──────────────────────────────────────────────────────────────
leaveBtn.addEventListener("click", leaveChat);

const clearRoomHistoryBtn = document.getElementById("clear-room-history-btn");
const deleteRoomBtn       = document.getElementById("delete-room-btn");

if (clearRoomHistoryBtn) {
    clearRoomHistoryBtn.addEventListener("click", () => {
        showInAppConfirm({
            title: "🧹 CLEAR CHAT HISTORY",
            text: "Are you sure you want to clear all chat history in this room? All stored messages will be wiped for everyone.",
            confirmText: "YES, CLEAR HISTORY",
            confirmClass: "btn-warning",
            onConfirm: () => {
                if (ws && ws.readyState === WebSocket.OPEN) {
                    ws.send(JSON.stringify({ type: "clear_room_history" }));
                }
            }
        });
    });
}

if (deleteRoomBtn) {
    deleteRoomBtn.addEventListener("click", () => {
        showInAppConfirm({
            title: "🗑 DELETE CHAT ROOM",
            text: "Are you sure you want to PERMANENTLY delete this chat room? The room and all messages will be wiped for everyone.",
            confirmText: "YES, DELETE ROOM",
            confirmClass: "btn-danger",
            onConfirm: () => {
                if (ws && ws.readyState === WebSocket.OPEN) {
                    ws.send(JSON.stringify({ type: "delete_room" }));
                }
            }
        });
    });
}

// ── In-App Confirmation & Edit Modal Helpers ───────────────────────────────

let currentEditingMsgId = null;

async function handleIncomingEditedMessage(data) {
    let decryptedText = "";
    try {
        decryptedText = await decryptMessage(data.ciphertext, data.iv);
    } catch (e) {
        decryptedText = "⚠️ [DECRYPTION FAILED]";
    }

    if (!decryptedText) decryptedText = "";

    const cached = messageCache.get(data.msg_id) || {};
    cached.text = decryptedText;
    cached.is_edited = true;
    cached.ciphertext = data.ciphertext;
    cached.iv = data.iv;
    cached.signature = data.signature;
    messageCache.set(data.msg_id, cached);

    const msgEl = messagesScroll.querySelector(`[data-msg-id="${CSS.escape(data.msg_id)}"]`);
    if (msgEl) {
        const textEl = msgEl.querySelector(".message-text");
        if (textEl) {
            const parsed = parseMentions(escapeHtml(decryptedText));
            textEl.innerHTML = parsed.html;
        }

        const timeEl = msgEl.querySelector(".message-time");
        if (timeEl && !msgEl.querySelector(".edited-tag")) {
            const editedSpan = document.createElement("span");
            editedSpan.className = "edited-tag";
            editedSpan.title = "This message was edited";
            editedSpan.textContent = "(edited)";
            timeEl.after(editedSpan);
        }

        const replyBtn = msgEl.querySelector(".reply-btn");
        if (replyBtn) replyBtn.dataset.msgText = decryptedText;
    }
}

const editModalSave   = document.getElementById("edit-modal-save");
const editModalCancel = document.getElementById("edit-modal-cancel");

if (editModalCancel) {
    editModalCancel.addEventListener("click", () => {
        const overlay = document.getElementById("edit-modal-overlay");
        if (overlay) overlay.classList.add("hidden");
        currentEditingMsgId = null;
    });
}

if (editModalSave) {
    editModalSave.addEventListener("click", async () => {
        const overlay = document.getElementById("edit-modal-overlay");
        const inputEl = document.getElementById("edit-msg-input");
        if (!currentEditingMsgId || !inputEl) return;

        const updatedText = inputEl.value.trim();
        if (!updatedText) return;

        const msgData = messageCache.get(currentEditingMsgId);
        if (msgData) {
            const createdTs = msgData.created_at_ts || (Date.now() / 1000);
            if ((Date.now() / 1000) - createdTs > 300) {
                if (overlay) overlay.classList.add("hidden");
                showInAppAlert("EDIT EXPIRED", "This message is older than 5 minutes and can no longer be edited.");
                return;
            }
        }

        editModalSave.disabled = true;
        editModalSave.textContent = "SAVING...";

        try {
            const { ciphertext, iv } = await encryptMessage(updatedText);
            const signature = await signMaterial(ciphertext + iv);

            if (ws && ws.readyState === WebSocket.OPEN) {
                ws.send(JSON.stringify({
                    type:       "edit_message",
                    msg_id:     currentEditingMsgId,
                    ciphertext: ciphertext,
                    iv:         iv,
                    signature:  signature,
                    public_key: myPublicKeyJwk,
                }));
            }

            if (overlay) overlay.classList.add("hidden");

            handleIncomingEditedMessage({
                msg_id:     currentEditingMsgId,
                username:   currentUsername,
                ciphertext: ciphertext,
                iv:         iv,
                signature:  signature,
                public_key: myPublicKeyJwk,
                sig_valid:  true,
                is_edited:  true,
            });


        } catch (err) {
            console.error("Failed to save edits:", err);
            showInAppAlert("EDIT ERROR", "Failed to encrypt and save message edits.");
        } finally {
            editModalSave.disabled = false;
            editModalSave.textContent = "SAVE EDITS ►";
            currentEditingMsgId = null;
        }
    });
}

function showInAppConfirm({ title, text, confirmText, confirmClass, onConfirm }) {
    const overlay     = document.getElementById("confirm-modal-overlay");
    const titleEl     = document.getElementById("confirm-modal-title");
    const textEl      = document.getElementById("confirm-modal-text");
    const cancelBtn   = document.getElementById("confirm-modal-cancel");
    const confirmBtn  = document.getElementById("confirm-modal-confirm");

    if (!overlay || !titleEl || !textEl || !confirmBtn) return;

    titleEl.textContent = title || "⚠️ CONFIRM ACTION";
    textEl.textContent  = text || "Are you sure you want to perform this action?";
    confirmBtn.textContent = confirmText || "PROCEED ►";
    confirmBtn.className = `pixel-btn btn-sm ${confirmClass || "btn-danger"}`;

    overlay.classList.remove("hidden");

    const cleanup = () => {
        overlay.classList.add("hidden");
        cancelBtn.removeEventListener("click", handleCancel);
        confirmBtn.removeEventListener("click", handleConfirm);
    };

    const handleCancel = () => {
        cleanup();
    };

    const handleConfirm = () => {
        cleanup();
        if (onConfirm) onConfirm();
    };

    cancelBtn.addEventListener("click", handleCancel);
    confirmBtn.addEventListener("click", handleConfirm);
}

function showInAppAlert(title, text, onOk) {
    const overlay     = document.getElementById("confirm-modal-overlay");
    const titleEl     = document.getElementById("confirm-modal-title");
    const textEl      = document.getElementById("confirm-modal-text");
    const cancelBtn   = document.getElementById("confirm-modal-cancel");
    const confirmBtn  = document.getElementById("confirm-modal-confirm");

    if (!overlay || !titleEl || !textEl || !confirmBtn) return;

    titleEl.textContent = title || "ℹ NOTICE";
    textEl.textContent  = text || "";
    confirmBtn.textContent = "OK";
    confirmBtn.className = "pixel-btn btn-sm btn-warning";
    cancelBtn.style.display = "none";

    overlay.classList.remove("hidden");

    const handleOk = () => {
        overlay.classList.add("hidden");
        cancelBtn.style.display = "";
        confirmBtn.removeEventListener("click", handleOk);
        if (onOk) onOk();
    };

    confirmBtn.addEventListener("click", handleOk);
}

function leaveChat() {
    if (sessionTimer) { clearInterval(sessionTimer); sessionTimer = null; }
    if (roomPollTimer) { clearInterval(roomPollTimer); roomPollTimer = null; }
    playSound("leave");
    disconnect();
    // Reset per-session stats — totalXP is NOT reset (it's DB-persisted)
    streakCount = 0; messagesSent = 0;
    currentLevel = getCurrentRank().level;
    updateXPBar(); // redraw bar at persisted XP level
    // Reset room state
    currentRoomId = null; currentRoomName = null; currentRoomAvatar = null;
    // Clear chat UI
    messagesScroll.innerHTML = "";
    isJoined = false; isIntentionalClose = false;
    // Return to lobby (keep session token and crypto keys for rejoining)
    chatScreen.classList.add("hidden");
    showLobbyScreen();
}

// ── Lobby Logic ────────────────────────────────────────────────────────────────

function resetLobbyButtons() {
    if (createRoomBtn) {
        createRoomBtn.disabled = false;
        if (createRoomBtnText) createRoomBtnText.textContent = "✦ CREATE ROOM";
    }
    if (joinCodeBtn) {
        joinCodeBtn.disabled = false;
        if (joinCodeBtnText) joinCodeBtnText.textContent = "⌖ JOIN ROOM";
    }
}

function showLobbyScreen() {
    loginScreen.classList.add("hidden");
    chatScreen.classList.add("hidden");
    lobbyScreen.classList.remove("hidden");
    sessionStorage.setItem("pixel_active_screen", "lobby");
    saveSessionState();
    if (lobbyUsernameDisplay) lobbyUsernameDisplay.textContent = `▸ ${currentUsername.toUpperCase()}`;
    // Populate lobby XP chip
    const rank = getCurrentRank();
    const lobbyRankEl  = document.getElementById("lobby-xp-rank");
    const lobbyXpNums  = document.getElementById("lobby-xp-nums");
    const lobbyXpBar   = document.getElementById("lobby-xp-bar-fill");
    if (lobbyRankEl)  lobbyRankEl.textContent  = `${rank.emoji} ${rank.name}`;
    if (lobbyXpNums)  lobbyXpNums.textContent  = `${totalXP} XP`;
    if (lobbyXpBar) {
        const xpInLevel = totalXP - rank.xp;
        const xpNeeded  = rank.next - rank.xp;
        const pct = rank.level === 6 ? 100 : Math.min(100, (xpInLevel / xpNeeded) * 100);
        lobbyXpBar.style.width = `${pct.toFixed(1)}%`;
    }
    if (lobbyError) lobbyError.textContent = "";
    if (joinCodeError) joinCodeError.textContent = "";
    resetLobbyButtons();
    loadRooms();
    if (roomPollTimer) clearInterval(roomPollTimer);
    roomPollTimer = setInterval(loadRooms, 6000);
    if (roomNameInput) roomNameInput.focus();
}

function switchLobbyTab(tab) {
    if (lobbyError) lobbyError.textContent = "";
    if (joinCodeError) joinCodeError.textContent = "";
    if (tab === "create") {
        ltabCreate.classList.add("active");
        ltabJoin.classList.remove("active");
        lpanelCreate.classList.remove("hidden");
        lpanelJoin.classList.add("hidden");
        if (roomNameInput) roomNameInput.focus();
    } else {
        ltabJoin.classList.add("active");
        ltabCreate.classList.remove("active");
        lpanelJoin.classList.remove("hidden");
        lpanelCreate.classList.add("hidden");
        if (roomCodeInput) { roomCodeInput.value = ""; roomCodeInput.focus(); }
    }
}
window.switchLobbyTab = switchLobbyTab;

function setVisibility(pub) {
    isPublicRoom = pub;
    if (pub) {
        visPublicBtn.classList.add("active");
        visPrivateBtn.classList.remove("active");
        if (visibilityHint) visibilityHint.textContent = "Appears in the browse list";
    } else {
        visPrivateBtn.classList.add("active");
        visPublicBtn.classList.remove("active");
        if (visibilityHint) visibilityHint.textContent = "Join by code only — not listed";
    }
}
window.setVisibility = setVisibility;

async function loadRooms() {
    try {
        const res = await backendFetch(`${HTTP_PROTOCOL}//${BACKEND_HOST}/rooms`);
        if (!res.ok) return;
        const data = await res.json();
        roomListCache = data.rooms || [];
        renderRoomList(roomSearchInput ? roomSearchInput.value.trim() : "");
    } catch (e) {
        console.warn("[Lobby] loadRooms failed:", e);
    }
}

function renderRoomList(filter = "") {
    const filtered = filter
        ? roomListCache.filter(r =>
            r.name.toLowerCase().includes(filter.toLowerCase()) ||
            r.id.toLowerCase().includes(filter.toLowerCase())
          )
        : roomListCache;

    // Clear existing cards (keep empty notice)
    Array.from(roomListScroll.children).forEach(c => {
        if (!c.id || c.id !== "room-list-empty") c.remove();
    });

    if (filtered.length === 0) {
        if (roomListEmpty) roomListEmpty.style.display = "flex";
        return;
    }
    if (roomListEmpty) roomListEmpty.style.display = "none";

    filtered.forEach((room) => {
        const card = document.createElement("div");
        card.className = "room-card";
        const icon = room.avatar || "🏰";
        const onlineDot = room.online > 0
            ? `<span class="blink-dot-sm"></span> ${room.online} online`
            : `0 online`;

        const ownerUsername = room.created_by || "system";
        const ownerAvatarData = getAvatarData(room.owner_avatar || "wizard", ownerUsername);

        card.innerHTML = `
            <div class="room-card-icon">${icon}</div>
            <div class="room-card-body">
                <div class="room-card-name">${escapeHtml(room.name)}</div>
                <div class="room-card-meta">
                    <span class="room-code-badge">${escapeHtml(room.id)}</span>
                    <span class="room-owner-badge" title="Room Owner: @${escapeHtml(ownerUsername)}">
                        👑 ${ownerAvatarData.icon} @${escapeHtml(ownerUsername)}
                    </span>
                    <span class="room-player-count">${onlineDot}</span>
                </div>
            </div>
            <button class="room-card-join-btn" data-room-id="${escapeHtml(room.id)}" data-room-name="${escapeHtml(room.name)}">► JOIN</button>
        `;
        roomListScroll.appendChild(card);
    });
}

async function createRoomAction() {
    const name = roomNameInput ? roomNameInput.value.trim() : "";
    if (!name) { if (lobbyError) lobbyError.textContent = "! ENTER A ROOM NAME"; return; }
    if (lobbyError) lobbyError.textContent = "";
    createRoomBtn.disabled = true;
    createRoomBtnText.textContent = "CREATING...";
    try {
        const res = await backendFetch(`${HTTP_PROTOCOL}//${BACKEND_HOST}/rooms`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ name, is_public: isPublicRoom, avatar: selectedRoomAvatar, created_by: currentUsername }),
        });

        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || "Failed to create room");
        // Award room-creation XP if server returned it
        if (typeof data.xp_total === "number") {
            const gained = data.xp_awarded || 0;
            totalXP = data.xp_total;
            currentLevel = getCurrentRank().level;
            if (gained > 0) showXpGainToast(gained, "room created");
        }
        await joinRoom(data.room_id, data.name);
    } catch (err) {
        if (lobbyError) lobbyError.textContent = "! " + err.message.toUpperCase();
    } finally {
        resetLobbyButtons();
    }
}

async function joinRoomByCode() {
    const code = roomCodeInput ? roomCodeInput.value.trim().toUpperCase() : "";
    if (!code || code.length !== 6) {
        if (joinCodeError) joinCodeError.textContent = "! CODE MUST BE 6 CHARACTERS";
        return;
    }
    if (joinCodeError) joinCodeError.textContent = "";
    joinCodeBtn.disabled = true;
    joinCodeBtnText.textContent = "CHECKING...";
    try {
        const res = await backendFetch(`${HTTP_PROTOCOL}//${BACKEND_HOST}/rooms/${code}`);
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || "Room not found");
        await joinRoom(data.id, data.name);
    } catch (err) {
        if (joinCodeError) joinCodeError.textContent = "! " + err.message.toUpperCase();
    } finally {
        resetLobbyButtons();
    }
}

async function joinRoom(roomId, roomName) {
    if (roomPollTimer) { clearInterval(roomPollTimer); roomPollTimer = null; }
    currentRoomId   = roomId;
    currentRoomName = roomName;
    // Rotate the authenticated session token before joining via WebSocket
    try {
        const res = await backendFetch(`${HTTP_PROTOCOL}//${BACKEND_HOST}/refresh-token`, {
            method:  "POST",
            headers: { "Content-Type": "application/json" },
            body:    JSON.stringify({ username: currentUsername, token: sessionToken }),
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || "Token refresh failed");
        sessionToken = data.token;
        if (data.username) currentUsername = data.username;
        // Sync XP from server on re-join
        if (typeof data.xp === "number") {
            totalXP = data.xp;
            currentLevel = getCurrentRank().level;
        }
    } catch (err) {
        console.error("[joinRoom] Token refresh failed:", err);
        if (lobbyError) lobbyError.textContent = "! SESSION ERROR — PLEASE LOG IN AGAIN";
        return;
    }
    isIntentionalClose = false;
    isJoined = false;
    connect();
}


// ── Lobby Event Listeners ─────────────────────────────────────────────────────

if (createRoomBtn) createRoomBtn.addEventListener("click", createRoomAction);
if (joinCodeBtn)   joinCodeBtn.addEventListener("click", joinRoomByCode);

if (roomNameInput) {
    roomNameInput.addEventListener("keydown", (e) => { if (e.key === "Enter") createRoomAction(); });
}
if (roomCodeInput) {
    roomCodeInput.addEventListener("input", () => {
        roomCodeInput.value = roomCodeInput.value.toUpperCase().replace(/[^A-Z0-9]/g, "");
    });
    roomCodeInput.addEventListener("keydown", (e) => { if (e.key === "Enter") joinRoomByCode(); });
}

if (roomListScroll) {
    roomListScroll.addEventListener("click", (e) => {
        const btn = e.target.closest(".room-card-join-btn");
        if (!btn) return;
        const roomId   = btn.dataset.roomId;
        const roomName = btn.dataset.roomName;
        if (roomId && roomName) joinRoom(roomId, roomName);
    });
}

if (roomSearchInput) {
    roomSearchInput.addEventListener("input", () => {
        renderRoomList(roomSearchInput.value.trim());
    });
}

if (refreshRoomsBtn) {
    refreshRoomsBtn.addEventListener("click", () => {
        refreshRoomsBtn.classList.add("spinning");
        loadRooms().then(() => setTimeout(() => refreshRoomsBtn.classList.remove("spinning"), 400));
    });
}

if (lobbyLogoutBtn) {
    lobbyLogoutBtn.addEventListener("click", () => {
        if (roomPollTimer) { clearInterval(roomPollTimer); roomPollTimer = null; }
        clearSavedSession();
        currentUsername = ""; sessionToken = null;
        aesKey = null; ecdsaKeyPair = null; myPublicKeyJwk = null; cryptoReady = false;
        setCryptoStatusUI(false);
        lobbyScreen.classList.add("hidden");
        loginScreen.classList.remove("hidden");
        registerBtn.disabled = false; registerBtnText.textContent = "► SIGN UP";
        loginBtn.disabled = false; loginBtnText.textContent = "► LOGIN";
        loginError.textContent = "";
        regPassword.value = ""; loginPassword.value = "";
        switchTab("register");
    });
}

async function tryRestoreSession() {
    let savedToken    = sessionStorage.getItem("pixel_session_token");
    let savedUsername = sessionStorage.getItem("pixel_username");
    let savedAvatar   = sessionStorage.getItem("pixel_avatar");
    let activeScreen  = sessionStorage.getItem("pixel_active_screen");
    let savedRoomId   = sessionStorage.getItem("pixel_room_id");
    let savedRoomName = sessionStorage.getItem("pixel_room_name");

    // If tab's sessionStorage is empty (e.g. Cmd+T new tab in same window)
    if (!savedToken || !savedUsername) {
        const storedStr = localStorage.getItem("pixel_active_session");
        if (storedStr) {
            try {
                const stored = JSON.parse(storedStr);
                if (stored && stored.token && stored.username) {
                    const diffX = Math.abs(window.screenX - (stored.screenX || 0));
                    const diffY = Math.abs(window.screenY - (stored.screenY || 0));
                    const diffW = Math.abs(window.outerWidth - (stored.outerWidth || 0));
                    const diffH = Math.abs(window.outerHeight - (stored.outerHeight || 0));

                    // Same window check: screen position and outer dimensions match closely
                    const isSameWindow = (diffX <= 80 && diffY <= 80) || (diffW <= 30 && diffH <= 30);

                    if (isSameWindow) {
                        savedToken    = stored.token;
                        savedUsername = stored.username;
                        savedAvatar   = stored.avatar;
                        activeScreen  = stored.activeScreen || "lobby";
                        savedRoomId   = stored.roomId;
                        savedRoomName = stored.roomName;

                        sessionStorage.setItem("pixel_session_token", savedToken);
                        sessionStorage.setItem("pixel_username", savedUsername);
                        if (savedAvatar) sessionStorage.setItem("pixel_avatar", savedAvatar);
                        sessionStorage.setItem("pixel_active_screen", activeScreen);
                        if (savedRoomId) sessionStorage.setItem("pixel_room_id", savedRoomId);
                        if (savedRoomName) sessionStorage.setItem("pixel_room_name", savedRoomName);
                    }
                }
            } catch (e) {}
        }
    }

    if (savedToken && savedUsername) {
        sessionToken    = savedToken;
        currentUsername = savedUsername;
        selectedAvatar  = savedAvatar || "wizard";

        try {
            await initCrypto();
            if (activeScreen === "chat" && savedRoomId) {
                currentRoomId   = savedRoomId;
                currentRoomName = savedRoomName || "";
                await joinRoom(savedRoomId, savedRoomName);
            } else {
                showLobbyScreen();
            }
            saveSessionState();
            return true;
        } catch (err) {
            console.warn("[SessionRestore] Could not restore session:", err);
            sessionStorage.clear();
        }
    }
    return false;
}

// ── Init ──────────────────────────────────────────────────────────────────────
async function initApp() {
    renderAvatarPicker();
    renderRoomAvatarPicker();
    updateXPBar();
    setCryptoStatusUI(false);
    const restored = await tryRestoreSession();
    if (!restored) {
        regUsername.focus();
    }
}

async function checkBackendHealth() {
    try {
        const res = await backendFetch(`${HTTP_PROTOCOL}//${BACKEND_HOST}/health`);
        if (!res.ok) throw new Error("Health check failed");
        return true;
    } catch (e) {
        // Handle both Chrome ("Failed to fetch") and Firefox ("NetworkError when attempting to fetch resource.")
        if (e instanceof TypeError || (e.message && e.message.toLowerCase().includes("fetch"))) {
            const overlay = document.getElementById("cert-overlay");
            const link = document.getElementById("cert-auth-link");
            if (overlay && link) {
                link.href = `${HTTP_PROTOCOL}//${BACKEND_HOST}/health`;
                overlay.classList.remove("hidden");
                
                // Poll automatically so they don't have to refresh
                const pollTimer = setInterval(async () => {
                    try {
                        const check = await backendFetch(`${HTTP_PROTOCOL}//${BACKEND_HOST}/health`);
                        if (check.ok) {
                            clearInterval(pollTimer);
                            overlay.classList.add("hidden");
                            initApp();
                        }
                    } catch (err) {
                        // Still blocked, continue polling
                    }
                }, 1500);
            }
            return false;
        }
        return true;
    }
}

window.addEventListener("load", async () => {
    const isHealthy = await checkBackendHealth();
    if (isHealthy) {
        initApp();
    }
});
