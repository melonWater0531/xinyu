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
from collections import deque
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Optional

import numpy as np
import cv2

from fastapi import Body, FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from core.device_config import (
    DEVICE_IP_ENV,
    bypass_proxy_for_device,
    device_sscma_ws_url,
    normalize_device_ip,
)
from core.device_config_store import device_config_store
from core.event import Event
from core.event_bus import EventBusClient
from utils.logger import get_logger, setup_root_logger
from vision.person_stabilizer import StablePersonCounter

logger = get_logger(__name__)


# NOTE: FastAPI is UI + telemetry only. It emits Events to the localhost
# EventBus and never imports or calls the hardware control layer.



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
        bypass_proxy_for_device(self._device_ip)
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
_person_count_stabilizer = StablePersonCounter()
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
_meeting_report = {
    "status": "idle", "summary": "", "minutes": "", "transcript": "",
    "turns": 0, "duration_min": 0.0, "error": "",
}
_meeting_summary_task = None
_meeting_recording_task = None
_asr_queue = None
_asr_worker_task = None
_asr_loop = None
_asr_enqueued_turns: set[str] = set()
_asr_running_turns: set[str] = set()
_asr_stats = {
    "pending": 0,
    "running": 0,
    "done": 0,
    "failed": 0,
    "last_error": "",
    "last_error_at": 0.0,
}
_wake_word_service = None
try:
    from services.voice_policy import voice_policy
except Exception:
    voice_policy = None
# Single/multi tracking mode — UI state only (no hardware binding)
_tracking_mode: str = "single"
_single_track_active: bool = False
_multi_track_active: bool = False
_ui_session_id: str = ""
CONTROL_LEASE_MS = 5000
EVENTBUS_TIMEOUT_S = float(os.environ.get("RECAMERA_EVENTBUS_TIMEOUT", "2.0"))
HEARTBEAT_EVENTBUS_TIMEOUT_S = float(os.environ.get("RECAMERA_HEARTBEAT_EVENTBUS_TIMEOUT", "0.75"))
RECORDING_START_TIMEOUT_S = float(os.environ.get("RECAMERA_RECORDING_START_TIMEOUT", "4.0"))
RECORDING_STOP_TIMEOUT_S = float(os.environ.get("RECAMERA_RECORDING_STOP_TIMEOUT", "3.0"))
ASR_IDLE_WAIT_S = float(os.environ.get("RECAMERA_ASR_IDLE_WAIT", "45.0"))
_heartbeat_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="eventbus-heartbeat")
# Recorder start/stop can block indefinitely inside PortAudio when the audio
# device is absent/busy. A dedicated single-worker pool caps the damage to one
# stranded thread; a stuck start is detected via _recorder_start_future below.
_recorder_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="recorder-ctl")
_recorder_start_future = None
# EventBus emits get their own small pool so a wedged recorder thread can
# never starve the control plane (which previously shared the default pool).
_bus_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="eventbus-emit")
_heartbeat_future = None
_heartbeat_eventbus_in_flight = False
_heartbeat_state = {
    "state": "idle",
    "last_ok_at": 0.0,
    "last_error": "",
    "last_error_at": 0.0,
    "eventbus_in_flight": False,
}
_TRACKING_TELEMETRY_DEFAULTS = {
    "tracking_state": "IDLE",
    "target_visible": False,
    "locked_track_id": None,
    "raw_bbox": None,
    "track_bbox": None,
    "control_target": None,
    "last_control_target": None,
    "face_center": None,
    "frame_center": {"x": 960.0, "y": 540.0},
    "error_x_px": None,
    "error_y_px": None,
    "error_x_ratio": None,
    "error_y_ratio": None,
    "deadband_x_px": None,
    "deadband_y_px": None,
    "safe_roi": None,
    "edge_margin": None,
    "target_yaw_deg": None,
    "target_pitch_deg": None,
    "command_yaw_deg": None,
    "command_pitch_deg": None,
    "yaw_cmd": None,
    "pitch_cmd": None,
    "centered_reason": "",
    "centered_block_reason": "no_target",
    "demo_stop_shake_mode": False,
    "demo_zone": "NO_FACE",
    "demo_hold_active": False,
    "demo_hold_reason": "",
    "body_align_suppressed": False,
    "motion_blocked_reason": "",
    "command_delta_yaw_deg": None,
    "command_delta_pitch_deg": None,
    "command_sent": False,
    "frame_age_ms": None,
    "face_detection_ms": None,
    "embedding_ms": None,
    "tracker_update_ms": None,
    "control_loop_ms": None,
    "vision_hz": None,
    "control_hz": None,
    "telemetry_hz": None,
    "ui_push_hz": None,
    "tracking_config_loaded": False,
    "tracking_config_path": "",
    "tracking_config_error": "",
}
_runtime_cache = {
    **_TRACKING_TELEMETRY_DEFAULTS,
    "connected": False,
    "active_feature": "inactive",
    "session_id": "",
    "lease_remaining_ms": 0,
    "authority": "unreachable",
}
_led_runtime_mode = ""
_last_audio_event_active = False
_last_audio_event_session_id = ""
_lip_motion_history = {}
_analysis_features = {
    "gesture_interaction": False,
    "health_pwa": False,
    "llm_diary": False,
}


def _current_running_loop():
    try:
        return asyncio.get_running_loop()
    except RuntimeError:
        return None


def _emotion_valence(emotion: str = "", probabilities: dict | None = None, fallback=None):
    if fallback is not None:
        try:
            return round(float(fallback), 4)
        except (TypeError, ValueError):
            pass
    probs = probabilities or {}
    positive = float(probs.get("Happiness", 0.0)) + 0.35 * float(probs.get("Surprise", 0.0))
    negative = sum(float(probs.get(k, 0.0)) for k in ("Sadness", "Anger", "Fear", "Disgust", "Contempt"))
    if probs:
        return round(max(-1.0, min(1.0, positive - negative)), 4)
    if emotion == "Happiness":
        return 0.8
    if emotion == "Surprise":
        return 0.2
    if emotion in {"Sadness", "Anger", "Fear", "Disgust", "Contempt"}:
        return -0.7
    return 0.0


def _device_config_state() -> dict:
    ip = app_config.device_ip if app_config else ""
    stored = device_config_store.read()
    control_connected = bool(_runtime_cache.get("connected"))
    return {
        "ip": ip,
        "configured": bool(ip),
        "sscma_url": device_sscma_ws_url(ip) if ip else "",
        "video_connected": bool(video_client.connected) if video_client else False,
        "control_connected": control_connected,
        "control_state": "ready" if control_connected else "video_only",
        "version": int(stored.get("version", 0)),
        "updated_at": stored.get("updated_at"),
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
        new_client._event_loop = _current_running_loop()
    except RuntimeError:
        pass
    new_client.start()

    with _video_client_lock:
        video_client = new_client
    if app_config:
        app_config.device_ip = ip
    bypass_proxy_for_device(ip)
    return True, "video_client_restarted"


def _audio_event(doa_deg: float, speech: bool, source: str = "doa", session_id: str = "") -> Event:
    payload = {"doa_deg": float(doa_deg), "speech": bool(speech)}
    if session_id:
        payload["session_id"] = session_id
    return Event.make("audio", "speech_detected", source, payload)


def _vision_event(cx: float, cy: float, conf: float, source: str = "vision") -> Event:
    return Event.make("vision", "target_detected", source, {"cx": float(cx), "cy": float(cy), "conf": float(conf)})


def _runtime_with_telemetry_defaults(runtime: dict | None = None) -> dict:
    data = {
        k: (dict(v) if isinstance(v, dict) else v)
        for k, v in _TRACKING_TELEMETRY_DEFAULTS.items()
    }
    if runtime:
        data.update(runtime)
    return data


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
    _runtime_cache = _runtime_with_telemetry_defaults({**runtime, "connected": True})
    feature = _runtime_cache.get("active_feature", "inactive")
    _single_track_active = feature == "single_face_analysis"
    _multi_track_active = feature in {"multi_sound_yaw", "meeting_sound_yaw"}
    if feature == "inactive" and previous_feature in {"multi_sound_yaw", "meeting_recording", "meeting_sound_yaw"}:
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
        "hardware_io": runtime.get("hardware_io", {}),
        "hardware_ready": bool(runtime.get("hardware_ready")),
        "resource_locks": {
            "gimbal": runtime.get("active_feature", "inactive"),
            "analytics": [name for name, active in _analysis_features.items() if active],
        },
        "eventbus": {
            "host": _eventbus.host, "port": _eventbus.port,
            "last_result": {"ok": True, "accepted": True},
        },
        "active_feature": runtime.get("active_feature", "inactive"),
        "session_id": runtime.get("session_id", ""),
        "lease_remaining_ms": runtime.get("lease_remaining_ms", 0),
        "tracking_phase": runtime.get("tracking_phase", "inactive"),
        "lock_state": runtime.get("lock_state", "acquiring"),
        "lock_candidate_id": runtime.get("lock_candidate_id"),
        "lock_confirm_frames": runtime.get("lock_confirm_frames", 0),
        "target_point": runtime.get("target_point", {"x": 0.5, "y": 0.32, "framing_mode": "upper_body"}),
        "tracking_error": runtime.get("tracking_error", {"x": 0.0, "y": 0.0}),
        "command_suppressed_reason": runtime.get("command_suppressed_reason", ""),
        "heartbeat_state": dict(_heartbeat_state),
        "last_heartbeat_ok_at": _heartbeat_state.get("last_ok_at", 0.0),
        "last_heartbeat_error": _heartbeat_state.get("last_error", ""),
        "eventbus_in_flight": bool(_heartbeat_eventbus_in_flight),
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
        result = await loop.run_in_executor(_bus_pool, lambda: _eventbus.emit(event))
        if result.get("ok") and isinstance(result.get("runtime"), dict):
            _apply_runtime_result(result)
            _set_respeaker_led_for_feature(_runtime_cache.get("active_feature", "inactive"))
        else:
            _runtime_cache = _runtime_with_telemetry_defaults({
                **_runtime_cache,
                "connected": False,
                "authority": "unreachable",
                "lease_remaining_ms": 0,
            })
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
            lip_motion, lip_score, lip_track_id = _lip_motion_evidence()
            event = Event.make(
                "audio", "speech_detected", "respeaker",
                payload={
                    "doa_deg": float(_doa_reader.doa), "speech": True, "session_id": session_id,
                    "vad_confidence": 0.8, "lip_motion": lip_motion,
                    "lip_motion_score": lip_score, "lip_track_id": lip_track_id,
                },
            )
            result = await loop.run_in_executor(_bus_pool, lambda: _eventbus.emit(event))
            _apply_runtime_result(result)
        elif _last_audio_event_active:
            event = Event.make(
                "audio", "timeout", "respeaker",
                payload={"speech": False, "session_id": session_id or _last_audio_event_session_id},
            )
            result = await loop.run_in_executor(_bus_pool, lambda: _eventbus.emit(event))
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


def _lip_motion_evidence() -> tuple[Optional[bool], float, Optional[int]]:
    """Return 800 ms normalized mouth-motion evidence for visible tracks."""
    now = time.monotonic()
    best_score, best_track, reliable = 0.0, None, False
    active_ids = set()
    for person in list(_latest_pose_persons):
        track_id = int(getattr(person, "_track_id", -1) or -1)
        points = {getattr(kp, "name", ""): (float(kp.x), float(kp.y)) for kp in getattr(person, "keypoints", [])}
        if "left_mouth" not in points or "right_mouth" not in points:
            continue
        bbox = getattr(person, "bbox", (0, 0, 1, 1))
        face_scale = max(1.0, float(bbox[2]) - float(bbox[0]))
        mouth_width = abs(points["right_mouth"][0] - points["left_mouth"][0]) / face_scale
        history = _lip_motion_history.setdefault(track_id, deque(maxlen=16))
        history.append((now, mouth_width))
        while history and now - history[0][0] > 0.8:
            history.popleft()
        active_ids.add(track_id)
        if len(history) >= 4 and history[-1][0] - history[0][0] >= 0.45:
            reliable = True
            score = max(value for _, value in history) - min(value for _, value in history)
            if score > best_score:
                best_score, best_track = score, track_id
    for track_id in list(_lip_motion_history):
        if track_id not in active_ids:
            _lip_motion_history.pop(track_id, None)
    if not reliable:
        return None, 0.0, None
    normalized = max(0.0, min(1.0, best_score / 0.04))
    return normalized >= 0.35, normalized, best_track


def _meeting_speaker_provider(doa_deg: Optional[float]):
    if doa_deg is None:
        return None
    from services.speaker_mapper import speaker_mapper

    match = speaker_mapper.lookup(float(doa_deg))
    if match:
        return {
            "label": str(match.get("label", "未知说话人")),
            "track_id": match.get("track_id"),
            "confidence": float(match.get("confidence", 0.0) or 0.0),
        }

    try:
        state = build_state_snapshot().get("data", {})
        face_lock = state.get("face_lock") or {}
        gimbal = state.get("gimbal") or {}
        pitch = gimbal.get("pitch")
        if face_lock.get("locked"):
            info = speaker_mapper.register(
                doa_deg=float(doa_deg),
                track_id=face_lock.get("track_id"),
                pitch=float(pitch) if pitch is not None else None,
                confidence=0.8,
            )
            return {
                "label": str(info["label"]),
                "track_id": info.get("track_id"),
                "confidence": float(info.get("confidence", 0.8) or 0.8),
            }

        pose = state.get("pose") or {}
        persons = pose.get("persons") or []
        video = state.get("video") or {}
        width = int(video.get("width") or 1920)
        yaw = float(gimbal.get("yaw") or doa_deg)
        face = speaker_mapper.find_closest_face_to_doa(persons, float(doa_deg), width, yaw)
        if face:
            info = speaker_mapper.register(
                doa_deg=float(doa_deg),
                track_id=face.get("track_id"),
                pitch=float(pitch) if pitch is not None else None,
                confidence=float(face.get("association_confidence", 0.65)),
            )
            return {
                "label": str(info["label"]),
                "track_id": info.get("track_id"),
                "confidence": float(info.get("confidence", face.get("association_confidence", 0.65)) or 0.0),
            }
    except Exception as exc:
        logger.debug("meeting speaker provider failed: %s", str(exc)[:100])
    return None


def _wake_word_state() -> dict:
    if _wake_word_service is None:
        return {"enabled": False, "available": False, "listening": False, "paused": False, "error": ""}
    return _wake_word_service.state()


def _voice_state() -> dict:
    if voice_policy is None:
        return {
            "enabled": False,
            "available": False,
            "speaking": False,
            "queue_len": 0,
            "last_utterance": "",
            "last_reason": "",
            "engine": "browser_speech",
            "cooldowns": {},
            "recent_events": [],
            "error": "voice_policy_unavailable",
        }
    return voice_policy.state()


async def _emit_voice(
    text: str,
    reason: str = "manual",
    priority: str = "normal",
    interrupt: bool = False,
    source: str = "fastapi",
    force: bool = False,
) -> dict:
    if voice_policy is None:
        return {"ok": False, "reason": "voice_policy_unavailable"}
    utterance = voice_policy.build(
        text,
        reason=reason,
        priority=priority,
        interrupt=interrupt,
        source=source,
        force=force,
    )
    if utterance is None:
        return {"ok": False, "reason": "suppressed", "state": _voice_state()}
    event = utterance.to_event()
    await ws_mgr.broadcast(event)
    return {"ok": True, "event": event, "state": _voice_state()}


async def _emit_voice_reason(
    reason: str,
    priority: str = "normal",
    interrupt: bool = False,
    source: str = "fastapi",
    force: bool = False,
) -> dict:
    if voice_policy is None:
        return {"ok": False, "reason": "voice_policy_unavailable"}
    text = voice_policy.short_text_for(reason)
    return await _emit_voice(
        text,
        reason=reason,
        priority=priority,
        interrupt=interrupt,
        source=source,
        force=force,
    )


async def _emit_voice_stop(reason: str = "manual") -> dict:
    if voice_policy is None:
        return {"ok": False, "reason": "voice_policy_unavailable"}
    event = voice_policy.stop_event(reason=reason)
    await ws_mgr.broadcast(event)
    return {"ok": True, "event": event, "state": _voice_state()}


def _pause_wake_word() -> None:
    if _wake_word_service is not None:
        _wake_word_service.pause()


def _resume_wake_word() -> None:
    if _wake_word_service is not None:
        _wake_word_service.resume()


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
        speaker_provider=_meeting_speaker_provider,
        sample_rate=16000,
        device=audio_device,
        segment_callback=_on_conversation_segment,
    )
    return _conversation_recorder


