"""
Thread-safe shared state store for the Dashboard.

The control loop (main_phase3.py) writes to this every frame.
The WebSocket server (server.py) reads from this every ~100ms.

No locking contention — a single lock protects a shallow copy.
"""

import threading
import time
from typing import Any, Dict, List, Optional


class DashboardState:
    """
    Singleton shared state between control loop and dashboard server.

    Usage:
        from dashboard.shared_state import dashboard_state

        # In control loop (every frame):
        dashboard_state.update(
            state="TRACK",
            bbox=[100, 200, 300, 400],
            center=[200, 300],
            error=[-400, -200],
            norm=[-0.25, -0.20],
            filtered=[-0.64, -0.50],
            send=[-0.62, -0.49],
            lost_frames=0,
            fps=15.0,
            safe_mode=True,
            emergency_stop=False,
            blocked_count=0,
            passed_count=0,
        )

        # In WebSocket server:
        snapshot = dashboard_state.snapshot()
    """

    _instance: Optional["DashboardState"] = None
    _lock = threading.Lock()

    def __new__(cls) -> "DashboardState":
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
            "state": "INIT",
            "safe_mode": True,
            "emergency_stop": False,
            "bbox": None,
            "center": None,
            "error": [0, 0],
            "norm": [0.0, 0.0],
            "filtered": [0.0, 0.0],
            "send": [0.0, 0.0],
            "lost_frames": 0,
            "fps": 0.0,
            "blocked_count": 0,
            "passed_count": 0,
            "oscillation": False,
            "manual_mode": False,
            "gimbal_mode": "ai_track",
            "video_frame": "",          # base64 JPEG for dashboard rendering
            "video_fps": 0.0,
            "video_boxes": [],          # AI detection boxes from reCamera
            "frame_id": 0,
            "timestamp": time.time(),
        }

    # Sentinel for "explicitly set to None" vs "not provided"
    _UNSET = object()

    def update(
        self,
        state: str = "",
        safe_mode: Optional[bool] = _UNSET,
        emergency_stop: Optional[bool] = _UNSET,
        bbox=_UNSET,
        center=_UNSET,
        error=_UNSET,
        norm=_UNSET,
        filtered=_UNSET,
        send=_UNSET,
        lost_frames: Optional[int] = _UNSET,
        fps: Optional[float] = _UNSET,
        blocked_count: Optional[int] = _UNSET,
        passed_count: Optional[int] = _UNSET,
        oscillation: Optional[bool] = _UNSET,
        manual_mode: Optional[bool] = _UNSET,
        gimbal_mode: Optional[str] = _UNSET,
        video_frame: Optional[str] = _UNSET,
        video_fps: Optional[float] = _UNSET,
        video_boxes: Optional[list] = _UNSET,
        frame_id: Optional[int] = _UNSET,
    ) -> None:
        """Non-blocking update from the control loop. Use `_UNSET` sentinel
        to distinguish 'set to None' from 'not provided'."""
        with self._lock:
            if state:
                self._data["state"] = state
            if safe_mode is not self._UNSET:
                self._data["safe_mode"] = safe_mode
            if emergency_stop is not self._UNSET:
                self._data["emergency_stop"] = emergency_stop
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
            if blocked_count is not self._UNSET:
                self._data["blocked_count"] = blocked_count
            if passed_count is not self._UNSET:
                self._data["passed_count"] = passed_count
            if oscillation is not self._UNSET:
                self._data["oscillation"] = oscillation
            if manual_mode is not self._UNSET:
                self._data["manual_mode"] = manual_mode
            if gimbal_mode is not self._UNSET:
                self._data["gimbal_mode"] = gimbal_mode
            if video_frame is not self._UNSET:
                self._data["video_frame"] = video_frame
            if video_fps is not self._UNSET:
                self._data["video_fps"] = video_fps
            if video_boxes is not self._UNSET:
                self._data["video_boxes"] = video_boxes
            if frame_id is not self._UNSET:
                self._data["frame_id"] = frame_id
            self._data["timestamp"] = time.time()

    def snapshot(self) -> Dict[str, Any]:
        """Return a shallow copy of current state (for WebSocket push)."""
        with self._lock:
            return dict(self._data)


# Global singleton
dashboard_state = DashboardState()
