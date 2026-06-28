"""Localhost EventBus for UI/system events.

The bus carries newline-delimited JSON Event envelopes. It does not know about
FSMs, orchestrators, commands, or hardware.
"""

from __future__ import annotations

import json
import socket
import threading
from typing import Callable, Optional

from core.event import Event


class EventBusClient:
    def __init__(self, host: str = "127.0.0.1", port: int = 8765, timeout: float = 0.35) -> None:
        self.host = host
        self.port = int(port)
        self.timeout = float(timeout)

    def emit(self, event: Event) -> dict:
        payload = json.dumps(event.to_dict(), ensure_ascii=False).encode("utf-8") + b"\n"
        try:
            with socket.create_connection((self.host, self.port), timeout=self.timeout) as sock:
                sock.settimeout(self.timeout)
                sock.sendall(payload)
                data = sock.recv(4096)
        except OSError as exc:
            return {
                "ok": False,
                "accepted": False,
                "authority": "unreachable",
                "reason": str(exc),
            }
        if not data:
            return {"ok": False, "accepted": False, "authority": "unreachable", "reason": "empty_response"}
        try:
            return json.loads(data.decode("utf-8", errors="replace"))
        except json.JSONDecodeError:
            return {"ok": False, "accepted": False, "authority": "unreachable", "reason": "bad_response"}


class EventBusServer:
    def __init__(
        self,
        handler: Callable[[Event], dict],
        host: str = "127.0.0.1",
        port: int = 8765,
    ) -> None:
        self.handler = handler
        self.host = host
        self.port = int(port)
        self._server: Optional[socket.socket] = None
        self._thread: Optional[threading.Thread] = None
        self._running = False

    def start(self) -> bool:
        if self._running:
            return True
        try:
            server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server.bind((self.host, self.port))
            server.listen(8)
            server.settimeout(0.5)
        except OSError:
            self.close()
            return False
        self._server = server
        self._running = True
        self._thread = threading.Thread(target=self._accept_loop, daemon=True, name="event-bus")
        self._thread.start()
        return True

    def close(self) -> None:
        self._running = False
        if self._server is not None:
            try:
                self._server.close()
            except OSError:
                pass
        self._server = None
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(timeout=1.0)
        self._thread = None

    def _accept_loop(self) -> None:
        while self._running:
            try:
                assert self._server is not None
                client, _addr = self._server.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            with client:
                client.settimeout(0.5)
                response = self._handle_client(client)
                client.sendall(json.dumps(response, ensure_ascii=False).encode("utf-8") + b"\n")

    def _handle_client(self, client: socket.socket) -> dict:
        data = b""
        try:
            while b"\n" not in data and len(data) < 65536:
                chunk = client.recv(4096)
                if not chunk:
                    break
                data += chunk
        except OSError as exc:
            return {"ok": False, "accepted": False, "authority": "eventbus", "reason": str(exc)}
        try:
            raw = json.loads(data.decode("utf-8", errors="replace").strip())
            event = Event.from_dict(raw)
        except Exception as exc:
            return {"ok": False, "accepted": False, "authority": "eventbus", "reason": f"bad_event: {exc}"}
        return self.handler(event)
