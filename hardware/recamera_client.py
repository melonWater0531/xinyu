"""
reCamera Gimbal WiFi HTTP Client.

Phase 3: Real hardware control over WiFi.
Connects to reCamera Gimbal 2002W at the configured IP.

Key design:
  - 200ms timeout per request (WiFi may have latency)
  - 3 retries on failure
  - Both delta (relative) and absolute angle modes
  - Falls back to dry-run if unreachable
  - Simulates responses when API not available (for testing)

Usage:
    client = RecameraClient(base_url="http://192.168.201.84")
    if client.connect():
        client.send_delta(pan=1.5, tilt=-0.8)  # relative movement
        client.send_absolute(pan=90, tilt=45)   # absolute position
"""

import threading
import time
import json
from typing import Optional, Tuple
from urllib import request, error

from core.event import ControlCommand
from utils.logger import get_logger

logger = get_logger(__name__)

# ══════════════════════════════════════════════════════════════->#  Constants
# ══════════════════════════════════════════════════════════════->
DEFAULT_BASE_URL = "http://192.168.201.84"
DEFAULT_TIMEOUT_MS = 200
DEFAULT_RETRY = 3


# ══════════════════════════════════════════════════════════════->#  RecameraClient
# ══════════════════════════════════════════════════════════════->
class RecameraClient:
    """
    WiFi HTTP client for reCamera Gimbal 2002W.

    Features:
      - Connection health check
      - Delta (relative) and absolute angle commands
      - Automatic retry with backoff
      - Dry-run mode when unreachable
      - Request latency tracking
    """

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        timeout_ms: int = DEFAULT_TIMEOUT_MS,
        retry: int = DEFAULT_RETRY,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout_ms = timeout_ms
        self._timeout_sec = timeout_ms / 1000.0
        self._retry = retry

        # State
        self._connected: bool = False
        self._dry_run: bool = True
        self._last_request_time: float = 0.0
        self._request_count: int = 0
        self._fail_count: int = 0
        self._consecutive_fails: int = 0
        self._last_latency_ms: float = 0.0

        # Transport mode: "http" or "socketio"
        self._transport: str = "http"
        self._sio = None  # Socket.IO client
        self._sio_widget_id: str = "1528e53340ceac14"
        self._sio_path: str = "/dashboard/socket.io"

        # Known endpoints (tried in order)
        self._control_urls = [
            f"{self._base_url}/gimbal/control",
            f"{self._base_url}/api/gimbal",
            f"{self._base_url}/motor/control",
        ]

    # ── Connection ──────────────────────────────────

    def connect(self, dry_run: bool = False) -> bool:
        """
        Establish connection to reCamera.

        Auto-detects:
          - Port 1880 ->Node-RED Dashboard ->Socket.IO
          - Other ports ->HTTP POST /gimbal/control

        Args:
            dry_run: If True, skip real connection (safe testing mode).
        """
        if dry_run:
            self._dry_run = True
            self._connected = False
            logger.info("DRY-RUN mode ->commands NOT sent to %s", self._base_url)
            return True

        # ── Try Socket.IO (Node-RED Dashboard on port 1880) ──
        try:
            import socketio
            sio = socketio.Client(logger=False)
            sio_url = self._base_url
            sio_path = self._sio_path
            connected_event = threading.Event()

            @sio.on("connect")
            def _ok():
                self._connected = True
                connected_event.set()

            @sio.on("disconnect")
            def _dc():
                self._connected = False

            @sio.on("connect_error")
            def _err(data):
                logger.debug("Socket.IO connect_error: %s", data)

            sio.connect(sio_url, socketio_path=sio_path, wait_timeout=3.0)

            if connected_event.wait(timeout=3.0):
                self._sio = sio
                self._transport = "socketio"
                self._dry_run = False
                logger.info("🟢 CONNECTED via Socket.IO ->%s%s", sio_url, sio_path)
                return True
            else:
                try:
                    sio.disconnect()
                except Exception:
                    pass
        except Exception as e:
            logger.debug("Socket.IO probe failed: %s", str(e)[:80])

        # ── Fall back to HTTP ──
        for attempt in range(self._retry):
            for endpoint in ["/device/info", "/gimbal/status", "/"]:
                try:
                    url = f"{self._base_url}{endpoint}"
                    req = request.Request(url, method="GET")
                    resp = request.urlopen(req, timeout=self._timeout_sec)
                    if resp.status in (200, 301, 302):
                        self._connected = True
                        self._dry_run = False
                        self._transport = "http"
                        logger.info("CONNECTED via HTTP ->%s", self._base_url)
                        return True
                except Exception:
                    pass
            if attempt < self._retry - 1:
                time.sleep(0.1)

        # Unreachable ->enter dry-run
        logger.warning("UNREACHABLE at %s ->entering DRY-RUN mode", self._base_url)
        self._dry_run = True
        self._connected = False
        return True

    # ── Control commands ─────────────────────────────

    def apply_command(self, command: ControlCommand) -> bool:
        """Only hardware exit: apply a normalized ControlCommand."""
        if not isinstance(command, ControlCommand):
            raise TypeError("RecameraClient.apply_command requires ControlCommand")
        if command.stop:
            return self.emergency_stop()
        if command.mode == "delta":
            return self.send_delta(float(command.yaw or 0.0), float(command.pitch or 0.0))
        if command.yaw is not None or command.pitch is not None:
            return self.send_absolute(
                float(command.yaw if command.yaw is not None else 180.0),
                float(command.pitch if command.pitch is not None else 90.0),
            )
        return True

    def send_delta(self, pan: float, tilt: float) -> bool:
        """Send relative pan/tilt movement. Auto-routes via Socket.IO or HTTP."""
        self._request_count += 1
        pan = max(-2.5, min(2.5, pan))
        tilt = max(-2.5, min(2.5, tilt))
        if abs(pan) < 0.01 and abs(tilt) < 0.01:
            return True

        if self._dry_run:
            self._consecutive_fails = 0
            logger.debug("[DRY-RUN] pan=%+.2f° tilt=%+.2f°", pan, tilt)
            return True

        # ── Socket.IO transport (Node-RED Dashboard) ──
        if self._transport == "socketio" and self._sio and self._connected:
            try:
                # Send pan via widget-change event
                pan_int = int(round(pan + 90))  # convert delta→absolute for slider widget
                self._sio.emit("widget-change", (self._sio_widget_id, pan_int))
                self._consecutive_fails = 0
                return True
            except Exception:
                self._consecutive_fails += 1
                self._fail_count += 1
                logger.warning("Socket.IO send failed (%d consecutive)", self._consecutive_fails)
                return False

        # ── HTTP transport ──
        return self._http_send({"pan": round(pan, 2), "tilt": round(tilt, 2)})

    def send_absolute(self, pan: float, tilt: float) -> bool:
        """
        Send absolute pan/tilt position.

        Args:
            pan:  Target pan angle in degrees.
            tilt: Target tilt angle in degrees.

        Returns:
            True if command was sent.
        """
        self._request_count += 1

        if self._dry_run:
            self._consecutive_fails = 0
            logger.debug(
                "[DRY-RUN] ABS  | pan=%5.1f°  tilt=%5.1f°",
                pan, tilt,
            )
            return True

        return self._http_send({
            "pan": round(pan, 1),
            "tilt": round(tilt, 1),
            "mode": "absolute",
        })

    def emergency_stop(self) -> bool:
        """
        Immediately stop all gimbal movement.

        Sends zero-delta command with highest priority.
        """
        logger.warning("EMERGENCY STOP ->halting gimbal")

        if self._dry_run:
            return True

        # Try stop endpoint first
        for url in [
            f"{self._base_url}/gimbal/stop",
            f"{self._base_url}/motor/stop",
        ]:
            try:
                data = json.dumps({"stop": True}).encode()
                req = request.Request(
                    url, data=data,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                request.urlopen(req, timeout=self._timeout_sec)
                return True
            except Exception:
                pass

        # Fallback: send zero movement
        return self._http_send({"pan": 0.0, "tilt": 0.0, "stop": True})

    # ── Status ──────────────────────────────────────

    def get_status(self) -> Optional[dict]:
        """Query device status. Returns None if unreachable."""
        if self._dry_run:
            return {"mode": "dry_run", "connected": False}

        try:
            url = f"{self._base_url}/gimbal/status"
            req = request.Request(url, method="GET")
            resp = request.urlopen(req, timeout=self._timeout_sec)
            return json.loads(resp.read())
        except Exception:
            return None

    # ── Internal ────────────────────────────────────

    def _http_send(self, payload: dict) -> bool:
        """Send JSON payload to the control endpoint with retry."""
        data = json.dumps(payload).encode()
        last_error = None

        for attempt in range(self._retry):
            for url in self._control_urls:
                try:
                    t0 = time.monotonic()
                    req = request.Request(
                        url, data=data,
                        headers={"Content-Type": "application/json"},
                        method="POST",
                    )
                    resp = request.urlopen(req, timeout=self._timeout_sec)
                    self._last_latency_ms = (time.monotonic() - t0) * 1000

                    if resp.status in (200, 202, 204):
                        self._consecutive_fails = 0
                        return True
                except error.HTTPError as e:
                    last_error = f"HTTP {e.code}"
                except error.URLError as e:
                    last_error = f"URL error: {e.reason}"
                except Exception as e:
                    last_error = str(e)

            if attempt < self._retry - 1:
                time.sleep(0.05)

        self._consecutive_fails += 1
        self._fail_count += 1
        logger.warning(
            "RecameraClient: send failed (%d consecutive) ->%s",
            self._consecutive_fails, last_error,
        )
        return False

    # ── Cleanup ──────────────────────────────────

    def close(self) -> None:
        """Clean shutdown: stop gimbal, disconnect, release resources."""
        if not self._dry_run and self._connected:
            try:
                self.emergency_stop()
            except Exception:
                pass
        if self._sio:
            try:
                self._sio.disconnect()
            except Exception:
                pass
            self._sio = None
        self._connected = False
        self._dry_run = True
        logger.debug("RecameraClient closed")

    # ── Properties ──────────────────────────────────

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def is_dry_run(self) -> bool:
        return self._dry_run

    @property
    def consecutive_fails(self) -> int:
        return self._consecutive_fails

    @property
    def request_count(self) -> int:
        return self._request_count

    @property
    def last_latency_ms(self) -> float:
        return self._last_latency_ms
