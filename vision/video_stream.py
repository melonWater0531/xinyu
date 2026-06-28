"""
Video stream client ->receives frames + AI results from reCamera WebSocket.

Connects to ws://<RECAMERA_IP>:8090/ (sscma-node).
Receives: JSON with base64-encoded JPEG + detection boxes.

Usage:
    vs = VideoStream(url="ws://<RECAMERA_IP>:8090/")
    vs.start()
    frame = vs.latest_frame    # base64 JPEG string or None
    boxes = vs.latest_boxes    # list of [x1,y1,x2,y2,conf,cls]
    vs.stop()
"""

import base64
import json
import threading
import time
from typing import Any, Dict, List, Optional

from utils.logger import get_logger

logger = get_logger(__name__)


class VideoStream:
    """
    Connects to reCamera sscma-node WebSocket and receives:
      - JPEG frames (base64-encoded in JSON)
      - YOLO detection boxes
      - Inference performance metrics

    Runs in a daemon thread. Auto-reconnects on disconnect.
    """

    def __init__(self, url: str,
                 reconnect_delay: float = 2.0) -> None:
        if not url:
            raise ValueError("VideoStream url is required")
        self._url = url
        self._reconnect_delay = reconnect_delay

        self._lock = threading.Lock()
        self._running = False
        self._thread: Optional[threading.Thread] = None

        # Latest data
        self._frame_b64: str = ""          # base64 JPEG
        self._boxes: List[List] = []       # [[x1,y1,x2,y2,conf,cls], ...]
        self._resolution: List[int] = [1920, 1080]
        self._frame_count: int = 0
        self._perf: Dict[str, Any] = {}
        self._fps: float = 0.0
        self._connected: bool = False

        # FPS tracking
        self._fps_t0 = time.monotonic()
        self._fps_frames = 0

    # ── Public ──────────────────────────────────────

    def start(self) -> None:
        """Start background thread."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._recv_loop, daemon=True,
                                        name="video-stream")
        self._thread.start()
        logger.info("VideoStream: connecting to %s", self._url)

    def stop(self) -> None:
        """Stop and clean up."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=3.0)
        logger.info("VideoStream: stopped")

    @property
    def frame_b64(self) -> str:
        """Latest JPEG frame as base64 string (for direct HTML img src)."""
        with self._lock:
            return self._frame_b64

    @property
    def boxes(self) -> List[List]:
        """Latest detection boxes [[x1,y1,x2,y2,conf,cls], ...]."""
        with self._lock:
            return list(self._boxes)

    @property
    def resolution(self) -> List[int]:
        with self._lock:
            return list(self._resolution)

    @property
    def frame_count(self) -> int:
        return self._frame_count

    @property
    def fps(self) -> float:
        with self._lock:
            return self._fps

    @property
    def connected(self) -> bool:
        return self._connected

    # ── Internal ────────────────────────────────────

    def _recv_loop(self) -> None:
        """Main receive loop with auto-reconnect."""
        import websocket

        while self._running:
            ws = None
            try:
                ws = websocket.WebSocket()
                ws.settimeout(3.0)
                ws.connect(self._url, timeout=5.0)
                self._connected = True
                logger.info("📷 VideoStream: connected to %s", self._url)

                while self._running:
                    try:
                        ws.settimeout(1.0)
                        msg = ws.recv()
                        self._process_message(msg)
                    except websocket.TimeoutError:
                        continue
                    except Exception:
                        break

            except Exception as e:
                logger.debug("VideoStream: %s", str(e)[:80])
            finally:
                self._connected = False
                if ws:
                    try:
                        ws.close()
                    except Exception:
                        pass

            if self._running:
                logger.debug("VideoStream: reconnect in %.1fs", self._reconnect_delay)
                time.sleep(self._reconnect_delay)

    def _process_message(self, msg: bytes) -> None:
        """Parse JSON message, extract image and boxes."""
        try:
            text = msg.decode("utf-8")
            data = json.loads(text)
            payload = data.get("data", {})
        except (UnicodeDecodeError, json.JSONDecodeError):
            return

        # Extract base64 JPEG
        img_b64 = payload.get("image", "")
        boxes = payload.get("boxes", [])
        resolution = payload.get("resolution", [1920, 1080])
        perf = payload.get("perf", [])

        with self._lock:
            if img_b64:
                self._frame_b64 = img_b64
            if boxes is not None:
                self._boxes = boxes
            if resolution:
                self._resolution = resolution
            if perf:
                self._perf = {"preprocess": perf[0] if len(perf) > 0 else 0,
                              "inference": perf[1] if len(perf) > 1 else 0,
                              "postprocess": perf[2] if len(perf) > 2 else 0}
            self._frame_count = payload.get("count", self._frame_count + 1)

            # FPS calculation
            self._fps_frames += 1
            elapsed = time.monotonic() - self._fps_t0
            if elapsed >= 1.0:
                self._fps = self._fps_frames / elapsed
                self._fps_frames = 0
                self._fps_t0 = time.monotonic()
