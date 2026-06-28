#!/usr/bin/env python3
"""Phase 3 runner using the single FSM control plane."""

from __future__ import annotations

import argparse
import atexit
import os
import signal
import sys
import threading
import time
from collections import deque
from typing import List, Optional

import numpy as np

from core.device_config import DEVICE_IP_ENV, device_http_url, device_sscma_ws_url, normalize_device_ip
from core.event import BBox, ControlCommand, Event
from core.event_bus import EventBusServer
from core.fsm import SystemState
from core.orchestrator import Orchestrator
from core.safety_layer import SafetyLayer
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
    parser.add_argument("--gimbal-ip", type=str, default=os.environ.get(DEVICE_IP_ENV, ""),
                        help=f"reCamera address (default: ${DEVICE_IP_ENV})")
    parser.add_argument("--manual-control", action="store_true",
                        help="Enable localhost EventBus for FastAPI UI control events")
    parser.add_argument("--eventbus-host", type=str, default="127.0.0.1")
    parser.add_argument("--eventbus-port", type=int, default=8765)
    parser.add_argument("--max-cycles", type=int, default=0,
                        help="Max control cycles before exit (0 = unlimited)")
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
        gimbal_ip: str = "",
        manual_control: bool = False,
        eventbus_host: str = "127.0.0.1",
        eventbus_port: int = 8765,
        max_cycles: int = 500,
        face_conf_thresh: float = 0.60,
        person_conf_thresh: float = 0.42,
    ) -> None:
        self._fps = max(1.0, float(fps))
        self._frame_delay = 1.0 / self._fps
        self._max_cycles = int(max_cycles)
        self._face_conf_thresh = float(face_conf_thresh)
        self._person_conf_thresh = float(person_conf_thresh)
        self._event_queue: deque[Event] = deque(maxlen=256)
        self._event_lock = threading.Lock()
        self._last_event: Optional[Event] = None
        self._last_command: Optional[ControlCommand] = None
        self._last_block_reason = ""

        device_ip = normalize_device_ip(gimbal_ip, required=enable_control)

        # Vision source: real SSCMA when enable_control, mock otherwise.
        sscma_url = device_sscma_ws_url(device_ip, required=enable_control) if not use_mock else ""
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
        self._safety = SafetyLayer(safe_mode=not enable_control, enable_real_control=enable_control)
        self._hw = RecameraClient(base_url=device_http_url(device_ip, required=enable_control), timeout_ms=200, retry=3)
        self._hw.connect(dry_run=not enable_control)
        self._eventbus: Optional[EventBusServer] = None
        if manual_control:
            self._eventbus = EventBusServer(self._handle_bus_event, host=eventbus_host, port=eventbus_port)
            if self._eventbus.start():
                logger.info("EventBus listening on %s:%s", eventbus_host, eventbus_port)
            else:
                logger.error("EventBus failed to bind %s:%s", eventbus_host, eventbus_port)
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
                self._drain_event_queue()

                # Person detections from SSCMA (or mock)
                person_bboxes = [
                    b for b in (self._coerce_bbox(r) for r in self._vision.get_bboxes())
                    if b.confidence >= self._person_conf_thresh
                ]

                # Face detections (real mode only, when FaceTrackerV2 available)
                face_bboxes = self._get_face_bboxes()

                # Priority: face > person > empty  (class_name drives Stage routing)
                signal_bboxes = face_bboxes if face_bboxes else person_bboxes

                command = self._handle_vision_event(signal_bboxes)
                self._apply(command)
                self._print_frame(signal_bboxes, command)
                if self._max_cycles and self._frame_id >= self._max_cycles:
                    break
                elapsed = time.monotonic() - start
                time.sleep(max(0.0, self._frame_delay - elapsed))
        finally:
            self._apply(ControlCommand.make("main_phase3", stop=True, reason="shutdown"))
            if self._eventbus is not None:
                self._eventbus.close()

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
        allowed = self._safety.filter(command)
        self._last_block_reason = self._safety.last_block_reason
        if allowed is None:
            logger.debug("SafetyLayer blocked command: %s", self._last_block_reason)
            return
        self._last_command = allowed
        self._hw.apply_command(allowed)

    def _handle_bus_event(self, event: Event) -> dict:
        with self._event_lock:
            self._event_queue.append(event)
        return {
            "ok": True,
            "accepted": True,
            "authority": "main_phase3",
            "state": self._orchestrator.state.value,
        }

    def _drain_event_queue(self) -> None:
        while True:
            with self._event_lock:
                event = self._event_queue.popleft() if self._event_queue else None
            if event is None:
                return
            self._last_event = event
            self._apply(self._orchestrator.handle_event(event))

    def _handle_vision_event(self, bboxes: List[BBox]) -> Optional[ControlCommand]:
        if bboxes:
            primary = bboxes[0]
            event = Event.make(
                "vision",
                "target_detected",
                "main_phase3",
                payload={
                    "cx": primary.center_x / self._orchestrator.frame_width,
                    "cy": primary.center_y / self._orchestrator.frame_height,
                    "conf": primary.confidence,
                    "class_name": primary.class_name,
                },
            )
        else:
            event = Event.make("vision", "target_lost", "main_phase3", payload={"conf": 0.0})
        self._last_event = event
        return self._orchestrator.handle_event(event)

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
    try:
        runner = Phase3Runner(
            use_mock=use_mock,
            fps=args.fps,
            enable_control=args.enable_control,
            gimbal_ip=args.gimbal_ip,
            manual_control=args.manual_control,
            eventbus_host=args.eventbus_host,
            eventbus_port=args.eventbus_port,
            max_cycles=args.max_cycles,
            face_conf_thresh=args.face_conf,
            person_conf_thresh=args.person_conf,
        )
    except ValueError as exc:
        logger.error("%s", exc)
        raise SystemExit(2)
    runner.run()


if __name__ == "__main__":
    main()
