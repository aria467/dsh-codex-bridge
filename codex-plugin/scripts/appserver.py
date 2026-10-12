"""Small JSON-RPC client for the local Codex app-server Unix WebSocket socket."""

import base64
import hashlib
import json
import os
import socket
import struct
import time
from pathlib import Path


DEFAULT_SOCKET = Path.home() / ".codex/app-server-control/app-server-control.sock"


class AppServerError(RuntimeError):
    pass


class AppServer:
    def __init__(self, socket_path=DEFAULT_SOCKET, timeout=10):
        self.socket_path = str(socket_path)
        self.timeout = timeout
        self.sock = None
        self.buffer = b""
        self.next_id = 1

    def __enter__(self):
        self.sock = socket.socket(socket.AF_UNIX)
        self.sock.settimeout(self.timeout)
        self.sock.connect(self.socket_path)
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        request = (
            "GET / HTTP/1.1\r\nHost: localhost\r\nUpgrade: websocket\r\n"
            f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n"
        )
        self.sock.sendall(request.encode("ascii"))
        head = self._read_until(b"\r\n\r\n", 8192)
        if not head.startswith(b"HTTP/1.1 101 "):
            raise AppServerError("Codex app-server refused WebSocket upgrade")
        accept = base64.b64encode(hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest())
        if b"sec-websocket-accept: " + accept.lower() not in head.lower():
            raise AppServerError("invalid WebSocket handshake")
        self.request("initialize", {
            "clientInfo": {"name": "dsh_codex_bridge", "title": "DSH Codex Bridge", "version": "0.1.0"}
        })
        self.notify("initialized", {})
        return self

    def __exit__(self, *_):
        if self.sock:
            self.sock.close()

    def _read_until(self, marker, limit):
        while marker not in self.buffer:
            if len(self.buffer) > limit:
                raise AppServerError("WebSocket handshake too large")
            chunk = self.sock.recv(4096)
            if not chunk:
                raise AppServerError("app-server closed connection")
            self.buffer += chunk
        offset = self.buffer.index(marker) + len(marker)
        result, self.buffer = self.buffer[:offset], self.buffer[offset:]
        return result

    def _read_exact(self, count):
        while len(self.buffer) < count:
            chunk = self.sock.recv(max(4096, count - len(self.buffer)))
            if not chunk:
                raise AppServerError("app-server closed connection")
            self.buffer += chunk
        result, self.buffer = self.buffer[:count], self.buffer[count:]
        return result

    def _send_frame(self, message, opcode=1):
        payload = message if isinstance(message, bytes) else json.dumps(message, separators=(",", ":")).encode("utf-8")
        length = len(payload)
        header = bytes([0x80 | opcode])
        if length < 126:
            header += bytes([0x80 | length])
        elif length < 65536:
            header += bytes([0x80 | 126]) + struct.pack("!H", length)
        else:
            header += bytes([0x80 | 127]) + struct.pack("!Q", length)
        mask = os.urandom(4)
        masked = bytes(byte ^ mask[i % 4] for i, byte in enumerate(payload))
        self.sock.sendall(header + mask + masked)

    def _recv_message(self):
        while True:
            first, second = self._read_exact(2)
            opcode, length = first & 0x0F, second & 0x7F
            if length == 126:
                length = struct.unpack("!H", self._read_exact(2))[0]
            elif length == 127:
                length = struct.unpack("!Q", self._read_exact(8))[0]
            mask = self._read_exact(4) if second & 0x80 else None
            data = self._read_exact(length)
            if mask:
                data = bytes(byte ^ mask[i % 4] for i, byte in enumerate(data))
            if opcode == 9:
                self._send_frame(data, opcode=10)
                continue
            if opcode == 8:
                raise AppServerError("app-server closed WebSocket")
            if opcode != 1:
                continue
            return json.loads(data)

    def notify(self, method, params):
        self._send_frame({"method": method, "params": params})

    def request(self, method, params):
        request_id = self.next_id
        self.next_id += 1
        self._send_frame({"id": request_id, "method": method, "params": params})
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            message = self._recv_message()
            if message.get("id") != request_id:
                continue
            if "error" in message:
                raise AppServerError(f"{method}: {message['error']}")
            return message["result"]
        raise AppServerError(f"{method}: timed out")


def read_thread(thread_id):
    with AppServer() as client:
        return client.request("thread/read", {"threadId": thread_id, "includeTurns": False})["thread"]
