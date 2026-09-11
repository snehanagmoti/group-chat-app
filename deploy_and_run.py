"""Deploy the messaging backends, Go load balancer, and frontend to the lab VMs."""

from __future__ import annotations

import argparse
import os
import posixpath
import shlex
import time
from pathlib import Path

import paramiko

from lab_config import (
    BACKEND_URLS,
    LAB_HOST,
    SYS1_SSH_PORT,
    SYS1_LB_PUBLIC_PORT,
    SYS2_SSH_PORT,
    SYS3_SSH_PORT,
    SYS4_SSH_PORT,
    connect_ssh,
    ssh_password,
)


PROJECT_ROOT = Path(__file__).resolve().parent
REMOTE_ROOT = "group-chat-app"
BACKEND_PORT = int(os.environ.get("PORT", "5000"))
LB_PORT = int(os.environ.get("LB_PORT", "4000"))
PUBLIC_LB_PORT = int(os.environ.get("PUBLIC_LB_PORT", str(SYS1_LB_PUBLIC_PORT)))
FRONTEND_PORT = int(os.environ.get("FRONTEND_PORT", "3000"))
PIP_FLAGS = os.environ.get("LAB_PIP_FLAGS", "--break-system-packages")


def ssh_exec(
    client: paramiko.SSHClient,
    command: str,
    *,
    sudo: bool = False,
    timeout: int = 180,
) -> str:
    printable = f"sudo {command}" if sudo else command
    print(f"  > {printable}")
    actual = command
    if sudo:
        actual = f"sudo -S -p '' sh -c {shlex.quote(command)}"

    stdin, stdout, stderr = client.exec_command(actual, timeout=timeout)
    if sudo:
        stdin.write(ssh_password() + "\n")
        stdin.flush()

    output = stdout.read().decode("utf-8", errors="replace")
    error_output = stderr.read().decode("utf-8", errors="replace")
    exit_status = stdout.channel.recv_exit_status()
    if output.strip():
        print(output.rstrip())
    if error_output.strip():
        print(error_output.rstrip())
    if exit_status != 0:
        raise RuntimeError(f"Remote command failed with exit status {exit_status}: {printable}")
    return output


def _ensure_remote_directory(sftp: paramiko.SFTPClient, remote_directory: str) -> None:
    current = ""
    for component in remote_directory.strip("/").split("/"):
        current = posixpath.join(current, component)
        try:
            sftp.stat(current)
        except OSError:
            sftp.mkdir(current)


def upload_file(
    client: paramiko.SSHClient,
    local_path: str | Path,
    remote_path: str,
) -> None:
    source = PROJECT_ROOT / local_path
    if not source.is_file():
        raise FileNotFoundError(source)
    sftp = client.open_sftp()
    try:
        _ensure_remote_directory(sftp, posixpath.dirname(remote_path))
        print(f"  Uploading {source.relative_to(PROJECT_ROOT)} -> {remote_path}")
        sftp.put(str(source), remote_path)
    finally:
        sftp.close()


def upload_tree(
    client: paramiko.SSHClient,
    local_directory: str | Path,
    remote_directory: str,
) -> None:
    source_root = PROJECT_ROOT / local_directory
    if not source_root.is_dir():
        raise FileNotFoundError(source_root)
    sftp = client.open_sftp()
    try:
        _ensure_remote_directory(sftp, remote_directory)
        for source in sorted(path for path in source_root.rglob("*") if path.is_file()):
            if "__pycache__" in source.parts or source.suffix == ".pyc":
                continue
            relative = source.relative_to(source_root).as_posix()
            destination = posixpath.join(remote_directory, relative)
            _ensure_remote_directory(sftp, posixpath.dirname(destination))
            print(f"  Uploading {source.relative_to(PROJECT_ROOT)} -> {destination}")
            sftp.put(str(source), destination)
    finally:
        sftp.close()


