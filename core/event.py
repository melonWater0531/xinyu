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
    """Minimal event envelope: all perception/control inputs use this shape."""

    type: str
    name: str
    ts: float
    source: str
    data: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def make(
        cls,
        type: str,
        name: str,
        source: str,
        data: Optional[Dict[str, Any]] = None,
        ts: Optional[float] = None,
    ) -> "Event":
        return cls(
            type=str(type),
            name=str(name),
            ts=time.monotonic() if ts is None else float(ts),
            source=str(source),
            data=dict(data or {}),
        )


@dataclass(frozen=True)
class ControlCommand:
    """Single command type accepted by the gimbal control layer."""

    ts: float
    source: str
    mode: str = "absolute"
    yaw: Optional[float] = None
    pitch: Optional[float] = None
    speed: Optional[int] = None
    stop: bool = False
    reason: str = ""

    @classmethod
    def make(
        cls,
        source: str,
        *,
        mode: str = "absolute",
        yaw: Optional[float] = None,
        pitch: Optional[float] = None,
        speed: Optional[int] = None,
        stop: bool = False,
        reason: str = "",
    ) -> "ControlCommand":
        return cls(
            ts=time.monotonic(),
            source=str(source),
            mode=str(mode),
            yaw=None if yaw is None else float(yaw),
            pitch=None if pitch is None else float(pitch),
            speed=None if speed is None else int(speed),
            stop=bool(stop),
            reason=str(reason),
        )

    def has_motion(self) -> bool:
        return self.stop or self.yaw is not None or self.pitch is not None or self.speed is not None
