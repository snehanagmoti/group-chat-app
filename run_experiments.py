"""Run the fair one-vs-three-backend comparison on Sys1 and fetch results."""

from __future__ import annotations

import posixpath
import shlex
import time
from pathlib import Path

from deploy_and_run import LB_PORT, PROJECT_ROOT, REMOTE_ROOT, ssh_exec, upload_file, upload_tree
from lab_config import (
    BACKEND_URLS,
    SYS1_SSH_PORT,
    SYS2_SSH_PORT,
    SYS3_SSH_PORT,
    SYS4_SSH_PORT,
    connect_ssh,
)


def restore_three_backend_load_balancer(client) -> None:
    """Leave Sys1 serving the normal three-backend topology after measurement."""
    backends = ",".join(BACKEND_URLS)
    ssh_exec(client, "pkill -f '[l]oad_balancer' || true")
    ssh_exec(
        client,
        f"cd {REMOTE_ROOT} && setsid -f ./load_balancer "
        f"-backends {shlex.quote(backends)} -port {LB_PORT} "
        "-backend-insecure-skip-verify "
        "> lb.log 2>&1 < /dev/null",
    )
    time.sleep(2)
    ssh_exec(
        client,
        f"curl --fail --silent --show-error http://127.0.0.1:{LB_PORT}/lb/health",
    )


def check_backends() -> bool:
    print("=== Checking backend health ===")
    all_healthy = True
    for port, name in (
        (SYS2_SSH_PORT, "Sys2"),
        (SYS3_SSH_PORT, "Sys3"),
        (SYS4_SSH_PORT, "Sys4"),
    ):
        client = connect_ssh(port)
        try:
            try:
                output = ssh_exec(
                    client,
                    "curl -k --fail --silent --show-error https://127.0.0.1:5000/health",
                )
                healthy = '"status":"ok"' in output.replace(" ", "")
            except RuntimeError:
                healthy = False
            print(f"  {name}: {'healthy' if healthy else 'DOWN'}")
            all_healthy = all_healthy and healthy
        finally:
            client.close()
    return all_healthy


def upload_experiment_sources(client) -> None:
    upload_file(client, "go.mod", f"{REMOTE_ROOT}/go.mod")
    upload_file(client, "run_experiments.sh", f"{REMOTE_ROOT}/run_experiments.sh")
    upload_tree(client, "cmd/load-balancer", f"{REMOTE_ROOT}/cmd/load-balancer")
    upload_tree(client, "cmd/load-generator", f"{REMOTE_ROOT}/cmd/load-generator")
    upload_tree(client, "internal/loadbalancer", f"{REMOTE_ROOT}/internal/loadbalancer")
    upload_tree(client, "internal/loadgenerator", f"{REMOTE_ROOT}/internal/loadgenerator")


def download_results(client) -> None:
    sftp = client.open_sftp()
    try:
        for filename in (
            "results.csv",
            "1_backend.json",
            "3_backends.json",
            "1_backend_lb_status.json",
            "1_backend_lb_metrics.json",
            "3_backends_lb_status.json",
            "3_backends_lb_metrics.json",
            "lb.log",
        ):
            remote = posixpath.join(REMOTE_ROOT, filename)
            local = PROJECT_ROOT / filename
            sftp.get(remote, str(local))
            print(f"  Downloaded {filename}")
    finally:
        sftp.close()


def main() -> int:
    if not check_backends():
        print("At least one backend is unhealthy. Deploy or repair the backends before comparing them.")
        return 1

    print("\n=== Uploading experiment sources to Sys1 ===")
    client = connect_ssh(SYS1_SSH_PORT)
    try:
        upload_experiment_sources(client)
        ssh_exec(client, f"chmod +x {REMOTE_ROOT}/run_experiments.sh")
        ssh_exec(client, "pkill -f '[l]oad_balancer' || true")

        print("\n=== Running experiments on Sys1 ===")
        started = time.monotonic()
        ssh_exec(client, f"cd {REMOTE_ROOT} && ./run_experiments.sh", timeout=600)
        print(f"Experiments completed in {time.monotonic() - started:.1f}s")

        print("\n=== Downloading results ===")
        download_results(client)
    finally:
        print("\n=== Restoring the three-backend load balancer ===")
        restore_three_backend_load_balancer(client)
        client.close()

    print("Experiment artifacts are synchronized to the project folder.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
