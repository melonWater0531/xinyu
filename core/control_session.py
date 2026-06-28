"""Lease-backed control authority for dashboard feature sessions."""

from __future__ import annotations

import time
from enum import Enum


class ControlMode(str, Enum):
    INACTIVE = "inactive"
    SINGLE_FACE_ANALYSIS = "single_face_analysis"
    MULTI_SOUND_YAW = "multi_sound_yaw"
    MEETING_RECORDING = "meeting_recording"
    MEETING_SOUND_YAW = "meeting_sound_yaw"
    MANUAL_GIMBAL_DEBUG = "manual_gimbal_debug"


_FEATURES = {mode.value for mode in ControlMode if mode is not ControlMode.INACTIVE}


class ControlSession:
    """Tracks the one UI session allowed to influence control decisions."""

    def __init__(self, default_lease_ms: int = 2500) -> None:
        self.default_lease_ms = max(500, int(default_lease_ms))
        self.mode = ControlMode.INACTIVE
        self.session_id = ""
        self._deadline = 0.0

    def start(self, feature: str, session_id: str, lease_ms: int | None = None) -> bool:
        if feature not in _FEATURES or not session_id:
            return False
        self.mode = ControlMode(feature)
        self.session_id = str(session_id)
        self._renew(lease_ms)
        return True

    def heartbeat(self, session_id: str, lease_ms: int | None = None) -> bool:
        if not self.matches(session_id):
            return False
        self._renew(lease_ms)
        return True

    def update_mode(self, feature: str, session_id: str, lease_ms: int | None = None) -> bool:
        if feature not in _FEATURES or not self.matches(session_id):
            return False
        self.mode = ControlMode(feature)
        self._renew(lease_ms)
        return True

    def stop(self, session_id: str) -> bool:
        if not self.matches(session_id):
            return False
        self.clear()
        return True

    def clear(self) -> None:
        self.mode = ControlMode.INACTIVE
        self.session_id = ""
        self._deadline = 0.0

    def matches(self, session_id: str) -> bool:
        return bool(session_id) and self.mode is not ControlMode.INACTIVE and session_id == self.session_id

    def expired(self, now: float | None = None) -> bool:
        return self.mode is not ControlMode.INACTIVE and (time.monotonic() if now is None else now) >= self._deadline

    def snapshot(self) -> dict:
        remaining = max(0, int((self._deadline - time.monotonic()) * 1000)) if self._deadline else 0
        return {
            "active_feature": self.mode.value,
            "session_id": self.session_id,
            "lease_remaining_ms": remaining,
            "active": self.mode is not ControlMode.INACTIVE,
        }

    def _renew(self, lease_ms: int | None) -> None:
        duration = self.default_lease_ms if lease_ms is None else max(500, min(10000, int(lease_ms)))
        self._deadline = time.monotonic() + duration / 1000.0
