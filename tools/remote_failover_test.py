"""Exercise Sys4 removal/recovery while keeping the deployed stack intact."""

from __future__ import annotations

import collections
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from deploy_and_run import REMOTE_ROOT, ssh_exec
from lab_config import LAB_HOST, SYS1_LB_PUBLIC_PORT, SYS4_SSH_PORT, connect_ssh


LB_STATUS_URL = f"https://{LAB_HOST}:{SYS1_LB_PUBLIC_PORT}/lb/status"
LB_ROOT_URL = f"https://{LAB_HOST}:{SYS1_LB_PUBLIC_PORT}/"
SYS4_URL = "https://172.17.0.41:5000"


def status() -> dict:
    return httpx.get(LB_STATUS_URL, verify=False, timeout=5).raise_for_status().json()


def wait_for_alive(expected: bool, timeout: float = 12) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        backend = next(item for item in status()["backends"] if item["url"] == SYS4_URL)
        if backend["alive"] is expected:
            return
        time.sleep(0.5)
    raise RuntimeError(f"Sys4 did not reach alive={expected} within {timeout}s")


def routing_counts(requests: int = 12) -> collections.Counter[str]:
    counts: collections.Counter[str] = collections.Counter()
    for _ in range(requests):
        response = httpx.get(LB_ROOT_URL, verify=False, timeout=5)
        response.raise_for_status()
        counts[response.headers["x-load-balancer-backend"]] += 1
    return counts


def main() -> None:
    sys4 = connect_ssh(SYS4_SSH_PORT)
    try:
        print("Stopping only the Sys4 messaging process...")
        ssh_exec(sys4, "pkill -f '[s]erver/server.py' || true")
        wait_for_alive(False)
        down_counts = routing_counts()
        if SYS4_URL in down_counts:
            raise RuntimeError(f"traffic reached unhealthy Sys4: {dict(down_counts)}")
        print(f"Failover routing PASS: {dict(down_counts)}")
    finally:
        print("Restarting Sys4 messaging process...")
        ssh_exec(
            sys4,
            f"cd {REMOTE_ROOT} && PORT=5000 setsid -f python3 server/server.py "
            "> server.log 2>&1 < /dev/null",
        )
        time.sleep(2)
        ssh_exec(sys4, "curl -k --fail --silent --show-error https://127.0.0.1:5000/health")
        sys4.close()

    wait_for_alive(True)
    recovered_counts = routing_counts()
    if SYS4_URL not in recovered_counts:
        raise RuntimeError(f"recovered Sys4 did not rejoin routing: {dict(recovered_counts)}")
    print(f"Recovery routing PASS: {dict(recovered_counts)}")


if __name__ == "__main__":
    main()
