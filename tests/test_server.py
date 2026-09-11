from __future__ import annotations

import importlib.util
import os
import secrets
import sys
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


PROJECT_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="session")
def server_module(tmp_path_factory):
    state_directory = tmp_path_factory.mktemp("pixelchat-state")
    os.environ["AES_GROUP_KEY"] = secrets.token_hex(32)
    os.environ["HMAC_SECRET"] = secrets.token_hex(32)
    os.environ["CHAT_DB_PATH"] = str(state_directory / "chat.db")
    os.environ["CHAT_UPLOAD_DIR"] = str(state_directory / "uploads")
    os.environ["SESSION_TTL_SECONDS"] = "3600"

    sys.path.insert(0, str(PROJECT_ROOT / "server"))
    specification = importlib.util.spec_from_file_location(
        "pixelchat_server",
        PROJECT_ROOT / "server" / "server.py",
    )
    module = importlib.util.module_from_spec(specification)
    assert specification.loader is not None
    specification.loader.exec_module(module)
    return module


@pytest.fixture
def client(server_module):
    with TestClient(server_module.app) as test_client:
        yield test_client


def unique_username() -> str:
    return "user_" + uuid.uuid4().hex[:10]


def test_health_delay_and_synthetic_failure(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}

    delayed = client.get("/?delay=2ms")
    assert delayed.status_code == 200
    assert delayed.json() == {"status": "ok"}

    failed = client.get("/?fail=true")
    assert failed.status_code == 503


def test_authenticated_session_survives_websocket_reconnect(client):
    username = unique_username()
    registered = client.post(
        "/register",
        json={"username": username, "password": "correct-horse", "avatar": "wizard"},
    )
    assert registered.status_code == 200
    token = registered.json()["token"]

    room_response = client.post(
        "/rooms",
        json={
            "name": "Reconnect Test",
            "created_by": username,
            "is_public": False,
            "avatar": "castle",
        },
    )
    assert room_response.status_code == 200
    room_id = room_response.json()["room_id"]

    join = {
        "type": "join",
        "token": token,
        "public_key": None,
        "room_id": room_id,
    }
    for _ in range(2):
        with client.websocket_connect("/ws") as websocket:
            websocket.send_json(join)
            welcome = websocket.receive_json()
            assert welcome["type"] == "system"
            assert room_id in welcome["room"]["id"]


def test_refresh_token_requires_the_existing_authenticated_session(client):
    username = unique_username()
    registered = client.post(
        "/register",
        json={"username": username, "password": "correct-horse", "avatar": "robot"},
    )
    token = registered.json()["token"]

    rejected = client.post(
        "/refresh-token",
        json={"username": username, "token": "not-a-valid-token"},
    )
    assert rejected.status_code == 401

    refreshed = client.post(
        "/refresh-token",
        json={"username": username, "token": token},
    )
    assert refreshed.status_code == 200
    replacement = refreshed.json()["token"]
    assert replacement != token

    old_token = client.post(
        "/refresh-token",
        json={"username": username, "token": token},
    )
    assert old_token.status_code == 401
