"""Hard gate for ControlCommand execution."""

from __future__ import annotations

import time
from typing import Optional

from core.event import ControlCommand


class SafetyLayer:
    """Allows or blocks ControlCommand values without modifying intent."""

    def __init__(
        self,
        safe_mode: bool = True,
        enable_real_control: bool = False,
        max_step_deg: float = 2.5,
        max_accel_ratio: float = 0.30,
        rate_limit_hz: float = 5.0,
        **_: object,
    ) -> None:
        self._safe_mode = bool(safe_mode)
        self._enable_real_control = bool(enable_real_control)
        self._max_step = float(max_step_deg)
        self._max_accel = float(max_accel_ratio)
        self._rate_limit_s = 1.0 / max(0.5, float(rate_limit_hz))
        self._last_cmd_time = 0.0
        self._blocked_count = 0
        self._passed_count = 0
        self._last_block_reason = ""
        self._last_output: Optional[ControlCommand] = None

    def filter(self, command: Optional[ControlCommand]) -> Optional[ControlCommand]:
        if command is None or not command.has_motion():
            return self._block("no_command")
        if self._safe_mode:
            return self._block("safe_mode")
        if not self._enable_real_control:
            return self._block("real_control_disabled")
        if command.stop:
            self._last_cmd_time = time.monotonic()
            self._last_output = command
            self._last_block_reason = ""
            self._passed_count += 1
            return command

        now = time.monotonic()
        if now - self._last_cmd_time < self._rate_limit_s:
            return self._block("rate_limit")

        if command.mode == "delta":
            if command.yaw is not None and abs(command.yaw) > self._max_step:
                return self._block("yaw_delta_limit")
            if command.pitch is not None and abs(command.pitch) > self._max_step:
                return self._block("pitch_delta_limit")
        else:
            if command.yaw is not None and not (1.0 <= command.yaw <= 345.0):
                return self._block("yaw_range")
            if command.pitch is not None and not (30.0 <= command.pitch <= 180.0):
                return self._block("pitch_range")

        self._last_cmd_time = now
        self._last_output = command
        self._last_block_reason = ""
        self._passed_count += 1
        return command

    def passes(self, *args, **kwargs):
        command = args[0] if args and isinstance(args[0], ControlCommand) else kwargs.get("command")
        constrained = self.filter(command)
        return constrained is not None, self._last_block_reason or "ok"

    def _block(self, reason: str) -> None:
        self._blocked_count += 1
        self._last_block_reason = reason
        return None

    def set_safe_mode(self, enabled: bool) -> None:
        self._safe_mode = bool(enabled)

    def set_enable_real_control(self, enabled: bool) -> None:
        self._enable_real_control = bool(enabled)

    def set_rate_limit_hz(self, hz: float) -> None:
        self._rate_limit_s = 1.0 / max(0.5, float(hz))

    @property
    def is_safe_mode(self) -> bool:
        return self._safe_mode

    @property
    def is_real_control(self) -> bool:
        return self._enable_real_control

    @property
    def is_emergency_stopped(self) -> bool:
        return False

    @property
    def last_block_reason(self) -> str:
        return self._last_block_reason

    @property
    def stats(self) -> dict:
        return {
            "passed": self._passed_count,
            "blocked": self._blocked_count,
            "safe_mode": self._safe_mode,
            "real_control": self._enable_real_control,
            "emergency": False,
            "rate_limit_ms": self._rate_limit_s * 1000,
        }

    @property
    def last_output(self) -> Optional[ControlCommand]:
        return self._last_output
