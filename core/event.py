"""Unified event and control command schema for multimodal gimbal control."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass(frozen=True)
class BBox:
    x1: int
    y1: int
    x2: int
    y2: int
    class_id: int = 0
    class_name: str = "target"
    confidence: float = 0.0

    @property
    def center_x(self) -> float:
        return (self.x1 + self.x2) / 2.0

    @property
    def center_y(self) -> float:
        return (self.y1 + self.y2) / 2.0

    @property
    def width(self) -> int:
        return self.x2 - self.x1

    @property
    def height(self) -> int:
        return self.y2 - self.y1


@dataclass(frozen=True)
class Event:
    """Event envelope for all vision/audio/ui/system inputs."""

    type: str
    name: str
    payload: Dict[str, Any] = field(default_factory=dict)
    timestamp: int = field(default_factory=lambda: int(time.time() * 1000))
    source: str = ""

    @classmethod
    def make(
        cls,
        type: str,
        name: str,
        source: str,
        data: Optional[Dict[str, Any]] = None,
        payload: Optional[Dict[str, Any]] = None,
        ts: Optional[float] = None,
        timestamp: Optional[int] = None,
    ) -> "Event":
        event_type = str(type)
        if event_type not in {"vision", "audio", "ui", "system"}:
            raise ValueError(f"unsupported event type: {event_type!r}")
        if timestamp is None:
            timestamp = int((time.time() if ts is None else float(ts)) * 1000)
        return cls(
            type=event_type,
            name=str(name),
            payload=dict(payload if payload is not None else (data or {})),
            timestamp=int(timestamp),
            source=str(source),
        )

    @property
    def data(self) -> Dict[str, Any]:
        """Compatibility alias while callers migrate to payload."""
        return self.payload

    @property
    def ts(self) -> float:
        """Compatibility alias as seconds for stale-data comparisons."""
        return self.timestamp / 1000.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": self.type,
            "name": self.name,
            "payload": dict(self.payload),
            "timestamp": self.timestamp,
            "source": self.source,
        }

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "Event":
        return cls.make(
            raw.get("type", ""),
            raw.get("name", ""),
            raw.get("source", ""),
            payload=raw.get("payload", raw.get("data", {})),
            timestamp=raw.get("timestamp"),
        )


@dataclass(frozen=True)
class ControlCommand:
    """Single command type accepted by the gimbal control layer."""

    ts: float
    source: str
    action: str = "move"
    mode: str = "absolute"
    yaw: Optional[float] = None
    pitch: Optional[float] = None
    speed: Optional[int] = None
    stop: bool = False
    reason: str = ""
    session_id: str = ""
    sequence: int = 0
    issued_at: float = 0.0
    expires_at: float = 0.0

    @classmethod
    def make(
        cls,
        source: str,
        *,
        action: str = "move",
        mode: str = "absolute",
        yaw: Optional[float] = None,
        pitch: Optional[float] = None,
        speed: Optional[int] = None,
        stop: bool = False,
        reason: str = "",
        session_id: str = "",
        sequence: int = 0,
        ttl_s: float = 0.75,
    ) -> "ControlCommand":
        now = time.time()
        return cls(
            ts=time.monotonic(),
            source=str(source),
            action=str(action),
            mode=str(mode),
            yaw=None if yaw is None else float(yaw),
            pitch=None if pitch is None else float(pitch),
            speed=None if speed is None else int(speed),
            stop=bool(stop),
            reason=str(reason),
            session_id=str(session_id),
            sequence=max(0, int(sequence)),
            issued_at=now,
            expires_at=now + max(0.1, float(ttl_s)),
        )

    def has_motion(self) -> bool:
        return (
            self.stop
            or self.action == "calibrate"
            or self.yaw is not None
            or self.pitch is not None
            or self.speed is not None
        )
