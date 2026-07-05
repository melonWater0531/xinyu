"""Audio playback client for the reCamera Node-RED bridge."""

from __future__ import annotations

import base64
import json
import os
import time
from pathlib import Path
from typing import Optional
from urllib import error, parse, request

from core.device_config import device_http_url
from utils.logger import get_logger

logger = get_logger(__name__)


def _bounded_int_env(name: str, default: int, low: int, high: int) -> int:
    try:
        return max(low, min(high, int(os.environ.get(name, str(default)) or default)))
    except (TypeError, ValueError):
        return default


def _float_env(name: str, default: float, low: float) -> float:
    try:
        return max(low, float(os.environ.get(name, str(default)) or default))
    except (TypeError, ValueError):
        return default


class RecameraAudioClient:
    """Talks to `/recamera-control/v1/audio/*`; never sends gimbal commands."""

    def __init__(self, base_url: str = "", timeout_ms: int = 3000) -> None:
        raw = (
            base_url
            or os.environ.get("RECAMERA_AUDIO_BRIDGE_URL", "")
            or device_http_url()
        ).rstrip("/")
        parsed = parse.urlparse(raw)
        host = parsed.hostname or ""
        scheme = parsed.scheme or "http"
        port = parsed.port or 1880
        self._bridge_url = f"{scheme}://{host}:{port}/recamera-control/v1" if host else ""
        self._timeout_sec = max(0.2, timeout_ms / 1000.0)
        self._aplay_device = (
            os.environ.get("RECAMERA_APLAY_DEVICE")
            or os.environ.get("VOICE_APLAY_DEVICE")
            or "auto"
        ).strip() or "auto"
        self._max_retries = _bounded_int_env("RECAMERA_AUDIO_BRIDGE_RETRIES", 5, 1, 5)
        self._retry_base_sec = _float_env("RECAMERA_AUDIO_BRIDGE_RETRY_BASE_SEC", 0.2, 0.05)
        self._direct_opener = request.build_opener(request.ProxyHandler({}))
        self.last_error = ""
        self.last_result: dict = {}
        self.last_ok_at = 0.0

    @property
    def configured(self) -> bool:
        return bool(self._bridge_url)

    def play(self, audio_path: str, audio_id: str = "", content_type: str = "audio/wav") -> dict:
        path = Path(audio_path)
        if not self._bridge_url:
            return self._fail("unconfigured")
        if not path.is_file():
            return self._fail("audio_missing")
        payload = {
            "audio_id": str(audio_id or path.stem),
            "filename": path.name,
            "content_type": content_type or "audio/wav",
            "encoding": "base64",
            "sample_rate": 16000,
            "sample_format": "S16_LE",
            "aplay_device": self._aplay_device,
            "audio_base64": base64.b64encode(path.read_bytes()).decode("ascii"),
        }
        data = self._request_json("POST", "audio/play", payload)
        if data is None:
            return self._fail(self.last_error or "bridge_unreachable")
        ok = bool(data.get("ok", data.get("accepted", False)))
        self.last_result = dict(data)
        if ok:
            self.last_error = ""
            self.last_ok_at = time.time()
        else:
            self.last_error = str(data.get("error") or data.get("reason") or "play_rejected")
        return {"ok": ok, **data}

    def status(self) -> dict:
        if not self._bridge_url:
            return {"ok": False, "state": "unconfigured", "error": "unconfigured"}
        data = self._request_json("GET", "audio/status")
        if data is None:
            return {"ok": False, "state": "unreachable", "error": self.last_error or "bridge_unreachable"}
        self.last_result = dict(data)
        return data

    def stop(self, reason: str = "api") -> dict:
        if not self._bridge_url:
            return {"ok": False, "state": "unconfigured", "error": "unconfigured"}
        data = self._request_json("POST", "audio/stop", {"reason": reason})
        if data is None:
            return {"ok": False, "state": "unreachable", "error": self.last_error or "bridge_unreachable"}
        self.last_result = dict(data)
        return data

    def state(self) -> dict:
        return {
            "configured": self.configured,
            "bridge_url": self._bridge_url,
            "aplay_device": self._aplay_device,
            "max_retries": self._max_retries,
            "last_error": self.last_error,
            "last_ok_at": self.last_ok_at,
            "last_result": self.last_result,
        }

    def _request_json(self, method: str, path: str, payload: dict | None = None) -> Optional[dict]:
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        headers = {"Content-Type": "application/json"} if body is not None else {}
        attempts = self._max_retries
        for attempt in range(1, attempts + 1):
            try:
                req = request.Request(f"{self._bridge_url}/{path}", data=body, headers=headers, method=method)
                with self._direct_opener.open(req, timeout=self._timeout_sec) as response:
                    raw = response.read().decode("utf-8", errors="replace")
                    data = json.loads(raw) if raw else {}
                    if isinstance(data, dict):
                        data.setdefault("bridge_attempts", attempt)
                    return data
            except (error.URLError, error.HTTPError, TimeoutError, OSError, json.JSONDecodeError) as exc:
                self.last_error = str(exc)[:160]
                logger.warning(
                    "reCamera audio bridge failed: %s %s attempt=%s/%s (%s)",
                    method,
                    path,
                    attempt,
                    attempts,
                    self.last_error,
                )
                if attempt >= attempts:
                    return None
                time.sleep(min(2.0, self._retry_base_sec * (2 ** (attempt - 1))))
        return None

    def _fail(self, error_text: str) -> dict:
        self.last_error = str(error_text or "error")
        return {"ok": False, "error": self.last_error}
