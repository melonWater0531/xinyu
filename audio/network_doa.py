"""Network DOA receiver compatible with the existing ReSpeakerDOA interface.

The ReSpeaker/XVF host process may run on Windows, reCamera, or another Linux
machine. It sends newline-delimited DOA readings to this TCP listener, so WSL
does not need direct USB access.
"""
from __future__ import annotations

import json
import socket
import threading
import time
from typing import Optional, Tuple

from audio.doa import parse_doa_line
from utils.logger import get_logger

logger = get_logger(__name__)


class NetworkDOA:
    """Receive DOA text lines over TCP and expose ReSpeakerDOA-style state."""

    def __init__(
        self,
        host: str = "0.0.0.0",
        port: int = 9999,
        speech_hold_sec: float = 0.8,
    ) -> None:
        self.host = host
        self.port = int(port)
        self.speech_hold_sec = float(speech_hold_sec)
        self.source = "tcp"

        self._server: Optional[socket.socket] = None
        self._client: Optional[socket.socket] = None
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._lock = threading.Lock()
        self._doa_deg = 0.0
        self._last_update = 0.0
        self._speech_until = 0.0
        self._explicit_speech: Optional[bool] = None
        self._client_address = ""
        self._packet_count = 0
        self._last_line = ""

    def open(self) -> bool:
        if self._server is not None:
            return True
        try:
            server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server.bind((self.host, self.port))
            server.listen(2)
            server.settimeout(0.5)
            self._server = server
            logger.info("🎤 Network DOA listening on %s:%d", self.host, self.port)
            return True
        except OSError as exc:
            logger.warning("Network DOA listen failed on %s:%d: %s", self.host, self.port, exc)
            self.close()
            return False

    def start(self, interval: float = 0.1) -> None:
        del interval
        if self._running:
            return
        if self._server is None and not self.open():
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._accept_loop,
            daemon=True,
            name="network-doa",
        )
        self._thread.start()

    def _accept_loop(self) -> None:
        while self._running:
            if self._client is None:
                try:
                    assert self._server is not None
                    client, addr = self._server.accept()
                    client.settimeout(0.5)
                    self._client = client
                    self._client_address = f"{addr[0]}:{addr[1]}"
                    logger.info("🎤 Network DOA sender connected: %s", self._client_address)
                except socket.timeout:
                    continue
                except OSError:
                    break

            buffer = ""
            while self._running and self._client is not None:
                try:
                    data = self._client.recv(4096)
                    if not data:
                        break
                    buffer += data.decode("utf-8", errors="replace")
                    while "\n" in buffer:
                        line, buffer = buffer.split("\n", 1)
                        self._consume_line(line)
                except socket.timeout:
                    continue
                except OSError:
                    break

            if self._client is not None:
                try:
                    self._client.close()
                except OSError:
                    pass
            self._client = None
            self._client_address = ""

    def _consume_line(self, line: str) -> None:
        line = line.strip()
        if not line:
            return
        angle = parse_doa_line(line)
        speech: Optional[bool] = None
        if line.startswith("{"):
            try:
                payload = json.loads(line)
                if "speech" in payload:
                    speech = bool(payload["speech"])
                elif "has_speech" in payload:
                    speech = bool(payload["has_speech"])
            except (json.JSONDecodeError, TypeError):
                pass
        if angle is None:
            return

        now = time.monotonic()
        with self._lock:
            self._doa_deg = float(angle)
            self._last_update = now
            self._explicit_speech = speech
            self._speech_until = now + self.speech_hold_sec if speech is not False else now
            self._packet_count += 1
            self._last_line = line[:240]

    def read(self) -> Tuple[float, bool]:
        return self.doa, self.has_speech

    @property
    def doa(self) -> float:
        with self._lock:
            return self._doa_deg

    @property
    def has_speech(self) -> bool:
        with self._lock:
            if self._last_update <= 0:
                return False
            if self._explicit_speech is False:
                return False
            return time.monotonic() <= self._speech_until

    @property
    def age(self) -> float:
        with self._lock:
            last_update = self._last_update
        return 999.0 if last_update <= 0 else max(0.0, time.monotonic() - last_update)

    @staticmethod
    def to_gimbal_yaw(
        doa_deg: float,
        current_yaw: float = 180.0,
        max_step: float = 15.0,
    ) -> float:
        signed = float(doa_deg)
        if signed > 180.0:
            signed -= 360.0
        target = max(1.0, min(345.0, 180.0 + signed))
        delta = target - float(current_yaw)
        if abs(delta) > max_step:
            target = float(current_yaw) + (max_step if delta > 0 else -max_step)
        return max(1.0, min(345.0, target))

    def status(self) -> dict:
        with self._lock:
            doa_deg = round(self._doa_deg, 1) if self._last_update else None
            last_update = self._last_update
            explicit_speech = self._explicit_speech
            speech_until = self._speech_until
            detail = {
                "source": self.source,
                "listen_host": self.host,
                "listen_port": self.port,
                "sender_connected": self._client is not None,
                "sender": self._client_address,
                "packet_count": self._packet_count,
                "last_line": self._last_line,
                "doa_deg": doa_deg,
            }
        now = time.monotonic()
        detail["has_speech"] = bool(
            last_update > 0 and explicit_speech is not False and now <= speech_until
        )
        detail["age"] = round(999.0 if last_update <= 0 else max(0.0, now - last_update), 2)
        return detail

    def close(self) -> None:
        self._running = False
        for sock in (self._client, self._server):
            if sock is not None:
                try:
                    sock.close()
                except OSError:
                    pass
        self._client = None
        self._server = None
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(timeout=1.5)
        self._thread = None
