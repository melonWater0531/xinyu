#!/usr/bin/env python3
"""Run the three minimal multimodal closed loops without hardware."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.event import ControlCommand, Event
from core.orchestrator import Orchestrator


class DryRunGimbal:
    def __init__(self) -> None:
        self.commands: list[ControlCommand] = []

    def apply_command(self, command: ControlCommand) -> None:
        if not isinstance(command, ControlCommand):
            raise TypeError("DryRunGimbal accepts ControlCommand only")
        self.commands.append(command)


def audio_only_loop() -> ControlCommand:
    orch = Orchestrator()
    gimbal = DryRunGimbal()
    cmd = orch.handle(Event.make("audio", "speech_detected", "mvp_audio", {"doa_deg": 35.0, "speech": True}))
    assert cmd and cmd.yaw is not None and cmd.reason == "audio_only_loop"
    gimbal.apply_command(cmd)
    return gimbal.commands[-1]


def vision_only_loop() -> ControlCommand:
    orch = Orchestrator()
    gimbal = DryRunGimbal()
    cmd = None
    for _ in range(3):
        cmd = orch.handle(Event.make("vision", "target_detected", "mvp_vision", {"cx": 0.62, "cy": 0.42, "conf": 0.88}))
    assert cmd and cmd.yaw is not None and cmd.pitch is not None and cmd.reason == "vision_track"
    gimbal.apply_command(cmd)
    return gimbal.commands[-1]


def fusion_loop() -> ControlCommand:
    orch = Orchestrator()
    gimbal = DryRunGimbal()
    cmd_audio = orch.handle(Event.make("audio", "speech_detected", "mvp_audio", {"doa_deg": -25.0, "speech": True}))
    assert cmd_audio and cmd_audio.reason == "audio_only_loop"
    gimbal.apply_command(cmd_audio)
    cmd_vision = None
    for _ in range(3):
        cmd_vision = orch.handle(Event.make("vision", "target_detected", "mvp_vision", {"cx": 0.47, "cy": 0.55, "conf": 0.91}))
    assert cmd_vision and cmd_vision.reason == "fusion_loop"
    gimbal.apply_command(cmd_vision)
    return gimbal.commands[-1]


def main() -> None:
    for name, fn in (
        ("audio_only_loop", audio_only_loop),
        ("vision_only_loop", vision_only_loop),
        ("fusion_loop", fusion_loop),
    ):
        cmd = fn()
        print(f"{name}: state_command yaw={cmd.yaw} pitch={cmd.pitch} reason={cmd.reason}")


if __name__ == "__main__":
    main()
