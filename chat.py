#!/usr/bin/env python3
"""
P2P LAN Chat — peer-to-peer text messenger over UDP (discovery) + TCP (messaging).

Usage:
    python chat.py <ip_address> <username>

Example:
    python chat.py 127.0.0.1 Alice
    python chat.py 127.0.0.2 Bob
"""

import sys
import socket
import threading
import struct
import time
import select
from datetime import datetime
from collections import OrderedDict

# ─── Configuration ────────────────────────────────────────────────────────────
UDP_PORT   = 55555   # broadcast discovery
TCP_PORT   = 55556   # peer messaging
BCAST_ADDR = "255.255.255.255"
BUF_SIZE   = 4096

# ─── Message types ────────────────────────────────────────────────────────────
MSG_CHAT       = 1   # user chat message
MSG_NAME       = 2   # name handshake
MSG_JOIN       = 3   # (unused over TCP; kept for extensibility)
MSG_LEAVE      = 4   # graceful disconnect

# ─── Protocol helpers ─────────────────────────────────────────────────────────

def encode_msg(msg_type: int, payload: str) -> bytes:
    """Pack: [1 byte type][2 bytes length][n bytes utf-8 payload]"""
    data = payload.encode("utf-8")
    return struct.pack("!BH", msg_type, len(data)) + data


def recv_msg(sock: socket.socket):
    """
    Read exactly one framed message from *sock*.
    Returns (msg_type, payload_str) or raises ConnectionError on EOF.
    """
    header = _recv_exact(sock, 3)
    msg_type, length = struct.unpack("!BH", header)
    payload = _recv_exact(sock, length).decode("utf-8")
    return msg_type, payload


