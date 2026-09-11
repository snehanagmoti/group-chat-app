import argparse
import asyncio
import aiohttp
import random
import time
import string
import ssl

async def generate_user_load(user_id, num_messages, min_len, max_len, min_interval, max_interval, base_url, stats):
    ssl_context = ssl.create_default_context()
    ssl_context.check_hostname = False
    ssl_context.verify_mode = ssl.CERT_NONE

    async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=ssl_context)) as session:
        for i in range(num_messages):
            # Wait for random interval
            interval = random.uniform(min_interval, max_interval)
            await asyncio.sleep(interval)
            
            # Generate random message
            msg_length = random.randint(min_len, max_len)
            msg = ''.join(random.choices(string.ascii_letters + string.digits + " ", k=msg_length))
            
            payload = {
                "client-name": f"load_user_{user_id}",
                "msg": msg
            }
            
            # Send message
            start_time = time.monotonic()
            try:
                async with session.post(f"{base_url}/message", json=payload) as resp:
                    resp_data = await resp.json()
                    latency = time.monotonic() - start_time
                    stats['message_requests'] += 1
                    stats['message_latency'].append(latency)
                    if resp.status == 200:
                        stats['message_success'] += 1
                    else:
                        stats['message_fail'] += 1
            except Exception as e:
                stats['message_fail'] += 1

            # Get feed
            start_time = time.monotonic()
            try:
                async with session.get(f"{base_url}/feed") as resp:
                    await resp.json()
                    latency = time.monotonic() - start_time
                    stats['feed_requests'] += 1
                    stats['feed_latency'].append(latency)
                    if resp.status == 200:
                        stats['feed_success'] += 1
                    else:
                        stats['feed_fail'] += 1
            except Exception as e:
                stats['feed_fail'] += 1

async def main():
    parser = argparse.ArgumentParser(description="Lab 6 Load Generator")
    parser.add_argument("--url", type=str, default="https://127.0.0.1:8080", help="Load Balancer URL")
    parser.add_argument("--users", type=int, default=10, help="Number of concurrent users")
    parser.add_argument("--messages", type=int, default=10, help="Number of messages per user")
    parser.add_argument("--min-len", type=int, default=10, help="Minimum message length")
    parser.add_argument("--max-len", type=int, default=100, help="Maximum message length")
    parser.add_argument("--min-interval", type=float, default=0.1, help="Minimum interval between messages (seconds)")
    parser.add_argument("--max-interval", type=float, default=1.0, help="Maximum interval between messages (seconds)")
    
    args = parser.parse_args()
    
    print(f"Starting load generation with {args.users} users...")
    stats = {
        'message_requests': 0, 'message_success': 0, 'message_fail': 0, 'message_latency': [],
        'feed_requests': 0, 'feed_success': 0, 'feed_fail': 0, 'feed_latency': []
    }
    
    tasks = []
    for user_id in range(args.users):
        tasks.append(generate_user_load(
            user_id, args.messages, args.min_len, args.max_len,
            args.min_interval, args.max_interval, args.url, stats
        ))
        
    start_time = time.time()
    await asyncio.gather(*tasks)
    total_time = time.time() - start_time
    
    print("\n--- Load Test Results ---")
    print(f"Total Time: {total_time:.2f}s")
    
    if stats['message_requests'] > 0:
        avg_msg_lat = sum(stats['message_latency']) / len(stats['message_latency'])
        print(f"POST /message -> Total: {stats['message_requests']}, Success: {stats['message_success']}, Fail: {stats['message_fail']}, Avg Latency: {avg_msg_lat*1000:.2f}ms")
        
    if stats['feed_requests'] > 0:
        avg_feed_lat = sum(stats['feed_latency']) / len(stats['feed_latency'])
        print(f"GET /feed     -> Total: {stats['feed_requests']}, Success: {stats['feed_success']}, Fail: {stats['feed_fail']}, Avg Latency: {avg_feed_lat*1000:.2f}ms")

if __name__ == "__main__":
    asyncio.run(main())
