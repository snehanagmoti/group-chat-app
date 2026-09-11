from lab_config import (
    SYS1_SSH_PORT,
    SYS2_SSH_PORT,
    SYS3_SSH_PORT,
    SYS4_SSH_PORT,
    connect_ssh,
)

def check_ssh(port):
    try:
        print(f"Connecting to SSH port {port}...")
        client = connect_ssh(port)
        stdin, stdout, stderr = client.exec_command("go version")
        print(f"[port {port}] go version:", stdout.read().decode().strip())
    except Exception as e:
        print(f"[port {port}] Failed to connect: {e}")
    finally:
        if "client" in locals():
            client.close()

if __name__ == "__main__":
    for port in (SYS1_SSH_PORT, SYS2_SSH_PORT, SYS3_SSH_PORT, SYS4_SSH_PORT):
        check_ssh(port)