def _recv_exact(sock: socket.socket, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("Connection closed")
        buf += chunk
    return buf


# ─── Logging helpers ──────────────────────────────────────────────────────────

def ts() -> str:
    return datetime.now().strftime("%H:%M:%S")


def log(line: str):
    """Print a log line, then re-display the input prompt."""
    sys.stdout.write(f"\r\033[K{line}\n> ")
    sys.stdout.flush()


# ─── Chat Node ────────────────────────────────────────────────────────────────

class ChatNode:
    def __init__(self, local_ip: str, username: str):
        self.local_ip  = local_ip
        self.username  = username
        self.running   = True

        # ip → (name, tcp_socket)
        self.peers: dict[str, tuple[str, socket.socket]] = {}
        self._peers_lock = threading.Lock()

        # TCP server socket
        self.server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server_sock.bind((local_ip, TCP_PORT))
        self.server_sock.listen(32)

        # UDP socket for sending/receiving broadcasts
        self.udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.udp_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.udp_sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        self.udp_sock.bind((local_ip, UDP_PORT))

    # ── Startup ───────────────────────────────────────────────────────────────

    def start(self):
        threading.Thread(target=self._accept_loop,     daemon=True).start()
        threading.Thread(target=self._udp_listen_loop, daemon=True).start()

        # Announce ourselves to the LAN
        self._broadcast_join()

        # Input loop (main thread)
        self._input_loop()

    # ── Broadcasting ──────────────────────────────────────────────────────────

    def _broadcast_join(self):
        """Send a UDP broadcast so existing peers discover us."""
        pkt = encode_msg(MSG_NAME, self.username)
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sock.sendto(pkt, (BCAST_ADDR, UDP_PORT))
            sock.close()
        except Exception as e:
            log(f"[!] Broadcast error: {e}")

    # ── UDP listener — discovers new peers ────────────────────────────────────

    def _udp_listen_loop(self):
        while self.running:
            try:
                ready, _, _ = select.select([self.udp_sock], [], [], 1.0)
                if not ready:
                    continue
                data, (sender_ip, _) = self.udp_sock.recvfrom(BUF_SIZE)
            except OSError:
                break

            if sender_ip == self.local_ip:
                continue  # ignore our own broadcast

            try:
                msg_type, payload = self._parse_raw(data)
            except Exception:
                continue

            if msg_type == MSG_NAME:
                self._connect_to_peer(sender_ip, payload)

    @staticmethod
    def _parse_raw(data: bytes):
        if len(data) < 3:
            raise ValueError("Too short")
        msg_type, length = struct.unpack("!BH", data[:3])
        payload = data[3:3 + length].decode("utf-8")
        return msg_type, payload

    # ── TCP accept loop — handles inbound connections ─────────────────────────

    def _accept_loop(self):
        while self.running:
            try:
                ready, _, _ = select.select([self.server_sock], [], [], 1.0)
                if not ready:
                    continue
                conn, (peer_ip, _) = self.server_sock.accept()
            except OSError:
                break

            threading.Thread(
                target=self._handle_inbound, args=(conn, peer_ip), daemon=True
            ).start()

    def _handle_inbound(self, conn: socket.socket, peer_ip: str):
        """Receive the peer's name, register them, then listen for messages."""
        try:
            msg_type, name = recv_msg(conn)
            if msg_type != MSG_NAME:
                conn.close()
                return
        except Exception:
            conn.close()
            return

        self._register_peer(peer_ip, name, conn)
        self._peer_recv_loop(peer_ip, conn)

    # ── Outbound TCP connection ───────────────────────────────────────────────

    def _connect_to_peer(self, peer_ip: str, peer_name: str):
        with self._peers_lock:
            if peer_ip in self.peers:
                return  # already connected

        try:
            conn = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            conn.settimeout(5)
            conn.connect((peer_ip, TCP_PORT))
            conn.settimeout(None)
            # Send our name
            conn.sendall(encode_msg(MSG_NAME, self.username))
        except Exception as e:
            log(f"[!] Could not connect to {peer_ip}: {e}")
            return

        self._register_peer(peer_ip, peer_name, conn)
        threading.Thread(
            target=self._peer_recv_loop, args=(peer_ip, conn), daemon=True
        ).start()

    # ── Peer registry ─────────────────────────────────────────────────────────

    def _register_peer(self, peer_ip: str, peer_name: str, conn: socket.socket):
        with self._peers_lock:
            if peer_ip in self.peers:
                conn.close()
                return
            self.peers[peer_ip] = (peer_name, conn)
        log(f"[{ts()}] ++ {peer_name} ({peer_ip}) joined the chat")

    def _remove_peer(self, peer_ip: str):
        with self._peers_lock:
            entry = self.peers.pop(peer_ip, None)
        if entry:
            name, conn = entry
            try:
                conn.close()
            except Exception:
                pass
            log(f"[{ts()}] -- {name} ({peer_ip}) left the chat")

    # ── Receive loop for one peer connection ──────────────────────────────────

    def _peer_recv_loop(self, peer_ip: str, conn: socket.socket):
        while self.running:
            try:
                msg_type, payload = recv_msg(conn)
            except Exception:
                break

            if msg_type == MSG_CHAT:
                with self._peers_lock:
                    entry = self.peers.get(peer_ip)
                name = entry[0] if entry else peer_ip
                log(f"[{ts()}] {name} ({peer_ip}): {payload}")

            elif msg_type == MSG_LEAVE:
                break

        self._remove_peer(peer_ip)

    # ── Sending ───────────────────────────────────────────────────────────────

    def _broadcast_message(self, text: str):
        pkt = encode_msg(MSG_CHAT, text)
        dead = []
        with self._peers_lock:
            snapshot = dict(self.peers)
        for ip, (name, conn) in snapshot.items():
            try:
                conn.sendall(pkt)
            except Exception:
                dead.append(ip)
        for ip in dead:
            self._remove_peer(ip)

    def _send_leave(self):
        pkt = encode_msg(MSG_LEAVE, "")
        with self._peers_lock:
            snapshot = dict(self.peers)
        for ip, (name, conn) in snapshot.items():
            try:
                conn.sendall(pkt)
            except Exception:
                pass

    # ── Input loop ────────────────────────────────────────────────────────────

    def _input_loop(self):
        print(f"\n  P2P Chat  |  {self.username} @ {self.local_ip}")
        print(f"  Commands: /quit  /peers\n")
        try:
            while self.running:
                sys.stdout.write("> ")
                sys.stdout.flush()
                try:
                    line = input()
                except EOFError:
                    break

                if not line.strip():
                    continue

                if line.strip() == "/quit":
                    break

                if line.strip() == "/peers":
                    with self._peers_lock:
                        if self.peers:
                            for ip, (name, _) in self.peers.items():
                                print(f"  {name} @ {ip}")
                        else:
                            print("  (no peers connected)")
                    continue

                # Regular chat message
                log(f"[{ts()}] You: {line}")
                self._broadcast_message(line)

        finally:
            self.running = False
            self._send_leave()
            self.server_sock.close()
            self.udp_sock.close()
            print("\n[Disconnected]")


# ─── Entry point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(f"Usage: python {sys.argv[0]} <ip_address> <username>")
        sys.exit(1)

    ip, name = sys.argv[1], sys.argv[2]

    # Validate IP
    try:
        socket.inet_aton(ip)
    except socket.error:
        print(f"Invalid IP address: {ip}")
        sys.exit(1)

    node = ChatNode(ip, name)
    node.start()