def _asr_state() -> dict:
    queue_size = 0
    if _asr_queue is not None:
        try:
            queue_size = int(_asr_queue.qsize())
        except Exception:
            queue_size = 0
    return {
        "queue": queue_size,
        "pending": int(_asr_stats.get("pending", 0) or 0),
        "running": int(_asr_stats.get("running", 0) or 0),
        "done": int(_asr_stats.get("done", 0) or 0),
        "failed": int(_asr_stats.get("failed", 0) or 0),
        "last_error": str(_asr_stats.get("last_error", "") or ""),
        "last_error_at": float(_asr_stats.get("last_error_at", 0.0) or 0.0),
    }


def _mark_asr_error(message: str) -> None:
    _asr_stats["failed"] = int(_asr_stats.get("failed", 0) or 0) + 1
    _asr_stats["last_error"] = str(message or "")[:160]
    _asr_stats["last_error_at"] = time.time()


def _ensure_asr_worker():
    global _asr_queue, _asr_worker_task, _asr_loop
    loop = _current_running_loop()
    if loop is None:
        return None
    if _asr_loop is not None and _asr_loop is not loop:
        if _asr_worker_task is not None and not _asr_worker_task.done():
            _asr_worker_task.cancel()
        _asr_queue = None
        _asr_worker_task = None
        _asr_enqueued_turns.clear()
        _asr_running_turns.clear()
    _asr_loop = loop
    if _asr_queue is None:
        _asr_queue = asyncio.Queue(maxsize=200)
    if _asr_worker_task is None or _asr_worker_task.done():
        _asr_worker_task = asyncio.create_task(_asr_worker_loop(), name="meeting-asr-worker")
    return _asr_worker_task


def _enqueue_asr_turn_now(turn: dict, force: bool = False) -> bool:
    if _asr_queue is None:
        return False
    turn_id = str(turn.get("id") or "")
    wav_path = str(turn.get("wav_path") or "")
    if not turn_id or not wav_path:
        return False
    if not force and turn_id in _asr_enqueued_turns:
        return False
    if not force and str(turn.get("text") or "").strip():
        return False
    try:
        _asr_queue.put_nowait(dict(turn))
        _asr_enqueued_turns.add(turn_id)
        _asr_stats["pending"] = int(_asr_stats.get("pending", 0) or 0) + 1
        if _conversation_recorder is not None and hasattr(_conversation_recorder, "set_turn_status"):
            _conversation_recorder.set_turn_status(turn_id, "asr_pending")
        return True
    except asyncio.QueueFull:
        _mark_asr_error("ASR 队列已满，片段暂未转写")
        if _conversation_recorder is not None and hasattr(_conversation_recorder, "set_turn_status"):
            _conversation_recorder.set_turn_status(turn_id, "asr_failed", "asr_queue_full")
        return False


def _on_conversation_segment(turn: dict) -> None:
    """Thread-safe callback from ConversationRecorder when a wav segment is ready."""
    _ensure_asr_worker()
    if _asr_loop is None or _asr_loop.is_closed():
        return
    try:
        _asr_loop.call_soon_threadsafe(_enqueue_asr_turn_now, dict(turn), False)
    except RuntimeError:
        pass


async def _asr_worker_loop():
    from pathlib import Path as _Path
    from services.cloud_asr import cloud_asr as _cloud_asr

    while True:
        turn = await _asr_queue.get()
        turn_id = str(turn.get("id") or "")
        wav = str(turn.get("wav_path") or "")
        _asr_stats["pending"] = max(0, int(_asr_stats.get("pending", 0) or 0) - 1)
        _asr_running_turns.add(turn_id)
        _asr_stats["running"] = len(_asr_running_turns)
        try:
            if _conversation_recorder is not None and hasattr(_conversation_recorder, "set_turn_status"):
                _conversation_recorder.set_turn_status(turn_id, "transcribing")
            if not wav or not _Path(wav).exists():
                raise FileNotFoundError("audio segment missing")
            text = await _cloud_asr.transcribe(wav)
            if _conversation_recorder is not None:
                _conversation_recorder.set_transcript(turn_id, text or "")
            if text:
                _asr_stats["done"] = int(_asr_stats.get("done", 0) or 0) + 1
            else:
                _mark_asr_error("ASR 返回空文本")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            _mark_asr_error(str(exc))
            if _conversation_recorder is not None and hasattr(_conversation_recorder, "set_turn_status"):
                _conversation_recorder.set_turn_status(turn_id, "asr_failed", str(exc))
        finally:
            _asr_running_turns.discard(turn_id)
            _asr_stats["running"] = len(_asr_running_turns)
            _asr_queue.task_done()


async def _wait_for_asr_idle(timeout_s: float = ASR_IDLE_WAIT_S) -> bool:
    deadline = time.monotonic() + max(0.0, float(timeout_s))
    while time.monotonic() < deadline:
        state = _asr_state()
        if state["queue"] <= 0 and state["running"] <= 0 and state["pending"] <= 0:
            return True
        await asyncio.sleep(0.2)
    return False


def _start_conversation_recording() -> bool:
    global _last_conversation_start_attempt
    recorder = _ensure_conversation_recorder()
    if recorder.active:
        return True
    now = time.monotonic()
    _last_conversation_start_attempt = now
    return bool(recorder.start())


async def _start_conversation_recording_async() -> bool:
    loop = _current_running_loop()
    if loop is None:
        return _start_conversation_recording()
    return bool(await asyncio.wait_for(
        loop.run_in_executor(None, _start_conversation_recording),
        timeout=RECORDING_START_TIMEOUT_S,
    ))


async def _start_meeting_recording_background() -> None:
    global _conversation_recording_requested, _meeting_report
    _ensure_asr_worker()
    _meeting_report = {
        **_meeting_report,
        "status": "recording_starting",
        "error": "",
        "progress": 0,
    }
    try:
        doa_ok = _ensure_doa_reader()
        ok = await _start_conversation_recording_async()
        if doa_ok and ok:
            _meeting_report = {
                **_meeting_report,
                "status": "recording",
                "error": "",
                "progress": 0,
            }
            return
        _conversation_recording_requested = False
        reason = "ReSpeaker DOA 或录音输入不可用"
        if _conversation_recorder is not None:
            reason = _conversation_recorder.state().get("error") or reason
        _meeting_report = {
            **_meeting_report,
            "status": "recording_degraded",
            "error": reason,
            "progress": 0,
        }
    except asyncio.TimeoutError:
        _conversation_recording_requested = False
        _meeting_report = {
            **_meeting_report,
            "status": "recording_degraded",
            "error": f"录音设备启动超过 {RECORDING_START_TIMEOUT_S:.1f}s，已降级为仅定位",
            "progress": 0,
        }
    except Exception as exc:
        _conversation_recording_requested = False
        _meeting_report = {
            **_meeting_report,
            "status": "recording_degraded",
            "error": str(exc)[:160],
            "progress": 0,
        }


