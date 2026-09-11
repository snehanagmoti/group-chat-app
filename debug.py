import shlex

from lab_config import BACKEND_URLS, SYS1_SSH_PORT, connect_ssh

client = connect_ssh(SYS1_SSH_PORT)

try:
    print("Checking hosts file...")
    stdin, stdout, stderr = client.exec_command("cat /etc/hosts")
    print(stdout.read().decode())

    for backend_url in BACKEND_URLS:
        health_url = backend_url.rstrip("/") + "/health"
        print(f"Checking {health_url}...")
        stdin, stdout, stderr = client.exec_command(
            f"curl -k --fail --show-error --silent {shlex.quote(health_url)}"
        )
        print(stdout.read().decode())
        error = stderr.read().decode()
        if error:
            print(error)
finally:
    client.close()
