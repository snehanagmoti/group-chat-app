"""Read-only acceptance probe for the four assigned lab machines."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lab_config import (
    SYS1_LB_PUBLIC_PORT,
    SYS1_SSH_PORT,
    SYS2_SSH_PORT,
    SYS3_SSH_PORT,
    SYS4_SSH_PORT,
    connect_ssh,
)


@dataclass(frozen=True)
class System:
    name: str
    ssh_port: int
    role: str


SYSTEMS = (
    System("stu70_sys1", SYS1_SSH_PORT, "load balancer + frontend"),
    System("stu70_sys2", SYS2_SSH_PORT, "backend 1"),
    System("stu70_sys3", SYS3_SSH_PORT, "backend 2"),
    System("stu70_sys4", SYS4_SSH_PORT, "backend 3"),
)


def run(client, command: str) -> tuple[int, str]:
    _, stdout, stderr = client.exec_command(command, timeout=30)
    output = stdout.read().decode("utf-8", errors="replace").strip()
    error = stderr.read().decode("utf-8", errors="replace").strip()
    status = stdout.channel.recv_exit_status()
    if error:
        output = f"{output}\nSTDERR: {error}".strip()
    return status, output


def probe(system: System) -> None:
    print(f"\n=== {system.name} ({system.role}, SSH {system.ssh_port}) ===")
    client = connect_ssh(system.ssh_port)
    try:
        checks = [
            ("hostname", "hostname"),
            ("addresses", "hostname -I"),
            ("listeners", "ss -ltn"),
            (
                "app processes",
                "ps -eo pid,args | grep -E '[s]erver/server.py|[l]oad_balancer|[c]lient/serve.py' || true",
            ),
            ("project", "test -d group-chat-app && echo present || echo missing"),
        ]
        if system.ssh_port == SYS1_SSH_PORT:
            checks.extend(
                [
                    ("frontend", "curl -k -sS -o /dev/null -w '%{http_code}' https://127.0.0.1:3000/"),
                    ("frontend config", f"curl -k -sS https://127.0.0.1:3000/config.js | grep -F 'BACKEND_PORT = {SYS1_LB_PUBLIC_PORT}'"),
                    ("LB health", "curl -sS http://127.0.0.1:4000/lb/health"),
                    ("LB status", "curl -sS http://127.0.0.1:4000/lb/status"),
                    ("LB metrics", "curl -sS http://127.0.0.1:4000/lb/metrics"),
                    ("LB log tail", "tail -n 30 group-chat-app/lb.log"),
                    ("frontend log tail", "tail -n 20 group-chat-app/frontend.log"),
                ]
            )
        else:
            checks.extend(
                [
                    ("backend health", "curl -k -sS https://127.0.0.1:5000/health"),
                    ("backend log tail", "tail -n 40 group-chat-app/server.log"),
                ]
            )

        for label, command in checks:
            status, output = run(client, command)
            print(f"[{label}] exit={status}\n{output or '<no output>'}")
    finally:
        client.close()


def main() -> None:
    for system in SYSTEMS:
        probe(system)


if __name__ == "__main__":
    main()
