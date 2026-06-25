"""
Thread-safe shared state store for the Demo Dashboard.

The control loop (recamera_demo.py) writes to this every frame.
The WebSocket server (demo_server.py) reads from this every ~80ms.

Isolated from the existing dashboard/shared_state.py — no interference.

Adds DOA (Direction of Arrival) fields for respeaker sound source localization.
"""

import threading
import time
from typing import Any, Dict, List, Optional


class DemoDashboardState:
    """
    Singleton shared state between control loop and demo dashboard server.

    Usage:
        from dashboard.demo_shared_state import demo_dashboard_state

        # In control loop (every frame):
        demo_dashboard_state.update(
            state="TRACK",
            bbox=[100, 200, 300, 400],
            ...
            doa_azimuth=45.2,
            doa_age=0.15,
        )

        # In WebSocket server:
        snapshot = demo_dashboard_state.snapshot()
    """

    _instance: Optional["DemoDashboardState"] = None
    _lock = threading.Lock()

    def __new__(cls) -> "DemoDashboardState":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    obj = super().__new__(cls)
                    obj._init()
                    cls._instance = obj
        return cls._instance

    def _init(self) -> None:
        self._lock = threading.Lock()
        self._data: Dict[str, Any] = {
            # ── System state ──
            "state": "INIT",
            "safe_mode": True,
            "emergency_stop": False,
            "gimbal_mode": "ai_track",
            "manual_mode": False,

            # ── Vision / tracking ──
            "bbox": None,
            "center": None,
            "error": [0, 0],
            "norm": [0.0, 0.0],
            "filtered": [0.0, 0.0],
            "send": [0.0, 0.0],
            "lost_frames": 0,

            # ── Performance ──
            "fps": 0.0,
            "frame_id": 0,
            "blocked_count": 0,
            "passed_count": 0,
            "oscillation": False,

            # ── Video ──
            "video_frame": "",          # base64 JPEG for dashboard rendering
            "video_fps": 0.0,
            "video_boxes": [],          # AI detection boxes from reCamera
            "video_connected": False,

            # ── DOA (respeaker sound source localization) ──
            "doa_azimuth": None,        # float degrees or None (0°=front, +=right, -=left)
            "doa_age": 999.0,           # seconds since last DOA reading
            "doa_source": "none",       # "tcp" | "mock" | "subprocess" | "none"
            "doa_connected": False,     # True if receiving fresh DOA data (<2s old)

            # ── Connection ──
            "gimbal_connected": False,
            "gimbal_ip": "",
            "timestamp": time.time(),
        }

    # Sentinel for "explicitly set to None" vs "not provided"
    _UNSET = object()

    def update(
        self,
        # System
        state: str = "",
        safe_mode: Optional[bool] = _UNSET,
        emergency_stop: Optional[bool] = _UNSET,
        gimbal_mode: Optional[str] = _UNSET,
        manual_mode: Optional[bool] = _UNSET,
        # Vision
        bbox=_UNSET,
        center=_UNSET,
        error=_UNSET,
        norm=_UNSET,
        filtered=_UNSET,
        send=_UNSET,
        lost_frames: Optional[int] = _UNSET,
        # Performance
        fps: Optional[float] = _UNSET,
        frame_id: Optional[int] = _UNSET,
        blocked_count: Optional[int] = _UNSET,
        passed_count: Optional[int] = _UNSET,
        oscillation: Optional[bool] = _UNSET,
        # Video
        video_frame: Optional[str] = _UNSET,
        video_fps: Optional[float] = _UNSET,
        video_boxes: Optional[list] = _UNSET,
        video_connected: Optional[bool] = _UNSET,
        # DOA
        doa_azimuth=_UNSET,
        doa_age: Optional[float] = _UNSET,
        doa_source: Optional[str] = _UNSET,
        doa_connected: Optional[bool] = _UNSET,
        # Connection
        gimbal_connected: Optional[bool] = _UNSET,
        gimbal_ip: Optional[str] = _UNSET,
    ) -> None:
        """Non-blocking update from the control loop."""
        with self._lock:
            if state:
                self._data["state"] = state
            if safe_mode is not self._UNSET:
                self._data["safe_mode"] = safe_mode
            if emergency_stop is not self._UNSET:
                self._data["emergency_stop"] = emergency_stop
            if gimbal_mode is not self._UNSET:
                self._data["gimbal_mode"] = gimbal_mode
            if manual_mode is not self._UNSET:
                self._data["manual_mode"] = manual_mode

            if bbox is not self._UNSET:
                self._data["bbox"] = bbox
            if center is not self._UNSET:
                self._data["center"] = [round(c, 1) for c in center] if center else None
            if error is not self._UNSET:
                self._data["error"] = [round(e, 1) for e in error] if error else [0, 0]
            if norm is not self._UNSET:
                self._data["norm"] = [round(n, 3) for n in norm] if norm else [0.0, 0.0]
            if filtered is not self._UNSET:
                self._data["filtered"] = [round(f, 3) for f in filtered] if filtered else [0.0, 0.0]
            if send is not self._UNSET:
                self._data["send"] = [round(s, 3) for s in send] if send else [0.0, 0.0]
            if lost_frames is not self._UNSET:
                self._data["lost_frames"] = lost_frames

            if fps is not self._UNSET:
                self._data["fps"] = round(fps, 1) if fps else 0.0
            if frame_id is not self._UNSET:
                self._data["frame_id"] = frame_id
            if blocked_count is not self._UNSET:
                self._data["blocked_count"] = blocked_count
            if passed_count is not self._UNSET:
                self._data["passed_count"] = passed_count
            if oscillation is not self._UNSET:
                self._data["oscillation"] = oscillation

            if video_frame is not self._UNSET:
                self._data["video_frame"] = video_frame or ""
            if video_fps is not self._UNSET:
                self._data["video_fps"] = video_fps
            if video_boxes is not self._UNSET:
                self._data["video_boxes"] = video_boxes or []
            if video_connected is not self._UNSET:
                self._data["video_connected"] = video_connected

            if doa_azimuth is not self._UNSET:
                self._data["doa_azimuth"] = doa_azimuth
            if doa_age is not self._UNSET:
                self._data["doa_age"] = doa_age
            if doa_source is not self._UNSET:
                self._data["doa_source"] = doa_source
            if doa_connected is not self._UNSET:
                self._data["doa_connected"] = doa_connected

            if gimbal_connected is not self._UNSET:
                self._data["gimbal_connected"] = gimbal_connected
            if gimbal_ip is not self._UNSET:
                self._data["gimbal_ip"] = gimbal_ip

            self._data["timestamp"] = time.time()

    def snapshot(self) -> Dict[str, Any]:
        """Return a shallow copy of current state (for WebSocket push)."""
        with self._lock:
            return dict(self._data)


# Global singleton
demo_dashboard_state = DemoDashboardState()
