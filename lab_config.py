"""Shared lab topology and SSH configuration.

Secrets are read from the environment or prompted interactively; they are never
stored in source files. Override any default with the LAB_* variables documented
in .env.example.
"""

from __future__ import annotations

import getpass
import os
from functools import lru_cache

import paramiko


LAB_HOST = os.environ.get("LAB_HOST", "10.1.75.53")
LAB_USER = os.environ.get("LAB_USER", "student")
SYS1_SSH_PORT = int(os.environ.get("SYS1_SSH_PORT", "2237"))
SYS2_SSH_PORT = int(os.environ.get("SYS2_SSH_PORT", "2238"))
SYS3_SSH_PORT = int(os.environ.get("SYS3_SSH_PORT", "2239"))
SYS4_SSH_PORT = int(os.environ.get("SYS4_SSH_PORT", "2240"))


def mapped_application_port(ssh_port: int, container_port: int) -> int:
    """Return the Docker-host port assigned by the lab's SSH-port mapping rule."""
    if container_port not in {3000, 4000, 5000, 6000, 7000}:
        raise ValueError(f"unsupported mapped container port: {container_port}")
    return container_port + (ssh_port - 2000)


SYS1_FRONTEND_PUBLIC_PORT = int(
    os.environ.get(
        "SYS1_FRONTEND_PUBLIC_PORT",
        str(mapped_application_port(SYS1_SSH_PORT, 3000)),
    )
)
SYS1_LB_PUBLIC_PORT = int(
    os.environ.get(
        "SYS1_LB_PUBLIC_PORT",
        str(mapped_application_port(SYS1_SSH_PORT, 4000)),
    )
)

BACKEND_URLS = [
    value.strip()
    for value in os.environ.get(
        "LAB_BACKEND_URLS",
        "https://172.17.0.39:5000,https://172.17.0.40:5000,https://172.17.0.41:5000",
    ).split(",")
    if value.strip()
]


@lru_cache(maxsize=1)
def ssh_password() -> str:
    password = os.environ.get("LAB_SSH_PASSWORD", "")
    if password:
        return password
    return getpass.getpass(f"SSH password for {LAB_USER}@{LAB_HOST}: ")


def connect_ssh(port: int, *, timeout: int = 10) -> paramiko.SSHClient:
    client = paramiko.SSHClient()
    client.load_system_host_keys()
    if os.environ.get("LAB_SSH_STRICT_HOST_KEY", "0") == "1":
        client.set_missing_host_key_policy(paramiko.RejectPolicy())
    else:
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(
        hostname=LAB_HOST,
        port=port,
        username=LAB_USER,
        password=ssh_password(),
        timeout=timeout,
    )
    return client
