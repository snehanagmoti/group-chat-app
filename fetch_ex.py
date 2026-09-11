import sys
import paramiko
from lab_config import connect_ssh, SYS2_SSH_PORT

client = connect_ssh(SYS2_SSH_PORT)
_, out, err = client.exec_command(
    "python3 -c \"import sys; lines = open('group-chat-app/server.log', 'rb').read().decode('utf-8', 'ignore').split('\\n'); excs = [i for i, line in enumerate(lines) if 'Exception in ASGI application' in line]; print('\\n'.join(lines[excs[-1]:excs[-1]+80])) if excs else print('No exception found')\""
)
print(out.read().decode())
client.close()
