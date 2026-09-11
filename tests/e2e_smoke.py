"""Local smoke test for three HTTPS backends behind the Go load balancer.

Run after building the load balancer:
    python tests/e2e_smoke.py --load-balancer tmp/bin/load_balancer.exe
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx
from websockets.sync.client import connect


PROJECT_ROOT = Path(__file__).resolve().parent.parent
BACKEND_PORTS = (18081, 18082, 18083)
LB_PORT = 18080
FRONTEND_PORT = 18079


def hidden_process_flags() -> int:
    return subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


def wait_for(url: str, *, verify: bool, timeout: float = 20) -> None:
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            response = httpx.get(url, verify=verify, timeout=1)
            if response.is_success:
                return
        except Exception as error:  # service is still starting
            last_error = error
        time.sleep(0.2)
    raise RuntimeError(f"Service did not become ready: {url}") from last_error


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--load-balancer", type=Path, required=True)
    parser.add_argument(
        "--hold",
        action="store_true",
        help="Keep the verified local stack running for browser inspection.",
    )
    args = parser.parse_args()
    load_balancer = args.load_balancer.resolve()
    if not load_balancer.is_file():
        raise FileNotFoundError(load_balancer)

    processes: list[subprocess.Popen] = []
    log_handles = []
    log_paths: list[Path] = []
    with tempfile.TemporaryDirectory(prefix="pixelchat-e2e-") as state:
        state_directory = Path(state)
        try:
            for index, port in enumerate(BACKEND_PORTS):
                environment = os.environ.copy()
                environment.update(
                    {
                        "PORT": str(port),
                        "AES_GROUP_KEY": "11" * 32,
                        "HMAC_SECRET": "22" * 32,
                        "CHAT_DB_PATH": str(state_directory / f"backend-{index}.db"),
                        "CHAT_UPLOAD_DIR": str(state_directory / f"uploads-{index}"),
                        "SESSION_TTL_SECONDS": "3600",
                    }
                )
                log_path = state_directory / f"backend-{index}.log"
                log_handle = log_path.open("w", encoding="utf-8")
                log_paths.append(log_path)
                log_handles.append(log_handle)
                processes.append(
                    subprocess.Popen(
                        [sys.executable, str(PROJECT_ROOT / "server" / "server.py")],
                        cwd=PROJECT_ROOT,
                        env=environment,
                        stdout=log_handle,
                        stderr=subprocess.STDOUT,
                        creationflags=hidden_process_flags(),
                    )
                )
            for port in BACKEND_PORTS:
                wait_for(f"https://127.0.0.1:{port}/health", verify=False)

            backends = ",".join(
                f"https://127.0.0.1:{port}" for port in BACKEND_PORTS
            )
            log_path = state_directory / "load-balancer.log"
            log_handle = log_path.open("w", encoding="utf-8")
            log_paths.append(log_path)
            log_handles.append(log_handle)
            processes.append(
                subprocess.Popen(
                    [
                        str(load_balancer),
                        "-backends",
                        backends,
                        "-port",
                        str(LB_PORT),
                        "-backend-insecure-skip-verify",
                        "-health-interval",
                        "200ms",
                        "-backend-timeout",
                        "2s",
                    ],
                    cwd=PROJECT_ROOT,
                    stdout=log_handle,
                    stderr=subprocess.STDOUT,
                    creationflags=hidden_process_flags(),
                )
            )
            base_url = f"http://127.0.0.1:{LB_PORT}"
            wait_for(base_url + "/lb/health", verify=True)

            frontend_environment = os.environ.copy()
            frontend_environment.update(
                {
                    "FRONTEND_PORT": str(FRONTEND_PORT),
                    "BACKEND_PORT": str(LB_PORT),
                    "FRONTEND_TLS": "0",
                }
            )
            log_path = state_directory / "frontend.log"
            log_handle = log_path.open("w", encoding="utf-8")
            log_paths.append(log_path)
            log_handles.append(log_handle)
            processes.append(
                subprocess.Popen(
                    [sys.executable, str(PROJECT_ROOT / "client" / "serve.py")],
                    cwd=PROJECT_ROOT,
                    env=frontend_environment,
                    stdout=log_handle,
                    stderr=subprocess.STDOUT,
                    creationflags=hidden_process_flags(),
                )
            )
            wait_for(f"http://127.0.0.1:{FRONTEND_PORT}/", verify=True)

            selected_backends = {
                httpx.get(base_url + "/?delay=1ms", timeout=2).headers[
                    "x-load-balancer-backend"
                ]
                for _ in range(9)
            }
            if len(selected_backends) != 3:
                raise AssertionError(
                    f"round robin reached {len(selected_backends)} backend(s): "
                    f"{selected_backends}"
                )

            username = f"e2e_{int(time.time())}"
            with httpx.Client(base_url=base_url, timeout=5) as client:
                registered = client.post(
                    "/register",
                    json={
                        "username": username,
                        "password": "correct-horse",
                        "avatar": "wizard",
                    },
                )
                registered.raise_for_status()
                token = registered.json()["token"]

                room = client.post(
                    "/rooms",
                    json={
                        "name": "Load Balanced Room",
                        "created_by": username,
                        "is_public": False,
                    },
                )
                room.raise_for_status()
                room_id = room.json()["room_id"]

                refreshed = client.post(
                    "/refresh-token",
                    json={"username": username, "token": token},
                )
                refreshed.raise_for_status()
                token = refreshed.json()["token"]
                cookie_header = "; ".join(
                    f"{cookie.name}={cookie.value}" for cookie in client.cookies.jar
                )

            with connect(
                f"ws://127.0.0.1:{LB_PORT}/ws",
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
                welcome = json.loads(websocket.recv(timeout=5))
                if welcome.get("type") != "system":
                    raise AssertionError(f"unexpected WebSocket response: {welcome}")

            print(
                "E2E PASS: round robin reached all three backends and the "
                "messaging WebSocket completed through the load balancer."
            )
            if args.hold:
                print(
                    f"STACK READY: frontend=http://127.0.0.1:{FRONTEND_PORT} "
                    f"status={base_url}/lb/status"
                )
                while True:
                    time.sleep(1)
            return 0
        except Exception:
            for log_handle in log_handles:
                log_handle.flush()
            for log_path in log_paths:
                print(f"\n--- {log_path.name} ---", file=sys.stderr)
                print(
                    log_path.read_text(encoding="utf-8", errors="replace"),
                    file=sys.stderr,
                )
            raise
        finally:
            for process in reversed(processes):
                if process.poll() is None:
                    process.terminate()
            for process in reversed(processes):
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
            for log_handle in log_handles:
                log_handle.close()


if __name__ == "__main__":
    raise SystemExit(main())
