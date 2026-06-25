#!/usr/bin/env python3
"""
reCamera Multimodal ->Main Dashboard (FastAPI)
══════════════════════════════════════════════════════════════════

Architecture:
  Device (192.168.201.84)                This Server (0.0.0.0:8001)
  ┌─────────────────────->             ┌──────────────────────────->  ->SSCMA Node :8090    │──WebSocket──→│ /video_feed  (MJPEG)     ->  ->Node-RED  :1880     │←─Socket.IO───->/api/gimbal/* (control)  ->  ->                    ->             ->/ws          (state push) ->  ->                    ->             ->/home        (心屿)       ->  ->                    ->             ->/v2          (控制->     ->  └─────────────────────->             └──────────────────────────->
Usage:
    python recamera_fastapi.py                                      # safe dry-run
    python recamera_fastapi.py --device-ip 192.168.201.84           # real device
    python recamera_fastapi.py --device-ip 192.168.201.84 --no-dry-run  # real control

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
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional

import numpy as np
import cv2

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from core.event import ControlCommand
from utils.logger import get_logger, setup_root_logger

logger = get_logger(__name__)


class GimbalMode(str, Enum):
    AI_TRACK = "ai_track"
    MANUAL = "manual"
    SLEEP = "sleep"
    STANDBY = "standby"
    EMERGENCY_STOP = "ui_emergency_disabled"


class ControlModeState:
    """UI mode holder only."""

    def __init__(self) -> None:
        self._mode = GimbalMode.AI_TRACK
        self._manual_control = (0.0, 0.0)

    @property
    def mode(self) -> GimbalMode:
        return self._mode

    @property
    def mode_name(self) -> str:
        return self._mode.value

    @property
    def is_emergency(self) -> bool:
        return self._mode == GimbalMode.EMERGENCY_STOP

    def set_mode(self, mode) -> bool:
        self._mode = mode if isinstance(mode, GimbalMode) else GimbalMode(str(mode))
        return True

    def trigger_ui_emergency_disabled(self) -> bool:
        return self.set_mode(GimbalMode.EMERGENCY_STOP)

    def set_manual_control(self, yaw_delta: float, pitch_delta: float) -> None:
        self._manual_control = (float(yaw_delta), float(pitch_delta))

    def get_manual_control(self) -> tuple[float, float]:
        return self._manual_control


gimbal_state = ControlModeState()


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

# ══════════════════════════════════════════════════════════════->#  Configuration
# ══════════════════════════════════════════════════════════════->
DASHBOARD_DIR = Path(__file__).resolve().parent / "dashboard"
HTML_FILE = DASHBOARD_DIR / "recamera_v2_live.html"

@dataclass
class Config:
    device_ip: str = "192.168.201.84"
    host: str = "0.0.0.0"
    port: int = 8001
    dry_run: bool = True
    ssl_enabled: bool = False


# ══════════════════════════════════════════════════════════════->#  SSCMA Video Client (adapted from health-app camera_service.py)
# ══════════════════════════════════════════════════════════════->
class SSCMAVideoClient:
    """
    Connects to reCamera SSCMA WebSocket (ws://<device>:8090/).
    Receives base64 JPEG frames + YOLO detection boxes.
    Runs in a background thread.
    """

    def __init__(self, device_ip: str = "192.168.201.84"):
        self._device_ip = device_ip
        self.url = f"ws://{device_ip}:8090/"
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

                        # Signal MJPEG generator
                        if self._frame_event:
                            self._frame_event.set()

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
                logger.debug("SSCMA: %s", str(e)[:80])
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


# ══════════════════════════════════════════════════════════════->#  Gimbal Controller (Socket.IO to Node-RED Dashboard)
# ══════════════════════════════════════════════════════════════->
@dataclass
class GimbalStateData:
    connected: bool = False
    yaw_angle: float = 180.0
    yaw_target: float = 180.0
    pitch_angle: float = 90.0
    pitch_target: float = 90.0
    speed: int = 360
    tracking: bool = False
    sound_tracking: bool = False


class GimbalController:
    """
    Controls reCamera gimbal via Socket.IO ->Node-RED Dashboard (port 1880).

    """

    # Node-RED Dashboard 2.0 widget IDs (from official Gimbal flow)
    WIDGET_YAW   = "1528e53340ceac14"
    WIDGET_PITCH = "45dd35115125460f"
    WIDGET_SPEED = "141a718c4aca75ea"

    def __init__(self, device_ip: str = "192.168.201.84", dry_run: bool = True):
        self._device_ip = device_ip
        self._dry_run = dry_run
        self._state = GimbalStateData()
        self._lock = threading.Lock()
        self._sio = None
        self._sio_connected = False
        self._sio_url = f"http://{device_ip}:1880"
        self._sio_path = "/dashboard/socket.io"

        # Face tracking
        self._face_tracking: bool = False

    @property
    def connected(self) -> bool:
        return self._sio_connected and not self._dry_run

    @property
    def face_tracking(self) -> bool:
        return self._face_tracking

    def get_state(self) -> GimbalStateData:
        with self._lock:
            return GimbalStateData(
                connected=self.connected,
                yaw_angle=self._state.yaw_angle,
                yaw_target=self._state.yaw_target,
                pitch_angle=self._state.pitch_angle,
                pitch_target=self._state.pitch_target,
                speed=self._state.speed,
                tracking=self._face_tracking,
                sound_tracking=self._state.sound_tracking,
            )

    def start(self):
        if self._dry_run:
            logger.warning("=" * 55)
            logger.warning("🔒 GIMBAL DRY-RUN ->云台指令不会发出")
            logger.warning("   启动时加 --no-dry-run 才会真正控制云台")
            logger.warning("=" * 55)
            return

        _bypass_proxy_for_device(self._device_ip)

        try:
            import socketio
            import requests

            session = requests.Session()
            session.trust_env = False
            sio = socketio.Client(http_session=session)
            ev = threading.Event()

            @sio.on("connect")
            def _ok():
                self._sio_connected = True
                ev.set()
                logger.info("🟢 Gimbal connected via Socket.IO ->%s", self._sio_url)

            @sio.on("disconnect")
            def _dc():
                self._sio_connected = False
                logger.warning("Gimbal Socket.IO disconnected")

            sio.connect(
                self._sio_url,
                socketio_path=self._sio_path,
                wait_timeout=5.0,
                transports=["polling"],
            )
            if ev.wait(timeout=5.0):
                self._sio = sio
                logger.info("   Widgets: yaw=%s pitch=%s speed=%s",
                            self.WIDGET_YAW[-8:], self.WIDGET_PITCH[-8:], self.WIDGET_SPEED[-8:])
            else:
                logger.warning("Socket.IO timeout ->fallback to DRY-RUN")
                self._dry_run = True
        except ImportError:
            logger.warning("python-socketio not installed ->DRY-RUN only")
            self._dry_run = True
        except Exception as e:
            logger.warning("Socket.IO failed (%s) ->DRY-RUN", str(e)[:60])
            self._dry_run = True

    def stop(self):
        if self._sio:
            try: self._sio.disconnect()
            except: pass
        self._sio = None
        self._sio_connected = False

    # ── Emit slider value to Node-RED ──────────
    # Tries Socket.IO first, falls back to HTTP POST

    def _emit(self, widget_id: str, value: int):
        sent = False
        # Try Socket.IO
        if not self._dry_run and self._sio_connected and self._sio:
            try:
                self._sio.emit("widget-change", (widget_id, value))
                sent = True
            except Exception:
                pass
        # Fallback: direct HTTP POST to Node-RED
        if not sent and not self._dry_run:
            try:
                import requests
                url = self._sio_url + "/widget-change"
                requests.post(url, json={"widget": widget_id, "value": value}, timeout=1.0)
                sent = True
            except Exception:
                pass
        # Log what happened
        status = "⚡SENT" if sent else ("🔒DRY-RUN" if self._dry_run else "❌NO-CONN")
        return sent

    # ── Commands ────────────────────────────────

    def ui_apply_disabled(self, command: ControlCommand):
        """Only hardware exit: apply a normalized ControlCommand."""
        return False

    def _ui_yaw_disabled_raw(self, angle: float):
        angle = max(0.0, min(360.0, float(angle)))
        with self._lock:
            self._state.yaw_target = angle
        sent = self._emit(self.WIDGET_YAW, int(angle))
        status = "sent" if sent else ("dry" if self._dry_run else "disc")
        logger.info("🎯 Yaw ->%.0f° %s (sio=%s)", angle, status, self._sio_connected)

    def _ui_pitch_disabled_raw(self, angle: float):
        angle = max(0.0, min(180.0, float(angle)))
        with self._lock:
            self._state.pitch_target = angle
        sent = self._emit(self.WIDGET_PITCH, int(angle))
        status = "sent" if sent else ("dry" if self._dry_run else "disc")
        logger.info("🎯 Pitch ->%.0f° %s (sio=%s)", angle, status, self._sio_connected)

    def _ui_speed_disabled_raw(self, speed: int):
        speed = max(0, min(720, int(speed)))
        with self._lock:
            self._state.speed = speed
        self._emit(self.WIDGET_SPEED, speed)

    def ui_yaw_disabled(self, angle: float):
        return False

    def ui_pitch_disabled(self, angle: float):
        return False

    def ui_speed_disabled(self, speed: int):
        return False

    def sleep(self):
        logger.info("💤 SLEEP (yaw=180, pitch=180)")
        return False

    def standby(self):
        logger.info("-> STANDBY (yaw=180, pitch=90)")
        return False

    def ui_emergency_disabled(self):
        logger.warning("🛑 EMERGENCY STOP")
        return False

    def start_face_tracking(self):
        if self._face_tracking: return
        self._face_tracking = True

    def stop_face_tracking(self):
        self._face_tracking = False
        self._ft_locked = False
        self._ft_lock_cnt = 0
        self._ft_last_center = None
        try:
            _face_capture_reset("face_tracking_stopped")
        except NameError:
            pass

    def update_face_tracking(self, face_center, fw, fh):
        if not self._face_tracking:
            return {"active": False, "reason": "face_tracking_off"}
        import time as _time
        now = _time.monotonic()
        if not hasattr(self, '_last_ft_cmd'):
            self._last_ft_cmd = 0.0
            self._ft_ema_yaw = None
            self._ft_ema_pitch = None
            self._ft_prev_err_yaw = None
            self._ft_prev_err_pitch = None
            self._ft_prev_err_ts = None
            self._ft_log_count = 0
        rapid = getattr(self, '_rapid_align', False)

        fx, fy = face_center
        ex = (fx - fw/2) / fw  # normalized error [-0.5, 0.5]
        ey = (fy - fh/2) / fh

        alpha = 0.62 if rapid else 0.38
        if self._ft_ema_yaw is None:
            self._ft_ema_yaw = ex
            self._ft_ema_pitch = ey
        else:
            self._ft_ema_yaw = alpha * ex + (1 - alpha) * self._ft_ema_yaw
            self._ft_ema_pitch = alpha * ey + (1 - alpha) * self._ft_ema_pitch

        ex_smooth = self._ft_ema_yaw
        ey_smooth = self._ft_ema_pitch

        dt = 0.12
        if self._ft_prev_err_ts is not None:
            dt = max(0.04, min(0.30, now - self._ft_prev_err_ts))
        vx = 0.0 if self._ft_prev_err_yaw is None else (ex_smooth - self._ft_prev_err_yaw) / dt
        vy = 0.0 if self._ft_prev_err_pitch is None else (ey_smooth - self._ft_prev_err_pitch) / dt
        self._ft_prev_err_yaw = ex_smooth
        self._ft_prev_err_pitch = ey_smooth
        self._ft_prev_err_ts = now

        cooldown = 0.07 if rapid else 0.16
        if now - self._last_ft_cmd < cooldown:
            return {
                "active": True,
                "reason": "cooldown",
                "cooldown": cooldown,
            }

        def _pd_step(err, vel):
            lead = 0.10 if rapid else 0.045
            pred = err + vel * lead
            a = abs(pred)
            dead = 0.014 if rapid else 0.028
            if a < dead:
                return 0.0, pred, "deadzone"
            kp = 22.0 if rapid else 8.5
            kd = 1.2 if rapid else 0.35
            max_step = 7.0 if rapid else 2.2
            step = kp * pred + kd * vel
            if rapid and 0.06 <= a < 0.20:
                step *= 1.18
            return max(-max_step, min(max_step, step)), pred, "pd"

        moved = False
        # On the current gimbal, yaw must move opposite to image-x error:
        # target right of center -> ex > 0 -> decrease yaw.
        dy, pred_x, mode_x = _pd_step(-ex_smooth, -vx)
        yaw_cmd = None
        if abs(dy) > (0.30 if rapid else 0.38):
            tgt_yaw = max(0, min(360, self._state.yaw_target + dy))
            self._state.yaw_target = tgt_yaw
            return {"active": False, "reason": "fastapi_control_disabled"}
            self._last_ft_cmd = now
            yaw_cmd = tgt_yaw
            moved = True

        # Image-y grows downward.  On this gimbal, moving the camera upward
        # means decreasing pitch, so the image-y error can be applied directly:
        # face above center -> ey < 0 -> pitch decreases.
        dp, pred_y, mode_y = _pd_step(ey_smooth, vy)
        pitch_cmd = None
        if abs(dp) > (0.30 if rapid else 0.38):
            tgt_pitch = max(30, min(150, self._state.pitch_target + dp))
            self._state.pitch_target = tgt_pitch
            return {"active": False, "reason": "fastapi_control_disabled"}
            self._last_ft_cmd = now
            pitch_cmd = tgt_pitch
            moved = True

        if moved:
            self._ft_log_count += 1
            if self._ft_log_count % 10 == 1:  # log every 10th move
                logger.info("🎯 FT: fx=%.0f,fy=%.0f fw=%d,fh=%d ->ex=%.3f,ey=%.3f ->yaw=%d°,pitch=%d° %s",
                    fx, fy, fw, fh, ex_smooth, ey_smooth,
                    int(self._state.yaw_target), int(self._state.pitch_target),
                    "(DRY-RUN)" if self._dry_run else "(LIVE)")
        return {
            "active": True,
            "reason": "moved" if moved else "no_step",
            "target": [round(float(fx), 1), round(float(fy), 1)],
            "error": [round(float(ex_smooth), 4), round(float(ey_smooth), 4)],
            "velocity": [round(float(vx), 4), round(float(vy), 4)],
            "predicted_error": [round(float(pred_x), 4), round(float(pred_y), 4)],
            "delta": [round(float(dy), 3), round(float(dp), 3)],
            "cmd": {
                "yaw": round(float(yaw_cmd), 2) if yaw_cmd is not None else None,
                "pitch": round(float(pitch_cmd), 2) if pitch_cmd is not None else None,
            },
            "mode": {"yaw": mode_x, "pitch": mode_y},
            "cooldown": cooldown,
            "rapid": bool(rapid),
        }


# ══════════════════════════════════════════════════════════════->#  WebSocket Connection Manager
# ══════════════════════════════════════════════════════════════->
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


# ══════════════════════════════════════════════════════════════->#  Global instances (set during lifespan)
# ══════════════════════════════════════════════════════════════->
video_client: Optional[SSCMAVideoClient] = None
gimbal_ctrl: Optional[GimbalController] = None
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
_mp_face_result = {"success": False, "ear_avg": 0.3, "eye_open": True, "head_yaw": 0, "head_pitch": 0}
_mp_landmarks5 = None
_eye_metrics = {"ear_avg": 0.3, "blink_rate": 0, "perclos": 0, "focus_score": 100}
_emotieff_result = None  # EmotiEffLib parallel inference result
_sound_tracking = False
_tracking_mode = "single"
_last_sound_yaw_cmd = 0.0
_tracking_debug = {}
_face_capture_state = {
    "state": "IO_IDLE",
    "target_id": None,
    "candidate_id": None,
    "candidate_count": 0,
    "last_center": None,
    "last_seen": 0.0,
    "lost_since": None,
    "velocity": [0.0, 0.0],
    "search_center_yaw": None,
    "search_center_pitch": None,
    "search_yaw": None,
    "search_dir": 1,
    "last_search_cmd": 0.0,
    "reason": "init",
}
SWEEP_CENTER_YAW = 180.0
SWEEP_AMPLITUDE_DEG = 40.0
SWEEP_STEP_DEG = 2.0
_conversation_recorder = None
_conversation_recording_requested = False
_last_conversation_start_attempt = 0.0
_sound_follow_state = {
    "active": False,
    "doa_deg": None,
    "has_speech": False,
    "target_yaw": None,
    "age": 999.0,
    "reason": "idle",
}


def _audio_event(doa_deg: float, speech: bool, source: str = "doa") -> dict:
    return {"type": "audio", "source": source, "doa_deg": float(doa_deg), "speech": bool(speech)}


def _vision_event(cx: float, cy: float, conf: float, source: str = "vision") -> dict:
    return {"type": "vision", "source": source, "cx": float(cx), "cy": float(cy), "conf": float(conf)}


# ══════════════════════════════════════════════════════════════->#  Build state snapshot dict
# ══════════════════════════════════════════════════════════════->
def detect_target(frame_jpeg: bytes, want_face: bool = False) -> dict:
    """
    三级目标检-> ->->肩膀 ->身体 bbox->    返回归一化坐->(0-1)->
    want_face=True: 只要人脸 (Stage 2 垂直对准->
    want_face=False: ->> 肩膀 > 身体 (Stage 1 水平对准->
    """
    import cv2, numpy as np
    arr = np.frombuffer(frame_jpeg, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        return {"found": False, "type": "none", "detail": "decode failed"}

    h, w = img.shape[:2]

    # ── Level 1: YuNet 人脸 (高置信度) ──
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

    # Stage 2 只要->->没脸就返回空
    if want_face:
        return {"found": False, "type": "none", "detail": "no face for pitch align"}

    # ── Level 2: 肩膀关键->──
    for p in _latest_pose_persons:
        shoulders = [kp for kp in p.keypoints
                     if kp.name in ("left_shoulder", "right_shoulder") and kp.conf > 0.6]
        if len(shoulders) == 2:
            cx = sum(kp.x for kp in shoulders) / 2
            cy = sum(kp.y for kp in shoulders) / 2
            return {"found": True, "type": "shoulder",
                    "cx": cx / w, "cy": cy / h, "quality": 0.8,
                    "detail": "shoulder midpoint"}

    # ── Level 3: YOLO bbox ->SSCMA format [cx, cy, w, h, conf, cls]
    boxes = video_client.boxes if video_client else []
    for box in boxes:
        if len(box) < 6: continue
        cx_b, cy_b, bw, bh = float(box[0]), float(box[1]), float(box[2]), float(box[3])
        conf_raw = box[4]
        conf = conf_raw / 100.0 if conf_raw > 1 else float(conf_raw)
        area_ratio = (bw * bh) / (w * h)
        if conf >= 0.6 and area_ratio >= 0.03:
            cy = cy_b - bh * 0.3  # center往->0% ->靠近胸部
            return {"found": True, "type": "body",
                    "cx": cx_b / w, "cy": cy / h,
                    "quality": conf, "detail": f"body conf={conf:.2f}"}

    return {"found": False, "type": "none", "detail": "no target"}


def _has_complete_face(person) -> bool:
    required = {"left_eye", "right_eye", "nose", "left_mouth", "right_mouth"}
    names = {kp.name for kp in person.keypoints if kp.conf >= 0.45}
    return bool(person.face_center) and required.issubset(names)


def _face_track_id(person):
    return getattr(person, "_track_id", None)


def _face_is_primary(person) -> bool:
    return bool(getattr(person, "_is_primary", False))


def _same_face_candidate(track_id, center, prev_id, prev_center, fw: int, fh: int) -> bool:
    if track_id is not None and prev_id is not None:
        return track_id == prev_id
    if center is None or prev_center is None:
        return False
    dx = (float(center[0]) - float(prev_center[0])) / max(1.0, float(fw))
    dy = (float(center[1]) - float(prev_center[1])) / max(1.0, float(fh))
    return (dx * dx + dy * dy) ** 0.5 < 0.10


def _best_complete_face(min_conf: float = 0.45, fw: int = 1920, fh: int = 1080):
    candidates = [
        p for p in _latest_pose_persons
        if p.face_center and p.face_conf and p.face_conf >= min_conf and _has_complete_face(p)
    ]
    if not candidates:
        return None

    target_id = _face_capture_state.get("target_id")
    candidate_id = _face_capture_state.get("candidate_id")
    last_center = _face_capture_state.get("last_center")

    def score(p):
        tid = _face_track_id(p)
        cx, cy = p.face_center
        center_dx = (float(cx) / max(1, fw)) - 0.5
        center_dy = (float(cy) / max(1, fh)) - 0.45
        center_penalty = (center_dx * center_dx + center_dy * center_dy) ** 0.5
        continuity = 0.0
        if tid is not None and tid == target_id:
            continuity += 0.35
        elif tid is not None and tid == candidate_id:
            continuity += 0.16
        elif _same_face_candidate(tid, p.face_center, target_id, last_center, fw, fh):
            continuity += 0.20
        primary_bonus = 0.18 if _face_is_primary(p) else 0.0
        source_bonus = 0.06 if getattr(p, "_source", "") == "face_tracker_v2" else 0.0
        return float(p.face_conf or 0.0) + continuity + primary_bonus + source_bonus - center_penalty

    return max(candidates, key=score, default=None)


def _best_person_from_boxes(fw: int, fh: int, min_conf: float = 0.42) -> Optional[dict]:
    boxes = video_client.boxes if video_client else []
    best = None
    for b in boxes:
        if len(b) < 6:
            continue
        if int(b[5]) != 0:
            continue
        conf = b[4] / 100.0 if b[4] > 1 else float(b[4])
        if conf < min_conf:
            continue
        cx_b, cy_b, bw, bh = float(b[0]), float(b[1]), float(b[2]), float(b[3])
        area_ratio = (bw * bh) / max(1.0, float(fw * fh))
        if area_ratio < 0.01:
            continue
        x1 = cx_b - bw / 2
        y1 = cy_b - bh / 2
        face_est_y = y1 + bh * 0.18
        item = {
            "cx": cx_b / fw,
            "cy": cy_b / fh,
            "face_y": face_est_y / fh,
            "conf": conf,
            "area": area_ratio,
        }
        if best is None or item["conf"] > best["conf"]:
            best = item
    return best


def _best_person_from_pose(fw: int, fh: int, min_conf: float = 0.42) -> Optional[dict]:
    best = None
    for p in _latest_pose_persons:
        conf = float(getattr(p, "conf", 0.0) or 0.0)
        if conf < min_conf:
            continue
        x1, y1, x2, y2 = p.bbox
        bw, bh = x2 - x1, y2 - y1
        if bw <= 0 or bh <= 0:
            continue
        if p.face_center:
            face_y = float(p.face_center[1]) / fh
        else:
            face_y = (float(y1) + float(bh) * 0.18) / fh
        item = {
            "cx": (float(x1) + float(bw) / 2) / fw,
            "cy": (float(y1) + float(bh) / 2) / fh,
            "face_y": face_y,
            "conf": conf,
            "area": (float(bw) * float(bh)) / max(1.0, float(fw * fh)),
        }
        if best is None or item["conf"] > best["conf"]:
            best = item
    return best


def _best_person_target(fw: int, fh: int, min_conf: float = 0.42) -> Optional[dict]:
    return _best_person_from_boxes(fw, fh, min_conf) or _best_person_from_pose(fw, fh, min_conf)


def _person_debug_target(t: Optional[dict]) -> Optional[dict]:
    if not t:
        return None
    return {
        "cx": round(float(t.get("cx", 0.0)), 4),
        "cy": round(float(t.get("cy", 0.0)), 4),
        "face_y": round(float(t.get("face_y", 0.0)), 4),
        "conf": round(float(t.get("conf", 0.0)), 3),
        "area": round(float(t.get("area", 0.0)), 4),
    }


def _face_debug_target(p) -> Optional[dict]:
    if not p:
        return None
    return {
        "track_id": _face_track_id(p),
        "source": getattr(p, "_source", "unknown"),
        "primary": _face_is_primary(p),
        "lost_frames": int(getattr(p, "_lost_frames", 0) or 0),
        "bbox": [round(float(v), 1) for v in p.bbox],
        "face_center": [round(float(p.face_center[0]), 1), round(float(p.face_center[1]), 1)]
                       if p.face_center else None,
        "face_conf": round(float(p.face_conf or 0.0), 3),
        "keypoints": [kp.name for kp in p.keypoints if kp.conf >= 0.45],
    }


def _set_tracking_debug(**kwargs) -> None:
    global _tracking_debug
    data = dict(_tracking_debug)
    data.update(kwargs)
    data["updated_at"] = round(time.time(), 3)
    _tracking_debug = _json_clean(data)


def _face_capture_reset(reason: str = "reset") -> None:
    _face_capture_state.update({
        "state": "IO_IDLE",
        "target_id": None,
        "candidate_id": None,
        "candidate_count": 0,
        "last_center": None,
        "last_seen": 0.0,
        "lost_since": None,
        "velocity": [0.0, 0.0],
        "search_center_yaw": None,
        "search_center_pitch": None,
        "search_yaw": None,
        "search_dir": 1,
        "last_search_cmd": 0.0,
        "reason": reason,
    })


def _update_face_capture_state(face, fw: int, fh: int) -> dict:
    """Display-only face capture state."""
    now = time.monotonic()
    state = _face_capture_state.get("state", "IO_IDLE")
    prev_center = _face_capture_state.get("last_center")
    prev_seen = float(_face_capture_state.get("last_seen") or 0.0)

    if face is not None and face.face_center:
        tid = _face_track_id(face)
        center = [float(face.face_center[0]), float(face.face_center[1])]
        same_locked = _same_face_candidate(
            tid, center,
            _face_capture_state.get("target_id"), prev_center,
            fw, fh,
        )
        same_candidate = _same_face_candidate(
            tid, center,
            _face_capture_state.get("candidate_id"), prev_center,
            fw, fh,
        )

        dt = max(0.04, min(0.50, now - prev_seen)) if prev_seen else 0.2
        if prev_center is not None:
            vx = (center[0] - float(prev_center[0])) / dt
            vy = (center[1] - float(prev_center[1])) / dt
            old_vx, old_vy = _face_capture_state.get("velocity", [0.0, 0.0])
            velocity = [0.45 * vx + 0.55 * float(old_vx), 0.45 * vy + 0.55 * float(old_vy)]
        else:
            velocity = [0.0, 0.0]

        if state in ("IO_PRESENT", "IO_MISSING") and same_locked:
            new_state = "IO_PRESENT"
            count = max(2, int(_face_capture_state.get("candidate_count", 0)))
            reason = "same_target_reacquired" if state == "IO_MISSING" else "same_target"
        else:
            count = int(_face_capture_state.get("candidate_count", 0)) + 1 if same_candidate else 1
            new_state = "IO_PRESENT"
            reason = "face_present"

        _face_capture_state.update({
            "state": new_state,
            "target_id": tid,
            "candidate_id": tid,
            "candidate_count": count,
            "last_center": center,
            "last_seen": now,
            "lost_since": None,
            "velocity": velocity,
            "search_center_yaw": None,
            "search_center_pitch": None,
            "search_yaw": None,
            "reason": reason,
            "face_conf": round(float(face.face_conf or 0.0), 3),
            "source": getattr(face, "_source", "unknown"),
            "primary": _face_is_primary(face),
        })
        if new_state == "IO_PRESENT" and _face_capture_state.get("target_id") is None:
            _face_capture_state["target_id"] = tid
        return dict(_face_capture_state)

    # No valid face this frame.
    if state == "IO_PRESENT":
        _face_capture_state.update({
            "state": "IO_MISSING",
            "lost_since": now,
            "candidate_count": 0,
            "reason": "face_missing_grace",
        })
    elif state == "IO_MISSING":
        lost_for = now - float(_face_capture_state.get("lost_since") or now)
        if lost_for > 2.8:
            _face_capture_state.update({
                "state": "IO_IDLE",
                "target_id": None,
                "candidate_id": None,
                "candidate_count": 0,
                "reason": "lost_timeout_search",
            })
        else:
            _face_capture_state["reason"] = "predictive_reacquire"
    elif state == "IO_CANDIDATE":
        if now - prev_seen > 0.8:
            _face_capture_state.update({
                "state": "IO_IDLE",
                "candidate_id": None,
                "candidate_count": 0,
                "reason": "candidate_timeout",
            })
    else:
        _face_capture_state["state"] = "IO_IDLE"
        _face_capture_state["reason"] = "no_face"
    return dict(_face_capture_state)


def _predictive_reacquire_step(gc: GimbalController, fw: int, fh: int) -> Optional[dict]:
    """Local search around the last known yaw/pitch, biased by last image velocity."""
    return {"active": False, "reason": "reacquire_control_removed"}
    if not gc or _face_capture_state.get("state") != "IO_MISSING":
        return None
    now = time.monotonic()
    if now - float(_face_capture_state.get("last_search_cmd") or 0.0) < 0.22:
        return {"active": True, "reason": "search_cooldown"}

    lost_since = float(_face_capture_state.get("lost_since") or now)
    lost_for = max(0.0, now - lost_since)
    gs = gc._state
    if _face_capture_state.get("search_center_yaw") is None:
        vx, vy = _face_capture_state.get("velocity", [0.0, 0.0])
        # Positive image-x velocity means target moved right in the image; yaw
        # should decrease first to follow that motion on this gimbal.
        search_dir = -1 if float(vx) > 8 else 1
        _face_capture_state.update({
            "search_center_yaw": float(gs.yaw_target),
            "search_center_pitch": float(gs.pitch_target),
            "search_yaw": float(gs.yaw_target),
            "search_dir": search_dir,
        })

    center_yaw = float(_face_capture_state.get("search_center_yaw") or gs.yaw_target)
    center_pitch = float(_face_capture_state.get("search_center_pitch") or gs.pitch_target)
    search_yaw = float(_face_capture_state.get("search_yaw") or center_yaw)
    search_dir = int(_face_capture_state.get("search_dir") or 1)
    amp = min(28.0, 6.0 + lost_for * 9.0)
    step = 2.0 + min(3.0, lost_for * 1.6)
    search_yaw += search_dir * step
    if search_yaw > center_yaw + amp:
        search_yaw = center_yaw + amp
        search_dir = -1
    elif search_yaw < center_yaw - amp:
        search_yaw = center_yaw - amp
        search_dir = 1

    _, vy = _face_capture_state.get("velocity", [0.0, 0.0])
    pitch_bias = max(-8.0, min(8.0, float(vy) / max(1.0, float(fh)) * 55.0))
    search_pitch = max(30, min(150, center_pitch + pitch_bias))
    gc.ui_yaw_disabled(int(max(0, min(360, search_yaw))))
    if abs(search_pitch - gs.pitch_target) > 1.0:
        gc.ui_pitch_disabled(int(search_pitch))
    _face_capture_state.update({
        "search_yaw": search_yaw,
        "search_dir": search_dir,
        "last_search_cmd": now,
    })
    return {
        "active": True,
        "reason": "predictive_local_sweep",
        "lost_for": round(float(lost_for), 2),
        "yaw": round(float(search_yaw), 2),
        "pitch": round(float(search_pitch), 2),
        "amp": round(float(amp), 2),
        "dir": search_dir,
    }


def _ensure_doa_reader() -> bool:
    """Start the configured DOA source without requiring ReSpeaker USB in WSL."""
    global _doa_reader
    if _doa_reader is not None:
        return True
    try:
        source = os.environ.get("RECAMERA_DOA_SOURCE", "tcp").strip().lower()
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
        return {"available": False, "source": os.environ.get("RECAMERA_DOA_SOURCE", "tcp")}
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


def _resume_ai_gimbal_mode(reason: str = "") -> bool:
    """Ensure automatic control is allowed unless emergency-stop is active."""
    if gimbal_state.mode == GimbalMode.AI_TRACK:
        return True
    if gimbal_state.mode == GimbalMode.EMERGENCY_STOP:
        logger.warning("Cannot resume AI gimbal mode during emergency stop (%s)", reason)
        return False
    return bool(gimbal_state.set_mode(GimbalMode.AI_TRACK))


def _update_sound_tracking_yaw() -> None:
    """Yaw-only gimbal follow for multi-person recording mode."""
    global _last_sound_yaw_cmd, _sound_follow_state
    if not _sound_tracking or not gimbal_ctrl:
        _sound_follow_state.update({"active": False, "reason": "sound_tracking_disabled"})
        return
    if not _ensure_doa_reader() or _doa_reader is None:
        _sound_follow_state.update({"active": True, "age": 999.0, "has_speech": False, "reason": "doa_unavailable"})
        return

    now = time.monotonic()
    if now - _last_sound_yaw_cmd < 0.2:
        return

    doa_deg, has_speech = _doa_reader.read()
    conv_current = _conversation_recorder.state().get("current", {}) if _conversation_recorder is not None else {}
    vad_speech = bool(conv_current.get("has_speech", False))
    voice_active = bool(has_speech or vad_speech)
    _sound_follow_state.update({
        "active": True,
        "doa_deg": round(float(doa_deg), 1),
        "has_speech": voice_active,
        "hid_speech": bool(has_speech),
        "vad_speech": vad_speech,
        "age": round(float(_doa_reader.age), 2),
        "reason": "ready",
    })
    if _doa_reader.age > 1.0:
        _sound_follow_state["reason"] = "stale_doa"
        return
    if not voice_active:
        _sound_follow_state["reason"] = "waiting_for_speech"
        return

    cmd = None
    _last_sound_yaw_cmd = now
    if cmd and cmd.yaw is not None:
        _sound_follow_state["target_yaw"] = round(float(cmd.yaw), 1)
        _sound_follow_state["reason"] = cmd.reason or "command_sent"
        logger.info("🎤 Sound follow: doa=%.1f° ->yaw=%.0f° (%s)", doa_deg, cmd.yaw, cmd.reason)
    else:
        _sound_follow_state["reason"] = "no_command"


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
        "sound_tracking": bool(_sound_tracking),
        "sound_follow": _sound_follow_state,
        "gimbal_mode": gimbal_state.mode_name,
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

    # ── YuNet face detection (高阈-> 减少假阳-> ──
    faces = []
    try:
        yunet_path = "models/face_detection_yunet.onnx"
        yunet = cv2.FaceDetectorYN_create(yunet_path, "", (w, h), 0.75, 0.4, 5000)
        _, faces = yunet.detect(img)
        if faces is None: faces = []
    except Exception: pass

    result = []

    # ── YuNet faces ->真实五官关键->──
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

    # ── YuNet missed but pose already has face points: keep them for lock/attention ──
    if not result:
        for p in persons:
            face_names = {kp.name for kp in p.keypoints if kp.conf >= 0.3}
            if p.face_center and {"nose", "left_eye", "right_eye"}.issubset(face_names):
                p.face_conf = max(float(p.face_conf or 0.0), float(p.conf or 0.0), 0.55)
                p._source = "pose_face"
                result.append(p)

    # ── 无脸-> 只用设备 person 框画肩膀, 不画假脸 ──
    if not result:
        device_boxes = video_client.boxes if video_client else []
        for box in device_boxes[:5]:
            if len(box) < 6: continue
            cls = int(box[5]) if len(box) > 5 else -1
            if cls != 0: continue  # 只要 person
            conf = box[4]/100.0 if box[4] > 1 else float(box[4])
            if conf < 0.55: continue
            cx_b, cy_b, bw, bh = [float(v) for v in box[:4]]
            if bh < 50 or bw*bh/(w*h) < 0.02: continue  # 太小跳过
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
        kps = [{"x": float(kp.x), "y": float(kp.y),
                "conf": round(float(kp.conf), 2), "name": str(kp.name)}
               for kp in p.keypoints]
        persons.append({
            "bbox": [round(float(v), 1) for v in p.bbox],
            "conf": round(float(p.conf), 2),
            "keypoints": kps,
            "face_center": [round(float(p.face_center[0]), 1),
                            round(float(p.face_center[1]), 1)]
                           if p.face_center else None,
            "face_conf": round(float(p.face_conf), 2),
        })
    return {"persons": persons, "count": len(persons)}


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


def _tracking_point_from_landmarks5(landmarks5) -> Optional[tuple]:
    """Prefer a real facial anchor over bbox center for visual tracking."""
    if landmarks5 is None:
        return None
    try:
        pts = np.asarray(landmarks5, dtype=np.float32)
        if pts.shape[0] < 3:
            return None
        left_eye, right_eye, nose = pts[0, :2], pts[1, :2], pts[2, :2]
        eye_mid = (left_eye + right_eye) / 2.0
        target = 0.45 * eye_mid + 0.55 * nose
        return (float(target[0]), float(target[1]))
    except Exception:
        return None


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


def build_state_snapshot() -> dict:
    gs = gimbal_ctrl.get_state() if gimbal_ctrl else GimbalStateData()

    # Extract detections from video boxes
    detections = []
    if video_client:
        for box in video_client.boxes:
            if len(box) >= 6:
                # SSCMA format: [cx, cy, w, h, conf, cls]
                cx_b, cy_b, bw, bh = float(box[0]), float(box[1]), float(box[2]), float(box[3])
                detections.append({
                    "x": cx_b - bw/2, "y": cy_b - bh/2,
                    "w": bw, "h": bh,
                    "class_name": "person" if int(box[5]) == 0 else f"class_{int(box[5])}",
                    "confidence": float(box[4]) / 100.0 if float(box[4]) > 1 else float(box[4]),
                })

    snapshot = {
        "type": "state_snapshot",
        "data": {
            "gimbal": {
                "connected": gs.connected,
                "dry_run": bool(getattr(gimbal_ctrl, "_dry_run", True)) if gimbal_ctrl else True,
                "sio_connected": bool(getattr(gimbal_ctrl, "_sio_connected", False)) if gimbal_ctrl else False,
                "yaw_angle": gs.yaw_angle,
                "yaw_target": gs.yaw_target,
                "pitch_angle": gs.pitch_angle,
                "pitch_target": gs.pitch_target,
                "speed": gs.speed,
                "tracking": gs.tracking,
                "sound_tracking": bool(_sound_tracking),
                "mode": gimbal_state.mode_name,
            },
            "tracking_mode": _tracking_mode,
            "video": {
                "connected": True if video_client else False,  # MJPEG流存活即connected
                "fps": video_client.fps if video_client else 0.0,
                "width": video_client.resolution[0] if video_client else 1920,
                "height": video_client.resolution[1] if video_client else 1080,
                "detections": detections,
            },
            "pose": _build_pose_data(),
            "doa": _doa_status(),
            "sound_follow": _sound_follow_state,
            "conversation": _conversation_state(),
            "face_tracking": gimbal_ctrl.face_tracking if gimbal_ctrl else False,
            "face_lock": {
                "locked": getattr(gimbal_ctrl, '_ft_locked', False) if gimbal_ctrl else False,
                "lock_cnt": getattr(gimbal_ctrl, '_ft_lock_cnt', 0) if gimbal_ctrl else 0,
            },
            "face_capture": _json_clean(_face_capture_state),
            "tracking_debug": _tracking_debug,
            "attention": _attn_result,
            "emotion": _emotion_result,
            "emotieff": _emotieff_result,
            "llm_diary": _llm_diary_entry,
            "llm_quote": _llm_quote_text,
            "mp_face": _mp_face_result,
            "eye_metrics": _eye_metrics,
            "timestamp": time.time(),
        },
    }
    return _json_clean(snapshot)


# ══════════════════════════════════════════════════════════════->#  FastAPI App
# ══════════════════════════════════════════════════════════════->
@asynccontextmanager
async def lifespan(app: FastAPI):

    global video_client, gimbal_ctrl

    # Start video client
    video_client = SSCMAVideoClient(device_ip=app_config.device_ip)
    video_client._frame_event = asyncio.Event()
    video_client.start()

    # Start gimbal controller
    gimbal_ctrl = GimbalController(
        device_ip=app_config.device_ip, dry_run=app_config.dry_run
    )
    gimbal_ctrl.start()

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
    global _mp_face, _eye_tracker
    _mp_face = None
    _eye_tracker = None

    # EmotiEffLib adapter
    from vision.emotieff_adapter import get_emotieff_adapter
    get_emotieff_adapter()

    # DOA defaults to TCP input, so ReSpeaker does not need USB passthrough to WSL.
    global _doa_reader, _sound_tracking, _conversation_recording_requested
    _doa_reader = None
    _sound_tracking = False
    _conversation_recording_requested = False
    _ensure_doa_reader()

    # Background tasks
    push_task = asyncio.create_task(state_push_loop())

    logger.info("=" * 55)
    logger.info("reCamera Demo Dashboard (FastAPI)")
    scheme = "https" if app_config.ssl_enabled else "http"
    ws_scheme = "wss" if app_config.ssl_enabled else "ws"
    logger.info("   Device IP:    %s", app_config.device_ip)
    logger.info("   Dashboard:    %s://localhost:%d/home", scheme, app_config.port)
    logger.info("   Gimbal:       %s (sio_connected=%s dry_run=%s)",
        "Socket.IO→Node-RED" if (gimbal_ctrl and gimbal_ctrl.connected) else "DRY-RUN / disconnected",
        getattr(gimbal_ctrl, '_sio_connected', False) if gimbal_ctrl else False,
        app_config.dry_run)
    logger.info("   MJPEG:        %s://localhost:%d/video_feed", scheme, app_config.port)
    logger.info("   WebSocket:    %s://localhost:%d/ws", ws_scheme, app_config.port)
    logger.info("   Gimbal:       %s", "DRY-RUN" if app_config.dry_run else "Socket.IO ->Node-RED")
    logger.info("=" * 55)

    yield

    # Cleanup
    push_task.cancel()
    try: await push_task
    except asyncio.CancelledError: pass

    _stop_conversation_recording(finalize=True)
    if video_client: video_client.stop()
    if gimbal_ctrl: gimbal_ctrl.stop()
    if _doa_reader: _doa_reader.close()
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


# ── State push loop ──────────────────────────────────────

async def state_push_loop():
    """Push UI snapshots to WebSocket clients."""
    global _attn_result, _emotion_result, _emotieff_result, _eye_metrics
    global _mp_face, _eye_tracker, _mp_face_result, _mp_landmarks5
    global _llm_engine, _llm_diary_entry, _llm_quote_text, _last_llm_diary_time
    global _doa_reader  # kept for potential future use, NOT used in pipeline
    pose_est = None
    pose_frame_count = 0
    _sweep_yaw = SWEEP_CENTER_YAW; _sweep_dir = -1

    # ── Pipeline: face first ->sweep search ->center person ->center face ->track ──
    # -1-> standby center ->0-> start sweep ->1-> person found ->2-> face locked
    _startup_phase = -1       # -1: go to standby center first
    _startup_ts = time.monotonic()
    _startup_diag_done = False

    while True:
        try:
            if not _startup_diag_done and video_client and video_client.connected:
                _startup_diag_done = True
                gc = gimbal_ctrl
                logger.info("🔍 DIAG: gimbal=%s dry_run=%s sio=%s",
                    "present" if gc else "MISSING",
                    gc._dry_run if gc else "?",
                    gc._sio_connected if gc else "?")
                if gc and gc._dry_run:
                    logger.warning("🔒 DRY-RUN ->add --no-dry-run for real gimbal control")

            # ── Gimbal mode priority check ──
            # MANUAL / SLEEP / STANDBY / EMERGENCY_STOP override auto search/track.
            # On MANUAL ->AI_TRACK transition, resume auto from current position.
            snapshot = build_state_snapshot()
            await ws_mgr.broadcast(snapshot)
            await asyncio.sleep(0.2)
            continue

            gmode = gimbal_state.mode
            _manual_active = (gmode != GimbalMode.AI_TRACK)

            if _manual_active:
                if gmode == GimbalMode.MANUAL:
                    dpan, dtilt = gimbal_state.get_manual_control()
                    if abs(dpan) > 0.001 or abs(dtilt) > 0.001:
                        if gimbal_ctrl:
                            gs = gimbal_ctrl._state if gimbal_ctrl else None
                            cur_yaw = gs.yaw_target if gs else 180
                            cur_pitch = gs.pitch_target if gs else 90
                            gimbal_ctrl.ui_yaw_disabled(int(max(1, min(345, cur_yaw + dpan))))
                            gimbal_ctrl.ui_pitch_disabled(int(max(1, min(175, cur_pitch + dtilt))))
                        gimbal_state.set_manual_control(0.0, 0.0)  # consume
                # Reset auto phases so we restart from standby center when AI resumes
                if _startup_phase != -1:
                    _startup_phase = -1
                    _startup_ts = time.monotonic()
                    _face_capture_reset("manual_or_non_ai_mode")

            # ── Multi-person recording: pure sound-source yaw follow ──
            if _sound_tracking:
                _ensure_doa_reader()
                if _conversation_recording_requested and (
                    _conversation_recorder is None or not _conversation_recorder.active
                ):
                    _start_conversation_recording()
                if gimbal_ctrl and gimbal_ctrl.face_tracking:
                    gimbal_ctrl.stop_face_tracking()
                    gimbal_ctrl._rapid_align = False
                _latest_pose_persons.clear()
                _attn_result = {"has_face": False}
                _mp_face_result = {"success": False, "ear_avg": 0.3, "eye_open": True, "head_yaw": 0, "head_pitch": 0}
                if _startup_phase != -1:
                    _startup_phase = -1
                    _startup_ts = time.monotonic()
                    _face_capture_reset("sound_tracking_mode")
                if not _manual_active:
                    _update_sound_tracking_yaw()
                snapshot = build_state_snapshot()
                await ws_mgr.broadcast(snapshot)
                await asyncio.sleep(0.2)
                continue

            # ── Single-person auto search/track (only when AI mode active and sound tracking off) ──
            if _startup_phase < 3 and not _manual_active and not _sound_tracking:
                now_ts = time.monotonic()
                res = video_client.resolution if video_client else [1920, 1080]
                fw, fh = res[0], res[1]
                gs = gimbal_ctrl._state if gimbal_ctrl else None
                cur_yaw = gs.yaw_target if gs else 180
                ft = gimbal_ctrl

                face_target = _best_complete_face(0.45, int(fw), int(fh))
                person_target = _best_person_target(fw, fh, 0.42)

                face_locked = (ft and ft.face_tracking and getattr(ft, '_ft_locked', False))
                _set_tracking_debug(
                    mode="single",
                    startup_phase=_startup_phase,
                    manual_active=bool(_manual_active),
                    sound_tracking=bool(_sound_tracking),
                    resolution=[int(fw), int(fh)],
                    face_target=_face_debug_target(face_target),
                    person_target=_person_debug_target(person_target),
                    face_locked=bool(face_locked),
                    latest_persons=len(_latest_pose_persons),
                    yaw_target=round(float(gs.yaw_target), 2) if gs else None,
                    pitch_target=round(float(gs.pitch_target), 2) if gs else None,
                    rapid_align=bool(getattr(ft, "_rapid_align", False)) if ft else False,
                    ft_lock_cnt=int(getattr(ft, "_ft_lock_cnt", 0)) if ft else 0,
                    phase2_error=None,
                    phase2_cmd=None,
                    controller=None,
                    face_capture=dict(_face_capture_state),
                )

                # ── Phase -1: initial check, otherwise return to standby center ──
                if _startup_phase == -1:
                    if face_target:
                        _startup_phase = 3
                        if gimbal_ctrl:
                            gimbal_ctrl.start_face_tracking()
                            gimbal_ctrl._rapid_align = False
                        logger.info("🎯 Phase 3: 初始状态已识别完整人脸 ->持续追踪")
                    elif person_target:
                        _startup_phase = 2
                        _startup_ts = now_ts
                        if gimbal_ctrl:
                            gimbal_ctrl.start_face_tracking()
                            gimbal_ctrl._rapid_align = True
                        logger.info("Phase 2: initial person detected (conf=%.0f%%); moving toward predicted face",
                            person_target["conf"] * 100)
                    else:
                        if gimbal_ctrl:
                            gimbal_ctrl.ui_yaw_disabled(180)
                            gimbal_ctrl.ui_pitch_disabled(90)
                        if now_ts - _startup_ts > 1.5:
                            _startup_phase = 0
                            logger.info("🔄 未识别到->->->已回到待机位 yaw=180°, pitch=90°")
                        elif pose_frame_count % 10 == 0:
                            logger.info("🔄 Phase -1: 归中待机->(yaw=180°, pitch=90°)...")

                # ── Phase 0 ->1 ──
                elif _startup_phase == 0:
                    if face_target:
                        _startup_phase = 3
                        if gimbal_ctrl:
                            gimbal_ctrl.start_face_tracking()
                            gimbal_ctrl._rapid_align = False
                        logger.info("🎯 Phase 3: 搜索前已识别完整人脸 ->持续追踪")
                    elif person_target:
                        _startup_phase = 2
                        _startup_ts = now_ts
                        if gimbal_ctrl:
                            gimbal_ctrl.start_face_tracking()
                            gimbal_ctrl._rapid_align = True
                        logger.info("🎯 Phase 2: 搜索前已识别->conf=%.0f%%) ->靠近脸部方向",
                            person_target["conf"] * 100)
                    else:
                        _startup_phase = 1
                        _startup_ts = now_ts
                        _sweep_yaw = SWEEP_CENTER_YAW
                        _sweep_dir = -1
                        if gimbal_ctrl:
                            gimbal_ctrl.ui_yaw_disabled(SWEEP_CENTER_YAW)
                            gimbal_ctrl.start_face_tracking()
                            gimbal_ctrl._rapid_align = True
                        logger.info(
                            "Phase 1: sweep yaw %.0f..%.0f around %.0f, pitch unchanged",
                            SWEEP_CENTER_YAW - SWEEP_AMPLITUDE_DEG,
                            SWEEP_CENTER_YAW + SWEEP_AMPLITUDE_DEG,
                            SWEEP_CENTER_YAW,
                        )

                # ── Phase 1: search ──
                elif _startup_phase == 1:
                    # Priority 1: complete face ->Phase 3
                    if face_locked or face_target:
                        _startup_phase = 3
                        if ft:
                            ft._rapid_align = False; ft._ft_ema_yaw = None; ft._ft_ema_pitch = None
                        logger.info("🎯 Phase 3: 完整人脸已到->->持续追踪")
                    # Priority 2: highest-confidence person detected ->Phase 2
                    elif person_target is not None and now_ts - _startup_ts > 1.0:
                        _startup_phase = 2
                        _startup_ts = now_ts
                        logger.info("Phase 2: person detected (conf=%.0f%%); yaw/pitch moving toward predicted face",
                            person_target["conf"] * 100)
                    # Priority 3: no person ->yaw sweep, keep pitch unchanged
                    else:
                        sweep_min = SWEEP_CENTER_YAW - SWEEP_AMPLITUDE_DEG
                        sweep_max = SWEEP_CENTER_YAW + SWEEP_AMPLITUDE_DEG
                        _sweep_yaw += _sweep_dir * SWEEP_STEP_DEG
                        if _sweep_yaw >= sweep_max:
                            _sweep_yaw = sweep_max
                            _sweep_dir = -1
                        elif _sweep_yaw <= sweep_min:
                            _sweep_yaw = sweep_min
                            _sweep_dir = 1
                        if gimbal_ctrl:
                            gimbal_ctrl.ui_yaw_disabled(int(_sweep_yaw))
                        if pose_frame_count % 30 == 0:
                            logger.info("🎯 Phase 1: sweep yaw=%.0f° ->等人(conf->2%%)", _sweep_yaw)

                # ── Phase 2: center person ->find face ──
                elif _startup_phase == 2:
                    if face_locked or face_target:
                        _startup_phase = 3
                        if ft:
                            ft._rapid_align = False; ft._ft_ema_yaw = None; ft._ft_ema_pitch = None
                        logger.info("🎯 Phase 3: 完整人脸已锁->->持续追踪")
                    elif person_target is not None:
                        # Yaw: center highest-confidence person horizontally
                        error_x = person_target["cx"] - 0.5
                        phase2_yaw_cmd = None
                        phase2_pitch_cmd = None
                        if abs(error_x) > 0.03:
                            dyaw = -error_x * 60.0
                            dyaw = max(-12, min(12, dyaw))
                            if gimbal_ctrl:
                                phase2_yaw_cmd = int(max(1, min(345, cur_yaw + dyaw)))
                                gimbal_ctrl.ui_yaw_disabled(phase2_yaw_cmd)
                        # Pitch: move toward predicted face location from person bbox/pose
                        error_y = person_target["face_y"] - 0.45
                        if abs(error_y) > 0.03:
                            dpitch = error_y * 30.0
                            dpitch = max(-5, min(5, dpitch))
                            cp = gs.pitch_target if gs else 90
                            new_p = max(30, min(150, cp + dpitch))
                            if gimbal_ctrl and abs(dpitch) > 0.3:
                                phase2_pitch_cmd = int(new_p)
                                gimbal_ctrl.ui_pitch_disabled(phase2_pitch_cmd)
                        _set_tracking_debug(
                            phase2_error=[round(float(error_x), 4), round(float(error_y), 4)],
                            phase2_cmd={"yaw": phase2_yaw_cmd, "pitch": phase2_pitch_cmd},
                        )
                    else:
                        # Person lost ->back to sweep
                        _startup_phase = 1
                        _startup_ts = now_ts
                        logger.info("🎯 Phase 2->: 人丢-> 回到扫描")

                    if pose_frame_count % 10 == 0:
                        logger.info("🎯 Phase 2: yaw=%.0f° person_cx=%.2f face=%s",
                            cur_yaw, person_target["cx"] if person_target else 0,
                            "LOCKED" if face_locked else "searching")
                _set_tracking_debug(startup_phase_after=_startup_phase)
            # ── Face detection: FaceTrackerV2 (SCRFD + Kalman/ByteTrack) ──
            pose_frame_count += 1
            if video_client:
                jpeg = video_client.jpeg_bytes
                if jpeg:
                    loop = asyncio.get_event_loop()
                    tracked_faces = []
                    if _face_tracker and _face_tracker.available:
                        try:
                            from vision.pose_estimator import PersonPose, Keypoint
                            arr = np.frombuffer(jpeg, np.uint8)
                            frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
                            if frame is not None:
                                tracks = await loop.run_in_executor(
                                    None, _face_tracker.update, frame)
                                if tracks:
                                    persons = []
                                    for t in tracks:
                                        x1,y1,x2,y2 = t['bbox']
                                        cx,cy = t['face_center']
                                        kps = []
                                        lm5 = t.get('landmarks_5')
                                        if lm5 is not None and lm5.shape[0] >= 5:
                                            for idx, name in enumerate(['left_eye','right_eye','nose','left_mouth','right_mouth']):
                                                kps.append(Keypoint(x=float(lm5[idx,0]),y=float(lm5[idx,1]),conf=0.9,name=name))
                                        else:
                                            lm = t.get('landmarks_106')
                                            # Fallback only: prefer InsightFace's native 5-point kps when present.
                                            # The 106-point model index layout can vary across model packs.
                                            if lm is not None and lm.shape[0] >= 60:
                                                for idx, name in [(54,'nose'),(38,'left_eye'),(88,'right_eye'),(91,'left_mouth'),(100,'right_mouth')]:
                                                    if idx < lm.shape[0]:
                                                        kps.append(Keypoint(x=float(lm[idx,0]),y=float(lm[idx,1]),conf=0.9,name=name))
                                        pp = PersonPose(
                                            bbox=(x1,y1,x2,y2),conf=t['confidence'],
                                            keypoints=kps,face_center=(cx,cy),face_conf=t['confidence'])
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
                    # Fallback
                    if not tracked_faces:
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

            # ── Attention engine ──
            if _attention_engine and _latest_pose_persons:
                for p in _latest_pose_persons:
                    face_kps = {kp.name: (kp.x, kp.y) for kp in p.keypoints
                                if kp.name in ('left_eye','right_eye','nose','left_mouth','right_mouth')}
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
                        )
                        break
                else:
                    _attn_result = _attention_engine.update(None)
            else:
                _attn_result = {"has_face": False}

            # ── MediaPipe face + eye metrics (fine landmarks, throttled) ──
            if pose_frame_count % 2 == 0:
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
                            _mp_face_result = {"success": True, "ear_avg": round(float(mp_res.ear_avg), 3),
                                "eye_open": bool(mp_res.eye_open),
                                "landmarks5": [[round(float(x), 1), round(float(y), 1)]
                                               for x, y in np.asarray(mp_res.landmarks5)[:, :2]]
                                               if mp_res.landmarks5 is not None else [],
                                "landmarks_eye": [[round(float(mp_res.landmarks[i][0]),1), round(float(mp_res.landmarks[i][1]),1)] for i in [33,160,158,133,153,144,362,385,387,263,373,380]],
                                "landmarks_mesh": [[round(float(mp_res.landmarks[i][0]),1), round(float(mp_res.landmarks[i][1]),1)] for i in [10,152,234,454,0,17,61,291]]}
                            em = _eye_tracker.update(landmarks=mp_res.landmarks)
                            _eye_metrics = {"ear_avg": round(float(em.ear_avg), 3),
                                "blink_rate": float(em.blink_rate), "perclos": round(float(em.perclos), 3),
                                "focus_score": int(em.focus_score), "blink_count": int(em.blink_count)}
                    except Exception as e:
                        logger.warning(f"MediaPipe: {e}")

            # ── Emotion recognition (both models, same face crop) ──
            jpeg = video_client.jpeg_bytes if video_client else None
            landmarks = None
            if _latest_pose_persons:
                for p in _latest_pose_persons:
                    face_kps = {kp.name: (kp.x, kp.y) for kp in p.keypoints
                                if kp.name in ('left_eye','right_eye','nose','left_mouth','right_mouth')}
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

                    # Use tight face crop (better accuracy than full-frame warp)
                    crop_result = extract_face_crop(frame, landmarks, None)
                    img_for_emo = crop_result.crop if crop_result.crop is not None else None

                    if img_for_emo is not None:
                        # ── Frame counter (shared across both models) ──
                        if not hasattr(state_push_loop, '_emo_frame_cnt'):
                            state_push_loop._emo_frame_cnt = 0
                        state_push_loop._emo_frame_cnt += 1
                        fc = state_push_loop._emo_frame_cnt

                        # ── EmotiEffLib (8-class emotion): send raw max result to UI ──
                        raw_result = get_emotieff_adapter().predict(img_for_emo)
                        if raw_result and raw_result.get("emotion"):
                            raw_probs = {
                                str(k): float(v) for k, v in raw_result.get("probabilities", {}).items()
                            }
                            top_emo = max(raw_probs, key=raw_probs.get) if raw_probs else str(raw_result["emotion"])
                            top_conf = float(raw_probs.get(top_emo, raw_result.get("confidence", 0.0)))
                            _emotieff_result = {
                                "emotion": top_emo,
                                "confidence": round(float(top_conf), 4),
                                "probabilities": raw_probs,
                                "source": "emotiefflib_raw_max",
                            }
                            _emotion_result = _emotieff_result  # mirror for compatibility

                        if fc % 30 == 0:
                            cv2.imwrite("/tmp/debug_face_crop.jpg", img_for_emo)
                            logger.info("📸 debug_face_crop.jpg saved (frame #%d) emotion=%s", fc, _emotieff_result.get("emotion","?"))

            # ── LLM diary: trigger on emotion change ──
            if not hasattr(state_push_loop, '_last_llm_emo'):
                state_push_loop._last_llm_emo = None
            emo_name = _emotieff_result.get("emotion", "Neutral") if (_emotieff_result and _emotieff_result.get("emotion")) else "Neutral"
            emotion_changed = emo_name != state_push_loop._last_llm_emo
            attn_sc = int(_attn_result.get("score", 50)) if _attn_result.get("has_face") else 50
            if _llm_engine is None:
                try:
                    from vision.llm_reflect import get_llm
                    _llm_engine = get_llm()
                except: pass
            if _llm_engine and _llm_engine.loaded:
                loop = asyncio.get_event_loop()
                # Diary: trigger on emotion change
                if emotion_changed:
                    try:
                        text = await loop.run_in_executor(None, _llm_engine.diary, emo_name, attn_sc, "")
                        if text:
                            _llm_diary_entry = {"time": time.strftime("%H:%M"), "emotion": emo_name, "text": text, "editable": True}
                            _last_llm_diary_time = time.time()
                        state_push_loop._last_llm_emo = emo_name
                    except: pass
                # Quote every 5 min
                if not hasattr(state_push_loop, '_lq'): state_push_loop._lq = 0
                if time.time() - state_push_loop._lq > 300:
                    state_push_loop._lq = time.time()
                    try:
                        lvl = "专注" if attn_sc >= 70 else "微澜" if attn_sc >= 40 else "飘远"
                        _llm_quote_text = await loop.run_in_executor(None, _llm_engine.quote, emo_name, lvl)
                    except: pass

            # Face tracking display state.
            if gimbal_ctrl and gimbal_ctrl.face_tracking:
                res = video_client.resolution if video_client else [1920, 1080]
                fw, fh = res[0], res[1]

                if not hasattr(gimbal_ctrl, '_ft_lock_cnt'):
                    gimbal_ctrl._ft_lock_cnt = 0
                    gimbal_ctrl._ft_locked = False
                    gimbal_ctrl._ft_last_center = None

                best_face = _best_complete_face(0.45, int(fw), int(fh))
                capture = _update_face_capture_state(best_face, int(fw), int(fh))

                track_target = None
                track_conf = 0.0
                track_source = "none"
                if best_face:
                    x1, y1, x2, y2 = best_face.bbox
                    fw_px, fh_px = x2 - x1, y2 - y1
                    if fw_px >= 40 and fh_px >= 40:
                        track_target = best_face.face_center
                        mp_target = _tracking_point_from_landmarks5(_mp_landmarks5)
                        if mp_target is not None:
                            mx, my = mp_target
                            if x1 - 80 <= mx <= x2 + 80 and y1 - 80 <= my <= y2 + 80:
                                track_target = mp_target
                                track_source = "mediapipe_eye_nose"
                            else:
                                track_source = "face_center_mp_outside"
                        else:
                            track_source = "face_center"
                        track_conf = float(best_face.face_conf)

                if capture.get("state") == "IO_PRESENT" and track_target is not None:
                    gimbal_ctrl._ft_lock_cnt = max(2, int(capture.get("candidate_count", 0)))
                    gimbal_ctrl._ft_last_center = track_target
                    if not gimbal_ctrl._ft_locked:
                        gimbal_ctrl._ft_locked = True
                        gimbal_ctrl._rapid_align = False
                        gimbal_ctrl._ft_ema_yaw = None
                        gimbal_ctrl._ft_ema_pitch = None
                        logger.info("🎯 Face lock-in: tracking face (conf=%.2f size=%d×%d px)",
                            track_conf,
                            int(fw_px), int(fh_px))
                    cmd = None
                    ctrl_debug = {
                        "active": bool(cmd),
                        "reason": cmd.reason if cmd else "no_command",
                        "target": [round(float(track_target[0]), 1), round(float(track_target[1]), 1)],
                        "cmd": {
                            "yaw": round(float(cmd.yaw), 2) if cmd and cmd.yaw is not None else None,
                            "pitch": round(float(cmd.pitch), 2) if cmd and cmd.pitch is not None else None,
                        },
                        "fsm_state": "unavailable",
                    }
                    _set_tracking_debug(
                        face_capture=capture,
                        track_target=[round(float(track_target[0]), 1), round(float(track_target[1]), 1)],
                        track_source=track_source,
                        track_conf=round(float(track_conf), 3),
                        ft_locked=bool(gimbal_ctrl._ft_locked),
                        ft_lock_cnt=int(gimbal_ctrl._ft_lock_cnt),
                        controller=ctrl_debug,
                    )
                else:
                    gimbal_ctrl._ft_lock_cnt = 0
                    if capture.get("state") == "IO_MISSING":
                        if gimbal_ctrl._ft_locked:
                            logger.info("🎯 Face temporarily lost: predictive local search")
                        gimbal_ctrl._ft_locked = False
                        gimbal_ctrl._ft_last_center = None
                        reacquire_debug = _predictive_reacquire_step(gimbal_ctrl, int(fw), int(fh))
                    else:
                        reacquire_debug = None
                        if gimbal_ctrl._ft_locked:
                            logger.info("🎯 Face lock lost: returning to search")
                        gimbal_ctrl._ft_locked = False
                        gimbal_ctrl._ft_last_center = None

                    if capture.get("state") == "IO_IDLE" and _startup_phase == 3 and not _manual_active:
                        _startup_phase = 1
                        _startup_ts = time.monotonic()
                        gimbal_ctrl._rapid_align = True

                    if hasattr(gimbal_ctrl, '_ft_ema_yaw') and gimbal_ctrl._ft_ema_yaw is not None:
                        gimbal_ctrl._ft_ema_yaw *= 0.55
                        gimbal_ctrl._ft_ema_pitch *= 0.55

                    _set_tracking_debug(
                        face_capture=capture,
                        track_target=None,
                        track_source=track_source,
                        track_conf=round(float(track_conf), 3),
                        ft_locked=bool(gimbal_ctrl._ft_locked),
                        ft_lock_cnt=int(gimbal_ctrl._ft_lock_cnt),
                        reacquire=reacquire_debug,
                    )

            snapshot = build_state_snapshot()
            await ws_mgr.broadcast(snapshot)
        except Exception as e:
            logger.error("Push error: %s", str(e)[:120])
            import traceback
            logger.error(traceback.format_exc()[-200:])
        await asyncio.sleep(0.2)  # ~5 Hz


# ── WebSocket Endpoint ───────────────────────────────────

@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    global _sound_tracking, _tracking_mode, _conversation_recording_requested
    await ws_mgr.connect(ws)
    try:
        # Send initial snapshot immediately
        await ws_mgr.send_to(ws, build_state_snapshot())

        while True:
            msg = await ws.receive_text()

            if msg == "gimbal_sleep":
                await ws_mgr.send_to(ws, {"success": False, "reason": "fastapi_ui_only"})
            elif msg == "gimbal_standby":
                await ws_mgr.send_to(ws, {"success": False, "reason": "fastapi_ui_only"})
            elif msg == "ui_emergency_disabled":
                await ws_mgr.send_to(ws, {"success": False, "reason": "fastapi_ui_only"})
            elif msg.startswith("ui_yaw_disabled:"):
                try: await ws_mgr.send_to(ws, {"success": False, "reason": "fastapi_ui_only"})
                except ValueError: pass
            elif msg.startswith("ui_pitch_disabled:"):
                try: await ws_mgr.send_to(ws, {"success": False, "reason": "fastapi_ui_only"})
                except ValueError: pass
            elif msg.startswith("ui_speed_disabled:"):
                try: await ws_mgr.send_to(ws, {"success": False, "reason": "fastapi_ui_only"})
                except ValueError: pass
            elif msg == "request_state":
                await ws_mgr.send_to(ws, build_state_snapshot())
            # ── Face tracking ──
            elif msg == "face_track_start":
                if gimbal_ctrl: gimbal_ctrl.start_face_tracking()
            elif msg == "face_track_stop":
                if gimbal_ctrl: gimbal_ctrl.stop_face_tracking()
            elif msg == "face_track_toggle":
                if gimbal_ctrl:
                    if gimbal_ctrl.face_tracking: gimbal_ctrl.stop_face_tracking()
                    else: gimbal_ctrl.start_face_tracking()
            # ── Sound tracking ──
            elif msg == "sound_track_start":
                _sound_tracking = True
                _tracking_mode = "multi"
                _conversation_recording_requested = False
                _ensure_doa_reader()
                if gimbal_ctrl:
                    gimbal_ctrl._state.sound_tracking = True
                    gimbal_ctrl.stop_face_tracking()
                logger.info("🎤 Sound tracking ENABLED")
            elif msg == "sound_track_stop":
                _sound_tracking = False
                _tracking_mode = "single"
                _conversation_recording_requested = False
                _stop_conversation_recording(finalize=True)
                if gimbal_ctrl:
                    gimbal_ctrl._state.sound_tracking = False
                logger.info("🎤 Sound tracking DISABLED")
            elif msg == "sound_track_toggle":
                _sound_tracking = not _sound_tracking
                _tracking_mode = "multi" if _sound_tracking else "single"
                _conversation_recording_requested = False
                if _sound_tracking:
                    _ensure_doa_reader()
                else:
                    _stop_conversation_recording(finalize=True)
                if gimbal_ctrl:
                    gimbal_ctrl._state.sound_tracking = _sound_tracking
                    if _sound_tracking:
                        gimbal_ctrl.stop_face_tracking()
                logger.info("🎤 Sound tracking: %s", "ON" if _sound_tracking else "OFF")
            else:
                logger.debug("Unknown WS message: %s", msg[:40])
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.debug("WS error: %s", e)
    finally:
        await ws_mgr.disconnect(ws)


# ── MJPEG Video Feed ─────────────────────────────────────

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


# ── REST API Endpoints ───────────────────────────────────

@app.get("/api/state")
async def api_state():
    return build_state_snapshot()


@app.get("/api/gimbal/state")
async def api_gimbal_state():
    gs = gimbal_ctrl.get_state() if gimbal_ctrl else GimbalStateData()
    return {
        "connected": gs.connected,
        "dry_run": bool(getattr(gimbal_ctrl, "_dry_run", True)) if gimbal_ctrl else True,
        "sio_connected": bool(getattr(gimbal_ctrl, "_sio_connected", False)) if gimbal_ctrl else False,
        "yaw_angle": gs.yaw_angle, "yaw_target": gs.yaw_target,
        "pitch_angle": gs.pitch_angle, "pitch_target": gs.pitch_target,
        "speed": gs.speed, "tracking": gs.tracking,
    }


@app.post("/api/gimbal/yaw")
async def ui_yaw_disabled(payload: dict):
    return {"success": False, "reason": "fastapi_ui_only"}


@app.post("/api/gimbal/pitch")
async def ui_pitch_disabled(payload: dict):
    return {"success": False, "reason": "fastapi_ui_only"}


@app.post("/api/gimbal/speed")
async def ui_speed_disabled(payload: dict):
    return {"success": False, "reason": "fastapi_ui_only"}


@app.post("/api/gimbal/sleep")
async def gimbal_sleep():
    return {"success": False, "reason": "fastapi_ui_only"}


@app.post("/api/gimbal/standby")
async def gimbal_standby():
    return {"success": False, "reason": "fastapi_ui_only"}


@app.post("/api/gimbal/stop")
async def gimbal_stop():
    return {"success": False, "reason": "fastapi_ui_only"}


@app.post("/api/gimbal/calibrate")
async def gimbal_calibrate():
    return {"success": False, "message": "Direct gimbal calibration is disabled by single control plane"}


@app.get("/api/face_track/state")
async def api_face_track_state():
    return {
        "active": gimbal_ctrl.face_tracking if gimbal_ctrl else False,
        "persons": _build_pose_data(),
    }


@app.post("/api/face_track/start")
async def api_face_track_start():
    if gimbal_ctrl: gimbal_ctrl.start_face_tracking()
    return {"success": True, "active": gimbal_ctrl.face_tracking if gimbal_ctrl else False}


@app.post("/api/face_track/stop")
async def api_face_track_stop():
    if gimbal_ctrl: gimbal_ctrl.stop_face_tracking()
    return {"success": True, "active": False}


@app.get("/api/single_track/state")
async def api_single_track_state():
    return {
        "success": True,
        "mode": _tracking_mode,
        "active": bool(gimbal_ctrl.face_tracking) if gimbal_ctrl else False,
        "face_lock": {
            "locked": getattr(gimbal_ctrl, "_ft_locked", False) if gimbal_ctrl else False,
            "lock_cnt": getattr(gimbal_ctrl, "_ft_lock_cnt", 0) if gimbal_ctrl else 0,
        },
        "attention": _attn_result,
        "emotion": _emotieff_result or _emotion_result,
        "tracking_debug": _tracking_debug,
    }


@app.post("/api/single_track/start")
async def api_single_track_start(payload: dict = None):
    global _tracking_mode, _sound_tracking, _conversation_recording_requested
    _tracking_mode = "single"
    _sound_tracking = False
    _conversation_recording_requested = False
    _stop_conversation_recording(finalize=True)
    if gimbal_ctrl:
        gimbal_ctrl._state.sound_tracking = False
        gimbal_ctrl.start_face_tracking()
        gimbal_ctrl._rapid_align = True
        gimbal_ctrl._ft_locked = False
        gimbal_ctrl._ft_lock_cnt = 0
        gimbal_ctrl._ft_last_center = None
        gimbal_ctrl._ft_ema_yaw = None
        gimbal_ctrl._ft_ema_pitch = None
        gimbal_ctrl.ui_speed_disabled(int((payload or {}).get("speed", 360)))
    logger.info("👤 Single tracking started via UI")
    return await api_single_track_state()


@app.post("/api/single_track/stop")
async def api_single_track_stop():
    if gimbal_ctrl:
        gimbal_ctrl.stop_face_tracking()
        gimbal_ctrl._rapid_align = False
    logger.info("👤 Single tracking stopped via UI")
    return await api_single_track_state()


@app.get("/api/multi_track/state")
async def api_multi_track_state():
    return {
        "success": True,
        "mode": _tracking_mode,
        "active": bool(_sound_tracking),
        "doa_available": _doa_reader is not None,
        "doa": _doa_status(),
        "sound_follow": _sound_follow_state,
        "conversation": _conversation_state(),
        "gimbal_mode": gimbal_state.mode_name,
    }


@app.post("/api/multi_track/start")
async def api_multi_track_start(payload: dict = None):
    global _tracking_mode, _sound_tracking, _conversation_recording_requested
    payload = payload or {}
    _tracking_mode = "multi"
    _sound_tracking = True
    _conversation_recording_requested = bool(payload.get("save_audio", False))
    _resume_ai_gimbal_mode("multi_track_start")
    doa_ok = _ensure_doa_reader()
    recording_ok = _start_conversation_recording() if _conversation_recording_requested else True
    if gimbal_ctrl:
        gimbal_ctrl._state.sound_tracking = True
        gimbal_ctrl.stop_face_tracking()
        gimbal_ctrl._rapid_align = False
    logger.info(
        "🎤 Multi DOA tracking started (audio_recording=%s)",
        _conversation_recording_requested,
    )
    state = await api_multi_track_state()
    state["success"] = bool(doa_ok)
    state["recording_success"] = bool(recording_ok)
    return state


@app.post("/api/multi_track/stop")
async def api_multi_track_stop(payload: dict = None):
    global _sound_tracking, _conversation_recording_requested
    payload = payload or {}
    _sound_tracking = False
    _conversation_recording_requested = False
    _stop_conversation_recording(finalize=bool(payload.get("finalize", True)))
    if gimbal_ctrl:
        gimbal_ctrl._state.sound_tracking = False
    _sound_follow_state.update({
        "active": False,
        "has_speech": False,
        "target_yaw": None,
        "reason": "stopped",
    })
    logger.info("🎤 Multi tracking/recording stopped via UI")
    return await api_multi_track_state()


# ── Sound Tracking API ──

@app.get("/api/sound_track/state")
async def api_sound_track_state():
    global _sound_tracking
    return {
        "available": _doa_reader is not None,
        "source": _doa_status().get("source"),
        "active": bool(_sound_tracking),
        "doa_deg": round(_doa_reader.doa, 1) if _doa_reader else None,
        "has_speech": _doa_reader.has_speech if _doa_reader else False,
    }

@app.post("/api/sound_track/start")
async def api_sound_track_start():
    global _sound_tracking, _conversation_recording_requested
    _sound_tracking = True
    _conversation_recording_requested = False
    _resume_ai_gimbal_mode("sound_track_start")
    _ensure_doa_reader()
    if gimbal_ctrl:
        gimbal_ctrl._state.sound_tracking = True
        gimbal_ctrl.stop_face_tracking()
    logger.info("🎤 Sound tracking started via API")
    return {"success": _doa_reader is not None, "active": True, "available": _doa_reader is not None}

@app.post("/api/sound_track/stop")
async def api_sound_track_stop():
    global _sound_tracking, _conversation_recording_requested
    _sound_tracking = False
    _conversation_recording_requested = False
    _stop_conversation_recording(finalize=True)
    if gimbal_ctrl:
        gimbal_ctrl._state.sound_tracking = False
    logger.info("🎤 Sound tracking stopped via API")
    return {"success": True, "active": False}

@app.post("/api/sound_track/toggle")
async def api_sound_track_toggle():
    global _sound_tracking, _conversation_recording_requested
    _sound_tracking = not _sound_tracking
    if _sound_tracking:
        _conversation_recording_requested = False
        _resume_ai_gimbal_mode("sound_track_toggle")
        _ensure_doa_reader()
    else:
        _conversation_recording_requested = False
        _stop_conversation_recording(finalize=True)
    if gimbal_ctrl:
        gimbal_ctrl._state.sound_tracking = _sound_tracking
        if _sound_tracking:
            gimbal_ctrl.stop_face_tracking()
    return {"success": (not _sound_tracking) or (_doa_reader is not None), "active": _sound_tracking, "available": _doa_reader is not None}


@app.get("/api/tracking_mode")
async def api_tracking_mode_state():
    return {
        "mode": _tracking_mode,
        "sound_tracking": _sound_tracking,
    }


@app.post("/api/tracking_mode")
async def api_tracking_mode_set(payload: dict = None):
    global _tracking_mode, _sound_tracking, _conversation_recording_requested
    payload = payload or {}
    mode = payload.get("mode", "single")
    _tracking_mode = "multi" if mode == "multi" else "single"
    _sound_tracking = (_tracking_mode == "multi")
    _conversation_recording_requested = bool(payload.get("save_audio", False)) if _sound_tracking else False
    if _sound_tracking:
        _resume_ai_gimbal_mode("tracking_mode_multi")
    if gimbal_ctrl:
        gimbal_ctrl._state.sound_tracking = _sound_tracking
        if _sound_tracking:
            gimbal_ctrl.stop_face_tracking()
    if _sound_tracking:
        _ensure_doa_reader()
        if _conversation_recording_requested:
            _start_conversation_recording()
    else:
        _stop_conversation_recording(finalize=True)
    logger.info("🎛->Tracking mode: %s (sound_tracking=%s)", _tracking_mode, _sound_tracking)
    return {
        "success": (not _sound_tracking) or (_doa_reader is not None),
        "mode": _tracking_mode,
        "sound_tracking": _sound_tracking,
        "doa_available": _doa_reader is not None,
        "gimbal_mode": gimbal_state.mode_name,
        "sound_follow": _sound_follow_state,
    }


# ── Conversation Recording API ──

@app.get("/api/conversation/state")
async def api_conversation_state():
    return _conversation_state()


@app.get("/api/conversation/debug")
async def api_conversation_debug():
    return _conversation_debug_state()


@app.post("/api/conversation/start")
async def api_conversation_start(payload: dict = None):
    global _tracking_mode, _sound_tracking, _conversation_recording_requested
    payload = payload or {}
    _tracking_mode = "multi"
    _sound_tracking = True
    _conversation_recording_requested = bool(payload.get("save_audio", False))
    _resume_ai_gimbal_mode("conversation_start")
    _ensure_doa_reader()
    doa_ok = _ensure_doa_reader()
    ok = _start_conversation_recording() if _conversation_recording_requested else True
    if gimbal_ctrl:
        gimbal_ctrl._state.sound_tracking = True
        gimbal_ctrl.stop_face_tracking()
    return {"success": bool(doa_ok), "recording_success": bool(ok), "state": _conversation_state()}


@app.post("/api/conversation/stop")
async def api_conversation_stop(payload: dict = None):
    global _conversation_recording_requested
    payload = payload or {}
    _conversation_recording_requested = False
    _stop_conversation_recording(finalize=bool(payload.get("finalize", True)))
    return {"success": True, "state": _conversation_state()}


@app.post("/api/conversation/save")
async def api_conversation_save(payload: dict = None):
    # Segments and timeline are written incrementally; this endpoint is a stable
    # frontend action that returns the current persisted session metadata.
    return {"success": True, "state": _conversation_state()}

# ── Face Auto-Alignment ──

@app.post("/api/auto_align")
async def api_auto_align():
    """
    Yaw↔Pitch 交替对准: 先调 Yaw 让人居中, 再调 Pitch 让脸居中, 回头->Yaw, 循环直到两者都 OK.
    """
    FOV = 70
    YAW_OK, PITCH_OK = 0.04, 0.05  # 更严格的对准�?>(25px/32px)

    def _yaw(y):
        y = max(1, min(345, int(y) % 360))
        logger.info(f"  ->YAW {y}°")
        if gimbal_ctrl and gimbal_ctrl.connected: gimbal_ctrl.ui_yaw_disabled(y)

    def _pitch(p):
        p = max(1, min(175, int(p)))
        logger.info(f"  ->PITCH {p}°")
        if gimbal_ctrl and gimbal_ctrl.connected: gimbal_ctrl.ui_pitch_disabled(p)

    def _jpeg(): return video_client.jpeg_bytes if video_client else None

    # ── 检测缓-> 连续2帧确->──
    _last_cx_cache = [None, None]  # 2帧缓冲区
    _last_cy_cache = [None, None]

    def _get_target_cx():
        """返回人体水平中心 cx(0-1). 连续2帧都无才判None."""
        boxes = video_client.boxes if video_client else []
        # Get actual frame width from video resolution
        res = video_client.resolution if video_client else [1920, 1080]
        fw = res[0] if res[0] > 0 else 1920
        cx = None
        # 设备 YOLO person (�?>2%, 面积->.5%)
        for b in boxes:
            if len(b) < 6: continue
            if int(b[5]) != 0: continue
            conf = b[4]/100.0 if b[4] > 1 else float(b[4])
            if conf < 0.42: continue
            cx_b, cy_b = float(b[0]), float(b[1])
            bw, bh = float(b[2]), float(b[3])
            if bw*bh/(fw*fw) < 0.015: continue
            logger.info(f"  body: conf={conf:.0%} cx={cx_b/fw:.2f} {bw:.0f}x{bh:.0f}")
            cx = cx_b / fw
            break
        # fallback: 肩膀
        if cx is None:
            for p in _latest_pose_persons:
                s = [kp for kp in p.keypoints if kp.name in ("left_shoulder","right_shoulder") and kp.conf > 0.35]
                if len(s) >= 1:
                    cx = sum(kp.x for kp in s) / len(s) / fw
                    break
        _last_cx_cache.pop(0); _last_cx_cache.append(cx)
        return cx if any(v is not None for v in _last_cx_cache) else None

    def _get_face_cy():
        """返回人脸垂直中心 cy(0-1). 连续2帧都无才判None."""
        jpeg = _jpeg()
        cy = None
        if jpeg:
            import cv2, numpy as np
            arr = np.frombuffer(jpeg, dtype=np.uint8)
            img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
            if img is not None:
                h, w = img.shape[:2]
                # YuNet: 阈值降->.35
                try:
                    yunet = cv2.FaceDetectorYN_create("models/face_detection_yunet.onnx","",(w,h),0.35,0.3,5000)
                    _, faces = yunet.detect(img)
                except: faces = None
                if faces is not None and len(faces)>0:
                    best = max(faces, key=lambda f: f[14] if len(f)>14 else 0)
                    if float(best[14]) >= 0.35:
                        cy = (float(best[1]) + float(best[3])/2) / h
                # bbox top
                if cy is None:
                    boxes = video_client.boxes if video_client else []
                    for b in boxes:
                        if len(b) < 6: continue
                        if int(b[5]) != 0: continue
                        conf = b[4]/100.0 if b[4] > 1 else float(b[4])
                        if conf < 0.42: continue
                        cy_b2, bh_v = float(b[1]), float(b[3])
                        if bh_v > 40:
                            cy = (cy_b2 - bh_v * 0.3) / h
                            break
        _last_cy_cache.pop(0); _last_cy_cache.append(cy)
        return cy if any(v is not None for v in _last_cy_cache) else None

    # ── �?>+ 等检测管线就->──
    saved = gimbal_ctrl._state.speed if gimbal_ctrl else 360
    if gimbal_ctrl and gimbal_ctrl.connected: gimbal_ctrl.ui_speed_disabled(60)
    await asyncio.sleep(1.5)

    yaw = gimbal_ctrl._state.yaw_target if gimbal_ctrl else 180
    pitch = gimbal_ctrl._state.pitch_target if gimbal_ctrl else 90
    steps = []
    yaw_aligned = pitch_aligned = False

    # ── 交替循环: Yaw ->Pitch ->->Yaw (最->12 -> ──
    no_target_count = 0
    for round_num in range(12):
        if no_target_count >= 3:
            steps.append({"r":"abort","reason":"no_target_3x"}); break

        if not yaw_aligned:
            cx = _get_target_cx()
            if cx is not None:
                no_target_count = 0  # 找到目标, 重置
                rx = cx - 0.5
                if abs(rx) < YAW_OK:
                    yaw_aligned = True
                    steps.append({"r":round_num+1,"axis":"yaw","result":"aligned","rx":round(rx,3)})
                else:
                    dy = -rx * FOV * (0.5 if round_num < 2 else 0.7 if round_num < 4 else 0.6)
                    dy = max(-20, min(20, dy))  # gentler first step, prevent overshoot
                    yaw = max(1, min(345, (yaw + int(dy)) % 360))
                    _yaw(yaw)
                    steps.append({"r":round_num+1,"axis":"yaw","rx":round(rx,3),"dy":round(dy,1)})
                    await asyncio.sleep(1.0)  # reduced from 1.5s
                    continue  # 调了 Yaw, 下一轮先->Yaw 再调 Pitch
            else:
                steps.append({"r":round_num+1,"axis":"yaw","result":"no_target"})
                no_target_count += 1
                await asyncio.sleep(0.5)

        elif not pitch_aligned:
            cy = _get_face_cy()
            if cy is not None:
                no_target_count = 0  # 找到-> 重置
                ry = cy - 0.5
                if abs(ry) < PITCH_OK:
                    pitch_aligned = True
                    steps.append({"r":round_num+1,"axis":"pitch","result":"aligned","ry":round(ry,3)})
                else:
                    dp = ry * FOV * (0.5 if round_num < 2 else 0.7 if round_num < 4 else 0.6)
                    pitch = max(1, min(175, pitch + int(dp)))
                    _pitch(pitch)
                    steps.append({"r":round_num+1,"axis":"pitch","ry":round(ry,3),"dp":round(dp,1)})
                    await asyncio.sleep(1.0)  # reduced from 1.5s
                    # Pitch 调完 ->重验 Yaw (因为 Pitch 变化会影响水->
                    yaw_aligned = False
                    continue
            else:
                steps.append({"r":round_num+1,"axis":"pitch","result":"no_face"})
                no_target_count += 1
                await asyncio.sleep(0.5)

        if yaw_aligned and pitch_aligned:
            break

    # ── 搜索（都没找到时, 从原始位置左右交替扫）──
    if not yaw_aligned and not pitch_aligned:
        orig_yaw = gimbal_ctrl._state.yaw_target if gimbal_ctrl else 180
        for deg in [15, -15, 30, -30, 45, -45]:
            target_yaw = max(1, min(345, (orig_yaw + deg) % 360))
            _yaw(target_yaw)
            await asyncio.sleep(2.0)
            cx = _get_target_cx()
            if cx:
                rx = cx - 0.5
                final_yaw = max(1, min(345, (target_yaw + int(-rx * FOV * 0.5)) % 360))
                _yaw(final_yaw)
                steps.append({"r":"search","found":True,"deg":deg}); break
            steps.append({"r":"search","deg":deg})
        else:
            _yaw(180); _pitch(90); steps.append({"r":"search","result":"back_to_center"})

    # ── 两个轴都 OK 才算对准成功 ->auto face tracking ──
    ok = yaw_aligned or pitch_aligned  # yaw对准就开追踪
    if ok and gimbal_ctrl and gimbal_ctrl.connected:
        gimbal_ctrl.start_face_tracking()
        steps.append({"r":"done","face_tracking":"on"})

    if gimbal_ctrl and gimbal_ctrl.connected: gimbal_ctrl.ui_speed_disabled(saved)
    logger.info(f"=== AUTO_ALIGN: {len(steps)} steps, yaw_ok={yaw_aligned}, pitch_ok={pitch_aligned} ===")
    return {"success": ok, "yaw_aligned": yaw_aligned, "pitch_aligned": pitch_aligned, "steps": steps}


_last_snapshot = None  # cache last good frame

@app.get("/api/snapshot")
async def snapshot():
    """Return single JPEG frame. Uses _jpeg_bytes directly (always has last frame)."""
    from fastapi.responses import Response
    jpeg = video_client._jpeg_bytes if video_client else None
    if jpeg:
        return Response(content=jpeg, media_type="image/jpeg")
    return Response(status_code=204)


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


# ── Emotion debug (using EmotiEffLib now, see /api/state) ──


@app.post("/api/reflect")
async def api_llm_reflect(payload: dict = None):
    """LLM reflection: diary or quote."""
    global _llm_engine
    if _llm_engine is None:
        from vision.llm_reflect import get_llm
        _llm_engine = get_llm()
    if not _llm_engine.loaded: _llm_engine._load()
    if not _llm_engine.loaded:
        return {"error": "LLM not loaded"}

    if not payload: payload = {}
    mode = payload.get("mode", "diary")
    emotion = payload.get("emotion", "Neutral")
    attn = payload.get("attention", 50)
    prev = payload.get("prev_emotion", "")

    if mode == "diary":
        text = _llm_engine.diary(emotion, attn, prev)
    elif mode == "report":
        text = _llm_engine.report(
            payload.get("total_min", 0), payload.get("focused_pct", 0),
            emotion, attn,
        )
    else:
        text = _llm_engine.quote(emotion, "专注" if attn >= 70 else "微澜" if attn >= 40 else "飘远")

    return {"text": text, "time": round(_llm_engine._last_time, 2)}


# ── DeepSeek API client ──
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
    if stripped[-1] in "。！�?…~”」』）)":
        return False
    dangling = (
        "不用", "不是", "因为", "所�?, "但是", "不过", "如果", "虽然", "而且",
        "像是", "那种", "有点", "可以", "可能", "也许", "看起�?, "听起�?,
        "我猜", "小屿", "就是", "其实", "只是", "还是", "然后", "还有",
    )
    return any(stripped.endswith(w) for w in dangling) or stripped[-1] in "，、：�?


@app.post("/api/chat")
async def api_chat(payload: dict = None):
    """Chat endpoint: uses DeepSeek API with local template fallback."""
    global _llm_engine
    if not payload: payload = {}
    msg = payload.get("message", "")
    emotion = payload.get("emotion", "Neutral")
    user_name = payload.get("user_name", "")
    history = payload.get("history", [])
    context = payload.get("context", "")
    daily_state = payload.get("daily_state", {}) or {}
    memory = payload.get("memory", "") or ""
    nick = user_name or "用户"

    # Build system prompt
    system_prompt = (
        f"你是“小屿”，{nick}的情绪日记伙伴。你必须自称“小屿”，称呼用户为“{nick}”�?
        f"用户当前情绪标签：{emotion}�?
        "你会结合日记、历史记忆、默认情绪检测和专注度表现来回应，但不要机械复述数据�?
        "回应结构要自然：先接住用户刚说的具体感受，再用“我�?也许/听起来”做一个轻观察，最后问一个容易继续回答的问题�?
        "语气温柔、自然、亲近，不写报告，不列清单，不说教，不做医学诊断，不命令用户�?
        "如果用户只是继续聊天，就延续上一轮内容，不要重复第一次日记回复�?
        "回复控制�?0�?20字�?
    )

    # Build messages for DeepSeek
    messages = [{"role": "system", "content": system_prompt}]
    if daily_state or context or memory:
        messages.append({
            "role": "system",
            "content": (
                "可用上下文："
                f"\n- 当日默认状态：{daily_state}"
                f"\n- 历史记忆摘要：{memory[:1000]}"
                f"\n- 本次场景：{context[:1000]}"
            )
        })
    # Add recent history (last 6 exchanges)
    for h in history[-6:]:
        role = "assistant" if h.get("role") == "ai" else "user"
        messages.append({"role": role, "content": h.get("text", "")[:200]})
    messages.append({"role": "user", "content": msg[:500]})

    # ── Try DeepSeek first ──
    reply = await _deepseek_chat(messages)
    source = "deepseek"
    if reply and _reply_looks_incomplete(reply):
        logger.warning("DeepSeek reply looked incomplete; retrying once: %s", reply[:80])
        retry_messages = messages + [
            {"role": "assistant", "content": reply},
            {
                "role": "user",
                "content": (
                    "上一条回复像是被截断了。请不要续写半句话，"
                    "请重新给出一条完整、自然�?0�?20字的小屿回复�?
                    "结尾必须是完整句子�?
                ),
            },
        ]
        retry_reply = await _deepseek_chat(retry_messages, max_tokens=DEEPSEEK_MAX_TOKENS)
        if retry_reply:
            reply = retry_reply
            source = "deepseek_retry"

    # ── Fallback to local template reflection engine ──
    if not reply:
        logger.info("DeepSeek unavailable ->trying local template reflection...")
        if _llm_engine is None:
            from vision.llm_reflect import get_llm
            _llm_engine = get_llm()
        if not _llm_engine.loaded:
            _llm_engine._load()
        if _llm_engine.loaded:
            # Load user profile for template reflection
            profile = {}
            try:
                import json, os
                kb_path = f"/tmp/xinyu_profile_{user_name}.json" if user_name else None
                if kb_path and os.path.exists(kb_path):
                    with open(kb_path) as f:
                        profile = json.load(f)
            except Exception:
                pass
            reply = _llm_engine.respond_to_user(msg, emotion, user_name, history, profile, context)
            source = "template"

    # ── Fallback: built-in templates ──
    if not reply:
        source = "fallback"
        templates = {
            "Happiness": [f"小屿看见{nick}的快乐了，这份明亮值得被好好收起来�?, f"小屿也替{nick}高兴。是什么让这份开心变得这么具体呢�?],
            "Sadness": [f"小屿在这里陪着{nick}。低落不用急着赶走，先让它有个地方放下�?, f"谢谢{nick}愿意说出来。小屿在听�?],
            "Anger": [f"小屿知道这股不舒服是真实的。{nick}可以先把它写出来，再慢慢决定怎么回应�?],
            "Fear": [f"害怕不说明{nick}不勇敢。小屿陪你先看清最担心的那一小块�?],
            "Neutral": [f"平静里也有微光。小屿谢谢{nick}把这一刻交给我�?],
            "Surprise": [f"小屿也被这个转折轻轻碰了一下。{nick}愿意多说说它带来的感觉吗�?],
            "Disgust": [f"小屿听见了那份抗拒。让{nick}不舒服的东西，确实可以先保持一点距离�?],
            "Contempt": [f"小屿猜你是在保护自己的边界。保持距离，有时候也是一种清醒�?],
        }
        opts = templates.get(emotion, templates["Neutral"])
        reply = opts[hash(msg) % len(opts)]

    logger.info("💬 Chat reply (source=%s, len=%d): %s", source, len(reply), reply[:60])

    # Update knowledge base
    try:
        import json, os
        profile = {}
        kb_path = f"/tmp/xinyu_profile_{user_name}.json" if user_name else None
        if kb_path and os.path.exists(kb_path):
            with open(kb_path) as f:
                profile = json.load(f)
        if not profile.get("interactions"):
            profile["interactions"] = []
        profile["interactions"].append({
            "emotion": emotion, "user_msg": msg, "ai_reply": reply,
            "ts": __import__('time').time()
        })
        profile["interactions"] = profile["interactions"][-200:]
        facts = profile.get("facts", [])
        if any(w in msg for w in ["考试", "成绩", "分数", "学习"]):
            facts.append("关注学业成绩")
        if any(w in msg for w in ["工作","上班","加班","同事"]):
            facts.append("职场人士")
        if any(w in msg for w in ["�?, "�?, "疲惫", "没睡"]):
            facts.append("近期疲惫")
        profile["facts"] = list(set(facts))[-10:]
        if user_name:
            with open(f"/tmp/xinyu_profile_{user_name}.json", "w") as f:
                json.dump(profile, f, ensure_ascii=False)
    except Exception:
        pass

    return {"reply": reply, "source": source}


@app.get("/api/chat/status")
async def api_chat_status():
    return {
        "configured": bool(DEEPSEEK_API_KEY),
        "model": DEEPSEEK_MODEL,
        "api_url": DEEPSEEK_API_URL,
    }


@app.get("/api/health")
async def health():
    return {
        "status": "ok",
        "video": video_client._connected if video_client else False,
        "gimbal": gimbal_ctrl.connected if gimbal_ctrl else False,
    }


# ── Dashboard HTML ───────────────────────────────────────

@app.get("/")
async def serve_dashboard():
    # Tablet/PWA default entry.
    return RedirectResponse("/home")


@app.get("/simple")
async def serve_simple():
    t = DASHBOARD_DIR / "simple.html"
    return HTMLResponse(t.read_text(), headers={"Cache-Control":"no-store"}) if t.is_file() else HTMLResponse("Not found", status_code=404)


@app.get("/debug.html")
async def serve_debug():
    t = DASHBOARD_DIR / "debug.html"
    return HTMLResponse(t.read_text()) if t.is_file() else HTMLResponse("Not found", status_code=404)


@app.get("/xinyu")
async def serve_xinyu():
    """Redirect to /home ->the definitive 心屿 page."""
    return RedirectResponse("/home")


@app.get("/home")
async def serve_home(request: Request):
    t = DASHBOARD_DIR / "home.html"
    headers = {
        "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
        "Pragma": "no-cache", "Expires": "0"
    }
    if request.query_params.get("reset-sw") == "1":
        headers["Clear-Site-Data"] = '"cache", "storage"'
    return HTMLResponse(t.read_text(), headers=headers) if t.is_file() else HTMLResponse("Not found", status_code=404)


@app.get("/tablet")
async def serve_tablet_control():
    t = DASHBOARD_DIR / "tablet_control.html"
    headers = {
        "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
        "Pragma": "no-cache",
        "Expires": "0",
    }
    return HTMLResponse(t.read_text(), headers=headers) if t.is_file() else HTMLResponse("Not found", status_code=404)


@app.get("/conversation-mock")
async def serve_conversation_mock():
    t = DASHBOARD_DIR / "conversation_mock.html"
    headers = {
        "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
        "Pragma": "no-cache",
        "Expires": "0",
    }
    return HTMLResponse(t.read_text(), headers=headers) if t.is_file() else HTMLResponse("Not found", status_code=404)


@app.get("/video-demo")
async def serve_video_demo():
    t = DASHBOARD_DIR / "video_demo.html"
    headers = {
        "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
        "Pragma": "no-cache",
        "Expires": "0",
    }
    return HTMLResponse(t.read_text(), headers=headers) if t.is_file() else HTMLResponse("Not found", status_code=404)


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


@app.get("/camtest.html")
async def serve_camtest():
    t = DASHBOARD_DIR / "camtest.html"
    return HTMLResponse(t.read_text()) if t.is_file() else HTMLResponse("Not found", status_code=404)


@app.get("/device.html")
async def serve_device_dashboard():
    device_html = DASHBOARD_DIR / "recamera_device.html"
    if device_html.is_file():
        return HTMLResponse(device_html.read_text(),
            headers={"Cache-Control": "no-cache"})
    return HTMLResponse("Not found", status_code=404)


@app.get("/v2")
async def serve_dashboard_v2():
    if HTML_FILE.is_file():
        return HTMLResponse(
            HTML_FILE.read_text(),
            headers={"Cache-Control": "no-cache, no-store, must-revalidate",
                     "Pragma": "no-cache", "Expires": "0"},
        )
    return HTMLResponse("<h1>Dashboard not found</h1>", status_code=404)


# ══════════════════════════════════════════════════════════════->#  CLI + Entry point
# ══════════════════════════════════════════════════════════════->
def parse_args():
    p = argparse.ArgumentParser(
        description="reCamera Demo Dashboard (FastAPI+MJPEG)",
        epilog="Examples:\n"
               "  %(prog)s                          # safe: video + TCP DOA + gimbal dry-run\n"
               "  %(prog)s --device-ip 192.168.201.84  # use the current WiFi device\n"
               "  %(prog)s --no-dry-run             # enable real gimbal control",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--device-ip", default="192.168.201.84", help="reCamera device IP")
    p.add_argument("--host", default="0.0.0.0", help="Server host")
    p.add_argument("--port", type=int, default=8001, help="Server port")
    p.add_argument("--no-dry-run", action="store_true", help="Send REAL gimbal commands to device")
    p.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING"])
    p.add_argument("--ssl-keyfile", default="", help="Optional TLS key file for tablet PWA install")
    p.add_argument("--ssl-certfile", default="", help="Optional TLS cert file for tablet PWA install")
    return p.parse_args()


def main():
    args = parse_args()
    setup_root_logger(level=args.log_level)


    global app_config
    app_config = Config(
        device_ip=args.device_ip,
        host=args.host,
        port=args.port,
        dry_run=not args.no_dry_run,
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
    if not args.no_dry_run:
        logger.info("🔒 DRY-RUN mode ->gimbal commands NOT sent (use --no-dry-run to enable)")
    logger.info("🌐 Dashboard: %s://localhost:%d/home  (%s://localhost:%d/v2)", scheme, args.port, scheme, args.port)
    logger.info("📡 MJPEG:     %s://localhost:%d/video_feed", scheme, args.port)
    logger.info("🔌 WebSocket: %s://localhost:%d/ws", ws_scheme, args.port)
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning", **ssl_kwargs)


if __name__ == "__main__":
    main()