def _stop_conversation_recording(finalize: bool = True) -> None:
    if _conversation_recorder is not None:
        _conversation_recorder.stop(finalize=finalize)


async def _stop_conversation_recording_async(finalize: bool = True) -> bool:
    loop = _current_running_loop()
    if loop is None:
        _stop_conversation_recording(finalize=finalize)
        return True
    try:
        await asyncio.wait_for(
            loop.run_in_executor(None, lambda: _stop_conversation_recording(finalize=finalize)),
            timeout=RECORDING_STOP_TIMEOUT_S,
        )
        return True
    except asyncio.TimeoutError:
        logger.warning("Conversation recorder stop timed out after %.1fs", RECORDING_STOP_TIMEOUT_S)
        return False


def _conversation_state() -> dict:
    from services.speaker_mapper import speaker_mapper
    asr = _asr_state()
    if _conversation_recorder is None:
        return {
            "active": False,
            "available": True,
            "error": "",
            "mode": "doa_only",
            "requested": bool(_conversation_recording_requested),
            "recording_state": "starting" if (_meeting_recording_task is not None and not _meeting_recording_task.done()) else "idle",
            "meeting_state": _meeting_report.get("status", "idle"),
            "last_recording_error": str(_meeting_report.get("error", "") or ""),
            "last_asr_error": asr["last_error"],
            "asr_queue": asr["queue"],
            "asr_pending": asr["pending"],
            "asr_running": asr["running"],
            "asr_done": asr["done"],
            "asr_failed": asr["failed"],
            "session_id": "",
            "recording": False,
            "current": {},
            "timeline": [],
            "stats": {"turns": 0, "speakers": 0, "duration": 0.0},
            "speakers": speaker_mapper.get_registered_speakers(),
            "report": dict(_meeting_report),
        }
    state = _conversation_recorder.state()
    if state.get("active"):
        recording_state = "recording"
        mode = "audio_recording"
    elif _meeting_recording_task is not None and not _meeting_recording_task.done():
        recording_state = "starting"
        mode = "recording_starting"
    elif state.get("error") and _conversation_recording_requested:
        recording_state = "error"
        mode = "recording_error"
    elif _meeting_report.get("status") in {"summarizing", "ready", "recorded", "error"}:
        recording_state = "recorded"
        mode = "recording_complete"
    else:
        recording_state = "idle"
        mode = "recording_complete"
    state["mode"] = mode
    state["requested"] = bool(_conversation_recording_requested)
    state["recording_state"] = recording_state
    state["meeting_state"] = _meeting_report.get("status", "idle")
    state["last_recording_error"] = str(state.get("error") or _meeting_report.get("error", "") or "")
    state["last_asr_error"] = asr["last_error"]
    state["asr_queue"] = asr["queue"]
    state["asr_pending"] = asr["pending"]
    state["asr_running"] = asr["running"]
    state["asr_done"] = asr["done"]
    state["asr_failed"] = asr["failed"]
    state["asr"] = asr
    state["speakers"] = speaker_mapper.get_registered_speakers()
    state["report"] = dict(_meeting_report)
    return state


def _audio_processing_state() -> dict:
    if _conversation_recorder is not None:
        return _conversation_recorder.audio_processing_state()
    return {
        "noise_suppression": {"available": False, "enabled": False},
        "vad_mode": "rms",
        "fallback_reason": "recorder_not_started",
    }


def _zhipu_health_components() -> dict:
    try:
        import services.llm_router as llm_router
        import services.cloud_asr as cloud_asr_module
        llm_status = llm_router.router.status().get("zhipu", {})
        asr = cloud_asr_module.cloud_asr
        key = os.environ.get("ZHIPU_API_KEY", "")
        configured = bool(key)
        key_len = len(key) if key else 0
        asr_provider = os.environ.get("ASR_PROVIDER", "zhipu")
        llm_error = str(llm_status.get("last_error", "") or "")
        asr_error = str(getattr(asr, "last_error", "") or "")

        def _status(error: str, enabled: bool = True) -> str:
            if not configured or not enabled:
                return "offline"
            return "degraded" if error else "ready"

        return {
            "zhipu_llm": {
                "status": _status(llm_error),
                "configured": configured,
                "key_len": key_len,
                "last_error": llm_error,
                "last_success_at": float(llm_status.get("last_success_at", 0.0) or 0.0),
                "actionable_reason": (
                    "设置 ZHIPU_API_KEY 后可启用智谱 LLM"
                    if not configured else
                    {"auth": "智谱 Key 无效或权限不足", "quota": "智谱额度不足或被限流", "timeout": "智谱请求超时", "network": "访问智谱网络异常", "server_error": "智谱服务端异常", "bad_response": "智谱响应格式异常"}.get(llm_error, "")
                ),
            },
            "zhipu_asr": {
                "status": _status(asr_error, enabled=(asr_provider != "local")),
                "configured": configured,
                "key_len": key_len,
                "provider": asr_provider,
                "last_error": asr_error,
                "last_success_at": float(getattr(asr, "last_success_at", 0.0) or 0.0),
                "actionable_reason": (
                    "ASR_PROVIDER=local，当前不使用智谱 ASR"
                    if asr_provider == "local" else
                    "设置 ZHIPU_API_KEY 后可启用智谱 ASR"
                    if not configured else
                    {"auth": "智谱 Key 无效或 ASR 权限不足", "quota": "智谱 ASR 额度不足或被限流", "timeout": "智谱 ASR 请求超时", "network": "访问智谱 ASR 网络异常", "bad_response": "智谱 ASR 响应异常", "local_missing": "本地 ASR fallback 缺依赖"}.get(asr_error, "")
                ),
            },
        }
    except Exception as exc:
        reason = str(exc)[:160]
        return {
            "zhipu_llm": {"status": "degraded", "configured": False, "last_error": reason, "last_success_at": 0.0, "actionable_reason": reason},
            "zhipu_asr": {"status": "degraded", "configured": False, "last_error": reason, "last_success_at": 0.0, "actionable_reason": reason},
        }


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


_yunet_detector = None
_yunet_input_size = (0, 0)


def _get_yunet(w: int, h: int):
    """Cached YuNet detector; created once, resized only when frame size changes."""
    global _yunet_detector, _yunet_input_size
    if _yunet_detector is None:
        _yunet_detector = cv2.FaceDetectorYN_create(
            "models/face_detection_yunet.onnx", "", (w, h), 0.75, 0.4, 5000)
        _yunet_input_size = (w, h)
    elif _yunet_input_size != (w, h):
        _yunet_detector.setInputSize((w, h))
        _yunet_input_size = (w, h)
    return _yunet_detector


def _refine_faces(img, persons: list) -> list:
    """
    Use YuNet ONNX face detector for accurate facial keypoints.
    Falls back to geometric estimation if YuNet unavailable.
    Accepts a decoded BGR frame (shared per-tick decode).
    """
    from vision.pose_estimator import PersonPose, Keypoint

    if img is None:
        return persons

    h, w = img.shape[:2]

    # 鈹€鈹€ YuNet face detection (楂橀槇-> 鍑忓皯鍋囬槼-> 鈹€鈹€
    faces = []
    try:
        yunet = _get_yunet(w, h)
        _, faces = yunet.detect(img)
        if faces is None: faces = []
    except Exception as e:
        logger.debug("YuNet detect error: %s", str(e)[:80])

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
    stable = _person_count_stabilizer.update(len(persons))
    return {"persons": persons, "count": len(persons), **stable}


def _build_vision_observation() -> dict:
    """Build normalized, current-frame candidates for the control runtime."""
    global _observation_id
    _observation_id += 1
    width, height = (video_client.resolution if video_client else [1920, 1080])
    width, height = max(1, int(width)), max(1, int(height))
    stage_ms = dict(_perception_stats.get("stage_ms", {})) if "_perception_stats" in globals() else {}
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
        "face_detection_ms": stage_ms.get("face"),
        "embedding_ms": None,
        "tracker_update_ms": stage_ms.get("face"),
        "vision_hz": round(float(video_client.fps), 2) if video_client else 0.0,
        "telemetry_hz": 4.0,
        "ui_push_hz": 4.0,
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
    result = await loop.run_in_executor(_bus_pool, lambda: _eventbus.emit(event))
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
    control = dict(_control_obs)
    control.update({
        "heartbeat_state": dict(_heartbeat_state),
        "last_heartbeat_ok_at": _heartbeat_state.get("last_ok_at", 0.0),
        "last_heartbeat_error": _heartbeat_state.get("last_error", ""),
        "eventbus_in_flight": bool(_heartbeat_eventbus_in_flight),
    })
    locked_track_id = _runtime_cache.get("locked_track_id")
    tracking_phase = _runtime_cache.get("tracking_phase", "inactive")
    active_feature = str(control.get("active_feature") or _runtime_cache.get("active_feature") or "inactive")
    doa_status = _doa_status()
    face_lock = {
        "locked": locked_track_id is not None and (_runtime_cache.get("lock_state") in {None, "locked", "centered", "occlusion_hold"}),
        "track_id": locked_track_id,
        "phase": tracking_phase,
        "state": _runtime_cache.get("lock_state") or ("locked" if locked_track_id is not None else "acquiring"),
    }
    sound_follow = {
        "active": active_feature in {"multi_sound_yaw", "meeting_sound_yaw"},
        "doa_deg": doa_status.get("doa_deg"),
        "has_speech": bool(doa_status.get("has_speech")),
        "source": doa_status.get("source"),
    }

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
            "doa": doa_status,
            "sound_follow": sound_follow,
            "respeaker": _respeaker_state(),
            "conversation": _conversation_state(),
            "audio_processing": _audio_processing_state(),
            "voice": _voice_state(),
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
            "control": control,
            "face_lock": face_lock,
            "trace": list(_decision_trace)[-12:],
            "health": {
                "video_fps": round(float(video_client.fps), 1) if video_client else 0.0,
                "ws_clients": len(ws_mgr._connections),
                "doa_age": round(float(getattr(_doa_reader, "age", 999.0)), 2) if _doa_reader else None,
                "gimbal_latency_ms": None,
                "gimbal_connected": bool(_gimbal_tlm.get("connected")),
            },
            "locked_track_id": locked_track_id,
            "tracking_state": _runtime_cache.get("tracking_state", "IDLE"),
            "tracking_phase": tracking_phase,
            "stop_state": _runtime_cache.get("stop_state", "stopped"),
            "device_lease": dict(_runtime_cache.get("device_lease") or {}),
            "face_landmark_mode": _face_landmark_mode,
            "tracking_mode": _tracking_mode,
            "single_track": {"active": _single_track_active},
            "multi_track": {"active": _multi_track_active},
            "chat": {
                "configured": bool(DEEPSEEK_API_KEY),
                "model": DEEPSEEK_MODEL,
            },
            "timestamp": time.time(),
        },
    }
    return _json_clean(snapshot)


