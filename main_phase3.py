#!/usr/bin/env python3
"""Phase 3 runner using the single FSM control plane."""

from __future__ import annotations

import argparse
import atexit
import signal
import sys
import time
from typing import List, Optional

import numpy as np

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
    parser.add_argument("--mock", action="store_true",
                        help="Force mock vision (default when --enable-control not set)")
    parser.add_argument("--fps", type=float, default=10.0)
    parser.add_argument("--enable-control", action="store_true",
                        help="Enable real gimbal control + real SSCMA vision")
    parser.add_argument("--gimbal-ip", type=str, default="192.168.106.85")
    parser.add_argument("--max-cycles", type=int, default=500)
    parser.add_argument("--face-conf", type=float, default=0.60,
                        help="Min confidence to accept a face detection")
    parser.add_argument("--person-conf", type=float, default=0.42,
                        help="Min confidence to accept a person detection")
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING"])
    return parser.parse_args()


class Phase3Runner:
    def __init__(
        self,
        *,
        use_mock: bool = True,
        fps: float = 10.0,
        enable_control: bool = False,
        gimbal_ip: str = "192.168.106.85",
        max_cycles: int = 500,
        face_conf_thresh: float = 0.60,
        person_conf_thresh: float = 0.42,
    ) -> None:
        self._fps = max(1.0, float(fps))
        self._frame_delay = 1.0 / self._fps
        self._max_cycles = int(max_cycles)
        self._face_conf_thresh = float(face_conf_thresh)
        self._person_conf_thresh = float(person_conf_thresh)

        # Vision source: real SSCMA when enable_control, mock otherwise
        sscma_url = f"ws://{gimbal_ip.replace('http://', '')}:8090/"
        self._vision: VisionDataSource = create_vision_source(
            use_mock=use_mock,
            sscma_url=sscma_url,
        )

        # Optional face tracker (InsightFace SCRFD — loads lazily)
        self._face_tracker = None
        if not use_mock:
            try:
                from vision.face_tracker_v2 import FaceTrackerV2
                self._face_tracker = FaceTrackerV2()
                if self._face_tracker.available:
                    logger.info("FaceTrackerV2 ready — Stage 1/2 face search enabled")
                else:
                    logger.warning("FaceTrackerV2 models unavailable — Stage 2 disabled")
                    self._face_tracker = None
            except Exception as exc:
                logger.warning("FaceTrackerV2 import failed: %s — Stage 2 disabled", exc)

        self._orchestrator = Orchestrator(frame_width=1920, frame_height=1080)
        self._hw = RecameraClient(base_url=f"http://{gimbal_ip}", timeout_ms=200, retry=3)
        self._hw.connect(dry_run=not enable_control)
        self._running = True
        self._frame_id = 0

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

                # Person detections from SSCMA (or mock)
                person_bboxes = [
                    b for b in (self._coerce_bbox(r) for r in self._vision.get_bboxes())
                    if b.confidence >= self._person_conf_thresh
                ]

                # Face detections (real mode only, when FaceTrackerV2 available)
                face_bboxes = self._get_face_bboxes()

                # Priority: face > person > empty  (class_name drives Stage routing)
                signal_bboxes = face_bboxes if face_bboxes else person_bboxes

                command = self._orchestrator.handle_vision(signal_bboxes, source="main_phase3")
                self._apply(command)
                self._print_frame(signal_bboxes, command)
                if self._frame_id >= self._max_cycles:
                    break
                elapsed = time.monotonic() - start
                time.sleep(max(0.0, self._frame_delay - elapsed))
        finally:
            self._apply(ControlCommand.make("main_phase3", stop=True, reason="shutdown"))

    def _get_face_bboxes(self) -> List[BBox]:
        """Return face BBoxes from FaceTrackerV2 (Stage 1/2 signal)."""
        if self._face_tracker is None:
            return []
        jpeg_bytes = getattr(self._vision, "get_jpeg_bytes", lambda: None)()
        if not jpeg_bytes:
            return []
        try:
            import cv2
            arr = np.frombuffer(jpeg_bytes, dtype=np.uint8)
            frame_bgr = cv2.imdecode(arr, cv2.IMREAD_COLOR)
            if frame_bgr is None:
                return []
            tracks = self._face_tracker.update(frame_bgr)
            return [
                BBox(
                    x1=int(t["bbox"][0]), y1=int(t["bbox"][1]),
                    x2=int(t["bbox"][2]), y2=int(t["bbox"][3]),
                    class_name="face",
                    confidence=float(t["confidence"]),
                )
                for t in tracks
                if float(t["confidence"]) >= self._face_conf_thresh
            ]
        except Exception as exc:
            logger.debug("face detection error: %s", exc)
            return []

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
    # Real vision when --enable-control; explicit --mock overrides back to mock
    use_mock = args.mock or not args.enable_control
    runner = Phase3Runner(
        use_mock=use_mock,
        fps=args.fps,
        enable_control=args.enable_control,
        gimbal_ip=args.gimbal_ip,
        max_cycles=args.max_cycles,
        face_conf_thresh=args.face_conf,
        person_conf_thresh=args.person_conf,
    )
    runner.run()


if __name__ == "__main__":
    main()
