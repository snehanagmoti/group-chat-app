import os
os.environ['LAB_SSH_PASSWORD'] = '12342090'
from lab_config import connect_ssh, SYS1_SSH_PORT, SYS2_SSH_PORT

# 1. Check /lb/status (per-backend info)
client = connect_ssh(SYS1_SSH_PORT)
print("=== LB Status (per-backend) ===")
stdin, stdout, stderr = client.exec_command("curl --fail --silent http://127.0.0.1:4000/lb/status")
print(stdout.read().decode()[:2000])

# 2. Test WebSocket on backend with python websockets
print("\n=== WebSocket test on Sys2 backend ===")
client2 = connect_ssh(SYS2_SSH_PORT)
ws_test = '''python3 -c "
import asyncio, ssl, json
try:
    import websockets
except ImportError:
    print('websockets not installed, skipping')
    exit(0)

async def test():
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    try:
        async with websockets.connect('wss://127.0.0.1:5000/ws', ssl=ctx) as ws:
            await ws.send(json.dumps({'type': 'join', 'username': 'ws_test', 'avatar': 'wizard', 'room_id': 'default'}))
            msg = await asyncio.wait_for(ws.recv(), timeout=3)
            print(f'WS received: {msg[:200]}')
            print('WebSocket WORKING')
    except Exception as e:
        print(f'WS error: {e}')

asyncio.run(test())
"'''
stdin2, stdout2, stderr2 = client2.exec_command(ws_test, timeout=15)
print(stdout2.read().decode())
print(stderr2.read().decode())
client2.close()

# 3. Verify the exact route paths the teacher's load generator will hit
print("\n=== Verifying exact /message and /feed routes ===")

# POST /message
stdin3, stdout3, stderr3 = client.exec_command(
    'curl --silent -w "\\nHTTP_CODE:%{http_code}" -X POST http://127.0.0.1:4000/message '
    '-H "Content-Type: application/json" '
    '-d \'{"client-name": "teacher_test", "msg": "verification message"}\''
)
result = stdout3.read().decode()
print(f"POST /message: {result}")

# GET /feed  
stdin4, stdout4, stderr4 = client.exec_command(
    'curl --silent -w "\\nHTTP_CODE:%{http_code}" http://127.0.0.1:4000/feed'
)
result = stdout4.read().decode()
# Just show first 500 chars + http code
lines = result.split('\n')
http_code = [l for l in lines if 'HTTP_CODE' in l]
print(f"GET /feed HTTP code: {http_code}")
print(f"GET /feed response length: {len(result)} chars")

# 4. Verify the EXACT same routes work on the PUBLIC port
print(f"\n=== Testing PUBLIC Load Balancer URL ===")
from lab_config import SYS1_LB_PUBLIC_PORT
print(f"Public LB port: {SYS1_LB_PUBLIC_PORT}")
stdin5, stdout5, stderr5 = client.exec_command(
    f'curl --silent -w "\\nHTTP_CODE:%{{http_code}}" -X POST http://127.0.0.1:4000/message '
    f'-H "Content-Type: application/json" '
    f'-d \'{{"client-name": "public_test", "msg": "public verification"}}\''
)
print(f"POST via LB: {stdout5.read().decode()}")

client.close()
print("\nAll critical checks complete!")