def setup_backend(ssh_port: int, system_name: str) -> None:
    print(f"\n=== Deploying backend to {system_name} ({LAB_HOST}:{ssh_port}) ===")
    client = connect_ssh(ssh_port)
    try:
        ssh_exec(client, f"mkdir -p {REMOTE_ROOT}/server")
        for local, remote in (
            ("server/server.py", f"{REMOTE_ROOT}/server/server.py"),
            ("server/db.py", f"{REMOTE_ROOT}/server/db.py"),
            ("server/requirements.txt", f"{REMOTE_ROOT}/server/requirements.txt"),
            (".env", f"{REMOTE_ROOT}/.env"),
            ("cert.pem", f"{REMOTE_ROOT}/cert.pem"),
            ("key.pem", f"{REMOTE_ROOT}/key.pem"),
        ):
            upload_file(client, local, remote)

        ssh_exec(
            client,
            f"python3 -m pip install {PIP_FLAGS} -r {REMOTE_ROOT}/server/requirements.txt",
            timeout=300,
        )
        ssh_exec(client, f"fuser -k {BACKEND_PORT}/tcp || true")
        ssh_exec(
            client,
            f"cd {REMOTE_ROOT} && rm -f server/chat.db* && python3 -c 'from server.db import init_db; init_db()' && PORT={BACKEND_PORT} "
            "setsid -f python3 -m uvicorn server.server:app --host 0.0.0.0 --port $PORT --workers 4 --ssl-keyfile key.pem --ssl-certfile cert.pem > server.log 2>&1 < /dev/null",
        )
        time.sleep(3)
        ssh_exec(
            client,
            f"curl -k --fail --silent --show-error https://127.0.0.1:{BACKEND_PORT}/health",
        )
    finally:
        client.close()


def _upload_go_project(client: paramiko.SSHClient) -> None:
    upload_file(client, "go.mod", f"{REMOTE_ROOT}/go.mod")
    upload_tree(client, "cmd/load-balancer", f"{REMOTE_ROOT}/cmd/load-balancer")
    upload_tree(client, "internal/loadbalancer", f"{REMOTE_ROOT}/internal/loadbalancer")


def setup_load_balancer(ssh_port: int = SYS1_SSH_PORT) -> None:
    if len(BACKEND_URLS) != 3:
        raise ValueError("LAB_BACKEND_URLS must contain exactly three backend URLs")

    print(f"\n=== Deploying load balancer and frontend to Sys1 ({LAB_HOST}:{ssh_port}) ===")
    client = connect_ssh(ssh_port)
    try:
        ssh_exec(client, "apt-get update", sudo=True, timeout=300)
        ssh_exec(client, "apt-get install -y golang-go python3-pip", sudo=True, timeout=300)
        ssh_exec(client, f"mkdir -p {REMOTE_ROOT}")

        _upload_go_project(client)
        upload_file(client, "cert.pem", f"{REMOTE_ROOT}/cert.pem")
        upload_file(client, "key.pem", f"{REMOTE_ROOT}/key.pem")
        upload_tree(client, "client", f"{REMOTE_ROOT}/client")

        ssh_exec(client, "pkill -f '[l]oad_balancer' || true")
        ssh_exec(client, f"cd {REMOTE_ROOT} && go build -o load_balancer ./cmd/load-balancer")
        backends = ",".join(BACKEND_URLS)
        ssh_exec(
            client,
            f"cd {REMOTE_ROOT} && setsid -f ./load_balancer "
            f"-backends {shlex.quote(backends)} -port {LB_PORT} "
            "-backend-insecure-skip-verify "
            "-load-threshold 50 -backend-timeout 30s "
            "> lb.log 2>&1 < /dev/null",
        )
        time.sleep(3)
        ssh_exec(
            client,
            f"curl --fail --silent --show-error http://127.0.0.1:{LB_PORT}/lb/health",
        )

        ssh_exec(
            client,
            f"python3 -m pip install {PIP_FLAGS} fastapi 'uvicorn[standard]' python-dotenv",
            timeout=300,
        )
        ssh_exec(client, "pkill -f '[c]lient/serve.py' || true")
        ssh_exec(
            client,
            f"cd {REMOTE_ROOT} && FRONTEND_PORT={FRONTEND_PORT} "
            f"BACKEND_PORT={PUBLIC_LB_PORT} "
            "setsid -f python3 client/serve.py > frontend.log 2>&1 < /dev/null",
        )
        time.sleep(3)
        ssh_exec(
            client,
            f"curl -k --fail --silent --show-error https://127.0.0.1:{FRONTEND_PORT}/ >/dev/null",
        )
    finally:
        client.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "target",
        nargs="?",
        choices=("all", "backends", "lb"),
        default="all",
        help="Deployment portion to run (default: all)",
    )
    args = parser.parse_args()

    if args.target in {"all", "backends"}:
        for port, name in (
            (SYS2_SSH_PORT, "Sys2"),
            (SYS3_SSH_PORT, "Sys3"),
            (SYS4_SSH_PORT, "Sys4"),
        ):
            setup_backend(port, name)
    if args.target in {"all", "lb"}:
        setup_load_balancer()

    print("\nDeployment completed and every started service passed its health check.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
