"""reCamera Gimbal client for the companion Node-RED control bridge."""

from __future__ import annotations

import json
import time
from typing import Optional
from urllib import error, parse, request

from core.device_config import device_http_url
from core.event import ControlCommand
from utils.logger import get_logger

logger = get_logger(__name__)

DEFAULT_TIMEOUT_MS = 350
DEFAULT_RETRY = 3


class RecameraClient:
    """The sole adapter allowed to exchange gimbal commands and readback."""

    def __init__(self, base_url: str = "", timeout_ms: int = DEFAULT_TIMEOUT_MS, retry: int = DEFAULT_RETRY) -> None:
        raw = (base_url or device_http_url()).rstrip("/")
        parsed = parse.urlparse(raw)
        host = parsed.hostname or ""
        scheme = parsed.scheme or "http"
        port = parsed.port or 1880
        self._bridge_url = f"{scheme}://{host}:{port}/recamera-control/v1" if host else ""
        self._timeout_sec = max(0.05, timeout_ms / 1000.0)
        self._retry = max(1, int(retry))
        self._connected = False
        self._dry_run = True
        self._request_count = 0
        self._fail_count = 0
        self._consecutive_fails = 0
        self._last_latency_ms = 0.0
        self._session_id = ""
        self._sequence = 0
        self._lease_deadline = 0.0

    def connect(self, dry_run: bool = False) -> bool:
        if dry_run:
            self._dry_run = True
            self._connected = False
            logger.info("DRY-RUN mode: gimbal bridge disabled")
            return True
        self._dry_run = False
        status = self.get_status()
        if status is None:
            self._connected = False
            logger.error("Node-RED control bridge unavailable: %s/status", self._bridge_url)
            return False
        self._connected = bool(status.get("connected", True))
        logger.info("CONNECTED to Node-RED gimbal bridge: %s", self._bridge_url)
        return self._connected

    def apply_command(self, command: ControlCommand) -> bool:
        if not isinstance(command, ControlCommand):
            raise TypeError("RecameraClient.apply_command requires ControlCommand")
        if command.stop:
            return self.emergency_stop(command.session_id or self._session_id)
        session_id = command.session_id or self._session_id
        if not session_id or session_id != self._session_id:
            logger.warning("gimbal command rejected locally: invalid session")
            return False
        if time.monotonic() >= self._lease_deadline or time.time() >= command.expires_at:
            logger.warning("gimbal command rejected locally: expired lease/command")
            return False
        self._sequence = max(self._sequence + 1, int(command.sequence or 0))
        payload = {
            "mode": command.mode,
            "yaw": command.yaw,
            "pitch": command.pitch,
            "yaw_speed": command.speed or 180,
            "pitch_speed": command.speed or 180,
            "reason": command.reason,
            "session_id": session_id,
            "sequence": self._sequence,
            "issued_at": command.issued_at,
            "expires_at": command.expires_at,
        }
        return self._post("command", payload)

    def start_session(self, session_id: str, lease_ms: int = 750) -> bool:
        if not session_id:
            return False
        if self._dry_run:
            self._session_id = str(session_id)
            self._lease_deadline = time.monotonic() + lease_ms / 1000.0
            return True
        ok = self._post("session/start", {"session_id": str(session_id), "lease_ms": int(lease_ms)})
        if ok:
            self._session_id = str(session_id)
            self._sequence = 0
            self._lease_deadline = time.monotonic() + lease_ms / 1000.0
        return ok

    def renew_session(self, session_id: str, lease_ms: int = 750) -> bool:
        if not session_id or session_id != self._session_id:
            return False
        if self._dry_run:
            self._lease_deadline = time.monotonic() + lease_ms / 1000.0
            return True
        ok = self._post("session/heartbeat", {"session_id": session_id, "lease_ms": int(lease_ms)})
        if ok:
            self._lease_deadline = time.monotonic() + lease_ms / 1000.0
        return ok

    def emergency_stop(self, session_id: str = "") -> bool:
        sid = str(session_id or self._session_id)
        if self._dry_run:
            return True
        return self._post("stop", {"stop": True, "session_id": sid, "sequence": self._sequence + 1})

    def stop_session(self, session_id: str = "") -> bool:
        sid = str(session_id or self._session_id)
        ok = self.emergency_stop(sid)
        if not self._dry_run:
            ok = self._post("session/stop", {"session_id": sid}) and ok
        self._session_id = ""
        self._lease_deadline = 0.0
        return ok

    def get_status(self) -> Optional[dict]:
        if self._dry_run:
            return {
                "connected": False, "yaw": None, "pitch": None,
                "yaw_speed": None, "pitch_speed": None,
                "source": "dry_run", "age_ms": None,
            }
        data = self._request_json("GET", "status")
        if data is None:
            self._connected = False
            return None
        now_ms = int(time.time() * 1000)
        sample_ms = int(data.get("timestamp", now_ms))
        result = {
            "connected": bool(data.get("connected", True)),
            "yaw": _optional_float(data.get("yaw")),
            "pitch": _optional_float(data.get("pitch")),
            "yaw_speed": _optional_int(data.get("yaw_speed", data.get("speed"))),
            "pitch_speed": _optional_int(data.get("pitch_speed", data.get("speed"))),
            "source": str(data.get("source", "motor_readback")),
            "age_ms": max(0, now_ms - sample_ms),
            "timestamp": sample_ms,
            "device_lease": dict(data.get("device_lease") or {}),
            "authorized_session": str(data.get("authorized_session", "")),
            "last_sequence": int(data.get("last_sequence", 0) or 0),
        }
        self._connected = result["connected"] and result["age_ms"] <= 2000
        result["connected"] = self._connected
        return result

    def close(self) -> None:
        if not self._dry_run and self._connected:
            self.stop_session()
        self._connected = False
        self._dry_run = True

    def _post(self, path: str, payload: dict) -> bool:
        if self._dry_run:
            return True
        result = self._request_json("POST", path, payload)
        ok = bool(result and result.get("ok", result.get("accepted", False)))
        self._connected = ok
        return ok

    def _request_json(self, method: str, path: str, payload: dict | None = None) -> Optional[dict]:
        if not self._bridge_url:
            return None
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        headers = {"Content-Type": "application/json"} if body is not None else {}
        last_error = ""
        for attempt in range(self._retry):
            self._request_count += 1
            started = time.monotonic()
            try:
                req = request.Request(f"{self._bridge_url}/{path}", data=body, headers=headers, method=method)
                with request.urlopen(req, timeout=self._timeout_sec) as response:
                    self._last_latency_ms = (time.monotonic() - started) * 1000.0
                    raw = response.read().decode("utf-8", errors="replace")
                    data = json.loads(raw) if raw else {}
                    self._consecutive_fails = 0
                    return data
            except (error.URLError, error.HTTPError, TimeoutError, json.JSONDecodeError) as exc:
                last_error = str(exc)
            if attempt + 1 < self._retry:
                time.sleep(0.05)
        self._fail_count += 1
        self._consecutive_fails += 1
        logger.warning("gimbal bridge request failed: %s %s (%s)", method, path, last_error[:120])
        return None

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

    @property
    def session_id(self) -> str:
        return self._session_id


def _optional_float(value) -> Optional[float]:
    return None if value is None else float(value)


def _optional_int(value) -> Optional[int]:
    return None if value is None else int(value)
