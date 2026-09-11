import argparse
import sys
import threading
import select
import socket
import socketserver

from lab_config import SYS1_SSH_PORT, connect_ssh

def handler(chan, host, port):
    sock = socket.socket()
    try:
        sock.connect((host, port))
    except Exception as e:
        print(f"Forwarding request to {host}:{port} failed: {e}")
        return
    
    print(f"Connected! Tunnel open {chan.origin_addr} -> {chan.getpeername()} -> {host}:{port}")
    while True:
        r, w, x = select.select([sock, chan], [], [])
        if sock in r:
            data = sock.recv(1024)
            if len(data) == 0: break
            chan.sendall(data)
        if chan in r:
            data = chan.recv(1024)
            if len(data) == 0: break
            sock.sendall(data)
    chan.close()
    sock.close()
    print("Tunnel closed")

def reverse_forward_tunnel(server_port, remote_host, remote_port, transport):
    transport.request_port_forward('', server_port)
    while True:
        chan = transport.accept(1000)
        if chan is None:
            continue
        thr = threading.Thread(target=handler, args=(chan, remote_host, remote_port))
        thr.daemon = True
        thr.start()

def forward_tunnel(local_port, remote_host, remote_port, transport):
    class SubHander(socketserver.BaseRequestHandler):
        def handle(self):
            try:
                chan = transport.open_channel('direct-tcpip',
                                              (remote_host, remote_port),
                                              self.request.getpeername())
            except Exception as e:
                return
            if chan is None:
                return
            while True:
                r, w, x = select.select([self.request, chan], [], [])
                if self.request in r:
                    data = self.request.recv(1024)
                    if len(data) == 0: break
                    chan.sendall(data)
                if chan in r:
                    data = chan.recv(1024)
                    if len(data) == 0: break
                    self.request.sendall(data)
            chan.close()
            self.request.close()
    
    class ThreadingTCPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
        allow_reuse_address = True
        daemon_threads = True

    server = ThreadingTCPServer(('127.0.0.1', local_port), SubHander)
    server.serve_forever()

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Forward a local port to a service on Sys1.")
    parser.add_argument("--local-port", type=int, default=8082)
    parser.add_argument("--remote-port", type=int, default=4000)
    parser.add_argument("--remote-host", default="127.0.0.1")
    parser.add_argument("--ssh-port", type=int, default=SYS1_SSH_PORT)
    args = parser.parse_args()

    print("Connecting...")
    client = connect_ssh(args.ssh_port)
    print(
        f"Port forwarding 127.0.0.1:{args.local_port} -> "
        f"{args.remote_host}:{args.remote_port}"
    )
    try:
        forward_tunnel(
            args.local_port,
            args.remote_host,
            args.remote_port,
            client.get_transport(),
        )
    except KeyboardInterrupt:
        print("Exiting...")
        client.close()
        sys.exit(0)
