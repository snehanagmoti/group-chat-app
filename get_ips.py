from lab_config import (
    SYS1_SSH_PORT,
    SYS2_SSH_PORT,
    SYS3_SSH_PORT,
    SYS4_SSH_PORT,
    connect_ssh,
)

def get_ip(port):
    client = connect_ssh(port)
    try:
        stdin, stdout, stderr = client.exec_command("hostname -I")
        addresses = stdout.read().decode().strip().split()
        if not addresses:
            raise RuntimeError(f"No IP address returned for SSH port {port}")
        return addresses[0]
    finally:
        client.close()

print("sys1 IP:", get_ip(SYS1_SSH_PORT))
print("sys2 IP:", get_ip(SYS2_SSH_PORT))
print("sys3 IP:", get_ip(SYS3_SSH_PORT))
print("sys4 IP:", get_ip(SYS4_SSH_PORT))