# 鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲->#  FastAPI App
# 鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲->
@asynccontextmanager
async def lifespan(app: FastAPI):

    global video_client

    stored_ip = str(device_config_store.read().get("device_ip", ""))
    if app_config and not app_config.device_ip and stored_ip:
        app_config.device_ip = stored_ip

    # Start video client only when a device address is configured. FastAPI can
    # still run as UI/telemetry viewer without a reCamera address.
    if app_config.device_ip:
        video_client = SSCMAVideoClient(device_ip=app_config.device_ip)
        video_client._frame_event = asyncio.Event()
        video_client._event_loop = _current_running_loop()
        video_client.start()
    else:
        video_client = None
        logger.warning("No reCamera device address configured; /video_feed is disabled until /api/device/config is set")

    # Attention engine
    global _attention_engine
    from vision.attention_engine import AttentionConfig, AttentionEngine
    profile_id = "".join(ch for ch in os.environ.get("RECAMERA_PROFILE_ID", "default") if ch.isalnum() or ch in "-_") or "default"
    device_key = "".join(ch for ch in (app_config.device_ip or "unconfigured") if ch.isalnum())[-24:]
    baseline_path = Path(__file__).resolve().parent / "runtime" / f"attention_{profile_id}_{device_key}.json"
    _attention_engine = AttentionEngine(AttentionConfig(baseline_file=str(baseline_path)))

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

    # Preload the YOLO pose fallback off the event loop so the first
    # multi-person tick never pays the ONNX load + warmup inline.
    def _preload_pose():
        try:
            from vision.pose_estimator import get_pose_estimator
            get_pose_estimator()
            logger.info("YOLO pose estimator preloaded")
        except Exception as e:
            logger.warning("Pose estimator preload failed: %s", str(e)[:80])
    _slow_pool.submit(_preload_pose)

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
    global _doa_reader, _conversation_recording_requested, _wake_word_service
    _doa_reader = None
    _conversation_recording_requested = False
    _ensure_doa_reader()
    _ensure_asr_worker()

    audio_device = os.environ.get("RECAMERA_AUDIO_DEVICE", "").strip()
    if audio_device:
        try:
            audio_device = int(audio_device)
        except ValueError:
            pass
    else:
        audio_device = None
    from services.wake_word_service import WakeWordService
    _wake_word_service = WakeWordService(audio_device_index=audio_device)
    loop = asyncio.get_running_loop()

    def _on_wake(name: str, score: float) -> None:
        event = {"type": "wake_word_detected", "name": name, "score": float(score), "time": time.time()}
        def _schedule_wake_events():
            asyncio.create_task(ws_mgr.broadcast(event))
            asyncio.create_task(_emit_voice_reason(
                "wake_word",
                priority="high",
                interrupt=True,
                source="wake_word",
            ))
        loop.call_soon_threadsafe(_schedule_wake_events)

    _wake_word_service.on_wake(_on_wake)
    _wake_word_service.start()

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
    if _asr_worker_task is not None:
        _asr_worker_task.cancel()
    try: await push_task
    except asyncio.CancelledError: pass
    for task in (runtime_task, doa_task, _asr_worker_task):
        if task is None:
            continue
        try: await task
        except asyncio.CancelledError: pass

    _stop_conversation_recording(finalize=True)
    if _wake_word_service:
        _wake_word_service.stop()
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


from concurrent.futures import ThreadPoolExecutor

# Two single-worker pools: fast lane for decode + face tracking, slow lane for
# heavier secondary models. Prevents a 300ms MediaPipe call from head-of-line
# blocking the ~2Hz face tracker in the shared default executor.
_fast_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="vis-fast")
_slow_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="vis-slow")

# Downscale frames before face detection (0 disables). Track outputs are
# scaled back to full-resolution coordinates.
DETECT_MAX_WIDTH = int(os.environ.get("RECAMERA_DETECT_MAX_WIDTH", "960"))

# Rolling per-stage wall-time EMA (ms) + adaptive throttle state, exposed via /api/system/health
_perception_stats = {
    "stage_ms": {"decode": 0.0, "face": 0.0, "mediapipe": 0.0, "gesture": 0.0, "emotion": 0.0},
    "face_period": 2,
    "face_degraded": False,
}


def _record_stage_ms(name: str, t0: float):
    dt = (time.monotonic() - t0) * 1000.0
    prev = _perception_stats["stage_ms"].get(name, 0.0)
    _perception_stats["stage_ms"][name] = round(0.7 * prev + 0.3 * dt, 1) if prev else round(dt, 1)


def _decode_jpeg_bgr(jpeg: bytes):
    arr = np.frombuffer(jpeg, dtype=np.uint8)
    return cv2.imdecode(arr, cv2.IMREAD_COLOR)


def _decode_and_downscale(jpeg: bytes):
    """Decode once; also produce the (possibly downscaled) detection frame."""
    frame = _decode_jpeg_bgr(jpeg)
    det_frame, scale = frame, 1.0
    if frame is not None and DETECT_MAX_WIDTH > 0 and frame.shape[1] > DETECT_MAX_WIDTH:
        scale = DETECT_MAX_WIDTH / float(frame.shape[1])
        det_frame = cv2.resize(frame, (DETECT_MAX_WIDTH, max(1, int(frame.shape[0] * scale))))
    return frame, det_frame, scale


def _scale_tracks_inplace(tracks, inv: float):
    """Map face-tracker outputs from detection space back to full resolution."""
    if inv == 1.0 or not tracks:
        return tracks
    for t in tracks:
        t['bbox'] = tuple(v * inv for v in t['bbox'])
        if t.get('bbox_raw') is not None:
            t['bbox_raw'] = tuple(v * inv for v in t['bbox_raw'])
        if t.get('face_center') is not None:
            cx, cy = t['face_center']
            t['face_center'] = (cx * inv, cy * inv)
        for k in ('landmarks_5', 'landmarks_106'):
            if t.get(k) is not None:
                t[k] = t[k] * inv
    return tracks


class _EmotionSmoother:
    """Per-class probability EMA + confidence gate so the published emotion
    doesn't flicker with single-frame noise."""
    ALPHA = 0.4          # weight of the newest sample
    MIN_CONF = 0.35      # below this the previous label is held
    RESET_AFTER_S = 5.0  # no face for this long -> start fresh

    def __init__(self):
        self._probs: dict = {}
        self._last_update = 0.0
        self._last_label = ""

    def update(self, raw_probs: dict):
        now = time.time()
        if now - self._last_update > self.RESET_AFTER_S:
            self._probs = {}
            self._last_label = ""
        self._last_update = now
        if not self._probs:
            self._probs = dict(raw_probs)
        else:
            keys = set(self._probs) | set(raw_probs)
            self._probs = {
                k: (1 - self.ALPHA) * self._probs.get(k, 0.0) + self.ALPHA * raw_probs.get(k, 0.0)
                for k in keys
            }
        top = max(self._probs, key=self._probs.get)
        conf = float(self._probs[top])
        low_confidence = conf < self.MIN_CONF
        if low_confidence and self._last_label:
            top = self._last_label
            conf = float(self._probs.get(top, conf))
        else:
            self._last_label = top
        return top, conf, dict(self._probs), low_confidence


_emotion_smoother = _EmotionSmoother()


