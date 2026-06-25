#!/usr/bin/env python3
"""Phase 3 runner using the single FSM control plane."""

from __future__ import annotations

import argparse
import atexit
import signal
import sys
import time
from typing import Optional

from core.event import BBox, ControlCommand, Event
from core.fsm import SystemState
from core.orchestrator import Orchestrator
from hardware.recamera_client import RecameraClient
from utils.logger import get_logger, setup_root_logger
from vision.data_source import VisionDataSource, create_vision_source

logger = get_logger(__name__)

_global_hw_client: Optional[RecameraClient] = None


def _atexit_emergency_stop() -> None:
    if _global_hw_client is not None and not _global_hw_client.is_dry_run:
        try:
            _global_hw_client.apply_command(ControlCommand.make("atexit", stop=True, reason="atexit"))
        except Exception:
            pass


atexit.register(_atexit_emergency_stop)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="reCamera single-FSM gimbal control")
    parser.add_argument("--mock", action="store_true", help="Use mock vision data")
    parser.add_argument("--fps", type=float, default=10.0)
    parser.add_argument("--enable-control", action="store_true")
    parser.add_argument("--gimbal-ip", type=str, default="192.168.201.84")
    parser.add_argument("--max-cycles", type=int, default=5)
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING"])
    parser.set_defaults(use_mock=True)
    return parser.parse_args()


class Phase3Runner:
    def __init__(
        self,
        *,
        use_mock: bool = True,
        fps: float = 10.0,
        enable_control: bool = False,
        gimbal_ip: str = "192.168.201.84",
        max_cycles: int = 5,
    ) -> None:
        self._fps = max(1.0, float(fps))
        self._frame_delay = 1.0 / self._fps
        self._max_cycles = int(max_cycles)
        self._vision: VisionDataSource = create_vision_source(use_mock=use_mock)
        self._orchestrator = Orchestrator(frame_width=1920, frame_height=1080)
        self._hw = RecameraClient(base_url=f"http://{gimbal_ip}", timeout_ms=200, retry=3)
        self._hw.connect(dry_run=not enable_control)
        self._running = True
        self._frame_id = 0
        self._cycle_count = 0

        global _global_hw_client
        _global_hw_client = self._hw

    def run(self) -> None:
        def _on_shutdown_signal(sig: int, _frame) -> None:
            logger.warning("shutdown signal: %s", signal.Signals(sig).name)
            self._running = False

        signal.signal(signal.SIGINT, _on_shutdown_signal)
        signal.signal(signal.SIGTERM, _on_shutdown_signal)

        try:
            while self._running:
                start = time.monotonic()
                self._frame_id += 1
                bboxes = [self._coerce_bbox(b) for b in self._vision.get_bboxes()]
                command = self._orchestrator.handle_vision(bboxes, source="main_phase3")
                self._apply(command)
                self._print_frame(bboxes, command)
                if self._frame_id >= self._max_cycles:
                    break
                elapsed = time.monotonic() - start
                time.sleep(max(0.0, self._frame_delay - elapsed))
        finally:
            self._apply(ControlCommand.make("main_phase3", stop=True, reason="shutdown"))

    def _apply(self, command: Optional[ControlCommand]) -> None:
        if command is None or not command.has_motion():
            return
        self._hw.apply_command(command)

    @staticmethod
    def _coerce_bbox(raw) -> BBox:
        if isinstance(raw, BBox):
            return raw
        return BBox(
            x1=int(raw.x1),
            y1=int(raw.y1),
            x2=int(raw.x2),
            y2=int(raw.y2),
            class_id=int(getattr(raw, "class_id", 0)),
            class_name=str(getattr(raw, "class_name", "target")),
            confidence=float(getattr(raw, "confidence", 0.0)),
        )

    def _print_frame(self, bboxes: list[BBox], command: Optional[ControlCommand]) -> None:
        state = self._orchestrator.state
        if self._frame_id <= 3 or command or state == SystemState.LOST or self._frame_id % 15 == 0:
            logger.info(
                "[%04d] state=%s target=%s command=%s",
                self._frame_id,
                state.value,
                "yes" if bboxes else "no",
                command.reason if command else "hold",
            )


def main() -> None:
    args = parse_args()
    setup_root_logger(level=args.log_level)
    runner = Phase3Runner(
        use_mock=args.use_mock,
        fps=args.fps,
        enable_control=args.enable_control,
        gimbal_ip=args.gimbal_ip,
        max_cycles=args.max_cycles,
    )
    runner.run()


if __name__ == "__main__":
    main()
