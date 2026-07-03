"""Single-session target selection and gimbal command orchestration."""
from __future__ import annotations
import json
import math
from pathlib import Path
import time
from typing import Optional, Sequence
from core.control_session import ControlMode, ControlSession
from core.event import BBox, ControlCommand, Event
from core.fsm import FSM, SystemState


_TELEMETRY_FIELDS = {
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


def _default_tracking_telemetry():
    return {k: (dict(v) if isinstance(v, dict) else v) for k, v in _TELEMETRY_FIELDS.items()}


def _load_tracking_control_config():
    path = Path(__file__).resolve().parents[1] / "config" / "tracking_control.json"
    try:
        with path.open("r", encoding="utf-8") as fh:
            return json.load(fh), {
                "tracking_config_loaded": True,
                "tracking_config_path": str(path),
                "tracking_config_error": "",
            }
    except Exception as exc:
        # Phase 1A observes this file only. If it is missing or malformed,
        # control continues with the existing hardcoded runtime behavior.
        return {}, {
            "tracking_config_loaded": False,
            "tracking_config_path": str(path),
            "tracking_config_error": str(exc)[:160],
        }


class Orchestrator:
    def __init__(self, *, center_yaw=180.0, center_pitch=90.0, audio_max_step=35.0,
                 vision_yaw_gain=70.0, vision_pitch_gain=45.0, audio_stale_s=1.0,
                 vision_stale_s=0.6, frame_width=1920, frame_height=1080,
                 default_speed=180, lease_ms=1500):
        self.fsm = FSM()
        self.center_yaw, self.center_pitch = float(center_yaw), float(center_pitch)
        self.vision_yaw_gain, self.vision_pitch_gain = float(vision_yaw_gain), float(vision_pitch_gain)
        self.frame_width, self.frame_height = int(frame_width), int(frame_height)
        self.default_speed = int(default_speed)
        self.framing_mode = "upper_body"
        self.target_x, self.target_y = 0.5, 0.32
        self.face_confidence_threshold = 0.65
        self.lock_confirm_required = 3
        self.occlusion_hold_s = 1.2
        self.doa_offset_deg, self.doa_direction = 0.0, 1.0
        self.session = ControlSession(default_lease_ms=lease_ms)
        self.locked_track_id = None
        self.tracking_phase, self.stop_state = "inactive", "stopped"
        self._last_observation_id = -1
        self._last_lock_seen = 0.0
        self._no_target_since = None
        self._search_exhausted = False
        self._home_sent_at = 0.0
        self._ema_x = self._ema_y = None
        self._centered = False
        self._gimbal_yaw = self._gimbal_pitch = None
        self._yaw_target, self._pitch_target = self.center_yaw, self.center_pitch
        self._command_sequence = self._vision_lost_frames = self._frame_count = 0
        self._doa_candidate = self._active_doa = None
        self._doa_candidate_since = self._last_speech_at = 0.0
        self._speaker_seek = False
        self._speaker_confidence = 0.0
        self.lock_state = "acquiring"
        self._lock_candidate_id = None
        self._lock_candidate_frames = 0
        self._lock_candidate_seen_at = 0.0
        self._face_lock_established = False
        self._outside_frames = 0
        self._last_motion_at = 0.0
        self._last_yaw_direction = self._last_pitch_direction = 0
        self._reverse_yaw_frames = self._reverse_pitch_frames = 0
        self._tracking_error = {"x": 0.0, "y": 0.0}
        self._command_suppressed_reason = ""
        self._tracking_control_config, config_telemetry = _load_tracking_control_config()
        self._telemetry = _default_tracking_telemetry()
        self._telemetry.update(config_telemetry)
        self._control_params = self._load_control_params()
        self._clear_current_target_telemetry()

    @property
    def state(self):
        return self.fsm.state

    def update_gimbal_readback(self, yaw, pitch):
        now = time.monotonic()
        if yaw is not None:
            self._gimbal_yaw = float(yaw)
            if (not self.session.snapshot().get("active") or not self._last_motion_at
                    or (now - self._last_motion_at > 1.0 and abs(self._gimbal_yaw - self._yaw_target) <= 2.0)):
                self._yaw_target = self._gimbal_yaw
        if pitch is not None:
            self._gimbal_pitch = float(pitch)
            if (not self.session.snapshot().get("active") or not self._last_motion_at
                    or (now - self._last_motion_at > 1.0 and abs(self._gimbal_pitch - self._pitch_target) <= 2.0)):
                self._pitch_target = self._gimbal_pitch

    def handle_event(self, event):
        lifecycle = self._lifecycle(event)
        if lifecycle is not _NOT_LIFECYCLE:
            return lifecycle
        if self.session.mode is ControlMode.INACTIVE:
            return None
        event_session = str(event.payload.get("session_id", ""))
        if event.type == "vision" and event.name == "observation" and not self.session.matches(event_session):
            return None
        if event.type == "audio" and event_session and not self.session.matches(event_session):
            return None
        if event.type == "ui":
            return self._ui(event) if self._ui_allowed(event) else None
        if event.type == "system":
            return self._command(stop=True, reason=event.name) if event.name in {"shutdown", "emergency_stop"} else None
        if event.type == "vision" and event.name == "observation":
            return self._observation(event)
        if event.type == "audio" and self.session.mode in {ControlMode.MULTI_SOUND_YAW, ControlMode.MEETING_SOUND_YAW}:
            return self._audio(event)
        if event.type == "vision" and self.session.mode is ControlMode.SINGLE_FACE_ANALYSIS:
            return self._legacy_vision(event)
        return None

    def _lifecycle(self, event):
        if event.type == "ui" and event.name == "feature_start":
            ok = self.session.start(str(event.payload.get("feature", "")), str(event.payload.get("session_id", "")), event.payload.get("lease_ms"))
            if ok:
                self._reset()
                self.tracking_phase, self.stop_state = "waiting_target", "running"
            return None
        if event.type == "ui" and event.name == "feature_heartbeat":
            self.session.heartbeat(str(event.payload.get("session_id", "")), event.payload.get("lease_ms"))
            return None
        if event.type == "ui" and event.name == "feature_mode_update":
            if self.session.update_mode(str(event.payload.get("feature", "")), str(event.payload.get("session_id", "")), event.payload.get("lease_ms")):
                self._reset()
            return None
        if event.type == "ui" and event.name == "feature_stop":
            sid = str(event.payload.get("session_id", ""))
            if self.session.stop(sid):
                self._reset()
                self.tracking_phase, self.stop_state = "inactive", "stopping"
                return self._command(stop=True, reason="feature_stop", session_id=sid)
            return None
        if event.type == "ui" and event.name == "control_config":
            if not self.session.matches(str(event.payload.get("session_id", ""))):
                return None
            self.default_speed = max(1, min(720, int(event.payload.get("speed", self.default_speed))))
            self.doa_offset_deg = max(-180.0, min(180.0, float(event.payload.get("doa_offset_deg", self.doa_offset_deg))))
            self.doa_direction = -1.0 if float(event.payload.get("doa_direction", self.doa_direction)) < 0 else 1.0
            framing_mode = str(event.payload.get("framing_mode", self.framing_mode))
            self.framing_mode = framing_mode if framing_mode in {"upper_body", "face_center"} else "upper_body"
            default_y = 0.32 if self.framing_mode == "upper_body" else 0.5
            self.target_x = self._clamp(float(event.payload.get("target_x", self.target_x)), 0.2, 0.8)
            self.target_y = self._clamp(float(event.payload.get("target_y", default_y)), 0.2, 0.7)
            return None
        if event.type == "system" and event.name in {"lease_expired", "shutdown", "emergency_stop"}:
            sid = self.session.session_id
            self.session.clear()
            self._reset()
            self.tracking_phase, self.stop_state = "inactive", "stopping"
            return self._command(stop=True, reason=event.name, session_id=sid)
        return _NOT_LIFECYCLE

    def _observation(self, event):
        obs_t0 = time.monotonic()
        oid = int(event.payload.get("observation_id", -1))
        captured = float(event.payload.get("captured_at", event.timestamp))
        captured = captured * 1000 if captured < 10_000_000_000 else captured
        self._telemetry.update({
            "frame_age_ms": max(0.0, round(time.time() * 1000.0 - captured, 1)),
            "face_detection_ms": event.payload.get("face_detection_ms"),
            "embedding_ms": event.payload.get("embedding_ms"),
            "tracker_update_ms": event.payload.get("tracker_update_ms"),
            "vision_hz": event.payload.get("vision_hz"),
            "telemetry_hz": event.payload.get("telemetry_hz"),
            "ui_push_hz": event.payload.get("ui_push_hz"),
        })
        if oid <= self._last_observation_id or time.time() * 1000 - captured > 600:
            return None
        self._last_observation_id, self._frame_count = oid, self._frame_count + 1
        size = event.payload.get("frame_size") or {}
        self.frame_width = max(1, int(size.get("width", self.frame_width)))
        self.frame_height = max(1, int(size.get("height", self.frame_height)))
        faces = [x for x in event.payload.get("faces", []) if int(x.get("lost_frames", 0) or 0) == 0]
        persons = event.payload.get("persons", [])
        self._clear_current_target_telemetry()
        command = self._single(faces, persons) if self.session.mode is ControlMode.SINGLE_FACE_ANALYSIS else self._multi(faces)
        self._telemetry["tracker_update_ms"] = self._coalesce_ms(
            self._telemetry.get("tracker_update_ms"),
            (time.monotonic() - obs_t0) * 1000.0,
        )
        return command

    def _single(self, faces, persons):
        now = time.monotonic()
        face = self._locked(faces)
        if face is None and self.locked_track_id is not None and now - self._last_lock_seen <= self.occlusion_hold_s:
            self.lock_state = "occlusion_hold"
            self.tracking_phase = "occlusion_hold"
            self._command_suppressed_reason = "occlusion_hold"
            return None
        if face is None:
            if self.locked_track_id is not None:
                self._unlock(preserve_established=True)
                self.lock_state = "reacquiring"
            face = self._confirm_face(faces, reacquiring=self._face_lock_established)
        if face:
            self._vision_lost_frames = 0
            self._reset_search()
            self.tracking_phase = "face_lock"
            self.lock_state = "locked"
            self.fsm.transition(Event.make("vision", "target_detected", "orchestrator"))
            return self._track(face, "face_lock")
        if self._lock_candidate_id is not None:
            self.tracking_phase = "reacquiring" if self._face_lock_established else "acquiring"
            self._command_suppressed_reason = "lock_confirmation"
            return None
        if not self._face_lock_established and now - self._lock_candidate_seen_at <= .6:
            self.tracking_phase = "acquiring"
            self._command_suppressed_reason = "candidate_gap_hold"
            return None
        if self._face_lock_established:
            self.lock_state = self.tracking_phase = "reacquiring"
            self._command_suppressed_reason = "waiting_locked_face"
            return None
        person = self._best_person(persons)
        if person:
            self._vision_lost_frames = 0
            self._reset_search()
            self.tracking_phase = "body_align"
            self.lock_state = "acquiring"
            self.fsm.transition(Event.make("vision", "target_detected", "orchestrator"))
            return self._track(person, "body_align")
        self._vision_lost_frames += 1
        self.fsm.transition(Event.make("vision", "target_lost", "orchestrator"))
        return self._search(now)

    def _multi(self, faces):
        face = self._locked(faces)
        if face and self._last_speech_at and time.monotonic() - self._last_speech_at > 1.5 and not self._speaker_seek:
            self.tracking_phase = "speaker_hold"
            return None
        if face:
            self.tracking_phase = "speaker_face_lock"
            self.lock_state = "locked"
            return self._track(face, "speaker_face_lock")
        if self.locked_track_id is not None and time.monotonic() - self._last_lock_seen <= self.occlusion_hold_s:
            self.lock_state = "occlusion_hold"
            self.tracking_phase = "speaker_occlusion_hold"
            self._command_suppressed_reason = "occlusion_hold"
            return None
        if self.locked_track_id is not None:
            self._unlock(preserve_established=True)
        if self._speaker_seek:
            face = self._confirm_face(faces, reacquiring=True, weight=1.4)
            if face:
                self._speaker_seek = False
                self.tracking_phase = "speaker_face_lock"
                self.lock_state = "locked"
                return self._track(face, "speaker_face_lock")
            self.lock_state = "reacquiring"
        self.tracking_phase = "speaker_reacquire" if self._speaker_seek else "audio_wait"
        return None

    def _audio(self, event):
        now = time.monotonic()
        if event.name == "timeout" or not bool(event.payload.get("speech", True)):
            if now - self._last_speech_at > 1.5 and self.locked_track_id is not None:
                self.tracking_phase = "speaker_hold"
            return None
        doa = float(event.payload.get("doa_deg", 0.0)) % 360
        confidence = max(0.0, min(1.0, float(event.payload.get("speaker_confidence", event.payload.get("vad_confidence", 0.7)))))
        lip_motion = event.payload.get("lip_motion")
        if confidence < 0.55 or lip_motion is False:
            return None
        self._last_speech_at = now
        if self._active_doa is not None and self._angle(doa, self._active_doa) <= 20:
            self._doa_candidate = None
            return None
        if self._doa_candidate is None or self._angle(doa, self._doa_candidate) > 8:
            self._doa_candidate, self._doa_candidate_since = doa, now
            return None
        switching = self._active_doa is not None
        required_hold = 0.8 if switching else 0.5
        required_confidence = 0.75 if switching else 0.65
        if now - self._doa_candidate_since < required_hold or confidence < required_confidence:
            return None
        self._active_doa, self._doa_candidate, self._speaker_seek = doa, None, True
        self._speaker_confidence = confidence
        self._unlock()
        yaw = self._doa_yaw(doa)
        self.tracking_phase = "audio_coarse"
        self.fsm.transition(Event.make("audio", "speech_detected", "orchestrator"))
        return self._command(yaw=yaw, speed=360, reason="audio_coarse")

    def _track(self, item, reason):
        cx, cy = self._norm(item.get("cx"), self.frame_width), self._norm(item.get("cy"), self.frame_height)
        box = item.get("bbox") or []
        track_box = self._normalize_bbox(item.get("track_bbox") or item.get("bbox") or box)
        if (cx is None or cy is None) and track_box is not None:
            x1, y1, x2, y2 = track_box
            cx = (x1 + x2) / 2 / self.frame_width
            cy = (y1 + (y2-y1) * (0.28 if reason == "body_align" else 0.5)) / self.frame_height
        if cx is None or cy is None:
            return None
        alpha_x = self._param("yaw_smoothing_alpha")
        alpha_y = self._param("pitch_smoothing_alpha")
        self._ema_x = cx if self._ema_x is None else alpha_x*cx + (1-alpha_x)*self._ema_x
        self._ema_y = cy if self._ema_y is None else alpha_y*cy + (1-alpha_y)*self._ema_y
        ex, ey = self._ema_x - self.target_x, self._ema_y - self.target_y
        ex, ey = self._edge_adjusted_error(ex, ey, track_box)
        self._tracking_error = {"x": round(ex, 4), "y": round(ey, 4)}
        self._update_target_telemetry(item, box, cx, cy, ex, ey)
        enter, remain = self._centered_conditions(cx, cy, track_box)
        if enter or (self._centered and remain):
            self._centered = True
            self.lock_state = "centered" if self.locked_track_id is not None else self.lock_state
            self.tracking_phase = "speaker_centered" if "speaker" in reason else "locked_centered"
            self._outside_frames = 0
            self._command_suppressed_reason = "inside_deadzone"
            return None
        self._centered = False
        self._outside_frames += 1
        if self._outside_frames < 2:
            self._command_suppressed_reason = "error_confirmation"
            return None
        now = time.monotonic()
        if now - self._last_motion_at < .3:
            self._command_suppressed_reason = "command_interval"
            return None
        elapsed_since_motion = max(0.3, now - self._last_motion_at) if self._last_motion_at else 0.3
        yaw_limit = min(self._param("max_yaw_delta_deg_per_tick"), self._param("max_yaw_deg_per_sec") * elapsed_since_motion)
        pitch_limit = min(self._param("max_pitch_delta_deg_per_tick"), self._param("max_pitch_deg_per_sec") * elapsed_since_motion)
        yaw_step = self._clamp(-ex*45.0, -yaw_limit, yaw_limit)
        pitch_step = self._clamp(ey*30.0, -pitch_limit, pitch_limit)
        yaw_pending = self._gimbal_yaw is not None and abs(self._gimbal_yaw-self._yaw_target) > 1.5
        pitch_pending = self._gimbal_pitch is not None and abs(self._gimbal_pitch-self._pitch_target) > 1.5
        yaw_step = self._damped_axis(yaw_step, "yaw", yaw_pending)
        pitch_step = self._damped_axis(pitch_step, "pitch", pitch_pending)
        if abs(yaw_step) < .01 and abs(pitch_step) < .01:
            self._command_suppressed_reason = "reverse_suppression"
            return None
        yaw = self._clamp(self._yaw_target + yaw_step, 1, 345)
        pitch = self._clamp(self._pitch_target + pitch_step, 30, 150)
        mag = max(abs(ex), abs(ey))
        speed = 180 if mag > .25 else 90 if mag > .10 else 60
        self._yaw_target, self._pitch_target = yaw, pitch
        self._last_motion_at = now
        self._command_suppressed_reason = ""
        return self._command(yaw=yaw, pitch=pitch, speed=speed, reason=reason)

    def _search(self, now):
        if self._search_exhausted:
            self.tracking_phase = "standby_stopped"
            return None
        if self._no_target_since is None:
            self._no_target_since = now
            self.tracking_phase = "search_grace"
            return None
        elapsed = now - self._no_target_since
        if elapsed < .5:
            return None
        if elapsed <= 8:
            self.tracking_phase = "limited_search"
            return self._command(yaw=self.center_yaw + 35*math.sin((elapsed-.5)/4*math.tau),
                                 pitch=self.center_pitch, speed=180, reason="limited_search")
        if not self._home_sent_at:
            self._home_sent_at = now
            self.tracking_phase = "returning_standby"
            return self._command(yaw=self.center_yaw, pitch=self.center_pitch, speed=180, reason="search_timeout_home")
        home = self._gimbal_yaw is not None and self._gimbal_pitch is not None and abs(self._gimbal_yaw-self.center_yaw) <= 2 and abs(self._gimbal_pitch-self.center_pitch) <= 2
        if home or now-self._home_sent_at >= 2:
            self._search_exhausted = True
            self.tracking_phase = "standby_stopped"
            self.stop_state = "stopped"
            return None
        return None

    def _legacy_vision(self, event):
        if event.name == "target_lost":
            self.fsm.transition(event)
            return None
        self._ema_x, self._ema_y = float(event.payload.get("cx", .5)), float(event.payload.get("cy", .5))
        state = self.fsm.transition(event)
        if state is SystemState.VISION_TRACK:
            item = {"cx": self._ema_x, "cy": self._ema_y}
            self._ema_x = self._ema_y = None
            return self._track(item, "vision_track")
        return None

    def _locked(self, faces):
        for face in faces:
            track_id = face.get("track_id")
            if self.locked_track_id is not None and track_id is not None and int(track_id) == self.locked_track_id:
                self._last_lock_seen = time.monotonic()
                return face
        return None

    def _confirm_face(self, faces, *, reacquiring=False, weight=.6):
        valid = [face for face in faces
                 if face.get("track_id") is not None
                 and float(face.get("confidence", face.get("conf", 0.0))) >= self.face_confidence_threshold]
        face = self._best_face(valid, weight)
        if face is None:
            self._lock_candidate_id, self._lock_candidate_frames = None, 0
            self.lock_state = "reacquiring" if reacquiring else "acquiring"
            return None
        candidate_id = int(face["track_id"])
        self._lock_candidate_seen_at = time.monotonic()
        if candidate_id == self._lock_candidate_id:
            self._lock_candidate_frames += 1
        else:
            self._lock_candidate_id, self._lock_candidate_frames = candidate_id, 1
        self.lock_state = "reacquiring" if reacquiring else "acquiring"
        if self._lock_candidate_frames < self.lock_confirm_required:
            return None
        self._lock(face)
        return face

    def _best_face(self, faces, weight=.6):
        if not faces:
            return None
        def score(x):
            cx, cy = self._norm(x.get("cx"), self.frame_width), self._norm(x.get("cy"), self.frame_height)
            dist = abs((cx if cx is not None else .5)-.5) + .4*abs((cy if cy is not None else .5)-.5)
            return float(x.get("confidence", x.get("conf", 0))) - weight*dist
        return max(faces, key=score)

    @staticmethod
    def _best_person(persons):
        if not persons:
            return None
        def score(x):
            b=x.get("bbox") or [0,0,0,0]
            area=max(0,float(b[2])-float(b[0]))*max(0,float(b[3])-float(b[1])) if len(b)>=4 else 0
            return float(x.get("confidence",x.get("conf",0)))+area/10_000_000
        return max(persons,key=score)

    def _lock(self, face):
        self.locked_track_id = int(face["track_id"]) if face.get("track_id") is not None else None
        if self.locked_track_id is None:
            return
        self._last_lock_seen = time.monotonic()
        self._face_lock_established = True
        self._lock_candidate_id, self._lock_candidate_frames = None, 0
        self._lock_candidate_seen_at = 0.0
        self.lock_state = "locked"
        self._ema_x = self._ema_y = None
        self._centered = False

    def _unlock(self, preserve_established=False):
        self.locked_track_id = None
        self._lock_candidate_id, self._lock_candidate_frames = None, 0
        self._lock_candidate_seen_at = 0.0
        if not preserve_established:
            self._face_lock_established = False
            self.lock_state = "acquiring"
        self._ema_x = self._ema_y = None
        self._centered = False

    def _damped_axis(self, step, axis, pending):
        direction = 1 if step > 0 else -1 if step < 0 else 0
        last_name = f"_last_{axis}_direction"
        reverse_name = f"_reverse_{axis}_frames"
        previous = int(getattr(self, last_name))
        reverse_frames = int(getattr(self, reverse_name))
        if pending and direction and previous and direction != previous:
            reverse_frames += 1
            setattr(self, reverse_name, reverse_frames)
            if reverse_frames < 3:
                return 0.0
        else:
            setattr(self, reverse_name, 0)
        if direction:
            setattr(self, last_name, direction)
        return step

    def _reset_search(self):
        self._no_target_since, self._home_sent_at, self._search_exhausted = None, 0.0, False

    def _reset(self):
        self.fsm.transition(Event.make("system", "control_reset", "orchestrator"))
        self._last_observation_id, self._vision_lost_frames = -1, 0
        self._unlock()
        self._reset_search()
        self._doa_candidate = self._active_doa = None
        self._speaker_seek = False
        self._speaker_confidence = 0.0
        self._outside_frames = 0
        self._last_motion_at = 0.0
        self._last_yaw_direction = self._last_pitch_direction = 0
        self._reverse_yaw_frames = self._reverse_pitch_frames = 0
        self._tracking_error = {"x": 0.0, "y": 0.0}
        self._command_suppressed_reason = ""
        self._clear_current_target_telemetry()

    def _ui_allowed(self, event):
        if not self.session.matches(str(event.payload.get("session_id", ""))):
            return False
        if event.name == "dpad_move":
            return self.session.mode is ControlMode.MANUAL_GIMBAL_DEBUG
        return event.name in {"gimbal_home","gimbal_standby","gimbal_sleep","gimbal_stop","gimbal_calibrate"}

    def _ui(self, event):
        if event.name == "dpad_move":
            return self._command(mode="delta", yaw=self._clamp(float(event.payload.get("pan",0)),-2.5,2.5), pitch=self._clamp(float(event.payload.get("tilt",0)),-2.5,2.5), speed=self.default_speed, reason="ui_dpad_move")
        if event.name in {"gimbal_home", "gimbal_standby"}:
            return self._command(yaw=self.center_yaw,pitch=self.center_pitch,speed=360,reason="standby")
        if event.name == "gimbal_sleep":
            return self._command(yaw=self.center_yaw,pitch=175,speed=360,reason="sleep")
        if event.name == "gimbal_stop":
            return self._command(stop=True,reason="ui_stop")
        if event.name == "gimbal_calibrate":
            sid = self.session.session_id
            self.session.clear()
            self._reset()
            self.tracking_phase, self.stop_state = "calibrating", "stopping"
            return self._command(action="calibrate", speed=360, reason="calibrate", session_id=sid)
        return None

    def _doa_yaw(self, doa):
        corrected=(float(doa)+self.doa_offset_deg)%360
        signed=corrected if corrected<=180 else corrected-360
        return self._clamp(self.center_yaw+signed*self.doa_direction,1,345)

    def _command(self, *, session_id=None, **kwargs):
        self._command_sequence += 1
        # The hardware worker serializes lease and command requests; a 2.5 s
        # TTL survives one bounded bridge timeout while latest-wins prevents
        # stale motion from accumulating.
        return ControlCommand.make("orchestrator",session_id=self.session.session_id if session_id is None else session_id,sequence=self._command_sequence,ttl_s=2.5,**kwargs)

    def runtime_state(self):
        runtime = {**self.session.snapshot(),"fsm_state":self.state.value,"speed":self.default_speed,
                "doa_offset_deg":self.doa_offset_deg,"doa_direction":int(self.doa_direction),
                "locked_track_id":self.locked_track_id,"tracking_phase":self.tracking_phase,
                "lock_state":self.lock_state,"lock_candidate_id":self._lock_candidate_id,
                "lock_confirm_frames":self._lock_candidate_frames,
                "target_point":{"x":round(self.target_x,3),"y":round(self.target_y,3),"framing_mode":self.framing_mode},
                "tracking_error":dict(self._tracking_error),
                "command_suppressed_reason":self._command_suppressed_reason,
                "speaker_confidence":round(self._speaker_confidence,3),
                "stop_state":self.stop_state,"last_observation_id":self._last_observation_id}
        runtime.update(self._phase1a_telemetry())
        return runtime

    def update_telemetry(self, **fields):
        for key, value in fields.items():
            if key in self._telemetry:
                self._telemetry[key] = value

    def _load_control_params(self):
        defaults = {
            "center_deadband_x_ratio": 0.05,
            "center_deadband_y_ratio": 0.06,
            "safe_roi_width_ratio": 0.84,
            "safe_roi_height_ratio": 0.84,
            "edge_margin_x_ratio": 0.08,
            "edge_margin_y_ratio": 0.08,
            "max_yaw_deg_per_sec": 16.7,
            "max_pitch_deg_per_sec": 10.0,
            "max_yaw_delta_deg_per_tick": 5.0,
            "max_pitch_delta_deg_per_tick": 3.0,
            "yaw_smoothing_alpha": 0.20,
            "pitch_smoothing_alpha": 0.20,
        }
        configured = self._tracking_control_config.get("control") if isinstance(self._tracking_control_config, dict) else {}
        if not isinstance(configured, dict):
            return defaults
        params = dict(defaults)
        for key, default in defaults.items():
            try:
                params[key] = float(configured.get(key, default))
            except (TypeError, ValueError):
                params[key] = default
        params["center_deadband_x_ratio"] = self._clamp(params["center_deadband_x_ratio"], 0.0, 0.5)
        params["center_deadband_y_ratio"] = self._clamp(params["center_deadband_y_ratio"], 0.0, 0.5)
        params["safe_roi_width_ratio"] = self._clamp(params["safe_roi_width_ratio"], 0.01, 1.0)
        params["safe_roi_height_ratio"] = self._clamp(params["safe_roi_height_ratio"], 0.01, 1.0)
        params["edge_margin_x_ratio"] = self._clamp(params["edge_margin_x_ratio"], 0.0, 0.49)
        params["edge_margin_y_ratio"] = self._clamp(params["edge_margin_y_ratio"], 0.0, 0.49)
        params["max_yaw_deg_per_sec"] = self._clamp(params["max_yaw_deg_per_sec"], 0.01, 720.0)
        params["max_pitch_deg_per_sec"] = self._clamp(params["max_pitch_deg_per_sec"], 0.01, 720.0)
        params["max_yaw_delta_deg_per_tick"] = self._clamp(params["max_yaw_delta_deg_per_tick"], 0.01, 45.0)
        params["max_pitch_delta_deg_per_tick"] = self._clamp(params["max_pitch_delta_deg_per_tick"], 0.01, 45.0)
        params["yaw_smoothing_alpha"] = self._clamp(params["yaw_smoothing_alpha"], 0.01, 1.0)
        params["pitch_smoothing_alpha"] = self._clamp(params["pitch_smoothing_alpha"], 0.01, 1.0)
        return params

    def _param(self, name):
        return float(self._control_params[name])

    def _centered_conditions(self, face_cx, face_cy, track_box):
        center_x = abs(float(face_cx) - self.target_x)
        center_y = abs(float(face_cy) - self.target_y)
        in_deadband = (
            center_x <= self._param("center_deadband_x_ratio")
            and center_y <= self._param("center_deadband_y_ratio")
        )
        remain_deadband = (
            center_x <= max(0.08, self._param("center_deadband_x_ratio") * 1.6)
            and center_y <= max(0.10, self._param("center_deadband_y_ratio") * 1.6)
        )
        safe = self._bbox_inside_safe_roi(track_box)
        edge_clear = self._bbox_has_edge_margin(track_box)
        return in_deadband and safe and edge_clear, remain_deadband and safe and edge_clear

    def _bbox_inside_safe_roi(self, box):
        if box is None:
            return True
        x1, y1, x2, y2 = self._bbox_ratios(box)
        x_margin = (1.0 - self._param("safe_roi_width_ratio")) / 2.0
        y_margin = (1.0 - self._param("safe_roi_height_ratio")) / 2.0
        return x1 >= x_margin and x2 <= 1.0 - x_margin and y1 >= y_margin and y2 <= 1.0 - y_margin

    def _bbox_has_edge_margin(self, box):
        if box is None:
            return True
        x1, y1, x2, y2 = self._bbox_ratios(box)
        return (
            x1 >= self._param("edge_margin_x_ratio")
            and x2 <= 1.0 - self._param("edge_margin_x_ratio")
            and y1 >= self._param("edge_margin_y_ratio")
            and y2 <= 1.0 - self._param("edge_margin_y_ratio")
        )

    def _edge_adjusted_error(self, ex, ey, box):
        if box is None:
            return ex, ey
        x1, y1, x2, y2 = self._bbox_ratios(box)
        min_x = max(self._param("edge_margin_x_ratio"), (1.0 - self._param("safe_roi_width_ratio")) / 2.0)
        max_x = 1.0 - min_x
        min_y = max(self._param("edge_margin_y_ratio"), (1.0 - self._param("safe_roi_height_ratio")) / 2.0)
        max_y = 1.0 - min_y
        if x1 < min_x:
            ex = min(float(ex), x1 - min_x)
        elif x2 > max_x:
            ex = max(float(ex), x2 - max_x)
        if y1 < min_y:
            ey = min(float(ey), y1 - min_y)
        elif y2 > max_y:
            ey = max(float(ey), y2 - max_y)
        return ex, ey

    def _bbox_ratios(self, box):
        x1, y1, x2, y2 = box
        return (
            self._clamp(float(x1) / self.frame_width, 0.0, 1.0),
            self._clamp(float(y1) / self.frame_height, 0.0, 1.0),
            self._clamp(float(x2) / self.frame_width, 0.0, 1.0),
            self._clamp(float(y2) / self.frame_height, 0.0, 1.0),
        )

    def _phase1a_telemetry(self):
        telemetry = dict(self._telemetry)
        telemetry["tracking_state"] = self._tracking_state()
        telemetry["locked_track_id"] = self.locked_track_id
        telemetry["frame_center"] = {
            "x": round(self.frame_width / 2.0, 1),
            "y": round(self.frame_height / 2.0, 1),
        }
        return telemetry

    def _tracking_state(self):
        if not self.session.snapshot().get("active"):
            return "IDLE"
        phase = str(self.tracking_phase or "")
        lock = str(self.lock_state or "")
        if "occlusion_hold" in phase or lock == "occlusion_hold":
            return "OCCLUSION_HOLD"
        if phase in {"limited_search", "search_grace", "returning_standby"}:
            return "SEARCH"
        if phase == "standby_stopped":
            return "LOST"
        if lock == "centered" or phase in {"locked_centered", "speaker_centered"}:
            return "CENTERED"
        if self.locked_track_id is not None:
            return "LOCKED"
        return "ACQUIRE"

    def _update_target_telemetry(self, item, box, cx, cy, ex, ey):
        raw_box = item.get("raw_bbox") or item.get("bbox_raw") or box or None
        track_box = item.get("track_bbox") or item.get("bbox") or raw_box
        face_x, face_y = cx * self.frame_width, cy * self.frame_height
        target_x, target_y = (self.target_x + ex) * self.frame_width, (self.target_y + ey) * self.frame_height
        control_target = {"x": round(float(target_x), 1), "y": round(float(target_y), 1)}
        self._telemetry.update({
            "target_visible": True,
            "raw_bbox": self._normalize_bbox(raw_box),
            "track_bbox": self._normalize_bbox(track_box),
            "control_target": control_target,
            "last_control_target": dict(control_target),
            "face_center": {"x": round(float(face_x), 1), "y": round(float(face_y), 1)},
            "error_x_px": round(float(ex * self.frame_width), 1),
            "error_y_px": round(float(ey * self.frame_height), 1),
            "error_x_ratio": round(float(ex), 4),
            "error_y_ratio": round(float(ey), 4),
        })

    @staticmethod
    def _normalize_bbox(box):
        if box is None:
            return None
        if isinstance(box, dict):
            if all(k in box for k in ("x1", "y1", "x2", "y2")):
                values = [box["x1"], box["y1"], box["x2"], box["y2"]]
            elif all(k in box for k in ("left", "top", "right", "bottom")):
                values = [box["left"], box["top"], box["right"], box["bottom"]]
            else:
                return None
        else:
            try:
                if hasattr(box, "tolist"):
                    box = box.tolist()
                values = list(box)
            except TypeError:
                return None
            if len(values) < 4:
                return None
            values = values[:4]
        try:
            return [float(v) for v in values]
        except (TypeError, ValueError):
            return None

    def _clear_current_target_telemetry(self):
        self._telemetry.update({
            "target_visible": False,
            "raw_bbox": None,
            "track_bbox": None,
            "control_target": None,
            "face_center": None,
            "error_x_px": None,
            "error_y_px": None,
            "error_x_ratio": None,
            "error_y_ratio": None,
        })

    @staticmethod
    def _coalesce_ms(value, fallback):
        if value is not None:
            return value
        return round(float(fallback), 1)

    def handle(self,event):
        return self.handle_event(event)

    def handle_vision(self,bboxes:Sequence[BBox],*,source="vision"):
        self._frame_count += 1
        if not bboxes:
            return self.handle_event(Event.make("vision","target_lost",source,{"session_id":self.session.session_id}))
        b=bboxes[0]
        return self.handle_event(Event.make("vision","target_detected",source,{"session_id":self.session.session_id,"cx":b.center_x/self.frame_width,"cy":b.center_y/self.frame_height,"conf":b.confidence,"class_name":b.class_name}))

    @property
    def vision_lost_frames(self):
        return self._vision_lost_frames
    @property
    def frame_count(self):
        return self._frame_count

    @staticmethod
    def _norm(value,dimension):
        if value is None:return None
        value=float(value)
        return value/dimension if abs(value)>1 else value
    @staticmethod
    def _angle(a,b):
        return abs((float(a)-float(b)+180)%360-180)
    @staticmethod
    def _clamp(value,low,high):
        return max(low,min(high,float(value)))


def make_system_command(name,source="system"):
    return Orchestrator().handle_event(Event.make("system",name,source))
_NOT_LIFECYCLE=object()