async def state_push_loop():
    """Run perception and push UI snapshots. Contains NO gimbal control."""
    global _attn_result, _emotion_result, _emotieff_result, _eye_metrics
    global _mp_face, _eye_tracker, _mp_face_result, _mp_landmarks5
    global _gaze_result, _gesture_result, _proactive_intervention
    global _llm_engine, _llm_diary_entry, _llm_quote_text, _last_llm_diary_time
    pose_est = None
    pose_frame_count = 0
    cached_jpeg = None       # identity of the last decoded jpeg_bytes
    cached_frame = None      # its decoded BGR frame, shared by every consumer
    cached_det_frame = None  # downscaled detection frame
    cached_det_scale = 1.0
    mp_pose = None           # freshest (yaw, pitch, roll) from MediaPipe matrix
    mp_pose_ts = 0.0

    while True:
        try:
            pose_frame_count += 1
            multi_mode = bool(_multi_track_active and not _single_track_active)
            run_companion_detail = not multi_mode
            # Adaptive throttle: slow face stage backs the cadence off one notch
            face_ms = _perception_stats["stage_ms"]["face"]
            if face_ms > 350.0:
                _perception_stats["face_degraded"] = True
            elif face_ms < 200.0:
                _perception_stats["face_degraded"] = False
            base_face_period = 3 if multi_mode else 2
            face_period = base_face_period + (1 if _perception_stats["face_degraded"] else 0)
            _perception_stats["face_period"] = face_period
            face_due = pose_frame_count % face_period == 0
            pose_due = (pose_frame_count % 15 == 0) if multi_mode else (pose_frame_count % 4 == 0)
            # -- Scene gating: daily (single) runs face/emotion/eye; work (multi) runs pose only.
            #    When neither mode is active, both pipelines run (default observation mode). --
            run_face = True
            run_pose = True
            if _single_track_active and not _multi_track_active:
                run_pose = False   # 日常场景：人脸/情绪/专注，跳过 YOLO pose
            elif _multi_track_active and not _single_track_active:
                run_face = True   # Multi-person fusion still needs face candidates.
            # -- Decode the current frame once per tick; all consumers share it --
            jpeg = video_client.jpeg_bytes if video_client else None
            frame_is_new = jpeg is not None and jpeg is not cached_jpeg
            if frame_is_new:
                loop = _current_running_loop()
                t0 = time.monotonic()
                cached_frame, cached_det_frame, cached_det_scale = await loop.run_in_executor(
                    _fast_pool, _decode_and_downscale, jpeg)
                _record_stage_ms("decode", t0)
                cached_jpeg = jpeg
            frame = cached_frame if jpeg is not None else None
            det_frame = cached_det_frame if jpeg is not None else None
            det_inv = 1.0 / cached_det_scale if cached_det_scale else 1.0
            # Unchanged frame -> nothing new to infer; skip all model stages this tick
            run_inference = frame_is_new

            # -- Face detection: FaceTrackerV2 (SCRFD + Kalman/ByteTrack), YOLO fallback --
            if video_client and (face_due or pose_due) and run_inference:
                if frame is not None:
                    loop = _current_running_loop()
                    tracked_faces = []
                    if _face_tracker and _face_tracker.available and run_face and face_due:
                        try:
                            from vision.pose_estimator import PersonPose, Keypoint
                            if frame is not None:
                                t0 = time.monotonic()
                                tracks = await loop.run_in_executor(_fast_pool, _face_tracker.update, det_frame)
                                _record_stage_ms("face", t0)
                                _scale_tracks_inplace(tracks, det_inv)
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
                    if not tracked_faces and run_pose and pose_due:
                        if pose_est is None:
                            from vision.pose_estimator import get_pose_estimator
                            # ONNX load + warmup takes seconds on CPU; never on the event loop
                            pose_est = await loop.run_in_executor(_slow_pool, get_pose_estimator)
                        try:
                            persons = await loop.run_in_executor(_slow_pool, pose_est.detect_bgr, frame)
                            persons = await loop.run_in_executor(_slow_pool, _refine_faces, frame, persons)
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
                        # Prefer the fresh MediaPipe matrix pose over 5-pt solvePnP
                        ext_pose = mp_pose if (mp_pose and time.time() - mp_pose_ts < 2.0) else None
                        _attn_result = _attention_engine.update(
                            landmarks, nose_xy,
                            img_w=int(res[0]), img_h=int(res[1]),
                            eye_metrics=_eye_metrics,
                            gaze=_gaze_result,
                            external_pose=ext_pose,
                        )
                        break
                else:
                    _attn_result = _attention_engine.update(None)
            else:
                _attn_result = {"has_face": False}

            # -- MediaPipe face + eye metrics (throttled) --
            if pose_frame_count % 6 == 0 and run_face and run_companion_detail and run_inference:
                if frame is not None:
                    if _mp_face is None:
                        from vision.mediapipe_face import MPFaceDetector
                        from vision.eye_metrics import EyeMetricTracker
                        _mp_face = MPFaceDetector()
                        _eye_tracker = EyeMetricTracker()
                    try:
                        loop = _current_running_loop()
                        t0 = time.monotonic()
                        mp_res = await loop.run_in_executor(_slow_pool, _mp_face.detect, frame)
                        _record_stage_ms("mediapipe", t0)
                        if mp_res.success:
                            _mp_landmarks5 = mp_res.landmarks5
                            _apply_mediapipe_landmarks5(_mp_landmarks5)
                            if mp_res.head_yaw is not None:
                                mp_pose = (float(mp_res.head_yaw), float(mp_res.head_pitch or 0.0), float(mp_res.head_roll or 0.0))
                                mp_pose_ts = time.time()
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
                                "ear_left": float(em.ear_left), "ear_right": float(em.ear_right),
                                "blink_rate": float(em.blink_rate), "perclos": round(float(em.perclos), 3),
                                "focus_score": int(em.focus_score), "blink_count": int(em.blink_count),
                                "eye_open": bool(em.eye_open),
                                "fatigue_level": str(em.fatigue_level),
                                "calibrated": bool(em.calibrated)}
                        else:
                            _gaze_result = {"available": False, "state": "unknown", "x_offset": 0.0, "y_offset": 0.0, "confidence": 0.0}
                    except Exception as e:
                        logger.warning(f"MediaPipe: {e}")
                        _gaze_result = {"available": False, "state": "unknown", "x_offset": 0.0, "y_offset": 0.0, "confidence": 0.0}

            # -- Gesture recognition (companionship intents only; no control events) --
            # Adaptive cadence: scan slowly until a hand appears, then track fast
            gesture_period = 2 if (_gesture_detector is not None and getattr(_gesture_detector, "hand_seen", False)) else 8
            if pose_frame_count % gesture_period == 0 and run_face and run_companion_detail and run_inference:
                if frame is not None and _gesture_detector is not None:
                    try:
                        loop = _current_running_loop()
                        t0 = time.monotonic()
                        _gesture_result = await loop.run_in_executor(_slow_pool, _gesture_detector.detect, det_frame)
                        _record_stage_ms("gesture", t0)
                    except Exception as e:
                        _gesture_result = {"available": False, "name": "", "confidence": 0.0, "handedness": "", "stable_frames": 0, "intent": "", "intent_ready": False, "reason": str(e)[:80]}

            # -- Emotion recognition (EmotiEffLib) --
            landmarks = None
            if _latest_pose_persons and run_face:
                for p in _latest_pose_persons:
                    face_kps = {kp.name: (kp.x, kp.y) for kp in p.keypoints
                                if kp.name in ('left_eye', 'right_eye', 'nose', 'left_mouth', 'right_mouth')}
                    if len(face_kps) >= 5:
                        landmarks = [face_kps['left_eye'], face_kps['right_eye'], face_kps['nose'],
                                     face_kps['left_mouth'], face_kps['right_mouth']]
                        break

            if frame is not None and run_face and landmarks and run_companion_detail and pose_frame_count % 6 == 0 and run_inference:
                    from vision.face_crop import extract_face_crop
                    from vision.emotieff_adapter import get_emotieff_adapter
                    crop_result = extract_face_crop(frame, landmarks, None)
                    img_for_emo = crop_result.crop if crop_result.crop is not None else None
                    if img_for_emo is not None:
                        loop = _current_running_loop()
                        adapter = get_emotieff_adapter()
                        t0 = time.monotonic()
                        raw_result = await loop.run_in_executor(_slow_pool, adapter.predict, img_for_emo)
                        _record_stage_ms("emotion", t0)
                        if raw_result and raw_result.get("emotion"):
                            raw_probs = {str(k): float(v) for k, v in raw_result.get("probabilities", {}).items()}
                            if not raw_probs:
                                raw_probs = {str(raw_result["emotion"]): float(raw_result.get("confidence", 0.0))}
                            top_emo, top_conf, smoothed_probs, low_conf = _emotion_smoother.update(raw_probs)
                            _emotieff_result = {
                                "emotion": top_emo,
                                "confidence": round(float(top_conf), 4),
                                "probabilities": {k: round(v, 4) for k, v in smoothed_probs.items()},
                                "valence": _emotion_valence(top_emo, smoothed_probs, raw_result.get("valence")),
                                "low_confidence": bool(low_conf),
                                "source": "emotiefflib_ema",
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

            # -- Daily aggregation for diary linkage (smoothed emotion only) --
            try:
                from services.day_aggregator import day_aggregator
                emo_r = _emotieff_result or {}
                day_aggregator.update(
                    emotion=str(emo_r.get("emotion") or "") if not emo_r.get("low_confidence") else "",
                    confidence=float(emo_r.get("confidence") or 0.0),
                    valence=emo_r.get("valence"),
                    attention=_attn_result.get("score") if _attn_result.get("has_face") else None,
                    fatigue_level=str((_eye_metrics or {}).get("fatigue_level") or ""),
                    intervention_active=bool((_proactive_intervention or {}).get("active")),
                    has_face=bool(_attn_result.get("has_face")),
                )
            except Exception as e:
                logger.debug("day aggregator error: %s", str(e)[:80])

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
            if _llm_engine and _llm_engine.loaded and run_companion_detail:
                loop = _current_running_loop()
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
        await asyncio.sleep(0.25)  # ~4 Hz state push; heavy models are independently throttled


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
    try:
        saved = device_config_store.write(str(device_ip))
    except ValueError as exc:
        return {"ok": False, "reason": str(exc), "device": _device_config_state()}
    ok, reason = _restart_video_client(saved["device_ip"])
    event = Event.make("system", "device_config_update", "fastapi", {
        "device_ip": saved["device_ip"], "version": saved["version"],
    })
    loop = asyncio.get_running_loop()
    control_result = await loop.run_in_executor(_bus_pool, lambda: _eventbus.emit(event))
    return {
        "ok": ok,
        "reason": reason,
        "reconnect_state": "pending" if control_result.get("accepted") else "control_offline",
        "control": control_result,
        "device": _device_config_state(),
    }


@app.get("/api/system/health")
async def api_system_health():
    conversation = _conversation_state()
    doa = _doa_status()
    gimbal = dict(_gimbal_tlm)
    zhipu_components = _zhipu_health_components()
    components = {
        "fastapi": {"status": "ready", "reason": ""},
        "eventbus": {"status": "ready" if _runtime_cache.get("connected") else "offline", "reason": "启动 main_phase3 --manual-control" if not _runtime_cache.get("connected") else ""},
        "sscma": {"status": "ready" if video_client and video_client.connected else "offline", "reason": "检查设备地址和 8090 端口" if not (video_client and video_client.connected) else ""},
        "node_red": {"status": "ready" if gimbal.get("connected") else "degraded", "reason": gimbal.get("last_error") or "检查设备 1880 bridge 与电机读回"},
        "gimbal": {"status": "ready" if gimbal.get("verified") or gimbal.get("connected") else "degraded", "reason": "尚未验证电机动作" if not gimbal.get("verified") else ""},
        "respeaker": {"status": "ready" if doa.get("available") or doa.get("connected") else "offline", "reason": str(doa.get("error", "检查 USB/DOA 输入"))},
        "recorder": {"status": "ready" if conversation.get("available", True) else "offline", "reason": conversation.get("error", "")},
        "llm": {"status": "ready" if os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("ZHIPU_API_KEY") else "degraded", "reason": "未配置云端模型，将使用本地回退"},
        **zhipu_components,
    }
    overall = "ready" if all(v["status"] == "ready" for v in components.values()) else "degraded"
    return {"ok": True, "status": overall, "components": components,
            "perception": dict(_perception_stats), "device": _device_config_state()}


@app.get("/api/day_summary")
async def api_day_summary(date: str = ""):
    """Observed-emotion day summary for diary linkage (local-date keys)."""
    from services.day_aggregator import day_aggregator
    return {"ok": True, **day_aggregator.summary(date)}


@app.get("/api/day_summary/range")
async def api_day_summary_range(start: str = "", end: str = ""):
    from services.day_aggregator import day_aggregator
    if not start or not end:
        return {"ok": False, "error": "start and end are required (YYYY-MM-DD)"}
    return {"ok": True, "days": day_aggregator.range_summaries(start, end)}


@app.get("/api/features")
async def api_features():
    return {"ok": True, "analytics": dict(_analysis_features), "control": dict(_runtime_cache)}


@app.post("/api/features/{feature}/start")
async def api_feature_start(feature: str):
    if feature not in _analysis_features:
        return {"ok": False, "accepted": False, "reason": "unknown_or_control_feature"}
    _analysis_features[feature] = True
    return {"ok": True, "accepted": True, "feature": feature, "state": "running", "resource": "analytics"}


@app.post("/api/features/{feature}/stop")
async def api_feature_stop(feature: str):
    if feature not in _analysis_features:
        return {"ok": False, "accepted": False, "reason": "unknown_or_control_feature"}
    _analysis_features[feature] = False
    return {"ok": True, "accepted": True, "feature": feature, "state": "stopped", "resource": "analytics"}


@app.get("/api/gimbal/state")
async def api_gimbal_state():
    # Read-only telemetry (hardware truth). No control is exposed here.
    return dict(_gimbal_tlm)


# NOTE: removed direct hardware control endpoints:
# /api/gimbal/{yaw,pitch,speed,sleep,standby,stop,calibrate},
# /api/face_track/*, and /api/sound_track/*. Compatibility tracking endpoints
# remain and map to control runtime feature_start/feature_stop events.


# 鈹€鈹€ Conversation Recording API 鈹€鈹€

@app.get("/api/conversation/state")
async def api_conversation_state():
    return _conversation_state()


@app.get("/api/conversation/debug")
async def api_conversation_debug():
    return _conversation_debug_state()


@app.post("/api/conversation/start")
async def api_conversation_start(payload: dict = None):
    global _conversation_recording_requested, _ui_session_id, _meeting_report, _meeting_recording_task
    payload = payload or {}
    from services.speaker_mapper import speaker_mapper
    speaker_mapper.reset()
    _meeting_report = {
        "status": "recording_starting", "summary": "", "minutes": "", "transcript": "",
        "turns": 0, "duration_min": 0.0, "error": "", "progress": 0,
    }
    _pause_wake_word()
    session_result = None
    if payload.get("control_session"):
        session_result = await _start_feature("meeting_recording")
        if not session_result.get("accepted"):
            _resume_wake_word()
            return {"success": False, "recording_success": False, **session_result}
    _conversation_recording_requested = bool(payload.get("save_audio", False))
    _ensure_asr_worker()
    if _conversation_recording_requested:
        if _meeting_recording_task is None or _meeting_recording_task.done():
            _meeting_recording_task = asyncio.create_task(
                _start_meeting_recording_background(),
                name="conversation-recording-start",
            )
    else:
        _meeting_report["status"] = "idle"
    return {
        "success": True,
        "recording_success": bool(_conversation_recorder and _conversation_recorder.active),
        "recording_state": "starting" if _conversation_recording_requested else "disabled",
        "state": _conversation_state(), **(session_result or {}),
    }


@app.post("/api/conversation/stop")
async def api_conversation_stop(payload: dict = None):
    global _conversation_recording_requested
    payload = payload or {}
    _conversation_recording_requested = False
    await _stop_conversation_recording_async(finalize=bool(payload.get("finalize", True)))
    _resume_wake_word()
    session_result = await _stop_feature(str(payload.get("session_id", ""))) if payload.get("session_id") else {}
    await _emit_voice_reason(
        "meeting_stop",
        priority="normal",
        interrupt=False,
        source="conversation_stop",
    )
    return {"success": True, "state": _conversation_state(), **session_result}


@app.post("/api/conversation/asr/retry")
async def api_conversation_asr_retry(payload: dict = Body(default={})):
    """Requeue failed or selected meeting turns for ASR."""
    _ensure_asr_worker()
    recorder = _conversation_recorder
    if recorder is None:
        return {"ok": False, "accepted": False, "error": "录音会话不存在"}
    state = recorder.state()
    target_id = str(payload.get("turn_id", "") or "")
    queued = 0
    for turn in state.get("timeline", []):
        status = str(turn.get("status") or "")
        if target_id and str(turn.get("id")) != target_id:
            continue
        if target_id or status in {"asr_failed", "asr_empty", "audio_saved", "asr_pending"}:
            if _enqueue_asr_turn_now(turn, force=True):
                queued += 1
    return {"ok": True, "accepted": True, "queued": queued, "state": _conversation_state()}


@app.post("/api/conversation/save")
async def api_conversation_save(payload: dict = None):
    # Segments and timeline are written incrementally; this endpoint is a stable
    # frontend action that returns the current persisted session metadata.
    return {"success": True, "state": _conversation_state()}


@app.get("/api/meeting/speakers")
async def api_meeting_speakers():
    from services.speaker_mapper import speaker_mapper
    speakers = speaker_mapper.get_registered_speakers()
    return {"ok": True, "speakers": speakers, "total": len(speakers)}


@app.get("/api/wake_word/state")
async def api_wake_word_state():
    return _wake_word_state()


@app.get("/api/voice/state")
async def api_voice_state():
    return _voice_state()


@app.post("/api/voice/say")
async def api_voice_say(payload: dict = Body(default={})):
    payload = payload or {}
    text = str(payload.get("text") or "")
    reason = str(payload.get("reason") or "manual")
    if not text and voice_policy is not None:
        text = voice_policy.short_text_for(reason, "小屿语音测试。")
    return await _emit_voice(
        text,
        reason=reason,
        priority=str(payload.get("priority") or "normal"),
        interrupt=bool(payload.get("interrupt", False)),
        source=str(payload.get("source") or "api"),
        force=bool(payload.get("force", True)),
    )


@app.post("/api/voice/stop")
async def api_voice_stop(payload: dict = Body(default={})):
    payload = payload or {}
    return await _emit_voice_stop(str(payload.get("reason") or "api"))

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
    session_id = str(payload.get("session_id", ""))
    if not session_id:
        return {"ok": False, "accepted": False, "active": _single_track_active, "reason": "session_id_required"}
    _single_track_active = False
    result = await _stop_feature(session_id)
    return {**result, "active": False}


@app.post("/api/multi_track/start")
async def api_multi_track_start(payload: dict = Body(default={})):
    global _multi_track_active, _single_track_active, _tracking_mode
    global _conversation_recording_requested, _meeting_report, _meeting_recording_task
    from services.speaker_mapper import speaker_mapper

    save_audio = bool(payload.get("save_audio", False))
    if _runtime_cache.get("active_feature") == "multi_sound_yaw" and _ui_session_id:
        return {
            "ok": True, "accepted": True, "active": True, "reused": True,
            "session_id": _ui_session_id, "feature": "multi_sound_yaw",
            "recording_success": bool(_conversation_recorder and _conversation_recorder.active),
            "state": _conversation_state(),
        }
    speaker_mapper.reset()
    _meeting_report = {
        "status": "recording_starting" if save_audio else "idle",
        "summary": "", "minutes": "", "transcript": "",
        "turns": 0, "duration_min": 0.0, "error": "",
        "progress": 0,
    }
    _pause_wake_word()
    result = await _start_feature("multi_sound_yaw")
    if not result.get("accepted"):
        _resume_wake_word()
        return {**result, "active": False}
    _single_track_active = False
    _multi_track_active = True
    _tracking_mode = "multi"
    _conversation_recording_requested = save_audio
    _ensure_asr_worker()
    if save_audio:
        if _meeting_recording_task is None or _meeting_recording_task.done():
            _meeting_recording_task = asyncio.create_task(
                _start_meeting_recording_background(),
                name="meeting-recording-start",
            )
    return {
        **result,
        "success": True,
        "recording_success": bool(_conversation_recorder and _conversation_recorder.active),
        "recording_state": "starting" if save_audio else "disabled",
        "active": True,
        "state": _conversation_state(),
    }


@app.post("/api/multi_track/stop")
async def api_multi_track_stop(payload: dict = Body(default={})):
    global _multi_track_active, _conversation_recording_requested, _meeting_report
    session_id = str(payload.get("session_id", ""))
    if not session_id:
        return {"ok": False, "accepted": False, "active": _multi_track_active, "reason": "session_id_required"}
    _multi_track_active = False
    if payload.get("finalize", True):
        _conversation_recording_requested = False
        stop_ok = await _stop_conversation_recording_async(finalize=True)
        if _meeting_report.get("status") == "recording":
            _meeting_report["status"] = "recorded"
        elif _meeting_report.get("status") in {"recording_starting", "recording_degraded"}:
            _meeting_report["status"] = "recorded" if stop_ok else "error"
        if not stop_ok:
            _meeting_report["error"] = "录音停止超时，已尝试保留已有片段"
    _resume_wake_word()
    result = await _stop_feature(session_id)
    return {**result, "active": False, "state": _conversation_state()}


async def _emit_ui_event(name: str, payload: dict) -> dict:
    global _control_obs
    event = Event.make("ui", name, "fastapi", payload=payload)
    loop = _current_running_loop()
    try:
        result = await asyncio.wait_for(
            loop.run_in_executor(_bus_pool, lambda: _eventbus.emit(event)),
            timeout=EVENTBUS_TIMEOUT_S,
        )
    except asyncio.TimeoutError:
        result = {
            "ok": False,
            "accepted": False,
            "authority": "unreachable",
            "reason": "eventbus_timeout",
            "error": f"EventBus no response within {EVENTBUS_TIMEOUT_S:.1f}s",
        }
    except Exception as exc:
        result = {
            "ok": False,
            "accepted": False,
            "authority": "unreachable",
            "reason": "eventbus_error",
            "error": str(exc)[:160],
        }
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


def _heartbeat_degraded(reason: str, event: Event | None = None, error: str = "") -> dict:
    now = time.time()
    _heartbeat_state.update({
        "state": "degraded",
        "last_error": reason if not error else f"{reason}: {error}"[:160],
        "last_error_at": now,
        "eventbus_in_flight": bool(_heartbeat_eventbus_in_flight),
    })
    _control_obs.update({
        "heartbeat_state": dict(_heartbeat_state),
        "last_heartbeat_ok_at": _heartbeat_state.get("last_ok_at", 0.0),
        "last_heartbeat_error": _heartbeat_state.get("last_error", ""),
        "eventbus_in_flight": bool(_heartbeat_eventbus_in_flight),
    })
    return {
        "ok": False,
        "accepted": False,
        "degraded": True,
        "reason": reason,
        "error": error,
        "authority": "unreachable",
        "lease_may_be_stale": True,
        "last_ok_at": _heartbeat_state.get("last_ok_at", 0.0),
        "heartbeat_state": dict(_heartbeat_state),
        "event": event.to_dict() if event else None,
    }


async def _emit_heartbeat_event(session_id: str) -> dict:
    """Best-effort heartbeat: short, droppable, and never allowed to queue behind itself."""
    global _heartbeat_future, _heartbeat_eventbus_in_flight
    event = Event.make("ui", "feature_heartbeat", "fastapi", {
        "session_id": session_id,
        "lease_ms": CONTROL_LEASE_MS,
    })
    if not session_id:
        return _heartbeat_degraded("session_id_required", event)
    if _heartbeat_eventbus_in_flight and _heartbeat_future is not None and not _heartbeat_future.done():
        return _heartbeat_degraded("eventbus_busy", event)

    loop = _current_running_loop()
    _heartbeat_eventbus_in_flight = True
    _heartbeat_state.update({"state": "sending", "eventbus_in_flight": True})
    _heartbeat_future = loop.run_in_executor(_heartbeat_executor, lambda: _eventbus.emit(event))

    def _finish(fut):
        global _heartbeat_eventbus_in_flight
        _heartbeat_eventbus_in_flight = False
        _heartbeat_state["eventbus_in_flight"] = False
        try:
            result = fut.result()
        except Exception as exc:
            _heartbeat_state.update({
                "state": "degraded",
                "last_error": str(exc)[:160],
                "last_error_at": time.time(),
            })
            return
        if result.get("accepted"):
            _heartbeat_state.update({
                "state": "ready",
                "last_ok_at": time.time(),
                "last_error": "",
            })
        else:
            _heartbeat_state.update({
                "state": "degraded",
                "last_error": str(result.get("reason") or result.get("error") or "heartbeat_rejected")[:160],
                "last_error_at": time.time(),
            })
        _apply_runtime_result(result)

    _heartbeat_future.add_done_callback(lambda fut: loop.call_soon_threadsafe(_finish, fut))
    try:
        result = await asyncio.wait_for(asyncio.shield(_heartbeat_future), timeout=HEARTBEAT_EVENTBUS_TIMEOUT_S)
    except asyncio.TimeoutError:
        return _heartbeat_degraded("eventbus_timeout", event, f">{HEARTBEAT_EVENTBUS_TIMEOUT_S:.2f}s")
    except Exception as exc:
        return _heartbeat_degraded("eventbus_error", event, str(exc)[:160])

    eventbus_state = {"host": _eventbus.host, "port": _eventbus.port, "last_result": result}
    _control_obs.update({
        "authority": result.get("authority", "unreachable"),
        "last_event": _ev_brief(event),
        "command": result.get("command"),
        "eventbus": eventbus_state,
        "heartbeat_state": dict(_heartbeat_state),
    })
    return {
        **result,
        "degraded": not bool(result.get("accepted")),
        "lease_may_be_stale": not bool(result.get("accepted")),
        "last_ok_at": _heartbeat_state.get("last_ok_at", 0.0),
        "heartbeat_state": dict(_heartbeat_state),
        "event": event.to_dict(),
        "eventbus": eventbus_state,
    }


async def _start_feature(feature: str) -> dict:
    global _ui_session_id, _single_track_active, _multi_track_active
    if (_runtime_cache.get("connected") and _runtime_cache.get("active_feature") not in {None, "", "inactive"}
            and int(_runtime_cache.get("lease_remaining_ms", 0) or 0) > 0):
        return {
            "ok": False, "accepted": False, "reason": "control_busy",
            "owner_feature": _runtime_cache.get("active_feature"),
            "lease_remaining_ms": _runtime_cache.get("lease_remaining_ms", 0),
        }
    session_id = uuid.uuid4().hex
    result = await _emit_ui_event(
        "feature_start",
        {"feature": feature, "session_id": session_id, "lease_ms": CONTROL_LEASE_MS},
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
    return await _emit_heartbeat_event(str(payload.get("session_id", "")))


@app.get("/api/control/runtime")
async def api_control_runtime():
    runtime = _runtime_with_telemetry_defaults(dict(_runtime_cache))
    runtime.update({
        "heartbeat_state": dict(_heartbeat_state),
        "last_heartbeat_ok_at": _heartbeat_state.get("last_ok_at", 0.0),
        "last_heartbeat_error": _heartbeat_state.get("last_error", ""),
        "eventbus_in_flight": bool(_heartbeat_eventbus_in_flight),
    })
    return {"ok": bool(_runtime_cache.get("connected")), "runtime": runtime}


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
    framing_mode = str(payload.get("framing_mode", "upper_body"))
    return await _emit_ui_event("control_config", {
        "session_id": str(payload.get("session_id", "")),
        "speed": payload.get("speed", 180),
        "doa_offset_deg": payload.get("doa_offset_deg", 0),
        "doa_direction": payload.get("doa_direction", 1),
        "framing_mode": framing_mode,
        "target_x": payload.get("target_x", 0.5),
        "target_y": payload.get("target_y", 0.5 if framing_mode == "face_center" else 0.32),
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
        from services.emotion_prompt import build_reflect_messages

        current_state = build_state_snapshot().get("data", {})
        # Enrich with the server-side day summary when the client didn't send one
        if not payload.get("day_summary"):
            try:
                from services.day_aggregator import day_aggregator
                payload = {**payload, "day_summary": day_aggregator.summary()}
            except Exception:
                pass
        result = await _cloud_llm_complete(
            build_reflect_messages(user_text, current_state, str(payload.get("user_name", "")), payload),
            max_tokens=320,
        )
        ds_raw = str(result.get("text") or "")

        diary_entry = reply_text = ""
        source = "template"
        parsed = _extract_json_object(ds_raw)
        try:
            diary_entry = parsed.get("diary", "")
            reply_text  = parsed.get("reply", "")
            if diary_entry:
                source = result.get("provider") if result.get("provider") != "none" else "template"
        except Exception:
            pass

        emo_en = _EMO_ZH_EN.get(emotion, emotion)
        if not diary_entry:
            diary_entry = _llm_engine.diary(emo_en, attn, prev)
        if not reply_text:
            reply_text = _llm_engine.quote(emo_en, "mid")

        return {"diary": diary_entry, "reply": reply_text, "text": diary_entry, "source": source,
                "time": round(_llm_engine._last_time, 2)}

    elif mode == "report":
        total_min = payload.get("total_min", 0)
        focused_pct = payload.get("focused_pct", 0)
        messages = [
            {"role": "system", "content": "你是温柔的陪伴助手小屿，用中文写一段简短的一日陪伴总结（80字以内），语气温暖不评判。"},
            {"role": "user", "content": f"今天陪伴时长约 {total_min} 分钟，专注时间占比 {focused_pct}%，主要情绪是 {emotion}，专注均分 {attn}。请写一段总结。"},
        ]
        result = await _cloud_llm_complete(messages, max_tokens=200)
        text = str(result.get("text") or "")
        source = result.get("provider", "none")
        if not text:
            text = _llm_engine.report(total_min, focused_pct, emotion, attn)
            source = "template"
        return {"text": text, "source": source, "time": round(_llm_engine._last_time, 2)}
    else:
        text = _llm_engine.quote(emotion, "专注" if attn >= 70 else "微弱" if attn >= 40 else "飘远")
        return {"text": text, "time": round(_llm_engine._last_time, 2)}


# LLM cloud client wrapper (single source of truth: services/llm_router.py)
from services.llm_router import (
    DEEPSEEK_API_KEY, DEEPSEEK_API_URL, DEEPSEEK_MODEL, ZHIPU_API_KEY,
)
DEEPSEEK_MAX_TOKENS = int(os.getenv("DEEPSEEK_MAX_TOKENS", "600"))

async def _deepseek_chat(messages: list, max_tokens: int | None = None, temperature: float = 0.8) -> str:
    """Route to cloud LLM providers; endpoint handlers own local fallback."""
    from services.llm_router import router as _llm_router
    return await _llm_router.complete(messages, max_tokens or DEEPSEEK_MAX_TOKENS, temperature)


async def _cloud_llm_complete(messages: list, max_tokens: int | None = None, temperature: float = 0.8) -> dict:
    """Route to cloud LLM providers and include provider metadata."""
    from services.llm_router import router as _llm_router
    return await _llm_router.complete_with_provider(messages, max_tokens or DEEPSEEK_MAX_TOKENS, temperature)


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
    # frontend EMOTION_MAP.zh aliases
    "快乐": "Happiness", "低落": "Sadness", "不安": "Fear", "不适": "Disgust",
}

_EMO_CN = {
    "Happy": "开心",
    "Happiness": "开心",
    "Sad": "难过",
    "Sadness": "难过",
    "Angry": "生气",
    "Anger": "生气",
    "Fear": "紧张",
    "Surprise": "惊讶",
    "Disgust": "不适",
    "Contempt": "有些疏离",
    "Neutral": "平静",
}


def _extract_json_object(text: str) -> dict:
    import json as _json
    if not text:
        return {}
    try:
        return _json.loads(text)
    except Exception:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            try:
                return _json.loads(text[start:end + 1])
            except Exception:
                return {}
    return {}


def _has_observed_face(state_data: dict) -> bool:
    attention = state_data.get("attention") or {}
    if attention.get("has_face") is False:
        return False
    emotieff = state_data.get("emotieff") or {}
    return bool(attention.get("has_face") or emotieff.get("emotion"))


def _local_emotion_inference(state_data: dict) -> dict:
    emotieff = state_data.get("emotieff") or {}
    emotion = str(emotieff.get("emotion") or "Neutral")
    confidence = float(emotieff.get("confidence") or 0.0)
    valence = emotieff.get("valence")
    label = _EMO_CN.get(emotion, emotion or "平静")
    if valence is not None:
        if float(valence or 0.0) > 0.25 and emotion == "Neutral":
            label = "平静中带一点积极"
        elif float(valence or 0.0) < -0.25 and emotion == "Neutral":
            label = "平静中带一点低落"
    intensity = max(1, min(10, int(round(3 + confidence * 6))))
    explanation = f"基于当前 EmotiEff 分类和实时状态，主要线索偏向“{label}”。"
    return {"ok": True, "label": label, "intensity": intensity, "explanation": explanation, "provider": "local"}


@app.post("/api/emotion/infer")
async def api_emotion_infer():
    """Low-frequency open-vocabulary emotion inference using current state."""
    state_data = build_state_snapshot().get("data", {})
    if not _has_observed_face(state_data):
        return {
            "ok": True,
            "label": "暂未观察到",
            "intensity": 0,
            "explanation": "当前没有检测到稳定的人脸，暂不推断具体情绪。",
            "provider": "local",
        }

    from services.emotion_prompt import build_emotion_inference_messages

    result = await _cloud_llm_complete(build_emotion_inference_messages(state_data), max_tokens=180)
    raw = str(result.get("text") or "")
    parsed = _extract_json_object(raw)
    label = str(parsed.get("label") or "").strip()
    explanation = str(parsed.get("explanation") or "").strip()
    try:
        intensity = int(float(parsed.get("intensity")))
    except Exception:
        intensity = 0
    intensity = max(1, min(10, intensity))

    if label and explanation:
        return {
            "ok": True,
            "label": label[:40],
            "intensity": intensity,
            "explanation": explanation[:120],
            "provider": result.get("provider") if result.get("provider") != "none" else "local",
        }
    return _local_emotion_inference(state_data)


def _build_chat_messages(payload: dict) -> tuple[list, str, str, str]:
    """Shared prompt assembly for /api/chat and /api/chat/stream.
    Returns (messages, msg, emo_key, user_name). Server-side chat memory
    provides history; client context strings remain a supplementary hint."""
    from services.chat_memory import chat_memory
    from services.emotion_prompt import build_chat_system_prompt

    msg        = str(payload.get("message", "")).strip()
    emotion_zh = str(payload.get("emotion", ""))
    context_s  = str(payload.get("context", ""))
    diary_text = str(payload.get("diary_text", ""))
    # Sanitize the name that gets interpolated into the system prompt
    user_name  = str(payload.get("user_name", "")).replace("\n", " ").replace("{", "").replace("}", "")[:24]

    emo_key = _EMO_ZH_EN.get(emotion_zh, (_emotieff_result or {}).get("emotion", "Neutral"))
    sys_prompt = build_chat_system_prompt(build_state_snapshot().get("data", {}), user_name)
    user_ctx = f"用户当前选择/输入的情绪标签：{emotion_zh or emo_key}。"
    if diary_text:
        user_ctx += f"\n【今日日记】{diary_text[:200]}"
    if context_s:
        user_ctx += f"\n【背景】{context_s[:300]}"
    user_ctx += f"\n\n{msg or '请结合我今天的状态，给我一句有温度的话。'}"

    messages = [{"role": "system", "content": sys_prompt}]
    messages.extend(chat_memory.recent_messages())
    messages.append({"role": "user", "content": user_ctx})
    return messages, msg, emo_key, user_name


@app.post("/api/chat")
async def api_chat(payload: dict = Body(default={})):
    """Chat endpoint: DeepSeek with LLMReflect fallback. Accepts real emotion/attention/diary context."""
    global _llm_engine
    if _llm_engine is None:
        from vision.llm_reflect import get_llm
        _llm_engine = get_llm()

    from services.chat_memory import chat_memory
    messages, msg, emo_key, user_name = _build_chat_messages(payload)

    result = await _cloud_llm_complete(messages, max_tokens=150)
    reply = str(result.get("text") or "")

    if not reply or _reply_looks_incomplete(reply):
        reply  = _llm_engine.respond_to_user(msg, emo_key, user_name=user_name,
                                             context=str(payload.get("context", "")))
        source = "template"
    else:
        source = result.get("provider") if result.get("provider") != "none" else "template"

    if msg:
        chat_memory.append("user", msg)
    if reply:
        chat_memory.append("assistant", reply)
    return {"reply": reply, "source": source, "emotion": emo_key}


@app.post("/api/chat/stream")
async def api_chat_stream(payload: dict = Body(default={})):
    """SSE streaming chat. Events: data:{delta} lines; final event carries meta.
    Template fallback is emitted as a single chunk so the UI path is uniform."""
    global _llm_engine
    if _llm_engine is None:
        from vision.llm_reflect import get_llm
        _llm_engine = get_llm()

    from services.chat_memory import chat_memory
    from services.llm_router import router as _llm_router
    messages, msg, emo_key, user_name = _build_chat_messages(payload)

    async def _gen():
        import json as _j
        full = []
        provider = "template"
        try:
            async for name, delta in _llm_router.stream(messages, max_tokens=150):
                provider = name
                full.append(delta)
                yield f"data: {_j.dumps({'delta': delta}, ensure_ascii=False)}\n\n"
        except Exception as exc:
            yield f"event: error\ndata: {_j.dumps({'error': str(exc)[:80]}, ensure_ascii=False)}\n\n"
        reply = "".join(full)
        if not reply or _reply_looks_incomplete(reply):
            reply = _llm_engine.respond_to_user(msg, emo_key, user_name=user_name,
                                                context=str(payload.get("context", "")))
            provider = "template"
            yield f"data: {_j.dumps({'delta': reply}, ensure_ascii=False)}\n\n"
        if msg:
            chat_memory.append("user", msg)
        if reply:
            chat_memory.append("assistant", reply)
        yield f"event: done\ndata: {_j.dumps({'source': provider, 'emotion': emo_key}, ensure_ascii=False)}\n\n"

    return StreamingResponse(_gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/chat/history")
async def api_chat_history(date: str = ""):
    from services.chat_memory import chat_memory
    return {"ok": True, "date": date or time.strftime("%Y-%m-%d"),
            "messages": chat_memory.history(date)}


@app.post("/api/report/weekly")
async def api_report_weekly(payload: dict = Body(default={})):
    """Generate the weekly letter with real diary + observed-day data."""
    from services.emotion_prompt import build_weekly_report_prompt
    from services.day_aggregator import day_aggregator

    week_start = str(payload.get("week_start", ""))
    week_end = str(payload.get("week_end", ""))
    entries = payload.get("entries") or []
    user_name = str(payload.get("user_name", "")).replace("\n", " ")[:24]
    day_summaries = payload.get("day_summaries")
    if day_summaries is None and week_start and week_end:
        day_summaries = day_aggregator.range_summaries(week_start, week_end)

    messages = build_weekly_report_prompt(entries, day_summaries or [], user_name,
                                          week_start=week_start, week_end=week_end)
    result = await _cloud_llm_complete(messages, max_tokens=600, temperature=0.7)
    text = str(result.get("text") or "")
    if text:
        return {"ok": True, "content": text,
                "source": result.get("provider", "none")}
    # Template fallback keeps the button functional offline
    total = len(entries)
    return {"ok": True, "source": "template",
            "content": f"这一周有 {total} 天留下了记录。继续保持这份对自己的觉察，下周也慢慢来。"}


@app.get("/api/chat/status")
async def api_chat_status():
    from services.llm_router import router as _llm_router
    providers = _llm_router.status()
    configured = any(p["configured"] for p in providers.values())
    # Human-readable attribution for the dashboard status dot
    if not configured:
        reason = "未配置云端模型"
    else:
        errs = {n: p["last_error"] for n, p in providers.items()
                if p["configured"] and p["last_error"]}
        reason = "" if not errs else "；".join(
            f"{n}: " + {"auth": "认证失败", "quota": "配额受限", "timeout": "超时",
                        "network": "网络异常", "server_error": "服务端错误",
                        "bad_response": "响应异常"}.get(e, e)
            for n, e in errs.items())
    return {
        "configured": configured,
        "model": DEEPSEEK_MODEL,
        "api_url": DEEPSEEK_API_URL,
        "providers": providers,
        "reason": reason,
    }


_meeting_summarize_lock = asyncio.Lock()
MEETING_SUMMARY_BUDGET_S = float(os.environ.get("RECAMERA_MEETING_SUMMARY_BUDGET", "240"))
_MEETING_ASR_CONCURRENCY = 3


@app.post("/api/meeting/summarize")
async def api_meeting_summarize(payload: dict = Body(default={})):
    """Transcribe meeting segments and summarize with speaker labels.
    Guarded against concurrent invocations and bounded by an overall deadline."""
    global _meeting_report
    if _meeting_summarize_lock.locked():
        return JSONResponse(status_code=409, content={
            "ok": False, "error_code": "summarize_in_progress",
            "error": "已有一次整理正在进行，请稍候"})
    async with _meeting_summarize_lock:
        try:
            return await asyncio.wait_for(
                _meeting_summarize_impl(payload), timeout=MEETING_SUMMARY_BUDGET_S)
        except asyncio.TimeoutError:
            _meeting_report = {**_meeting_report, "status": "error",
                               "error": "整理超时，请重试或分段整理", "progress": 100}
            return {"ok": False, "error_code": "summarize_timeout", "error": _meeting_report["error"]}


async def _meeting_summarize_impl(payload: dict) -> dict:
    global _meeting_report
    from pathlib import Path as _Path
    from services.cloud_asr import cloud_asr as _cloud_asr
    from services.speaker_mapper import speaker_mapper

    recorder = _conversation_recorder
    _meeting_report = {
        **_meeting_report, "status": "summarizing", "error": "", "progress": 5,
    }
    if recorder is None:
        await _emit_voice_reason(
            "meeting_summary_error",
            priority="normal",
            interrupt=False,
            source="meeting_summarize",
        )
        _meeting_report.update({"status": "error", "error": "录音未启动，请先开启多人会议"})
        return {"ok": False, "error_code": "recording_not_started", "error": _meeting_report["error"]}

    session_state = recorder.state()
    turns = session_state.get("timeline", [])
    if not turns:
        await _emit_voice_reason(
            "meeting_summary_error",
            priority="normal",
            interrupt=False,
            source="meeting_summarize",
        )
        _meeting_report.update({"status": "error", "error": "本次无录音片段，请先录到语音片段"})
        return {"ok": False, "error_code": "no_segments", "error": _meeting_report["error"]}

    _ensure_asr_worker()
    for turn in turns:
        if not str(turn.get("text") or "").strip() and str(turn.get("wav_path") or ""):
            _enqueue_asr_turn_now(turn)
    await _wait_for_asr_idle(float(payload.get("asr_wait_s", ASR_IDLE_WAIT_S)))
    session_state = recorder.state()
    turns = session_state.get("timeline", turns)

    total_turns = max(1, len(turns))
    asr_sem = asyncio.Semaphore(_MEETING_ASR_CONCURRENCY)
    done_count = 0

    async def _transcribe_turn(turn):
        nonlocal done_count
        wav = turn.get("wav_path", "")
        text = None
        if wav and _Path(wav).exists():
            text = str(turn.get("text") or "").strip()
            if not text:
                async with asr_sem:
                    text = await _cloud_asr.transcribe(wav)
        done_count += 1
        _meeting_report["progress"] = min(65, 10 + int(55 * done_count / total_turns))
        return text

    turn_texts = await asyncio.gather(*(_transcribe_turn(t) for t in turns))

    transcripts = []
    for turn, text in zip(turns, turn_texts):
        if text is None:  # no wav file for this turn
            continue
        if text:
            speaker = str(turn.get("speaker_label") or "未知说话人")
            start = float(turn.get("start", 0.0) or 0.0)
            end = float(turn.get("end", start) or start)
            transcripts.append(f"[{start:.1f}-{end:.1f}s][{speaker}] {text}")
        recorder.set_transcript(str(turn.get("id") or ""), text)

    if not transcripts:
        await _emit_voice_reason(
            "meeting_summary_error",
            priority="normal",
            interrupt=False,
            source="meeting_summarize",
        )
        _meeting_report.update({
            "status": "error",
            "error": "转写结果为空（ASR 未配置、依赖缺失或语音过短）",
        })
        return {"ok": False, "error_code": "asr_empty", "error": _meeting_report["error"]}

    full_transcript = "\n".join(transcripts)
    duration_min = round(session_state.get("stats", {}).get("duration", 0) / 60, 1)
    speakers = speaker_mapper.get_registered_speakers()
    speaker_list = "、".join(s["label"] for s in speakers) if speakers else "暂未识别"
    sys_p = (
        "你是心屿，请将以下多人对话整理为会议记录。"
        "转写中方括号标记不同发言者；未知说话人也要如实保留。"
        "要求客观、可核查，不补写转写中不存在的事实。"
        "所有结论和行动项必须带可回查的发言时间；不推断情绪、身份或未出现的事实。"
        "输出严格JSON：{\"summary\":\"不超过40字\",\"topics\":[],\"decisions\":[],"
        "\"actions\":[{\"task\":\"\",\"owner\":\"待确认\",\"due\":\"待确认\",\"evidence\":\"时间戳\"}],"
        "\"disputes\":[],\"open_questions\":[],\"minutes\":\"结构化会议纪要\"}"
    )
    source_text = full_transcript
    _meeting_report["progress"] = 70
    if len(source_text) > 6000:
        chunk_notes = []
        for index in range(0, len(source_text), 5000):
            chunk = source_text[index:index + 5000]
            chunk_messages = [
                {"role": "system", "content": "只提取本段可核查事实、结论、行动项和对应时间戳，不补充内容。"},
                {"role": "user", "content": chunk},
            ]
            try:
                note = await _deepseek_chat(chunk_messages, max_tokens=500, temperature=0.15)
            except TypeError:  # compatibility with injected/test LLM callables
                note = await _deepseek_chat(chunk_messages, max_tokens=500)
            chunk_notes.append(note or chunk)
        source_text = "\n\n".join(chunk_notes)
    usr_p = (
        f"对话时长：{duration_min}分钟。\n"
        f"已注册说话人：{speaker_list}\n"
        f"逐句记录或分块事实（说话人标注）：\n{source_text}"
    )

    final_messages = [
        {"role": "system", "content": sys_p},
        {"role": "user",   "content": usr_p},
    ]
    try:
        raw = await _deepseek_chat(final_messages, max_tokens=1000, temperature=0.2)
    except TypeError:
        raw = await _deepseek_chat(final_messages, max_tokens=1000)

    minutes_text = summary_text = ""
    try:
        import json as _j
        parsed = _j.loads(raw)
        minutes_text = parsed.get("minutes") or parsed.get("diary", "")
        summary_text = parsed.get("summary", "")
    except Exception:
        minutes_text = raw[:800] if raw else "本次会议记录整理完成。"
        summary_text = minutes_text[:40]

    await _emit_voice_reason(
        "meeting_summary_ok",
        priority="normal",
        interrupt=False,
        source="meeting_summarize",
    )
    _meeting_report = {
        "status": "ready",
        "minutes": minutes_text,
        "diary": minutes_text,
        "summary": summary_text,
        "transcript": full_transcript,
        "turns": len(transcripts),
        "duration_min": duration_min,
        "error": "",
        "progress": 100,
        "structured": parsed if 'parsed' in locals() and isinstance(parsed, dict) else {},
    }
    report_path = recorder.save_report(_meeting_report)
    if report_path:
        _meeting_report["report_path"] = report_path
    return {
        "ok": True,
        "diary": minutes_text,
        "minutes": minutes_text,
        "summary": summary_text,
        "transcript": full_transcript,
        "turns": len(transcripts),
        "duration_min": duration_min,
        "report_path": report_path,
    }


@app.post("/api/meeting/complete")
async def api_meeting_complete(payload: dict = Body(default={})):
    """Stop recording immediately and continue ASR/LLM in the background."""
    global _meeting_summary_task, _meeting_report
    session_id = str(payload.get("session_id", ""))
    if not session_id:
        return {"ok": False, "accepted": False, "error_code": "session_id_required", "error": "缺少会议控制会话"}
    if _meeting_summary_task is None or _meeting_summary_task.done():
        _meeting_report = {**_meeting_report, "status": "stopping", "error": "", "progress": 0}
        _meeting_summary_task = asyncio.create_task(
            _meeting_complete_background({**payload, "session_id": session_id}),
            name="meeting-complete-background",
        )
        def _finish_summary(task):
            global _meeting_report
            try:
                task.result()
            except Exception as exc:
                logger.exception("meeting summary background task failed")
                _meeting_report = {**_meeting_report, "status": "error", "error": str(exc)[:160], "progress": 100}
        _meeting_summary_task.add_done_callback(_finish_summary)
        await asyncio.sleep(0)
    return {
        "ok": True, "accepted": True, "submitted": True, "stopped": False, "processing": True,
        "job_state": _meeting_report.get("status", "stopping"), "state": _conversation_state(),
    }


async def _meeting_complete_background(payload: dict) -> dict:
    global _meeting_report
    session_id = str(payload.get("session_id", ""))
    stop_result = {}
    try:
        stop_result = await api_multi_track_stop({"session_id": session_id, "finalize": True})
    except Exception as exc:
        logger.warning("meeting stop failed before summarize: %s", str(exc)[:120])
        stop_result = {"ok": False, "accepted": False, "reason": str(exc)[:120]}
        _meeting_report = {
            **_meeting_report,
            "status": "summarizing",
            "error": "控制停止异常，已继续处理已保存录音",
            "progress": 5,
        }
    if not stop_result.get("accepted"):
        _meeting_report = {
            **_meeting_report,
            "status": "summarizing",
            "error": "控制会话停止未确认，已继续处理已保存录音",
            "progress": 5,
        }
    else:
        _meeting_report = {**_meeting_report, "status": "summarizing", "error": "", "progress": 5}
    return await api_meeting_summarize({**payload, "background": True})


@app.get("/api/health")
async def health():
    return {
        "status": "ok",
        "video": video_client._connected if video_client else False,
        "gimbal": bool(_gimbal_tlm.get("connected")),
    }


# 鈹€鈹€ Two pages only 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€
# PAGE 1 = Control Dashboard (real telemetry/observability) -> /control , /v2
# PAGE 2 = User product home                                -> / , /home
HOME_FILE = DASHBOARD_DIR / "home.html"
_NOCACHE = {"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache", "Expires": "0"}


def _serve_html(path: Path):
    text = path.read_text(encoding="utf-8") if path.is_file() else ""
    return (HTMLResponse(text, headers=dict(_NOCACHE))
            if path.is_file() else HTMLResponse("Not found", status_code=404))


@app.get("/")
async def serve_root():
    return RedirectResponse("/home")


@app.get("/home")
async def serve_home():
    # PAGE 2: user product home. Engineering controls stay under /control.
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
