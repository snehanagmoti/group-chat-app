"""Run the one-vs-three-backend experiment from the local workstation.

The script controls the Sys1 load balancer over SSH, but every measured request
is sent by the local Go load generator through the lab's public port mapping:
10.1.75.53:4237 -> Sys1 container port 4000.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from deploy_and_run import LB_PORT, PROJECT_ROOT, REMOTE_ROOT, ssh_exec
from lab_config import (
    BACKEND_URLS,
    LAB_HOST,
    SYS1_LB_PUBLIC_PORT,
    SYS1_SSH_PORT,
    connect_ssh,
)


PUBLIC_LB_URL = f"http://{LAB_HOST}:{SYS1_LB_PUBLIC_PORT}"
RESULT_FILES = (
    "results.csv",
    "1_backend.json",
    "3_backends.json",
    "1_backend_lb_status.json",
    "1_backend_lb_metrics.json",
    "3_backends_lb_status.json",
    "3_backends_lb_metrics.json",
)


def generator_default() -> Path:
    suffix = ".exe" if os.name == "nt" else ""
    return PROJECT_ROOT / "tmp" / "bin" / f"load_generator{suffix}"


def wait_for_public_health(client: httpx.Client, timeout: float = 15) -> None:
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            response = client.get(f"{PUBLIC_LB_URL}/lb/health", timeout=3)
            response.raise_for_status()
            return
        except (httpx.HTTPError, OSError) as error:
            last_error = error
            time.sleep(0.5)
    raise RuntimeError(f"public load-balancer mapping did not become healthy: {last_error}")


def start_load_balancer(ssh, backends: list[str], client: httpx.Client) -> None:
    ssh_exec(ssh, "pkill -f '[l]oad_balancer' || true")
    backend_argument = shlex.quote(",".join(backends))
    ssh_exec(
        ssh,
        f"cd {REMOTE_ROOT} && setsid -f ./load_balancer "
        f"-backends {backend_argument} -port {LB_PORT} "
        "-backend-insecure-skip-verify "
        "> lb.log 2>&1 < /dev/null",
    )
    time.sleep(2)
    ssh_exec(
        ssh,
        f"curl --fail --silent --show-error http://127.0.0.1:{LB_PORT}/lb/health",
    )
    wait_for_public_health(client)


def write_json(name: str, payload: dict) -> None:
    (PROJECT_ROOT / name).write_text(
        json.dumps(payload, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )


def run_experiment(
    name: str,
    backends: list[str],
    generator: Path,
    requests: int,
    concurrency: int,
    delay: str,
    timeout: str,
    ssh,
    client: httpx.Client,
) -> None:
    print(f"\n=== {name}: local generator -> {PUBLIC_LB_URL} ===")
    start_load_balancer(ssh, backends, client)
    write_json(f"{name}_lb_status.json", client.get(f"{PUBLIC_LB_URL}/lb/status").json())

    subprocess.run(
        [
            str(generator),
            "-url",
            f"{PUBLIC_LB_URL}/?delay={delay}",
            "-requests",
            str(requests),
            "-concurrency",
            str(concurrency),
            "-timeout",
            timeout,
            "-experiment",
            name,
            "-out",
            f"{name}.json",
            "-csv",
            "results.csv",
        ],
        cwd=PROJECT_ROOT,
        check=True,
    )
    write_json(f"{name}_lb_metrics.json", client.get(f"{PUBLIC_LB_URL}/lb/metrics").json())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--generator", type=Path, default=generator_default())
    parser.add_argument("--requests", type=int, default=5000)
    parser.add_argument("--concurrency", type=int, default=200)
    parser.add_argument("--delay", default="100ms")
    parser.add_argument("--timeout", default="3s")
    args = parser.parse_args()

    generator = args.generator.resolve()
    if not generator.is_file():
        raise FileNotFoundError(
            f"local load generator not found at {generator}; build cmd/load-generator first"
        )

    for name in RESULT_FILES:
        (PROJECT_ROOT / name).unlink(missing_ok=True)

    ssh = connect_ssh(SYS1_SSH_PORT)
    with httpx.Client(trust_env=False, timeout=10) as client:
        try:
            run_experiment(
                "1_backend",
                BACKEND_URLS[:1],
                generator,
                args.requests,
                args.concurrency,
                args.delay,
                args.timeout,
                ssh,
                client,
            )
            run_experiment(
                "3_backends",
                BACKEND_URLS,
                generator,
                args.requests,
                args.concurrency,
                args.delay,
                args.timeout,
                ssh,
                client,
            )
        finally:
            print("\nRestoring the normal three-backend load balancer...")
            start_load_balancer(ssh, BACKEND_URLS, client)
            ssh.close()

    print("Local mapped-port experiments completed; JSON and CSV artifacts are updated.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
