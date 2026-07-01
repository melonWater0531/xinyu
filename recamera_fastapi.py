#!/usr/bin/env python3
"""
reCamera Multimodal ->Main Dashboard (FastAPI)
鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲

Architecture:
  Device (<RECAMERA_IP>)                This Server (0.0.0.0:8001)
  鈹屸攢鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€->             鈹屸攢鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€->  ->SSCMA Node :8090    鈹傗攢鈹€WebSocket鈹€鈹€鈫掆攤 /video_feed  (MJPEG)     ->  ->Node-RED  :1880     鈹傗啇鈹€Socket.IO鈹€鈹€鈹€->/api/gimbal/* (control)  ->  ->                    ->             ->/ws          (state push) ->  ->                    ->             ->/home        (蹇冨笨)       ->  ->                    ->             ->/v2          (鎺у埗->     ->  鈹斺攢鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€->             鈹斺攢鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€->
Usage:
    python recamera_fastapi.py                                      # safe dry-run
    export RECAMERA_DEVICE_IP=<RECAMERA_IP>
    python recamera_fastapi.py --device-ip "$RECAMERA_DEVICE_IP"    # video/perception source

Other entry points (secondary):
    main_phase3.py       ->Phase 3 control pipeline (AI tracking + gimbal)
    recamera_demo.py     ->Alternative dashboard with DOA support
    proxy.py             ->Dev reverse proxy :5173 ->:8080
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import signal
import struct
import sys
import threading
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np
import cv2

from fastapi import Body, FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from core.device_config import (
    DEVICE_IP_ENV,
    device_sscma_ws_url,
    normalize_device_ip,
)
from core.event import Event
from core.event_bus import EventBusClient
from utils.logger import get_logger, setup_root_logger

logger = get_logger(__name__)


# NOTE: FastAPI is UI + telemetry only. It emits Events to the localhost
# EventBus and never imports or calls the hardware control layer.


def _bypass_proxy_for_device(device_ip: str) -> None:
    """Keep LAN device traffic off local HTTP/WebSocket proxies."""
    import os

    hosts = [device_ip, "localhost", "127.0.0.1"]
    existing = os.environ.get("NO_PROXY") or os.environ.get("no_proxy") or ""
    parts = [p.strip() for p in existing.split(",") if p.strip()]
    for host in hosts:
        if host and host not in parts:
            parts.append(host)
    no_proxy = ",".join(parts)
    os.environ["NO_PROXY"] = no_proxy
    os.environ["no_proxy"] = no_proxy

# 鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲->#  Configuration
# 鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲->
DASHBOARD_DIR = Path(__file__).resolve().parent / "dashboard"
HTML_FILE = DASHBOARD_DIR / "recamera_v2_live.html"

@dataclass
class Config:
    device_ip: str = ""
    host: str = "0.0.0.0"
    port: int = 8001
    ssl_enabled: bool = False


# 鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲->#  SSCMA Video Client (adapted from health-app camera_service.py)
# 鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲->
class SSCMAVideoClient:
    """
    Connects to reCamera SSCMA WebSocket (ws://<device>:8090/).
    Receives base64 JPEG frames + YOLO detection boxes.
    Runs in a background thread.
    """

    def __init__(self, device_ip: str):
        self._device_ip = normalize_device_ip(device_ip, required=True)
        self.url = device_sscma_ws_url(self._device_ip, required=True)
        self._running = False
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None

        # Latest data
        self._jpeg_bytes: Optional[bytes] = None  # raw JPEG bytes for MJPEG
        self._jpeg_b64: str = ""                   # base64 for WebSocket
        self._boxes: list = []
        self._fps: float = 0.0
        self._connected: bool = False
        self._resolution: list = [1920, 1080]       # [w, h] ->updated on first frame
        self._frame_event: Optional[asyncio.Event] = None  # signal MJPEG generator
        self._event_loop: Optional[asyncio.AbstractEventLoop] = None  # for thread-safe set()
        self._fail_count: int = 0  # consecutive connection failures

    @property
    def resolution(self) -> list:
        with self._lock:
            return list(self._resolution)

    @property
    def jpeg_bytes(self) -> Optional[bytes]:
        with self._lock:
            return self._jpeg_bytes

    @property
    def jpeg_b64(self) -> str:
        with self._lock:
            return self._jpeg_b64

    @property
    def boxes(self) -> list:
        with self._lock:
            return list(self._boxes)

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def fps(self) -> float:
        with self._lock:
            return self._fps

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._recv_loop, daemon=True, name="sscma-video")
        self._thread.start()
        logger.info("📷 SSCMA connecting to %s", self.url)

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=3.0)

    def _recv_loop(self):
        import websocket
        _bypass_proxy_for_device(self._device_ip)
        fps_t0 = time.monotonic()
        fps_count = 0

        while self._running:
            ws = None
            try:
                ws = websocket.WebSocket()
                ws.settimeout(3.0)
                ws.connect(self.url, timeout=5.0, http_proxy_host=None, http_proxy_port=None)
                self._connected = True
                logger.info("📷 SSCMA connected")

                while self._running:
                    try:
                        ws.settimeout(1.0)
                        msg = ws.recv()
                        self._process_message(msg)

                        # Signal MJPEG generator (thread-safe)
                        if self._frame_event and self._event_loop and not self._event_loop.is_closed():
                            self._event_loop.call_soon_threadsafe(self._frame_event.set)

                        # FPS
                        fps_count += 1
                        elapsed = time.monotonic() - fps_t0
                        if elapsed >= 1.0:
                            with self._lock:
                                self._fps = fps_count / elapsed
                            fps_count = 0
                            fps_t0 = time.monotonic()
                    except websocket.TimeoutError:
                        continue
                    except Exception:
                        break
            except Exception as e:
                self._fail_count += 1
                if self._fail_count == 1:
                    logger.warning("📷 SSCMA connection failed (%s) ->retrying every 2s", str(e)[:80])
                elif self._fail_count % 15 == 0:
                    logger.warning("📷 SSCMA still unreachable after %d attempts (%s)", self._fail_count, str(e)[:60])
            finally:
                self._connected = False
                if ws:
                    try: ws.close()
                    except: pass
            if self._running:
                time.sleep(2.0)

    def _process_message(self, msg: bytes):
        try:
            text = msg.decode("utf-8")
            data = json.loads(text)
            payload = data.get("data", {})

            img_b64 = payload.get("image", "")
            boxes = payload.get("boxes", [])

            if img_b64:
                jpeg = base64.b64decode(img_b64)
                with self._lock:
                    self._jpeg_bytes = jpeg
                    self._jpeg_b64 = img_b64
                    self._boxes = boxes if boxes else []
                    # Extract actual resolution from JPEG on first frame
                    if self._resolution == [1920, 1080]:
                        try:
                            import cv2, numpy as np
                            arr = np.frombuffer(jpeg, np.uint8)
                            img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
                            if img is not None:
                                self._resolution = [img.shape[1], img.shape[0]]
                        except Exception:
                            pass
        except Exception:
            pass


# NOTE: GimbalController (Socket.IO/_emit control path, _pd_step PD controller,
# update_face_tracking, GimbalStateData mirror) removed. FastAPI no longer
# commands the gimbal or opens a hardware control client.



# 鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲->#  WebSocket Connection Manager
# 鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲->
class ConnectionManager:
    def __init__(self):
        self._connections: set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def connect(self, ws: WebSocket):
        await ws.accept()
        async with self._lock:
            self._connections.add(ws)

    async def disconnect(self, ws: WebSocket):
        async with self._lock:
            self._connections.discard(ws)

    async def broadcast(self, data: dict):
        payload = json.dumps(data, default=lambda o: float(o) if hasattr(o, 'item') else str(o))
        async with self._lock:
            dead = set()
            for ws in self._connections:
                try:
                    await ws.send_text(payload)
                except Exception:
                    dead.add(ws)
            self._connections -= dead

    async def send_to(self, ws: WebSocket, data: dict):
        try:
            await ws.send_text(json.dumps(data))
        except Exception:
            pass


# 鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲->#  Global instances (set during lifespan)
# 鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲->
video_client: Optional[SSCMAVideoClient] = None
_video_client_lock = threading.Lock()
_gimbal_tlm = {
    "connected": False,
    "yaw": None,
    "pitch": None,
    "speed": None,
    "mode": "external_control_runtime",
}
_eventbus = EventBusClient()
_control_obs = {
    "observe_only": True,
    "fsm_state": "IDLE",
    "authority": "telemetry_only",
    "last_event": None,
    "command": None,
    "safety": {"ok": False, "reason": "fastapi_no_hardware"},
    "eventbus": {"host": "127.0.0.1", "port": 8765, "last_result": None},
}
from collections import deque as _deque
_decision_trace = _deque(maxlen=40)

ws_mgr = ConnectionManager()
app_config: Optional[Config] = None
_latest_pose_persons: list = []  # Latest PersonPose results from pose estimator
_face_tracker = None             # InsightFace FaceTracker (or None if unavailable)
_attention_engine = None         # AttentionEngine singleton
_attn_result = {"has_face": False}  # Latest attention result
_emotion_result = {"emotion": "", "confidence": 0.0, "probabilities": []}
_llm_engine = None
_last_llm_diary_time = 0.0
_llm_diary_entry = {"time": "", "emotion": "", "text": ""}
_llm_quote_text = ""
_mp_face = None
_eye_tracker = None
_gaze_estimator = None
_gesture_detector = None
_emotion_intervention = None
_mp_face_result = {"success": False, "ear_avg": 0.3, "eye_open": True, "head_yaw": 0, "head_pitch": 0}
_gaze_result = {"available": False, "state": "unknown", "x_offset": 0.0, "y_offset": 0.0, "confidence": 0.0}
_gesture_result = {"available": False, "name": "", "confidence": 0.0, "handedness": "", "stable_frames": 0, "intent": "", "intent_ready": False}
_proactive_intervention = {"active": False, "type": "", "reason": "", "message": "", "cooldown_remaining_sec": 0}
_mp_landmarks5 = None
_observation_id = 0
_face_landmark_mode = "five"
_eye_metrics = {"ear_avg": 0.3, "blink_rate": 0, "perclos": 0, "focus_score": 100}
_emotieff_result = None  # EmotiEffLib parallel inference result
# Audio conversation recording (perception/recording only — does NOT move the gimbal).
_doa_reader = None
_conversation_recorder = None
_conversation_recording_requested = False
_last_conversation_start_attempt = 0.0
# Single/multi tracking mode — UI state only (no hardware binding)
_tracking_mode: str = "single"
_single_track_active: bool = False
_multi_track_active: bool = False
_ui_session_id: str = ""
_runtime_cache = {
    "connected": False,
    "active_feature": "inactive",
    "session_id": "",
    "lease_remaining_ms": 0,
    "authority": "unreachable",
}
_led_runtime_mode = ""
_last_audio_event_active = False
_last_audio_event_session_id = ""


def _device_config_state() -> dict:
    ip = app_config.device_ip if app_config else ""
    return {
        "ip": ip,
        "configured": bool(ip),
        "sscma_url": device_sscma_ws_url(ip) if ip else "",
        "video_connected": bool(video_client.connected) if video_client else False,
    }


def _restart_video_client(device_ip: str) -> tuple[bool, str]:
    """Restart FastAPI's display/perception SSCMA client only."""
    global video_client
    try:
        ip = normalize_device_ip(device_ip, required=True)
    except ValueError as exc:
        return False, str(exc)

    old_client = None
    with _video_client_lock:
        old_client = video_client
        video_client = None
    if old_client:
        old_client.stop()

    new_client = SSCMAVideoClient(device_ip=ip)
    try:
        new_client._frame_event = asyncio.Event()
        new_client._event_loop = asyncio.get_event_loop()
    except RuntimeError:
        pass
    new_client.start()

    with _video_client_lock:
        video_client = new_client
    if app_config:
        app_config.device_ip = ip
    _bypass_proxy_for_device(ip)
    return True, "video_client_restarted"


def _audio_event(doa_deg: float, speech: bool, source: str = "doa", session_id: str = "") -> Event:
    payload = {"doa_deg": float(doa_deg), "speech": bool(speech)}
    if session_id:
        payload["session_id"] = session_id
    return Event.make("audio", "speech_detected", source, payload)


def _vision_event(cx: float, cy: float, conf: float, source: str = "vision") -> Event:
    return Event.make("vision", "target_detected", source, {"cx": float(cx), "cy": float(cy), "conf": float(conf)})


# 鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲->#  Build state snapshot dict
# 鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲->
def detect_target(frame_jpeg: bytes, want_face: bool = False) -> dict:
    """
    涓夌骇鐩爣妫€-> ->->鑲╄唨 ->韬綋 bbox->    杩斿洖褰掍竴鍖栧潗->(0-1)->
    want_face=True: 鍙浜鸿劯 (Stage 2 鍨傜洿瀵瑰噯->
    want_face=False: ->> 鑲╄唨 > 韬綋 (Stage 1 姘村钩瀵瑰噯->
    """
    import cv2, numpy as np
    arr = np.frombuffer(frame_jpeg, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        return {"found": False, "type": "none", "detail": "decode failed"}

    h, w = img.shape[:2]

    # 鈹€鈹€ Level 1: YuNet 浜鸿劯 (楂樼疆淇″害) 鈹€鈹€
    try:
        yunet = cv2.FaceDetectorYN_create(
            "models/face_detection_yunet.onnx", "", (w, h), 0.7, 0.4, 5000)
        _, faces = yunet.detect(img)
    except Exception:
        faces = None

    if faces is not None and len(faces) > 0:
        best = max(faces, key=lambda f: f[14] if len(f) > 14 else 0)
        fx, fy, fw_v, fh_v = float(best[0]), float(best[1]), float(best[2]), float(best[3])
        conf = float(best[14]) if len(best) > 14 else 0.8
        size = fw_v * fh_v
        if conf >= 0.75 and size >= 1600:  # >= 40x40px
            return {"found": True, "type": "face",
                    "cx": (fx + fw_v/2) / w, "cy": (fy + fh_v/2) / h,
                    "quality": conf, "detail": f"face conf={conf:.2f}"}

    # Stage 2 鍙->->娌¤劯灏辫繑鍥炵┖
    if want_face:
        return {"found": False, "type": "none", "detail": "no face for pitch align"}

    # 鈹€鈹€ Level 2: 鑲╄唨鍏抽敭->鈹€鈹€
    for p in _latest_pose_persons:
        shoulders = [kp for kp in p.keypoints
                     if kp.name in ("left_shoulder", "right_shoulder") and kp.conf > 0.6]
        if len(shoulders) == 2:
            cx = sum(kp.x for kp in shoulders) / 2
            cy = sum(kp.y for kp in shoulders) / 2
            return {"found": True, "type": "shoulder",
                    "cx": cx / w, "cy": cy / h, "quality": 0.8,
                    "detail": "shoulder midpoint"}

    # 鈹€鈹€ Level 3: YOLO bbox ->SSCMA format [cx, cy, w, h, conf, cls]
    boxes = video_client.boxes if video_client else []
    for box in boxes:
        if len(box) < 6: continue
        cx_b, cy_b, bw, bh = float(box[0]), float(box[1]), float(box[2]), float(box[3])
        conf_raw = box[4]
        conf = conf_raw / 100.0 if conf_raw > 1 else float(conf_raw)
        area_ratio = (bw * bh) / (w * h)
        if conf >= 0.6 and area_ratio >= 0.03:
            cy = cy_b - bh * 0.3  # center寰€->0% ->闈犺繎鑳搁儴
            return {"found": True, "type": "body",
                    "cx": cx_b / w, "cy": cy / h,
                    "quality": conf, "detail": f"body conf={conf:.2f}"}

    return {"found": False, "type": "none", "detail": "no target"}


# NOTE: removed orphaned face-selection helpers (_has_complete_face,
# _face_track_id, _face_is_primary, _same_face_candidate, _best_complete_face,
# _best_person_*, _person_debug_target, _face_debug_target) used only by the
# deleted gimbal tracking loop.

# NOTE: removed control helpers _set_tracking_debug, _face_capture_reset,
# _update_face_capture_state, _predictive_reacquire_step (gimbal search/tracking).

def _ensure_doa_reader() -> bool:
    """Start the configured DOA source without requiring ReSpeaker USB in WSL."""
    global _doa_reader
    if _doa_reader is not None:
        return True
    try:
        source = os.environ.get("RECAMERA_DOA_SOURCE", "usb").strip().lower()
        if source == "usb":
            from audio.respeaker_doa import ReSpeakerDOA
            reader = ReSpeakerDOA()
        elif source == "tcp":
            from audio.network_doa import NetworkDOA
            reader = NetworkDOA(
                host=os.environ.get("RECAMERA_DOA_HOST", "0.0.0.0"),
                port=int(os.environ.get("RECAMERA_DOA_PORT", "9999")),
                speech_hold_sec=float(os.environ.get("RECAMERA_DOA_SPEECH_HOLD", "0.8")),
            )
        else:
            raise ValueError(f"unsupported RECAMERA_DOA_SOURCE={source!r}; use tcp or usb")
        if not reader.open():
            return False
        reader.start(interval=0.1)
        _doa_reader = reader
        logger.info("🎤 DOA ready for yaw-only sound tracking (source=%s)", source)
        return True
    except Exception as e:
        logger.warning("DOA init failed: %s", str(e)[:160])
        _doa_reader = None
        return False


def _doa_status() -> dict:
    if _doa_reader is None:
        return {
            "available": False,
            "source": os.environ.get("RECAMERA_DOA_SOURCE", "usb"),
            "led": {"hardware": False, "effect": "unavailable"},
        }
    status_fn = getattr(_doa_reader, "status", None)
    detail = status_fn() if callable(status_fn) else {}
    return {
        "available": True,
        "source": detail.get("source", "usb"),
        "doa_deg": round(float(_doa_reader.doa), 1),
        "has_speech": bool(_doa_reader.has_speech),
        "age": round(float(_doa_reader.age), 2),
        **detail,
    }


def _respeaker_state() -> dict:
    doa = _doa_status()
    return {
        "connected": bool(doa.get("available") or doa.get("connected")),
        "source": doa.get("source", os.environ.get("RECAMERA_DOA_SOURCE", "usb")),
        "doa_deg": doa.get("doa_deg"),
        "has_speech": bool(doa.get("has_speech")),
        "age": doa.get("age"),
        "audio_device": os.environ.get("RECAMERA_AUDIO_DEVICE", "system_default"),
        "led": doa.get("led", {"hardware": False, "effect": "unavailable"}),
    }


def _apply_runtime_result(result: dict) -> None:
    global _runtime_cache, _gimbal_tlm, _control_obs, _decision_trace
    global _single_track_active, _multi_track_active, _conversation_recording_requested
    runtime = result.get("runtime") if isinstance(result, dict) else None
    if not isinstance(runtime, dict):
        return
    previous_feature = _runtime_cache.get("active_feature", "inactive")
    _runtime_cache = {**runtime, "connected": True}
    feature = _runtime_cache.get("active_feature", "inactive")
    _single_track_active = feature == "single_face_analysis"
    _multi_track_active = feature in {"multi_sound_yaw", "meeting_sound_yaw"}
    if feature == "inactive" and previous_feature in {"meeting_recording", "meeting_sound_yaw"}:
        _conversation_recording_requested = False
        _stop_conversation_recording(finalize=True)
    _gimbal_tlm = dict(runtime.get("gimbal") or _gimbal_tlm)
    _control_obs = {
        "observe_only": False,
        "fsm_state": runtime.get("fsm_state", "IDLE"),
        "authority": runtime.get("authority", "main_phase3"),
        "last_event": runtime.get("last_event"),
        "command": runtime.get("last_command"),
        "safety": runtime.get("safety", {}),
        "eventbus": {
            "host": _eventbus.host, "port": _eventbus.port,
            "last_result": {"ok": True, "accepted": True},
        },
        "active_feature": runtime.get("active_feature", "inactive"),
        "session_id": runtime.get("session_id", ""),
        "lease_remaining_ms": runtime.get("lease_remaining_ms", 0),
    }
    _decision_trace.clear()
    _decision_trace.extend(runtime.get("trace", []))


def _set_respeaker_led_for_feature(feature: str) -> None:
    global _led_runtime_mode
    desired = "doa" if feature in {"multi_sound_yaw", "meeting_recording", "meeting_sound_yaw"} else "off"
    if desired == _led_runtime_mode or _doa_reader is None:
        return
    method = getattr(_doa_reader, "set_led_doa" if desired == "doa" else "set_led_off", None)
    if not callable(method):
        _led_runtime_mode = desired
    elif method():
        _led_runtime_mode = desired


async def runtime_sync_loop() -> None:
    global _runtime_cache, _led_runtime_mode
    loop = asyncio.get_running_loop()
    while True:
        event = Event.make("system", "runtime_snapshot_request", "fastapi")
        result = await loop.run_in_executor(None, lambda: _eventbus.emit(event))
        if result.get("ok") and isinstance(result.get("runtime"), dict):
            _apply_runtime_result(result)
            _set_respeaker_led_for_feature(_runtime_cache.get("active_feature", "inactive"))
        else:
            _runtime_cache = {**_runtime_cache, "connected": False, "authority": "unreachable", "lease_remaining_ms": 0}
            if _led_runtime_mode != "off":
                _set_respeaker_led_for_feature("inactive")
        await asyncio.sleep(0.25)


async def doa_event_loop() -> None:
    global _last_audio_event_active, _last_audio_event_session_id
    loop = asyncio.get_running_loop()
    while True:
        feature = str(_runtime_cache.get("active_feature", "inactive"))
        session_id = str(_runtime_cache.get("session_id", ""))
        control_active = feature in {"multi_sound_yaw", "meeting_sound_yaw"} and bool(session_id)
        active = bool(
            control_active
            and _doa_reader is not None
            and getattr(_doa_reader, "has_speech", False)
            and float(getattr(_doa_reader, "age", 999.0)) <= 1.0
        )
        if active:
            _last_audio_event_session_id = session_id
            event = Event.make(
                "audio", "speech_detected", "respeaker",
                payload={"doa_deg": float(_doa_reader.doa), "speech": True, "session_id": session_id},
            )
            result = await loop.run_in_executor(None, lambda: _eventbus.emit(event))
            _apply_runtime_result(result)
        elif _last_audio_event_active:
            event = Event.make(
                "audio", "timeout", "respeaker",
                payload={"speech": False, "session_id": session_id or _last_audio_event_session_id},
            )
            result = await loop.run_in_executor(None, lambda: _eventbus.emit(event))
            _apply_runtime_result(result)
            _last_audio_event_session_id = ""
        _last_audio_event_active = active
        await asyncio.sleep(0.1)


# NOTE: removed _resume_ai_gimbal_mode and _update_sound_tracking_yaw
# (gimbal control-mode + yaw-follow). DOA reader below is read-only perception.

def _conversation_doa_provider() -> tuple[Optional[float], bool]:
    if _doa_reader is None:
        return None, False
    return float(_doa_reader.doa), bool(_doa_reader.has_speech)


def _ensure_conversation_recorder():
    global _conversation_recorder
    if _conversation_recorder is not None:
        return _conversation_recorder
    from audio.conversation_recorder import ConversationRecorder
    records_root = Path(__file__).resolve().parent / "records" / "conversations"
    audio_device = os.environ.get("RECAMERA_AUDIO_DEVICE", "").strip()
    if audio_device:
        try:
            audio_device = int(audio_device)
        except ValueError:
            pass
    else:
        audio_device = None
    _conversation_recorder = ConversationRecorder(
        root=records_root,
        doa_provider=_conversation_doa_provider,
        sample_rate=16000,
        device=audio_device,
    )
    return _conversation_recorder


def _start_conversation_recording() -> bool:
    global _last_conversation_start_attempt
    recorder = _ensure_conversation_recorder()
    if recorder.active:
        return True
    now = time.monotonic()
    if now - _last_conversation_start_attempt < 5.0:
        return False
    _last_conversation_start_attempt = now
    return bool(recorder.start())


def _stop_conversation_recording(finalize: bool = True) -> None:
    if _conversation_recorder is not None:
        _conversation_recorder.stop(finalize=finalize)


def _conversation_state() -> dict:
    if not _conversation_recording_requested and (
        _conversation_recorder is None or not _conversation_recorder.active
    ):
        return {
            "active": False,
            "available": True,
            "error": "",
            "mode": "doa_only",
            "requested": bool(_conversation_recording_requested),
            "last_recording_error": (
                _conversation_recorder.state().get("error", "")
                if _conversation_recorder is not None else ""
            ),
            "session_id": "",
            "recording": False,
            "current": {},
            "timeline": [],
            "stats": {"turns": 0, "speakers": 0, "duration": 0.0},
        }
    state = _conversation_recorder.state()
    state["mode"] = "audio_recording"
    state["requested"] = bool(_conversation_recording_requested)
    return state


def _conversation_debug_state() -> dict:
    root = Path(__file__).resolve().parent / "records" / "conversations"
    state = _conversation_state()
    sessions = []
    latest = None
    if root.exists():
        for session_dir in sorted([p for p in root.iterdir() if p.is_dir()], key=lambda p: p.stat().st_mtime, reverse=True)[:12]:
            timeline = session_dir / "timeline.jsonl"
            session_json = session_dir / "session.json"
            segments_dir = session_dir / "audio" / "segments"
            wavs = sorted(segments_dir.glob("*.wav")) if segments_dir.exists() else []
            item = {
                "session_id": session_dir.name,
                "path": str(session_dir),
                "session_json": str(session_json) if session_json.exists() else None,
                "timeline": str(timeline) if timeline.exists() else None,
                "timeline_lines": 0,
                "segments_dir": str(segments_dir),
                "wav_count": len(wavs),
                "latest_wavs": [str(p) for p in wavs[-8:]],
                "mtime": round(session_dir.stat().st_mtime, 3),
            }
            if timeline.exists():
                try:
                    item["timeline_lines"] = sum(1 for _ in timeline.open("r", encoding="utf-8"))
                except Exception:
                    item["timeline_lines"] = -1
            sessions.append(item)
    if state.get("session_id"):
        latest = next((s for s in sessions if s["session_id"] == state["session_id"]), None)
    if latest is None and sessions:
        latest = sessions[0]
    return {
        "active_state": state,
        "root": str(root),
        "root_exists": root.exists(),
        "recorder_created": _conversation_recorder is not None,
        "latest_session": latest,
        "sessions": sessions,
        "doa": _doa_status(),
        "audio": _audio_devices_debug(),
    }


def _audio_devices_debug() -> dict:
    configured = os.environ.get("RECAMERA_AUDIO_DEVICE", "").strip()
    try:
        import sounddevice as sd
        devices = []
        for idx, dev in enumerate(sd.query_devices()):
            devices.append({
                "index": idx,
                "name": str(dev.get("name", "")),
                "hostapi": int(dev.get("hostapi", -1)),
                "max_input_channels": int(dev.get("max_input_channels", 0)),
                "max_output_channels": int(dev.get("max_output_channels", 0)),
                "default_samplerate": float(dev.get("default_samplerate", 0.0)),
            })
        default_device = []
        for x in sd.default.device:
            try:
                default_device.append(None if x is None else int(x))
            except Exception:
                default_device.append(str(x))
        return {
            "available": True,
            "configured_device": configured or None,
            "default_device": default_device,
            "devices": devices,
            "input_devices": [d for d in devices if d["max_input_channels"] > 0],
        }
    except Exception as e:
        return {
            "available": False,
            "configured_device": configured or None,
            "error": str(e),
            "devices": [],
            "input_devices": [],
        }


def _refine_faces(jpeg_bytes: bytes, persons: list) -> list:
    """
    Use YuNet ONNX face detector for accurate facial keypoints.
    Falls back to geometric estimation if YuNet unavailable.
    """
    from vision.pose_estimator import PersonPose, Keypoint
    import cv2, numpy as np

    arr = np.frombuffer(jpeg_bytes, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        return persons

    h, w = img.shape[:2]

    # 鈹€鈹€ YuNet face detection (楂橀槇-> 鍑忓皯鍋囬槼-> 鈹€鈹€
    faces = []
    try:
        yunet_path = "models/face_detection_yunet.onnx"
        yunet = cv2.FaceDetectorYN_create(yunet_path, "", (w, h), 0.75, 0.4, 5000)
        _, faces = yunet.detect(img)
        if faces is None: faces = []
    except Exception: pass

    result = []

    # 鈹€鈹€ YuNet faces ->鐪熷疄浜斿畼鍏抽敭->鈹€鈹€
    for face in faces:
        fx, fy, fw, fh = float(face[0]), float(face[1]), float(face[2]), float(face[3])
        conf = float(face[14]) if len(face) > 14 else 0.8
        fcx, fcy = fx + fw/2, fy + fh/2
        kps = []
        if len(face) >= 14:
            kps.append(Keypoint(x=float(face[8]),  y=float(face[9]),  conf=0.95, name="nose"))
            kps.append(Keypoint(x=float(face[4]),  y=float(face[5]),  conf=0.95, name="right_eye"))
            kps.append(Keypoint(x=float(face[6]),  y=float(face[7]),  conf=0.95, name="left_eye"))
            kps.append(Keypoint(x=float(face[10]), y=float(face[11]), conf=0.90, name="right_mouth"))
            kps.append(Keypoint(x=float(face[12]), y=float(face[13]), conf=0.90, name="left_mouth"))
        shoulder_y = min(fy + fh * 1.5, h - 5)
        kps.append(Keypoint(x=max(5, fx - fw * 0.2), y=shoulder_y, conf=0.70, name="left_shoulder"))
        kps.append(Keypoint(x=min(w-5, fx + fw * 1.2), y=shoulder_y, conf=0.70, name="right_shoulder"))
        pp = PersonPose(
            bbox=(max(0,fx-fw*0.3), max(0,fy-fh*0.1), min(w,fx+fw*1.3), min(h,fy+fh*4)),
            conf=conf, keypoints=kps, face_center=(fcx,fcy), face_conf=conf)
        pp._source = "yunet_refine"
        result.append(pp)

    # 鈹€鈹€ YuNet missed but pose already has face points: keep them for lock/attention 鈹€鈹€
    if not result:
        for p in persons:
            face_names = {kp.name for kp in p.keypoints if kp.conf >= 0.3}
            if p.face_center and {"nose", "left_eye", "right_eye"}.issubset(face_names):
                p.face_conf = max(float(p.face_conf or 0.0), float(p.conf or 0.0), 0.55)
                p._source = "pose_face"
                result.append(p)

    # 鈹€鈹€ 鏃犺劯-> 鍙敤璁惧 person 妗嗙敾鑲╄唨, 涓嶇敾鍋囪劯 鈹€鈹€
    if not result:
        device_boxes = video_client.boxes if video_client else []
        for box in device_boxes[:5]:
            if len(box) < 6: continue
            cls = int(box[5]) if len(box) > 5 else -1
            if cls != 0: continue  # 鍙 person
            conf = box[4]/100.0 if box[4] > 1 else float(box[4])
            if conf < 0.55: continue
            cx_b, cy_b, bw, bh = [float(v) for v in box[:4]]
            if bh < 50 or bw*bh/(w*h) < 0.02: continue  # 澶皬璺宠繃
            x1, y1 = cx_b-bw/2, cy_b-bh/2
            x2, y2 = cx_b+bw/2, cy_b+bh/2
            kps = [
                Keypoint(x=x1+bw*0.2, y=cy_b+bh*0.05, conf=0.65, name="left_shoulder"),
                Keypoint(x=x2-bw*0.2, y=cy_b+bh*0.05, conf=0.65, name="right_shoulder"),
            ]
            pp = PersonPose(bbox=(x1,y1,x2,y2), conf=conf, keypoints=kps,
                            face_center=None, face_conf=0)
            pp._source = "person_bbox"
            result.append(pp)

    return result


def _build_pose_data() -> dict:
    """Convert latest pose persons to JSON-serializable dict (all native Python types)."""
    persons = []
    for p in _latest_pose_persons:
        if int(getattr(p, "_lost_frames", 0) or 0) != 0:
            continue
        kps = [{"x": float(kp.x), "y": float(kp.y),
                "conf": round(float(kp.conf), 2), "name": str(kp.name)}
               for kp in p.keypoints]
        persons.append({
            "track_id": getattr(p, "_track_id", None),
            "lost_frames": int(getattr(p, "_lost_frames", 0) or 0),
            "source": str(getattr(p, "_source", "")),
            "is_primary": bool(getattr(p, "_is_primary", False)),
            "bbox": [round(float(v), 1) for v in p.bbox],
            "conf": round(float(p.conf), 2),
            "keypoints": kps,
            "face_center": [round(float(p.face_center[0]), 1),
                            round(float(p.face_center[1]), 1)]
                           if p.face_center else None,
            "face_conf": round(float(p.face_conf), 2),
        })
    return {"persons": persons, "count": len(persons)}


def _build_vision_observation() -> dict:
    """Build normalized, current-frame candidates for the control runtime."""
    global _observation_id
    _observation_id += 1
    width, height = (video_client.resolution if video_client else [1920, 1080])
    width, height = max(1, int(width)), max(1, int(height))
    pose = _build_pose_data()
    faces = []
    for person in pose["persons"]:
        center = person.get("face_center")
        if center is None or int(person.get("lost_frames", 0)) != 0:
            continue
        faces.append({
            "track_id": person.get("track_id"),
            "cx": float(center[0]) / width,
            "cy": float(center[1]) / height,
            "bbox": person.get("bbox"),
            "confidence": float(person.get("face_conf", 0.0)),
            "lost_frames": 0,
            "keypoints": person.get("keypoints", []),
        })
    people = []
    for detection in _extract_detections():
        if detection.get("class_name") != "person":
            continue
        x, y = float(detection["x"]), float(detection["y"])
        w, h = float(detection["w"]), float(detection["h"])
        people.append({
            "bbox": [x, y, x + w, y + h],
            "cx": (x + w / 2.0) / width,
            "cy": (y + h * 0.28) / height,
            "confidence": float(detection.get("confidence", 0.0)),
        })
    return {
        "session_id": str(_runtime_cache.get("session_id", "")),
        "observation_id": _observation_id,
        "captured_at": time.time() * 1000.0,
        "frame_size": {"width": width, "height": height},
        "faces": faces,
        "persons": people,
    }


async def _publish_vision_observation() -> None:
    feature = str(_runtime_cache.get("active_feature", "inactive"))
    if feature not in {"single_face_analysis", "multi_sound_yaw", "meeting_sound_yaw"}:
        return
    payload = _build_vision_observation()
    if not payload["session_id"]:
        return
    event = Event.make("vision", "observation", "fastapi_perception", payload=payload)
    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(None, lambda: _eventbus.emit(event))
    _apply_runtime_result(result)


def _apply_mediapipe_landmarks5(landmarks5) -> bool:
    """Use MediaPipe FaceMesh-derived 5 points for display/analysis on the current face."""
    if landmarks5 is None or len(_latest_pose_persons) == 0:
        return False
    try:
        from vision.pose_estimator import Keypoint

        pts = np.asarray(landmarks5, dtype=np.float32)
        if pts.shape[0] < 5:
            return False
        nose_x, nose_y = float(pts[2, 0]), float(pts[2, 1])
        best = None
        for p in _latest_pose_persons:
            x1, y1, x2, y2 = p.bbox
            if x1 - 20 <= nose_x <= x2 + 20 and y1 - 20 <= nose_y <= y2 + 20:
                best = p
                break
        if best is None:
            best = max(_latest_pose_persons, key=lambda p: float(p.face_conf or p.conf or 0.0))

        face_names = {"left_eye", "right_eye", "nose", "left_mouth", "right_mouth"}
        best.keypoints = [kp for kp in best.keypoints if kp.name not in face_names]
        for (x, y), name in zip(pts[:5], ["left_eye", "right_eye", "nose", "left_mouth", "right_mouth"]):
            best.keypoints.append(Keypoint(x=float(x), y=float(y), conf=0.98, name=name))
        center = np.mean(pts[:5, :2], axis=0)
        best.face_center = (float(center[0]), float(center[1]))
        best.face_conf = max(float(best.face_conf or 0.0), 0.98)
        return True
    except Exception as e:
        logger.debug("MediaPipe 5-point apply failed: %s", str(e)[:80])
        return False


# NOTE: removed _tracking_point_from_landmarks5 (used only by deleted face tracking).

def _json_clean(value):
    """Recursively convert numpy/model outputs into JSON-native Python values."""
    if isinstance(value, dict):
        return {str(k): _json_clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_clean(v) for v in value]
    if isinstance(value, np.ndarray):
        return _json_clean(value.tolist())
    if isinstance(value, np.generic):
        return _json_clean(value.item())
    if isinstance(value, float):
        return value if np.isfinite(value) else None
    if isinstance(value, (str, int, bool)) or value is None:
        return value
    if isinstance(value, Path):
        return str(value)
    return value


def _extract_detections() -> list:
    """Convert SSCMA boxes [cx, cy, w, h, conf, cls] to UI/observer detection dicts."""
    detections = []
    if video_client:
        for box in video_client.boxes:
            if len(box) >= 6:
                cx_b, cy_b, bw, bh = float(box[0]), float(box[1]), float(box[2]), float(box[3])
                detections.append({
                    "x": cx_b - bw / 2, "y": cy_b - bh / 2,
                    "w": bw, "h": bh,
                    "class_name": "person" if int(box[5]) == 0 else f"class_{int(box[5])}",
                    "confidence": float(box[4]) / 100.0 if float(box[4]) > 1 else float(box[4]),
                })
    return detections


def build_state_snapshot() -> dict:
    detections = _extract_detections()

    snapshot = {
        "type": "state_snapshot",
        "data": {
            "device": _device_config_state(),
            # Gimbal telemetry is owned by the external control runtime.
            # FastAPI does not open a hardware client.
            "gimbal": dict(_gimbal_tlm),
            "video": {
                "connected": bool(video_client.connected) if video_client else False,
                "fps": video_client.fps if video_client else 0.0,
                "width": video_client.resolution[0] if video_client else 1920,
                "height": video_client.resolution[1] if video_client else 1080,
                "detections": detections,
            },
            "pose": _build_pose_data(),
            "doa": _doa_status(),
            "respeaker": _respeaker_state(),
            "conversation": _conversation_state(),
            "attention": _attn_result,
            "emotion": _emotion_result,
            "emotieff": _emotieff_result,
            "llm_diary": _llm_diary_entry,
            "llm_quote": _llm_quote_text,
            "mp_face": _mp_face_result,
            "eye_metrics": _eye_metrics,
            "gaze": _gaze_result,
            "gesture": _gesture_result,
            "proactive_intervention": _proactive_intervention,
            # Observe-only control-plane mirror (FSM / decision / authority / safety).
            "control": dict(_control_obs),
            "trace": list(_decision_trace)[-12:],
            "health": {
                "video_fps": round(float(video_client.fps), 1) if video_client else 0.0,
                "ws_clients": len(ws_mgr._connections),
                "doa_age": round(float(getattr(_doa_reader, "age", 999.0)), 2) if _doa_reader else None,
                "gimbal_latency_ms": None,
                "gimbal_connected": bool(_gimbal_tlm.get("connected")),
            },
            "locked_track_id": _runtime_cache.get("locked_track_id"),
            "tracking_phase": _runtime_cache.get("tracking_phase", "inactive"),
            "stop_state": _runtime_cache.get("stop_state", "stopped"),
            "device_lease": dict(_runtime_cache.get("device_lease") or {}),
            "face_landmark_mode": _face_landmark_mode,
            "tracking_mode": _tracking_mode,
            "single_track": {"active": _single_track_active},
            "multi_track": {"active": _multi_track_active},
            "timestamp": time.time(),
        },
    }
    return _json_clean(snapshot)


# 鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲->#  FastAPI App
# 鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲->
@asynccontextmanager
async def lifespan(app: FastAPI):

    global video_client

    # Start video client only when a device address is configured. FastAPI can
    # still run as UI/telemetry viewer without a reCamera address.
    if app_config.device_ip:
        video_client = SSCMAVideoClient(device_ip=app_config.device_ip)
        video_client._frame_event = asyncio.Event()
        video_client._event_loop = asyncio.get_event_loop()
        video_client.start()
    else:
        video_client = None
        logger.warning("No reCamera device address configured; /video_feed is disabled until /api/device/config is set")

    # Attention engine
    global _attention_engine
    from vision.attention_engine import AttentionEngine
    _attention_engine = AttentionEngine()

    # FaceTrackerV2: Kalman + ByteTrack + ArcFace
    global _face_tracker
    try:
        from vision.face_tracker_v2 import get_face_tracker_v2
        _face_tracker = get_face_tracker_v2()
        logger.info("🔍 FaceTrackerV2: %s",
            "SCRFD+Kalman+ByteTrack ready" if _face_tracker.available
            else "unavailable, fallback to YOLO")
    except Exception as e:
        logger.warning("FaceTrackerV2 init skipped: %s", e)
        _face_tracker = None

    # EmotiEffLib only (old emotion model removed)

    # Reflection engine ->lightweight templates, pre-loaded for fast diary/chat
    global _llm_engine
    try:
        from vision.llm_reflect import get_llm
        _llm_engine = get_llm()
        logger.info("🤖 Loading lightweight reflection engine for diary chat...")
        _llm_engine._load()
        if _llm_engine.loaded:
            logger.info("->Reflection engine ready for diary chat")
        else:
            logger.warning("⚠️ Reflection engine failed to load ->chat will use fallback")
    except Exception as e:
        logger.warning("Reflection init skipped: %s ->chat will use fallback", e)
        _llm_engine = None

    # MediaPipe + Eye Metrics
    global _mp_face, _eye_tracker, _gaze_estimator, _gesture_detector, _emotion_intervention
    _mp_face = None
    _eye_tracker = None
    try:
        from vision.gaze_estimator import GazeEstimator
        from vision.gesture_detector import GestureDetector
        from core.emotion_intervention import EmotionInterventionPolicy
        _gaze_estimator = GazeEstimator()
        _gesture_detector = GestureDetector()
        _emotion_intervention = EmotionInterventionPolicy()
    except Exception as e:
        logger.warning("Companion perception policy init skipped: %s", e)
        _gaze_estimator = None
        _gesture_detector = None
        _emotion_intervention = None

    # EmotiEffLib adapter
    from vision.emotieff_adapter import get_emotieff_adapter
    get_emotieff_adapter()

    # USB is the production source; TCP remains an explicit fallback.
    global _doa_reader, _conversation_recording_requested
    _doa_reader = None
    _conversation_recording_requested = False
    _ensure_doa_reader()

    # Background task: perception/state push. Control telemetry comes from the
    # external control runtime, not from direct FastAPI hardware access.
    push_task = asyncio.create_task(state_push_loop())
    runtime_task = asyncio.create_task(runtime_sync_loop())
    doa_task = asyncio.create_task(doa_event_loop())

    logger.info("=" * 55)
    logger.info("reCamera Demo Dashboard (FastAPI) - display only")
    scheme = "https" if app_config.ssl_enabled else "http"
    ws_scheme = "wss" if app_config.ssl_enabled else "ws"
    logger.info("   Device IP:    %s", app_config.device_ip)
    logger.info("   Dashboard:    %s://localhost:%d/home", scheme, app_config.port)
    logger.info("   MJPEG:        %s://localhost:%d/video_feed", scheme, app_config.port)
    logger.info("   WebSocket:    %s://localhost:%d/ws", ws_scheme, app_config.port)
    logger.info("   Control:      UI Events -> EventBus -> main_phase3")
    logger.info("=" * 55)

    yield

    # Cleanup
    push_task.cancel()
    runtime_task.cancel()
    doa_task.cancel()
    try: await push_task
    except asyncio.CancelledError: pass
    for task in (runtime_task, doa_task):
        try: await task
        except asyncio.CancelledError: pass

    _stop_conversation_recording(finalize=True)
    if video_client: video_client.stop()
    if _doa_reader:
        led_off = getattr(_doa_reader, "set_led_off", None)
        if callable(led_off): led_off()
        _doa_reader.close()
    logger.info("Dashboard shutdown complete")


app = FastAPI(title="reCamera Demo Dashboard", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve dashboard static files (GLB models, etc.)
app.mount("/static", StaticFiles(directory=str(DASHBOARD_DIR)), name="static")


# 鈹€鈹€ State push loop 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€

def _external_control_telemetry() -> dict:
    """FastAPI-owned placeholder; main_phase3 owns hardware telemetry."""
    return dict(_gimbal_tlm)


_AUTHORITY = {
    "IDLE": "idle",
    "AUDIO_SEARCH": "audio",
    "VISION_TRACK": "vision",
    "FUSED_TRACK": "fusion",
    "LOST": "lost",
}


def _cmd_brief(cmd) -> Optional[dict]:
    if cmd is None:
        return None
    return {
        "action": getattr(cmd, "action", "move"),
        "reason": cmd.reason,
        "yaw": round(float(cmd.yaw), 1) if cmd.yaw is not None else None,
        "pitch": round(float(cmd.pitch), 1) if cmd.pitch is not None else None,
        "speed": cmd.speed,
        "stop": bool(cmd.stop),
        "source": cmd.source,
    }


def _ev_brief(ev) -> Optional[dict]:
    if ev is None:
        return None
    return {"type": ev.type, "name": ev.name, "source": ev.source}


def _observe_control_step(detections: list, fw: int, fh: int) -> None:
    """Record telemetry-only event summaries.

    FastAPI does not run FSM/orchestrator logic. It only reports raw perception
    observations; the control runtime owns state and commands.
    """
    global _control_obs
    if _runtime_cache.get("connected"):
        return
    last_event = None
    if _doa_reader is not None and bool(getattr(_doa_reader, "has_speech", False)) and float(getattr(_doa_reader, "age", 999.0)) <= 1.0:
        last_event = Event.make("audio", "speech_detected", "fastapi_telemetry",
                                {"doa_deg": float(_doa_reader.doa), "speech": True})
    elif detections:
        last_event = Event.make("vision", "target_detected", "fastapi_telemetry",
                                {"count": len(detections)})
    else:
        last_event = Event.make("vision", "target_lost", "fastapi_telemetry", {"count": 0})

    _control_obs = {
        "observe_only": True,
        "fsm_state": "EXTERNAL",
        "authority": "telemetry_only",
        "last_event": _ev_brief(last_event),
        "command": None,
        "safety": {"ok": False, "reason": "fastapi_no_hardware"},
        "vision_lost_frames": 0 if detections else None,
        "eventbus": dict(_control_obs.get("eventbus", {})),
    }

    _decision_trace.append({
        "t": round(time.time(), 2),
        "event": _ev_brief(last_event),
        "state": "EXTERNAL",
        "transition": False,
        "from": "EXTERNAL",
        "command": None,
        "authority": "telemetry_only",
    })


async def state_push_loop():
    """Run perception and push UI snapshots. Contains NO gimbal control."""
    global _attn_result, _emotion_result, _emotieff_result, _eye_metrics
    global _mp_face, _eye_tracker, _mp_face_result, _mp_landmarks5
    global _gaze_result, _gesture_result, _proactive_intervention
    global _llm_engine, _llm_diary_entry, _llm_quote_text, _last_llm_diary_time
    pose_est = None
    pose_frame_count = 0

    while True:
        try:
            pose_frame_count += 1
            # -- Scene gating: daily (single) runs face/emotion/eye; work (multi) runs pose only.
            #    When neither mode is active, both pipelines run (default observation mode). --
            run_face = True
            run_pose = True
            if _single_track_active and not _multi_track_active:
                run_pose = False   # 日常场景：人脸/情绪/专注，跳过 YOLO pose
            elif _multi_track_active and not _single_track_active:
                run_face = True   # Multi-person fusion still needs face candidates.
            # -- Face detection: FaceTrackerV2 (SCRFD + Kalman/ByteTrack), YOLO fallback --
            if video_client:
                jpeg = video_client.jpeg_bytes
                if jpeg:
                    loop = asyncio.get_event_loop()
                    tracked_faces = []
                    if _face_tracker and _face_tracker.available and run_face:
                        try:
                            from vision.pose_estimator import PersonPose, Keypoint
                            arr = np.frombuffer(jpeg, np.uint8)
                            frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
                            if frame is not None:
                                tracks = await loop.run_in_executor(None, _face_tracker.update, frame)
                                if not tracks:
                                    _latest_pose_persons.clear()
                                if tracks:
                                    persons = []
                                    for t in tracks:
                                        if int(t.get('lost_frames', 0) or 0) != 0:
                                            continue
                                        x1, y1, x2, y2 = t['bbox']
                                        cx, cy = t['face_center']
                                        kps = []
                                        lm5 = t.get('landmarks_5')
                                        if lm5 is not None and lm5.shape[0] >= 5:
                                            for idx, name in enumerate(['left_eye', 'right_eye', 'nose', 'left_mouth', 'right_mouth']):
                                                kps.append(Keypoint(x=float(lm5[idx, 0]), y=float(lm5[idx, 1]), conf=0.9, name=name))
                                        else:
                                            lm = t.get('landmarks_106')
                                            if lm is not None and lm.shape[0] >= 60:
                                                for idx, name in [(54, 'nose'), (38, 'left_eye'), (88, 'right_eye'), (91, 'left_mouth'), (100, 'right_mouth')]:
                                                    if idx < lm.shape[0]:
                                                        kps.append(Keypoint(x=float(lm[idx, 0]), y=float(lm[idx, 1]), conf=0.9, name=name))
                                        pp = PersonPose(
                                            bbox=(x1, y1, x2, y2), conf=t['confidence'],
                                            keypoints=kps, face_center=(cx, cy), face_conf=t['confidence'])
                                        pp._track_id = t.get('id')
                                        pp._is_primary = bool(t.get('is_primary', False))
                                        pp._lost_frames = int(t.get('lost_frames', 0) or 0)
                                        pp._source = "face_tracker_v2"
                                        persons.append(pp)
                                    tracked_faces = persons
                                    _latest_pose_persons.clear()
                                    _latest_pose_persons.extend(persons)
                        except Exception as e:
                            if pose_frame_count % 30 == 0:
                                logger.debug("FaceTrackerV2 error: %s", str(e)[:80])
                    if not tracked_faces and run_pose:
                        if pose_est is None:
                            from vision.pose_estimator import get_pose_estimator
                            pose_est = get_pose_estimator()
                        try:
                            persons = await loop.run_in_executor(None, pose_est.detect, jpeg)
                            persons = await loop.run_in_executor(None, _refine_faces, jpeg, persons)
                            _latest_pose_persons.clear()
                            _latest_pose_persons.extend(persons)
                        except Exception as e:
                            if pose_frame_count % 30 == 0:
                                logger.debug("YOLO fallback error: %s", str(e)[:80])

            # -- Attention engine --
            if _attention_engine and _latest_pose_persons and run_face:
                for p in _latest_pose_persons:
                    face_kps = {kp.name: (kp.x, kp.y) for kp in p.keypoints
                                if kp.name in ('left_eye', 'right_eye', 'nose', 'left_mouth', 'right_mouth')}
                    if len(face_kps) >= 5:
                        landmarks = [
                            face_kps['left_eye'], face_kps['right_eye'], face_kps['nose'],
                            face_kps['left_mouth'], face_kps['right_mouth']
                        ]
                        nose_xy = face_kps.get('nose')
                        res = video_client.resolution if video_client else [1920, 1080]
                        _attn_result = _attention_engine.update(
                            landmarks, nose_xy,
                            img_w=int(res[0]), img_h=int(res[1]),
                            eye_metrics=_eye_metrics,
                            gaze=_gaze_result,
                        )
                        break
                else:
                    _attn_result = _attention_engine.update(None)
            else:
                _attn_result = {"has_face": False}

            # -- MediaPipe face + eye metrics (throttled) --
            if pose_frame_count % 2 == 0 and run_face:
                jpeg = video_client.jpeg_bytes if video_client else None
                if jpeg:
                    if _mp_face is None:
                        from vision.mediapipe_face import MPFaceDetector
                        from vision.eye_metrics import EyeMetricTracker
                        _mp_face = MPFaceDetector()
                        _eye_tracker = EyeMetricTracker()
                    try:
                        loop = asyncio.get_event_loop()
                        mp_res = await loop.run_in_executor(None, _mp_face.detect,
                            cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR))
                        if mp_res.success:
                            _mp_landmarks5 = mp_res.landmarks5
                            _apply_mediapipe_landmarks5(_mp_landmarks5)
                            if _gaze_estimator is not None:
                                _gaze_result = _gaze_estimator.update(mp_res.landmarks)
                            _mp_face_result = {"success": True, "ear_avg": round(float(mp_res.ear_avg), 3),
                                "eye_open": bool(mp_res.eye_open),
                                "landmarks_count": int(mp_res.landmarks.shape[0]) if mp_res.landmarks is not None else 468,
                                "landmarks5": [[round(float(x), 1), round(float(y), 1)]
                                               for x, y in np.asarray(mp_res.landmarks5)[:, :2]]
                                               if mp_res.landmarks5 is not None else [],
                                "landmarks_eye": [[round(float(mp_res.landmarks[i][0]), 1), round(float(mp_res.landmarks[i][1]), 1)] for i in [33, 160, 158, 133, 153, 144, 362, 385, 387, 263, 373, 380]],
                                "landmarks_mesh": [[round(float(p[0]), 1), round(float(p[1]), 1)] for p in np.asarray(mp_res.landmarks)[:, :2]]}
                            em = _eye_tracker.update(landmarks=mp_res.landmarks)
                            _eye_metrics = {"ear_avg": round(float(em.ear_avg), 3),
                                "blink_rate": float(em.blink_rate), "perclos": round(float(em.perclos), 3),
                                "focus_score": int(em.focus_score), "blink_count": int(em.blink_count),
                                "eye_open": bool(em.eye_open)}
                        else:
                            _gaze_result = {"available": False, "state": "unknown", "x_offset": 0.0, "y_offset": 0.0, "confidence": 0.0}
                    except Exception as e:
                        logger.warning(f"MediaPipe: {e}")
                        _gaze_result = {"available": False, "state": "unknown", "x_offset": 0.0, "y_offset": 0.0, "confidence": 0.0}

            # -- Gesture recognition (companionship intents only; no control events) --
            if pose_frame_count % 3 == 0 and run_face:
                jpeg = video_client.jpeg_bytes if video_client else None
                if jpeg and _gesture_detector is not None:
                    try:
                        frame = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
                        loop = asyncio.get_event_loop()
                        _gesture_result = await loop.run_in_executor(None, _gesture_detector.detect, frame)
                    except Exception as e:
                        _gesture_result = {"available": False, "name": "", "confidence": 0.0, "handedness": "", "stable_frames": 0, "intent": "", "intent_ready": False, "reason": str(e)[:80]}

            # -- Emotion recognition (EmotiEffLib) --
            jpeg = video_client.jpeg_bytes if (video_client and run_face) else None
            landmarks = None
            if _latest_pose_persons and run_face:
                for p in _latest_pose_persons:
                    face_kps = {kp.name: (kp.x, kp.y) for kp in p.keypoints
                                if kp.name in ('left_eye', 'right_eye', 'nose', 'left_mouth', 'right_mouth')}
                    if len(face_kps) >= 5:
                        landmarks = [face_kps['left_eye'], face_kps['right_eye'], face_kps['nose'],
                                     face_kps['left_mouth'], face_kps['right_mouth']]
                        break

            if jpeg and landmarks:
                arr = np.frombuffer(jpeg, dtype=np.uint8)
                frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
                if frame is not None:
                    from vision.face_crop import extract_face_crop
                    from vision.emotieff_adapter import get_emotieff_adapter
                    crop_result = extract_face_crop(frame, landmarks, None)
                    img_for_emo = crop_result.crop if crop_result.crop is not None else None
                    if img_for_emo is not None:
                        raw_result = get_emotieff_adapter().predict(img_for_emo)
                        if raw_result and raw_result.get("emotion"):
                            raw_probs = {str(k): float(v) for k, v in raw_result.get("probabilities", {}).items()}
                            top_emo = max(raw_probs, key=raw_probs.get) if raw_probs else str(raw_result["emotion"])
                            top_conf = float(raw_probs.get(top_emo, raw_result.get("confidence", 0.0)))
                            _emotieff_result = {
                                "emotion": top_emo,
                                "confidence": round(float(top_conf), 4),
                                "probabilities": raw_probs,
                                "source": "emotiefflib_raw_max",
                            }
                            _emotion_result = _emotieff_result

            # -- Proactive intervention policy (state only; UI decides notification) --
            if _emotion_intervention is not None:
                try:
                    _proactive_intervention = _emotion_intervention.update(
                        _emotieff_result, _attn_result, _eye_metrics, _gaze_result
                    )
                except Exception as e:
                    _proactive_intervention = {"active": False, "type": "", "reason": str(e)[:80], "message": "", "cooldown_remaining_sec": 0}

            # -- LLM diary: trigger on emotion change --
            if not hasattr(state_push_loop, '_last_llm_emo'):
                state_push_loop._last_llm_emo = None
            emo_name = _emotieff_result.get("emotion", "Neutral") if (_emotieff_result and _emotieff_result.get("emotion")) else "Neutral"
            emotion_changed = emo_name != state_push_loop._last_llm_emo
            attn_sc = int(_attn_result.get("score", 50)) if _attn_result.get("has_face") else 50
            if _llm_engine is None:
                try:
                    from vision.llm_reflect import get_llm
                    _llm_engine = get_llm()
                except Exception:
                    pass
            if _llm_engine and _llm_engine.loaded:
                loop = asyncio.get_event_loop()
                if emotion_changed:
                    try:
                        text = await loop.run_in_executor(None, _llm_engine.diary, emo_name, attn_sc, "")
                        if text:
                            _llm_diary_entry = {"time": time.strftime("%H:%M"), "emotion": emo_name, "text": text, "editable": True}
                            _last_llm_diary_time = time.time()
                        state_push_loop._last_llm_emo = emo_name
                    except Exception:
                        pass
                if not hasattr(state_push_loop, '_lq'):
                    state_push_loop._lq = 0
                if time.time() - state_push_loop._lq > 300:
                    state_push_loop._lq = time.time()
                    try:
                        lvl = "high" if attn_sc >= 70 else "mid" if attn_sc >= 40 else "low"
                        _llm_quote_text = await loop.run_in_executor(None, _llm_engine.quote, emo_name, lvl)
                    except Exception:
                        pass

            # Publish the same curated candidates used by the overlay.
            await _publish_vision_observation()

            # Observe-only control-plane mirror (FSM/decision-trace; never commands).
            try:
                res = video_client.resolution if video_client else [1920, 1080]
                _observe_control_step(_extract_detections(), int(res[0]), int(res[1]))
            except Exception as e:
                logger.debug("observe step error: %s", str(e)[:80])

            snapshot = build_state_snapshot()
            await ws_mgr.broadcast(snapshot)
        except Exception as e:
            logger.error("Push error: %s", str(e)[:120])
            import traceback
            logger.error(traceback.format_exc()[-200:])
        await asyncio.sleep(0.2)  # ~5 Hz


# 鈹€鈹€ WebSocket Endpoint 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€

@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    """Display-only WebSocket: pushes telemetry/perception snapshots.

    No control messages are accepted here. All gimbal control lives in
    core/orchestrator.py -> hardware/recamera_client.py.
    """
    await ws_mgr.connect(ws)
    try:
        # Send initial snapshot immediately
        await ws_mgr.send_to(ws, build_state_snapshot())

        while True:
            msg = await ws.receive_text()
            if msg == "request_state":
                await ws_mgr.send_to(ws, build_state_snapshot())
            else:
                logger.debug("Ignored WS message (display-only server): %s", msg[:40])
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.debug("WS error: %s", e)
    finally:
        await ws_mgr.disconnect(ws)


# 鈹€鈹€ MJPEG Video Feed 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€

@app.get("/video_feed")
async def video_feed():
    """Stream camera frames as MJPEG ->event-driven, low latency."""

    async def generate_frames():
        last_jpeg = None
        while True:
            if video_client and video_client._frame_event:
                try:
                    await asyncio.wait_for(video_client._frame_event.wait(), timeout=0.05)
                except asyncio.TimeoutError:
                    pass
                video_client._frame_event.clear()

            jpeg = video_client.jpeg_bytes if video_client else None
            if jpeg is not None and jpeg is not last_jpeg:
                last_jpeg = jpeg
                yield (b'--frame\r\n'
                       b'Content-Type: image/jpeg\r\n'
                       b'Content-Length: ' + str(len(jpeg)).encode() + b'\r\n\r\n'
                       + jpeg + b'\r\n')
            elif jpeg is None:
                # Placeholder frame
                import cv2
                ph = np.zeros((480, 640, 3), dtype=np.uint8)
                cv2.putText(ph, "Waiting for camera...", (120, 240),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)
                _, jpg = cv2.imencode('.jpg', ph)
                yield (b'--frame\r\n'
                       b'Content-Type: image/jpeg\r\n\r\n' + jpg.tobytes() + b'\r\n')
                await asyncio.sleep(0.5)

    return StreamingResponse(
        generate_frames(),
        media_type="multipart/x-mixed-replace; boundary=frame",
    )


# 鈹€鈹€ REST API Endpoints 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€

@app.get("/api/state")
async def api_state():
    return build_state_snapshot()


@app.get("/api/device/config")
async def api_device_config():
    return {"ok": True, "device": _device_config_state()}


@app.post("/api/device/config")
async def api_set_device_config(payload: dict = Body(default={})):
    device_ip = payload.get("device_ip") or payload.get("ip") or ""
    ok, reason = _restart_video_client(str(device_ip))
    return {"ok": ok, "reason": reason, "device": _device_config_state()}


@app.get("/api/gimbal/state")
async def api_gimbal_state():
    # Read-only telemetry (hardware truth). No control is exposed here.
    return dict(_gimbal_tlm)


# NOTE: removed control endpoints: /api/gimbal/{yaw,pitch,speed,sleep,standby,
# stop,calibrate}, /api/face_track/*, /api/single_track/*, /api/multi_track/*,
# /api/sound_track/*, /api/tracking_mode. Control plane = core.orchestrator.


# 鈹€鈹€ Conversation Recording API 鈹€鈹€

@app.get("/api/conversation/state")
async def api_conversation_state():
    return _conversation_state()


@app.get("/api/conversation/debug")
async def api_conversation_debug():
    return _conversation_debug_state()


@app.post("/api/conversation/start")
async def api_conversation_start(payload: dict = None):
    global _conversation_recording_requested, _ui_session_id
    payload = payload or {}
    session_result = None
    if payload.get("control_session"):
        session_result = await _start_feature("meeting_recording")
        if not session_result.get("accepted"):
            return {"success": False, "recording_success": False, **session_result}
    _conversation_recording_requested = bool(payload.get("save_audio", False))
    doa_ok = _ensure_doa_reader()
    ok = _start_conversation_recording() if _conversation_recording_requested else True
    if not ok and session_result:
        await _stop_feature(session_result.get("session_id", ""))
    return {
        "success": bool(doa_ok and ok), "recording_success": bool(ok),
        "state": _conversation_state(), **(session_result or {}),
    }


@app.post("/api/conversation/stop")
async def api_conversation_stop(payload: dict = None):
    global _conversation_recording_requested
    payload = payload or {}
    _conversation_recording_requested = False
    _stop_conversation_recording(finalize=bool(payload.get("finalize", True)))
    session_result = await _stop_feature(str(payload.get("session_id", ""))) if payload.get("session_id") else {}
    return {"success": True, "state": _conversation_state(), **session_result}


@app.post("/api/conversation/save")
async def api_conversation_save(payload: dict = None):
    # Segments and timeline are written incrementally; this endpoint is a stable
    # frontend action that returns the current persisted session metadata.
    return {"success": True, "state": _conversation_state()}

# NOTE: removed /api/auto_align (gimbal yaw/pitch search + face-tracking start).

_last_snapshot = None  # cache last good frame

@app.get("/api/snapshot")
async def snapshot():
    """Return single JPEG frame. Uses _jpeg_bytes directly (always has last frame)."""
    from fastapi.responses import Response
    jpeg = video_client._jpeg_bytes if video_client else None
    if jpeg:
        return Response(content=jpeg, media_type="image/jpeg")
    return Response(status_code=204)


@app.post("/api/tracking_mode")
async def api_set_tracking_mode(payload: dict = Body(default={})):
    global _tracking_mode
    _tracking_mode = payload.get("mode", "single")
    return {"ok": True, "mode": _tracking_mode}


@app.post("/api/single_track/start")
async def api_single_track_start(payload: dict = Body(default={})):
    global _single_track_active, _multi_track_active, _tracking_mode
    result = await _start_feature("single_face_analysis")
    if not result.get("accepted"):
        return {**result, "active": False}
    _multi_track_active = False
    _single_track_active = True
    _tracking_mode = "single"
    return {**result, "active": True}


@app.post("/api/single_track/stop")
async def api_single_track_stop(payload: dict = Body(default={})):
    global _single_track_active
    _single_track_active = False
    result = await _stop_feature(str(payload.get("session_id", ""))) if payload.get("session_id") else {"ok": True}
    return {**result, "active": False}


@app.post("/api/multi_track/start")
async def api_multi_track_start(payload: dict = Body(default={})):
    global _multi_track_active, _single_track_active, _tracking_mode
    result = await _start_feature("multi_sound_yaw")
    if not result.get("accepted"):
        return {**result, "active": False}
    _single_track_active = False
    _multi_track_active = True
    _tracking_mode = "multi"
    if payload.get("save_audio", False):
        _start_conversation_recording()
    return {**result, "active": True}


@app.post("/api/multi_track/stop")
async def api_multi_track_stop(payload: dict = Body(default={})):
    global _multi_track_active
    _multi_track_active = False
    if payload.get("finalize", True):
        _stop_conversation_recording(finalize=True)
    result = await _stop_feature(str(payload.get("session_id", ""))) if payload.get("session_id") else {"ok": True}
    return {**result, "active": False}


async def _emit_ui_event(name: str, payload: dict) -> dict:
    global _control_obs
    event = Event.make("ui", name, "fastapi", payload=payload)
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, lambda: _eventbus.emit(event))
    _apply_runtime_result(result)
    eventbus_state = {
        "host": _eventbus.host,
        "port": _eventbus.port,
        "last_result": result,
    }
    _control_obs = {
        **_control_obs,
        "authority": result.get("authority", "unreachable"),
        "last_event": _ev_brief(event),
        "command": result.get("command"),
        "eventbus": eventbus_state,
    }
    return {
        **result,
        "event": event.to_dict(),
        "eventbus": eventbus_state,
    }


async def _start_feature(feature: str) -> dict:
    global _ui_session_id, _single_track_active, _multi_track_active
    session_id = uuid.uuid4().hex
    result = await _emit_ui_event(
        "feature_start",
        {"feature": feature, "session_id": session_id, "lease_ms": 1500},
    )
    if result.get("accepted"):
        _ui_session_id = session_id
        _single_track_active = feature == "single_face_analysis"
        _multi_track_active = feature in {"multi_sound_yaw", "meeting_sound_yaw"}
    return {**result, "session_id": session_id, "feature": feature}


async def _stop_feature(session_id: str) -> dict:
    global _ui_session_id
    if not session_id:
        return {"ok": False, "accepted": False, "reason": "session_id_required"}
    result = await _emit_ui_event("feature_stop", {"session_id": session_id})
    if session_id == _ui_session_id:
        _ui_session_id = ""
    return result


@app.post("/api/control/heartbeat")
async def api_control_heartbeat(payload: dict = Body(default={})):
    return await _emit_ui_event(
        "feature_heartbeat",
        {"session_id": str(payload.get("session_id", "")), "lease_ms": 1500},
    )


@app.get("/api/control/runtime")
async def api_control_runtime():
    return {"ok": bool(_runtime_cache.get("connected")), "runtime": dict(_runtime_cache)}


@app.get("/api/respeaker/state")
async def api_respeaker_state():
    return {"ok": True, "respeaker": _respeaker_state()}


@app.post("/api/control/manual/start")
async def api_manual_start():
    return await _start_feature("manual_gimbal_debug")


@app.post("/api/control/manual/stop")
async def api_manual_stop(payload: dict = Body(default={})):
    return await _stop_feature(str(payload.get("session_id", "")))


@app.post("/api/meeting/yaw/start")
async def api_meeting_yaw_start(payload: dict = Body(default={})):
    return await _emit_ui_event("feature_mode_update", {
        "feature": "meeting_sound_yaw", "session_id": str(payload.get("session_id", "")), "lease_ms": 2500,
    })


@app.post("/api/meeting/yaw/stop")
async def api_meeting_yaw_stop(payload: dict = Body(default={})):
    return await _emit_ui_event("feature_mode_update", {
        "feature": "meeting_recording", "session_id": str(payload.get("session_id", "")), "lease_ms": 2500,
    })


@app.post("/api/control/config")
async def api_control_config(payload: dict = Body(default={})):
    return await _emit_ui_event("control_config", {
        "session_id": str(payload.get("session_id", "")),
        "speed": payload.get("speed", 180),
        "doa_offset_deg": payload.get("doa_offset_deg", 0),
        "doa_direction": payload.get("doa_direction", 1),
    })


@app.post("/api/gimbal/home")
async def api_gimbal_home(payload: dict = Body(default={})):
    """Emit a UI Event. main_phase3 decides whether this becomes a command."""
    return await _emit_ui_event("gimbal_home", {"session_id": str(payload.get("session_id", ""))})


@app.post("/api/gimbal/standby")
async def api_gimbal_standby(payload: dict = Body(default={})):
    """Official Standby pose: yaw=180, pitch=90, speed=360 via control runtime."""
    return await _emit_ui_event("gimbal_standby", {"session_id": str(payload.get("session_id", ""))})


@app.post("/api/gimbal/sleep")
async def api_gimbal_sleep(payload: dict = Body(default={})):
    """Official Sleep pose: yaw=180, pitch=175, speed=360 via control runtime."""
    return await _emit_ui_event("gimbal_sleep", {"session_id": str(payload.get("session_id", ""))})


@app.post("/api/gimbal/stop")
async def api_gimbal_stop(payload: dict = Body(default={})):
    """Authorized stop event; main_phase3 chooses emergency stop vs session stop."""
    return await _emit_ui_event("gimbal_stop", {"session_id": str(payload.get("session_id", ""))})


@app.post("/api/gimbal/calibrate")
async def api_gimbal_calibrate(payload: dict = Body(default={})):
    """Official Calibrate action, mapped to Node-RED `gimbal cali` by the bridge."""
    return await _emit_ui_event("gimbal_calibrate", {"session_id": str(payload.get("session_id", ""))})


@app.post("/api/gimbal/move")
async def api_gimbal_move(payload: dict = Body(default={})):
    """Relative gimbal move. Body: {pan: float, tilt: float} degrees. Clamped to ±15/±10."""
    pan = max(-15.0, min(15.0, float(payload.get("pan", 0.0))))
    tilt = max(-10.0, min(10.0, float(payload.get("tilt", 0.0))))
    return await _emit_ui_event("dpad_move", {
        "pan": pan, "tilt": tilt, "session_id": str(payload.get("session_id", "")),
    })


@app.get("/api/debug/video")
async def debug_video():
    from fastapi.responses import Response
    global _last_snapshot
    vc = bool(video_client)
    jpeg_ok = bool(video_client._jpeg_bytes if video_client else None)
    snap_ok = bool(_last_snapshot)
    if video_client and video_client._jpeg_bytes:
        return Response(content=video_client._jpeg_bytes, media_type="image/jpeg")
    return dict(vc=vc, jpeg_ok=jpeg_ok, snap_ok=snap_ok, fps=video_client.fps if video_client else 0)


# 鈹€鈹€ Emotion debug (using EmotiEffLib now, see /api/state) 鈹€鈹€


@app.post("/api/reflect")
async def api_llm_reflect(payload: dict = Body(default={})):
    """LLM reflection: diary | quote | report. diary mode supports DeepSeek with richer context."""
    global _llm_engine
    if _llm_engine is None:
        from vision.llm_reflect import get_llm
        _llm_engine = get_llm()

    mode         = payload.get("mode", "diary")
    emotion      = payload.get("emotion", (_emotieff_result or {}).get("emotion", "Neutral"))
    attn         = int(payload.get("attention", (_attn_result or {}).get("score", 50)))
    prev         = payload.get("prev_emotion", "")
    user_text    = str(payload.get("user_text", ""))
    duration_min = int(payload.get("duration_min", 0))
    conf         = float((_emotieff_result or {}).get("confidence", 0.0))
    valence      = (_emotieff_result or {}).get("valence")

    if mode == "diary":
        val_desc = ("正向" if (valence or 0) > 0.1
                    else "负向" if (valence or 0) < -0.1 else "中性") if valence is not None else ""
        ds_sys = (
            "你是心屿，请以用户视角（'我'）生成今日日记条目。"
            "规则：不超过60字；不编造未提及的事件；时间词只用'今天'或'今日'；"
            "输出严格JSON，两个字段：{\"diary\":\"...\",\"reply\":\"一句温柔回应，不超过40字\"}"
        )
        ds_user = (
            f"情绪：{emotion}（置信度{conf:.0%}"
            f"{f'，监测{duration_min}分钟' if duration_min else ''}）；"
            f"专注分：{attn}/100{f'；情感效价：{val_desc}' if val_desc else ''}。"
            f"\n{f'用户自写：{user_text}' if user_text else '用户未填写文字。'}"
        )
        ds_raw = await _deepseek_chat([
            {"role": "system", "content": ds_sys},
            {"role": "user",   "content": ds_user},
        ], max_tokens=200)

        diary_entry = reply_text = ""
        source = "template"
        try:
            import json as _j
            parsed = _j.loads(ds_raw)
            diary_entry = parsed.get("diary", "")
            reply_text  = parsed.get("reply", "")
            if diary_entry:
                source = "deepseek"
        except Exception:
            pass

        if not diary_entry:
            diary_entry = _llm_engine.diary(emotion, attn, prev)
        if not reply_text:
            reply_text = _llm_engine.quote(emotion, "mid")

        return {"diary": diary_entry, "reply": reply_text, "text": diary_entry, "source": source,
                "time": round(_llm_engine._last_time, 2)}

    elif mode == "report":
        text = _llm_engine.report(
            payload.get("total_min", 0), payload.get("focused_pct", 0),
            emotion, attn,
        )
        return {"text": text, "time": round(_llm_engine._last_time, 2)}
    else:
        text = _llm_engine.quote(emotion, "专注" if attn >= 70 else "微弱" if attn >= 40 else "飘远")
        return {"text": text, "time": round(_llm_engine._last_time, 2)}


# 鈹€鈹€ DeepSeek API client 鈹€鈹€
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_API_URL = os.getenv("DEEPSEEK_API_URL", "https://api.deepseek.com/chat/completions")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash")
DEEPSEEK_MAX_TOKENS = int(os.getenv("DEEPSEEK_MAX_TOKENS", "600"))

async def _deepseek_chat(messages: list, max_tokens: int | None = None) -> str:
    """Call DeepSeek API. Returns reply text or empty string on failure."""
    import aiohttp
    if not DEEPSEEK_API_KEY:
        logger.info("DeepSeek API key not configured; using local/fallback chat")
        return ""
    headers = {
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": DEEPSEEK_MODEL,
        "messages": messages,
        "max_tokens": max_tokens or DEEPSEEK_MAX_TOKENS,
        "temperature": 0.8,
        "top_p": 0.9,
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(DEEPSEEK_API_URL, json=payload,
                                     headers=headers, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    choice = data["choices"][0]
                    finish_reason = choice.get("finish_reason")
                    if finish_reason and finish_reason != "stop":
                        logger.warning("DeepSeek finish_reason=%s; reply may be truncated", finish_reason)
                    return choice["message"]["content"].strip()
                else:
                    logger.warning("DeepSeek API returned %d: %s", resp.status, await resp.text())
                    return ""
    except Exception as e:
        logger.warning("DeepSeek API error: %s", str(e)[:100])
        return ""


def _reply_looks_incomplete(text: str) -> bool:
    """Heuristic guard for occasional provider-side half sentences."""
    if not text:
        return False
    stripped = text.strip()
    if len(stripped) < 12:
        return True
    if stripped[-1] in ".!?)]}":
        return False
    return stripped[-1] in ",:;"


_EMO_ZH_EN = {
    "开心": "Happiness", "悲伤": "Sadness", "愤怒": "Anger", "恐惧": "Fear",
    "惊讶": "Surprise", "厌恶": "Disgust", "轻蔑": "Contempt", "平静": "Neutral",
}


@app.post("/api/chat")
async def api_chat(payload: dict = Body(default={})):
    """Chat endpoint: DeepSeek with LLMReflect fallback. Accepts real emotion/attention/diary context."""
    global _llm_engine
    if _llm_engine is None:
        from vision.llm_reflect import get_llm
        _llm_engine = get_llm()

    msg        = str(payload.get("message", "")).strip()
    emotion_zh = str(payload.get("emotion", ""))
    context_s  = str(payload.get("context", ""))
    diary_text = str(payload.get("diary_text", ""))
    user_name  = str(payload.get("user_name", ""))

    emo_key = _EMO_ZH_EN.get(emotion_zh, (_emotieff_result or {}).get("emotion", "Neutral"))
    attn    = (_attn_result or {}).get("score", 75)
    conf    = (_emotieff_result or {}).get("confidence", 0.0)
    valence = (_emotieff_result or {}).get("valence")

    val_desc = ("正向" if (valence or 0) > 0.1
                else "负向" if (valence or 0) < -0.1 else "中性") if valence is not None else ""

    sys_prompt = (
        "你是心屿（XINYU），一个温柔陪伴型AI。"
        "风格：用第二人称；语气温柔、不过分热情，像熟悉的老朋友；"
        "接受负面情绪而不是急于解决；"
        "只引用给你的实测数字，绝不编造未提及的内容；"
        "每次回复不超过80字，自然段落，不使用列表或标题。"
    )
    user_ctx = (
        f"【实测状态】情绪：{emotion_zh or emo_key}（置信度{conf:.0%}）；"
        f"专注分：{attn}/100。"
    )
    if val_desc:
        user_ctx += f" 情感效价：{val_desc}。"
    if diary_text:
        user_ctx += f"\n【今日日记】{diary_text[:200]}"
    if context_s:
        user_ctx += f"\n【背景】{context_s[:300]}"
    user_ctx += f"\n\n{msg or '请结合我今天的状态，给我一句有温度的话。'}"

    reply = await _deepseek_chat([
        {"role": "system", "content": sys_prompt},
        {"role": "user",   "content": user_ctx},
    ], max_tokens=150)

    if not reply or _reply_looks_incomplete(reply):
        reply  = _llm_engine.respond_to_user(msg, emo_key, user_name=user_name, context=context_s)
        source = "template"
    else:
        source = "deepseek"

    return {"reply": reply, "source": source, "emotion": emo_key}


@app.get("/api/chat/status")
async def api_chat_status():
    return {
        "configured": bool(DEEPSEEK_API_KEY),
        "model": DEEPSEEK_MODEL,
        "api_url": DEEPSEEK_API_URL,
    }


@app.post("/api/meeting/summarize")
async def api_meeting_summarize(payload: dict = Body(default={})):
    """Transcribe WAV segments from ConversationRecorder, summarize with DeepSeek."""
    from pathlib import Path as _Path
    from audio.transcriber import transcribe_wav

    recorder = _conversation_recorder
    if recorder is None:
        return {"ok": False, "error": "录音未启动，请先开启多人场景"}

    session_state = recorder.state()
    turns = session_state.get("timeline", [])
    if not turns:
        return {"ok": False, "error": "本次无录音片段"}

    transcripts = []
    for turn in turns:
        wav = turn.get("wav_path", "")
        doa = turn.get("doa_mean")
        if wav and _Path(wav).exists():
            text = await transcribe_wav(wav)
            if text:
                zone = "左侧" if (doa or 180) < 135 else "右侧" if (doa or 180) > 225 else "正前方"
                transcripts.append(f"[{zone}] {text}")

    if not transcripts:
        return {"ok": False, "error": "转写结果为空（faster-whisper 未安装或语音过短）"}

    full_transcript = "\n".join(transcripts)
    duration_min = round(session_state.get("stats", {}).get("duration", 0) / 60, 1)

    sys_p = (
        "你是心屿，请将以下多人对话整理为一段今日会议摘要。"
        "要求：用'我'的视角；描述对话的核心内容和氛围；不超过100字；"
        "输出严格JSON：{\"diary\":\"...\",\"summary\":\"一句话摘要，不超过30字\"}"
    )
    usr_p = f"对话时长：{duration_min}分钟。\n逐句记录（方向标注）：\n{full_transcript[:1500]}"

    raw = await _deepseek_chat([
        {"role": "system", "content": sys_p},
        {"role": "user",   "content": usr_p},
    ], max_tokens=300)

    diary_text = summary_text = ""
    try:
        import json as _j
        parsed = _j.loads(raw)
        diary_text   = parsed.get("diary", "")
        summary_text = parsed.get("summary", "")
    except Exception:
        diary_text   = raw[:100] if raw else "本次会议记录整理完成。"
        summary_text = diary_text[:30]

    return {
        "ok": True,
        "diary": diary_text,
        "summary": summary_text,
        "transcript": full_transcript,
        "turns": len(transcripts),
        "duration_min": duration_min,
    }


@app.get("/api/health")
async def health():
    return {
        "status": "ok",
        "video": video_client._connected if video_client else False,
        "gimbal": bool(_gimbal_tlm.get("connected")),
    }


# 鈹€鈹€ Two pages only 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€
# PAGE 1 = Control Dashboard (real telemetry/observability) -> /control , /v2
# PAGE 2 = App / Demo (mock only)                           -> / , /home
HOME_FILE = DASHBOARD_DIR / "home.html"
_NOCACHE = {"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache", "Expires": "0"}


def _serve_html(path: Path):
    return (HTMLResponse(path.read_text(encoding="utf-8"), headers=dict(_NOCACHE))
            if path.is_file() else HTMLResponse("Not found", status_code=404))


@app.get("/")
async def serve_root():
    return RedirectResponse("/home")


@app.get("/home")
async def serve_home():
    # PAGE 2: product demo / feature preview (mock data only).
    return _serve_html(HOME_FILE)


@app.get("/control")
@app.get("/v2")
async def serve_control():
    # PAGE 1: real-time control dashboard + observability.
    return _serve_html(HTML_FILE)


@app.get("/manifest.webmanifest")
async def serve_webmanifest():
    t = DASHBOARD_DIR / "manifest.webmanifest"
    return FileResponse(
        t,
        media_type="application/manifest+json",
        headers={"Cache-Control": "no-cache"},
    ) if t.is_file() else HTMLResponse("Not found", status_code=404)


@app.get("/sw.js")
async def serve_service_worker():
    t = DASHBOARD_DIR / "sw.js"
    return FileResponse(
        t,
        media_type="application/javascript",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Service-Worker-Allowed": "/",
        },
    ) if t.is_file() else HTMLResponse("Not found", status_code=404)


# 鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲->#  CLI + Entry point
# 鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲->
def parse_args():
    p = argparse.ArgumentParser(
        description="reCamera Demo Dashboard (FastAPI+MJPEG)",
        epilog="Examples:\n"
               "  %(prog)s                          # dashboard + USB ReSpeaker + EventBus emitter\n"
               "  RECAMERA_DEVICE_IP=<RECAMERA_IP> %(prog)s  # use the current WiFi device\n",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--device-ip", default=os.environ.get(DEVICE_IP_ENV, ""), help=f"reCamera device address (or set {DEVICE_IP_ENV})")
    p.add_argument("--host", default="0.0.0.0", help="Server host")
    p.add_argument("--port", type=int, default=8001, help="Server port")
    p.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING"])
    p.add_argument("--ssl-keyfile", default="", help="Optional TLS key file for tablet PWA install")
    p.add_argument("--ssl-certfile", default="", help="Optional TLS cert file for tablet PWA install")
    return p.parse_args()


def main():
    args = parse_args()
    setup_root_logger(level=args.log_level)


    global app_config
    try:
        device_ip = normalize_device_ip(args.device_ip)
    except ValueError as exc:
        logger.error("%s", exc)
        raise SystemExit(2)
    app_config = Config(
        device_ip=device_ip,
        host=args.host,
        port=args.port,
        ssl_enabled=bool(args.ssl_keyfile and args.ssl_certfile),
    )

    import uvicorn
    ssl_kwargs = {}
    scheme = "http"
    ws_scheme = "ws"
    if args.ssl_keyfile and args.ssl_certfile:
        missing = [p for p in (args.ssl_keyfile, args.ssl_certfile) if not Path(p).is_file()]
        if missing:
            logger.error("TLS file not found: %s", ", ".join(missing))
            logger.error("Generate a local cert first, for example: mkdir -p certs && openssl req -x509 -newkey rsa:2048 -nodes -days 825 -keyout certs/xinyu-key.pem -out certs/xinyu-cert.pem -subj '/CN=localhost' -addext 'subjectAltName=DNS:localhost,IP:127.0.0.1'")
            raise SystemExit(2)
        ssl_kwargs = {"ssl_keyfile": args.ssl_keyfile, "ssl_certfile": args.ssl_certfile}
        scheme = "https"
        ws_scheme = "wss"
        logger.info("🔐 HTTPS enabled for PWA")
    logger.info("🔒 FastAPI emits UI Events only; main_phase3 owns hardware control")
    logger.info("🌐 Dashboard: %s://localhost:%d/home  (%s://localhost:%d/v2)", scheme, args.port, scheme, args.port)
    logger.info("📡 MJPEG:     %s://localhost:%d/video_feed", scheme, args.port)
    logger.info("🔌 WebSocket: %s://localhost:%d/ws", ws_scheme, args.port)
    uvicorn.run(app, host=args.host, port=args.port, log_level="info", **ssl_kwargs)


if __name__ == "__main__":
    main()
