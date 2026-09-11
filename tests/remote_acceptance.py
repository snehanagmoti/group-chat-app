"""Acceptance test for the deployed stack through a local SSH/proxy tunnel."""

from __future__ import annotations

import json
import os
import ssl
import time

import httpx
from websockets.sync.client import connect


BASE_URL = os.environ.get("REMOTE_BASE_URL", "https://127.0.0.1:18082")
WS_URL = os.environ.get("REMOTE_WS_URL", "wss://127.0.0.1:18082/ws")
UNVERIFIED_SSL = ssl.create_default_context()
UNVERIFIED_SSL.check_hostname = False
UNVERIFIED_SSL.verify_mode = ssl.CERT_NONE


def websocket_join(token: str, room_id: str, cookie_header: str) -> dict:
    with connect(
        WS_URL,
        ssl=UNVERIFIED_SSL,
        proxy=None,
        additional_headers={"Cookie": cookie_header},
        open_timeout=5,
    ) as websocket:
        websocket.send(
            json.dumps(
                {
                    "type": "join",
                    "token": token,
                    "public_key": None,
                    "room_id": room_id,
                }
            )
        )
        response = json.loads(websocket.recv(timeout=5))
        if response.get("type") != "system":
            raise AssertionError(f"unexpected WebSocket response: {response}")
        return response


def main() -> None:
    username = f"re_{int(time.time())}"
    room_id = ""
    with httpx.Client(base_url=BASE_URL, timeout=8, verify=False) as client:
        health = client.get("/health")
        health.raise_for_status()
        registered = client.post(
            "/register",
            json={
                "username": username,
                "password": "acceptance-only",
                "avatar": "wizard",
            },
        )
        registered.raise_for_status()
        token = registered.json()["token"]
        selected_backend = registered.headers["x-load-balancer-backend"]
        cookie_header = "; ".join(
            f"{cookie.name}={cookie.value}" for cookie in client.cookies.jar
        )
        client.headers["Cookie"] = cookie_header

        room = client.post(
            "/rooms",
            json={
                "name": "Remote Acceptance Room",
                "created_by": username,
                "is_public": False,
            },
        )
        room.raise_for_status()
        room_id = room.json()["room_id"]
        if room.headers["x-load-balancer-backend"] != selected_backend:
            raise AssertionError("affinity did not preserve the selected backend")

        try:
            refreshed = client.post(
                "/refresh-token",
                json={"username": username, "token": token},
            )
            refreshed.raise_for_status()
            token = refreshed.json()["token"]

            first = websocket_join(token, room_id, cookie_header)
            second = websocket_join(token, room_id, cookie_header)
            if first.get("room", {}).get("id") != room_id or second.get("room", {}).get("id") != room_id:
                raise AssertionError("WebSocket join response did not preserve the room")
        finally:
            deleted = client.request(
                "DELETE",
                f"/rooms/{room_id}",
                json={"username": username},
            )
            deleted.raise_for_status()

    print(
        "REMOTE E2E PASS: register, affinity, room creation, authenticated token "
        f"rotation, two WebSocket joins, and cleanup completed on {selected_backend}."
    )


if __name__ == "__main__":
    main()
