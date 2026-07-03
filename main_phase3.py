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

from core.device_config import DEVICE_IP_ENV, bypass_proxy_for_device, device_http_url, device_sscma_ws_url, normalize_device_ip
from core.device_config_store import device_config_store
from core.event import BBox, ControlCommand, Event
from core.event_bus import EventBusServer
from core.control_session import ControlMode
from core.fsm import SystemState
from core.orchestrator import Orchestrator
from core.safety_layer import SafetyLayer
from hardware.recamera_client import RecameraClient
from hardware.control_worker import HardwareControlWorker
from utils.logger import get_logger, setup_root_logger
from vision.data_source import VisionDataSource, create_vision_source

logger = get_logger(__name__)

DEVICE_LEASE_MS = 5000
# Direct Wi-Fi status latency on the device can exceed 250 ms. Hardware I/O is
# isolated in its worker, so a 500 ms boundary avoids false disconnects without
# blocking EventBus or video processing.
DEVICE_REQUEST_TIMEOUT_MS = 500
DEVICE_MOTION_TIMEOUT_MS = 800
DEVICE_REQUEST_RETRY = 1

_global_hw_client: Optional[RecameraClient] = None
_global_runner: Optional["Phase3Runner"] = None


def _atexit_emergency_stop() -> None:
    if _global_runner is not None:
        try:
            _global_runner.process_event(Event.make("system", "shutdown", "atexit"))
            _global_runner._hardware_worker.close()
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
        self._runtime_lock = threading.RLock()
        self._last_event: Optional[Event] = None
        self._last_command: Optional[ControlCommand] = None
        self._last_block_reason = ""
        self._trace: deque[dict] = deque(maxlen=40)
        self._last_apply_ok: Optional[bool] = None
        self._stop_state = "stopped"
        self._device_session_degraded = False
        self._external_perception = bool(manual_control)
        self._gimbal_tlm = {
            "connected": False, "yaw": None, "pitch": None,
            "yaw_speed": None, "pitch_speed": None,
            "source": "unavailable", "age_ms": None,
        }

        stored_ip = str(device_config_store.read().get("device_ip", ""))
        device_ip = normalize_device_ip(gimbal_ip or stored_ip, required=enable_control)

        local_vision = not self._external_perception and not use_mock
        sscma_url = device_sscma_ws_url(device_ip, required=enable_control) if local_vision else ""
        self._vision: VisionDataSource = create_vision_source(
            use_mock=not local_vision,
            sscma_url=sscma_url,
        )

        # Optional face tracker (InsightFace SCRFD — loads lazily)
        self._face_tracker = None
        if local_vision:
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
        bypass_proxy_for_device(device_ip)
        self._hw = RecameraClient(
            base_url=device_http_url(device_ip, required=enable_control),
            timeout_ms=DEVICE_REQUEST_TIMEOUT_MS,
            retry=DEVICE_REQUEST_RETRY,
            motion_timeout_ms=DEVICE_MOTION_TIMEOUT_MS,
        )
        initial_connected = self._hw.connect(dry_run=not enable_control)
        self._device_session_degraded = bool(enable_control and not initial_connected)
        if self._device_session_degraded:
            logger.warning("reCamera bridge unavailable at startup; control will recover in background")
        self._hardware_worker = HardwareControlWorker(
            self._hw, lease_ms=DEVICE_LEASE_MS, telemetry_interval=1.0,
            on_status=self._update_gimbal_status,
        )
        self._hardware_worker.start()
        self._eventbus: Optional[EventBusServer] = None
        if manual_control:
            self._eventbus = EventBusServer(self._handle_bus_event, host=eventbus_host, port=eventbus_port)
            if self._eventbus.start():
                logger.info("EventBus listening on %s:%s", eventbus_host, eventbus_port)
            else:
                logger.error("EventBus failed to bind %s:%s", eventbus_host, eventbus_port)
        self._running = True
        self._frame_id = 0
        self._last_control_tick_at = 0.0

        global _global_hw_client
        _global_hw_client = self._hw
        global _global_runner
        _global_runner = self

    def run(self) -> None:
        def _on_shutdown_signal(sig: int, _frame) -> None:
            logger.warning("shutdown signal: %s", signal.Signals(sig).name)
            self._running = False

        signal.signal(signal.SIGINT, _on_shutdown_signal)
        signal.signal(signal.SIGTERM, _on_shutdown_signal)

        try:
            while self._running:
                start = time.monotonic()
                control_hz = None
                if self._last_control_tick_at > 0.0:
                    dt = start - self._last_control_tick_at
                    control_hz = round(1.0 / dt, 2) if dt > 0 else None
                self._last_control_tick_at = start
                self._orchestrator.update_telemetry(control_hz=control_hz)
                self._frame_id += 1
                self._expire_lease_if_needed()

                signal_bboxes: List[BBox] = []
                if not self._external_perception and self._orchestrator.session.mode is ControlMode.SINGLE_FACE_ANALYSIS:
                    person_bboxes = [
                        b for b in (self._coerce_bbox(r) for r in self._vision.get_bboxes())
                        if b.confidence >= self._person_conf_thresh
                    ]

                    face_bboxes = self._get_face_bboxes()
                    signal_bboxes = face_bboxes if face_bboxes else person_bboxes

                command = self._handle_vision_event(signal_bboxes) if not self._external_perception and self._orchestrator.session.mode is ControlMode.SINGLE_FACE_ANALYSIS else None
                self._print_frame(signal_bboxes, command)
                if self._max_cycles and self._frame_id >= self._max_cycles:
                    break
                elapsed = time.monotonic() - start
                time.sleep(max(0.0, self._frame_delay - elapsed))
        finally:
            self.process_event(Event.make("system", "shutdown", "main_phase3"))
            self._hardware_worker.close()
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

    def _apply(self, command: Optional[ControlCommand]) -> dict:
        if command is None or not command.has_motion():
            return {"command": None, "applied": False, "reason": "no_command"}
        allowed = self._safety.filter(command)
        self._last_block_reason = self._safety.last_block_reason
        if allowed is None:
            logger.debug("SafetyLayer blocked command: %s", self._last_block_reason)
            self._last_apply_ok = False
            return {"command": self._command_dict(command), "applied": False, "reason": self._last_block_reason}
        self._last_command = allowed
        if allowed.stop:
            if allowed.reason in {"feature_stop", "lease_expired", "shutdown", "emergency_stop"}:
                self._hardware_worker.stop_session(allowed.session_id)
            else:
                self._hardware_worker.emergency_stop(allowed.session_id)
            ok = True
            self._stop_state = "stopping"
        else:
            self._hardware_worker.submit(allowed)
            ok = True
            self._stop_state = "running"
        self._last_apply_ok = ok
        return {"command": self._command_dict(allowed), "applied": ok, "reason": "ok" if ok else "hardware_error"}

    def _handle_bus_event(self, event: Event) -> dict:
        if event.type == "system" and event.name == "runtime_snapshot_request":
            return {"ok": True, "accepted": True, "authority": "main_phase3", "runtime": self.runtime_snapshot()}
        if event.type == "system" and event.name == "device_config_update":
            device_ip = normalize_device_ip(str(event.payload.get("device_ip", "")), required=True)
            self._hardware_worker.reconfigure(device_ip)
            active_session = str(self._orchestrator.runtime_state().get("session_id", ""))
            if active_session:
                self._hardware_worker.start_session(active_session)
            return {"ok": True, "accepted": True, "authority": "main_phase3", "reconnect_state": "pending"}
        return self.process_event(event)

    def process_event(self, event: Event) -> dict:
        with self._runtime_lock:
            control_t0 = time.monotonic()
            before = self._orchestrator.runtime_state()
            requested_session = str(event.payload.get("session_id", ""))
            session_bound = event.type == "ui" and event.name in {
                "feature_stop", "feature_heartbeat", "feature_mode_update",
                "control_config", "dpad_move", "gimbal_home", "gimbal_standby",
                "gimbal_sleep", "gimbal_stop", "gimbal_calibrate",
            }
            valid_before = requested_session == before.get("session_id") and bool(before.get("active"))
            if event.name == "dpad_move":
                valid_before = valid_before and before.get("active_feature") == "manual_gimbal_debug"
            self._last_event = event
            if event.type == "ui" and event.name == "feature_start" and before.get("active"):
                self._stop_state = "stopping"
                self._hardware_worker.stop_session(str(before.get("session_id", "")))
            command = self._orchestrator.handle_event(event)
            device_session_ok = True
            if event.type == "ui" and event.name == "feature_start":
                candidate = self._orchestrator.runtime_state()
                sid = str(candidate.get("session_id", ""))
                device_session_ok = bool(sid)
                if sid:
                    self._hardware_worker.start_session(sid)
                    self._stop_state = "recovering" if self._device_session_degraded else "starting"
            elif event.type == "ui" and event.name == "feature_heartbeat" and valid_before:
                recovering = self._device_session_degraded
                device_session_ok = True
                if recovering:
                    self._hardware_worker.start_session(requested_session)
                else:
                    self._hardware_worker.renew_session(requested_session)
            apply_result = self._apply(command)
            self._orchestrator.update_telemetry(control_loop_ms=round((time.monotonic() - control_t0) * 1000.0, 1))
            after = self._orchestrator.runtime_state()
            io_state = self._hardware_worker.snapshot()
            hardware_ready = not self._device_session_degraded and int(io_state.get("consecutive_failures", 0)) < 3
            accepted = not session_bound or valid_before
            if event.name == "feature_start":
                accepted = device_session_ok and after.get("session_id") == str(event.payload.get("session_id", ""))
            self._trace.append({
                "t": time.time(), "event": event.to_dict(),
                "state": after.get("fsm_state"), "feature": after.get("active_feature"),
                "command": apply_result.get("command"), "applied": apply_result.get("applied"),
                "accepted": accepted, "hardware_ready": hardware_ready,
            })
            return {
                "ok": accepted,
                "accepted": accepted,
                "authority": "main_phase3",
                "hardware_ready": hardware_ready,
                **{key: io_state.get(key) for key in (
                    "gimbal_bridge_status", "gimbal_bridge_last_error", "gimbal_bridge_last_ok_at",
                    "gimbal_bridge_fail_count", "gimbal_bridge_circuit_open",
                    "last_hardware_command_error", "hardware_command_queue_size",
                )},
                "state": self._orchestrator.state.value,
                **apply_result,
                "runtime": self.runtime_snapshot(),
            }

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
        result = self.process_event(event)
        return self._last_command if result.get("command") else None

    def _expire_lease_if_needed(self) -> None:
        if self._orchestrator.session.expired():
            self.process_event(Event.make("system", "lease_expired", "main_phase3"))

    @staticmethod
    def _command_dict(command: ControlCommand) -> dict:
        return {
            "action": command.action, "mode": command.mode, "yaw": command.yaw, "pitch": command.pitch,
            "speed": command.speed, "stop": command.stop, "reason": command.reason,
            "session_id": command.session_id, "sequence": command.sequence,
            "issued_at": command.issued_at, "expires_at": command.expires_at,
        }

    def runtime_snapshot(self) -> dict:
        with self._runtime_lock:
            io_state = self._hardware_worker.snapshot()
            hardware_ready = not self._device_session_degraded and int(io_state.get("consecutive_failures", 0)) < 3
            return {
                **self._orchestrator.runtime_state(),
                "authority": "main_phase3",
                "last_event": self._last_event.to_dict() if self._last_event else None,
                "last_command": self._command_dict(self._last_command) if self._last_command else None,
                "last_apply_ok": self._last_apply_ok,
                "safety": {**self._safety.stats, "last_block_reason": self._last_block_reason},
                "gimbal": dict(self._gimbal_tlm),
                "hardware_io": io_state,
                "stop_state": self._stop_state,
                "hardware_ready": hardware_ready,
                "device_lease": dict(self._gimbal_tlm.get("device_lease") or {}),
                **{key: io_state.get(key) for key in (
                    "gimbal_bridge_status", "gimbal_bridge_last_error", "gimbal_bridge_last_ok_at",
                    "gimbal_bridge_fail_count", "gimbal_bridge_circuit_open",
                    "last_hardware_command_error", "hardware_command_queue_size",
                )},
                "trace": list(self._trace)[-12:],
            }

    def _update_gimbal_status(self, status: dict) -> None:
        with self._runtime_lock:
            self._gimbal_tlm = dict(status)
            self._device_session_degraded = not bool(status.get("connected"))
            active = bool(self._orchestrator.session.snapshot().get("active"))
            self._stop_state = "running" if status.get("connected") and active else self._stop_state
            self._orchestrator.update_gimbal_readback(status.get("yaw"), status.get("pitch"))

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
