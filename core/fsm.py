"""Single FSM for the multimodal gimbal orchestrator."""

from __future__ import annotations

import time
from enum import Enum
from typing import Dict, Tuple

from core.event import Event


class SystemState(str, Enum):
    IDLE = "IDLE"
    AUDIO_SEARCH = "AUDIO_SEARCH"
    VISION_TRACK = "VISION_TRACK"
    FUSED_TRACK = "FUSED_TRACK"
    LOST = "LOST"


_DEBOUNCE: Dict[Tuple[SystemState, str, str], int] = {
    (SystemState.IDLE, "vision", "target_detected"): 3,
    (SystemState.AUDIO_SEARCH, "vision", "target_detected"): 3,
    (SystemState.VISION_TRACK, "vision", "target_lost"): 30,
    (SystemState.FUSED_TRACK, "vision", "target_lost"): 30,
    (SystemState.LOST, "vision", "target_detected"): 3,
    (SystemState.LOST, "system", "timeout"): 10,
}

_TRANSITIONS: Dict[Tuple[SystemState, str, str], SystemState] = {
    (SystemState.IDLE, "audio", "speech_detected"): SystemState.AUDIO_SEARCH,
    (SystemState.IDLE, "vision", "target_detected"): SystemState.VISION_TRACK,
    (SystemState.IDLE, "control", "manual_override"): SystemState.IDLE,
    (SystemState.IDLE, "control", "emergency_stop"): SystemState.IDLE,

    (SystemState.AUDIO_SEARCH, "vision", "target_detected"): SystemState.FUSED_TRACK,
    (SystemState.AUDIO_SEARCH, "audio", "speech_detected"): SystemState.AUDIO_SEARCH,
    (SystemState.AUDIO_SEARCH, "audio", "timeout"): SystemState.LOST,
    (SystemState.AUDIO_SEARCH, "vision", "target_lost"): SystemState.AUDIO_SEARCH,

    (SystemState.VISION_TRACK, "audio", "speech_detected"): SystemState.FUSED_TRACK,
    (SystemState.VISION_TRACK, "vision", "target_detected"): SystemState.VISION_TRACK,
    (SystemState.VISION_TRACK, "vision", "target_lost"): SystemState.LOST,

    (SystemState.FUSED_TRACK, "vision", "target_detected"): SystemState.FUSED_TRACK,
    (SystemState.FUSED_TRACK, "audio", "speech_detected"): SystemState.FUSED_TRACK,
    (SystemState.FUSED_TRACK, "vision", "target_lost"): SystemState.AUDIO_SEARCH,
    (SystemState.FUSED_TRACK, "audio", "timeout"): SystemState.VISION_TRACK,

    (SystemState.LOST, "audio", "speech_detected"): SystemState.AUDIO_SEARCH,
    (SystemState.LOST, "vision", "target_detected"): SystemState.VISION_TRACK,
    (SystemState.LOST, "system", "timeout"): SystemState.IDLE,
}


class FSM:
    """Small deterministic FSM. The orchestrator owns guards and policy."""

    def __init__(self) -> None:
        self._state = SystemState.IDLE
        self._state_since = time.monotonic()
        self._last_event: Event | None = None
        self._pending_key: Tuple[SystemState, str, str] | None = None
        self._pending_frames = 0
        self._total_frames = 0

    @property
    def state(self) -> SystemState:
        return self._state

    @property
    def state_duration(self) -> float:
        return time.monotonic() - self._state_since

    @property
    def last_event(self) -> Event | None:
        return self._last_event

    def transition(self, event: Event) -> SystemState:
        self._total_frames += 1
        if event.type == "control" and event.name in {"emergency_stop", "manual_override"}:
            next_state = SystemState.IDLE
        else:
            key = (self._state, event.type, event.name)
            next_state = _TRANSITIONS.get(key, self._state)
            required = _DEBOUNCE.get(key, 0)
            if next_state != self._state and required > 0:
                if self._pending_key == key:
                    self._pending_frames += 1
                else:
                    self._pending_key = key
                    self._pending_frames = 1
                if self._pending_frames < required:
                    self._last_event = event
                    return self._state
                self._pending_key = None
                self._pending_frames = 0

        self._last_event = event
        if next_state != self._state:
            self._state = next_state
            self._state_since = time.monotonic()
        return self._state

    @property
    def pending_frames(self) -> int:
        return self._pending_frames

    @property
    def total_frames(self) -> int:
        return self._total_frames
