from lab_config import SYS4_SSH_PORT, connect_ssh

client = connect_ssh(SYS4_SSH_PORT)
try:
    stdin, stdout, stderr = client.exec_command("cat group-chat-app/server.log")
    print(stdout.read().decode())
finally:
    client.close()
