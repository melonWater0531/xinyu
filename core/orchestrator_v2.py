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
    "person_visible": False,
    "locked_person_bbox": None,
    "face_search_roi": None,
    "person_align_reason": "",
    "reacquire_reason": "",
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


def _coerce_event(event) -> Event:
    if isinstance(event, Event):
        return event
    if isinstance(event, dict):
        raw = dict(event)
        raw_type = str(raw.get("type", ""))
        payload = dict(raw.get("payload") or raw.get("data") or {})
        for key, value in raw.items():
            if key not in {"type", "name", "payload", "data", "timestamp", "source"}:
                payload.setdefault(key, value)
        if ":" in raw_type and not raw.get("name"):
            event_type, name = raw_type.split(":", 1)
        else:
            event_type, name = raw_type, str(raw.get("name", ""))
        return Event.make(
            event_type,
            name,
            str(raw.get("source", "")),
            payload=payload,
            timestamp=raw.get("timestamp"),
        )
    raise TypeError("Orchestrator.handle_event requires Event or dict")


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
        self.face_confidence_threshold = 0.55
        self.lock_confirm_required = 3
        # Speaker-seek fast confirm: right after an audio_coarse turn we accept
        # a face in 1 frame at high confidence (2 frames otherwise) instead of 3.
        self.seek_confidence = 0.80
        self.seek_confirm_fast = 1
        self.seek_confirm_required = 2
        # DOA rear blind cone (mechanical dead zone): corrected DOA within
        # 180 +/- rear_cone_deg is ignored instead of slamming into the clamps.
        self.rear_cone_deg = 30.0
        self.occlusion_hold_s = 1.2
        self.doa_offset_deg, self.doa_direction = 0.0, 1.0
        self._load_doa_calibration()
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
        self.doa_switch_threshold_deg = 15.0
        self._doa_led_ema = None
        self._doa_led_last_sent = None
        self._doa_led_last_time = 0.0
        self._doa_led_alpha = 0.30
        self._doa_led_dead_zone_deg = 3.0
        self._doa_led_cooldown_s = 0.35
        self._doa_led_max_step_deg = 35.0
        self._speaker_seek = False
        self._speaker_confidence = 0.0
        self._seek_started_at = 0.0   # monotonic ts of the last audio_coarse
        self._seek_to_lock_ms = None  # latency of the last seek->face lock
        self.lock_state = "acquiring"
        self._lock_candidate_id = None
        self._lock_candidate_frames = 0
        self._lock_candidate_seen_at = 0.0
        self._face_lock_established = False
        self._locked_person_bbox = None
        self._last_person_seen_at = 0.0
        self._person_align_frames = 0
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
        self.lock_confirm_required = max(1, int(round(self._param("require_stable_frames"))))
        self._clear_current_target_telemetry()

    def _load_doa_calibration(self):
        """Load the persisted mic↔camera yaw offset written by the dashboard's
        对准我 calibration (runtime/doa_calibration.json)."""
        path = Path(__file__).resolve().parents[1] / "runtime" / "doa_calibration.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            offset = float(data.get("doa_offset_deg", 0.0))
            if -180.0 <= offset <= 180.0:
                self.doa_offset_deg = offset
            direction = float(data.get("doa_direction", self.doa_direction))
            self.doa_direction = -1.0 if direction < 0 else 1.0
        except (OSError, ValueError, TypeError):
            pass

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
        event = _coerce_event(event)
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
        if event.type == "audio" and self.session.mode in {ControlMode.MULTI_SOUND_YAW, ControlMode.MEETING_SOUND_YAW, ControlMode.MEETING_RECORDING}:
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
        stale_age_ms = time.time() * 1000 - captured
        if oid <= self._last_observation_id or stale_age_ms > 600:
            self._clear_current_target_telemetry()
            self._telemetry.update({
                "centered_block_reason": "stale_observation",
                "motion_blocked_reason": "stale_observation",
                "command_sent": False,
            })
            self._command_suppressed_reason = "stale_observation"
            return None
        self._last_observation_id, self._frame_count = oid, self._frame_count + 1
        size = event.payload.get("frame_size") or {}
        self.frame_width = max(1, int(size.get("width", self.frame_width)))
        self.frame_height = max(1, int(size.get("height", self.frame_height)))
        faces = [x for x in event.payload.get("faces", [])
                 if int(x.get("lost_frames", 0) or 0) == 0 and self._face_in_frame(x)]
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
        person = self._best_person(persons)
        if person:
            self._remember_person(person)
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
        if self._soft_face_candidate(faces):
            self.tracking_phase = "face_candidate_hold"
            self.lock_state = "reacquiring" if self._face_lock_established else "acquiring"
            self._command_suppressed_reason = "face_candidate_hold"
            self._telemetry.update({
                "motion_blocked_reason": "face_candidate_hold",
                "command_sent": False,
            })
            return None
        if not self._face_lock_established and now - self._lock_candidate_seen_at <= .6:
            self.tracking_phase = "acquiring"
            self._command_suppressed_reason = "candidate_gap_hold"
            return None
        if self._face_lock_established:
            if person or self._recent_person(now):
                self._unlock(preserve_established=True)
                self.lock_state = "reacquiring"
                self.tracking_phase = "face_reacquire_in_person"
                self._command_suppressed_reason = ""
                self.fsm.transition(Event.make("vision", "target_detected", "orchestrator"))
                return self._track(self._person_control_item(person), "face_reacquire_in_person")
            self.lock_state = "reacquiring"
            self._command_suppressed_reason = "waiting_locked_face"
            self.fsm.transition(Event.make("vision", "target_lost", "orchestrator"))
            return self._search(now)
        if person:
            self._vision_lost_frames = 0
            self._reset_search()
            if self._person_align_frames < int(round(self._param("person_confirm_frames"))):
                self.tracking_phase = "person_acquire"
                self.lock_state = "acquiring"
                self._command_suppressed_reason = "person_confirmation"
                self._telemetry.update({
                    "person_visible": True,
                    "locked_person_bbox": self._normalize_bbox(person.get("bbox")),
                    "face_search_roi": self._person_face_roi(person),
                    "person_align_reason": "person_confirmation",
                })
                return None
            self.tracking_phase = "person_align"
            self.lock_state = "acquiring"
            self.fsm.transition(Event.make("vision", "target_detected", "orchestrator"))
            return self._track(self._person_control_item(person), "person_align")
        if self._recent_person(now):
            self._vision_lost_frames = 0
            self.tracking_phase = "face_search_in_person"
            self.lock_state = "reacquiring" if self._face_lock_established else "acquiring"
            self._command_suppressed_reason = "person_hold"
            self._telemetry.update({
                "person_visible": False,
                "locked_person_bbox": self._locked_person_bbox,
                "face_search_roi": self._person_face_roi({"bbox": self._locked_person_bbox}),
                "person_align_reason": "recent_person_hold",
                "motion_blocked_reason": "person_hold",
            })
            return None
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
            face = self._confirm_face(faces, reacquiring=True, weight=1.4, seek=True)
            if face:
                self._speaker_seek = False
                if self._seek_started_at:
                    self._seek_to_lock_ms = round((time.monotonic() - self._seek_started_at) * 1000.0, 1)
                self.tracking_phase = "speaker_face_lock"
                self.lock_state = "locked"
                return self._track(face, "speaker_face_lock")
            self.lock_state = "reacquiring"
        self.tracking_phase = "speaker_reacquire" if self._speaker_seek else "audio_wait"
        return None

    def _audio(self, event):
        now = time.monotonic()
        if event.payload.get("doa_only") and self.session.mode is ControlMode.MULTI_SOUND_YAW:
            return self._doa_led_sync(event, now)
        if event.name == "timeout" or not bool(event.payload.get("speech", True)):
            if now - self._last_speech_at > 1.5 and self.locked_track_id is not None:
                self.tracking_phase = "speaker_hold"
            return None
        doa = float(event.payload.get("doa_deg", 0.0)) % 360
        confidence = max(0.0, min(1.0, float(event.payload.get("speaker_confidence", event.payload.get("vad_confidence", 0.7)))))
        lip_motion = event.payload.get("lip_motion")
        if lip_motion is False:
            confidence = max(0.0, confidence - 0.1)
            self._command_suppressed_reason = "weak_lip_motion"
        if confidence < 0.55:
            return None
        self._last_speech_at = now
        if self._active_doa is not None and self._angle(doa, self._active_doa) <= 20:
            self._doa_candidate = None
            return None
        if self._doa_candidate is None or self._angle(doa, self._doa_candidate) > self.doa_switch_threshold_deg:
            self._doa_candidate, self._doa_candidate_since = doa, now
            return None
        switching = self._active_doa is not None
        required_hold = 0.8 if switching else 0.5
        required_confidence = 0.75 if switching else 0.65
        if now - self._doa_candidate_since < required_hold or confidence < required_confidence:
            return None
        # Rear blind cone: the gimbal cannot physically point behind itself
        # (yaw clamp 1-345). Ignore rather than slam into an end stop.
        corrected = (doa + self.doa_offset_deg) % 360
        if abs(((corrected - 180.0 + 180.0) % 360) - 180.0) <= self.rear_cone_deg:
            self.tracking_phase = "audio_rear_ignored"
            self._command_suppressed_reason = "rear_cone"
            self._doa_candidate = None
            return None
        # Motor busy: previous motion still in flight -> keep the candidate
        # armed and commit on a later event instead of stacking commands.
        if (self._gimbal_yaw is not None
                and abs(self._gimbal_yaw - self._yaw_target) > 8.0):
            self._command_suppressed_reason = "motor_busy"
            return None
        self._active_doa, self._doa_candidate, self._speaker_seek = doa, None, True
        self._speaker_confidence = confidence
        self._seek_started_at = now
        self._seek_to_lock_ms = None
        self._unlock()
        yaw = self._doa_yaw(doa)
        self.tracking_phase = "audio_coarse"
        self.fsm.transition(Event.make("audio", "speech_detected", "orchestrator"))
        # Track the commanded yaw so fine tracking and the safety slew cap
        # reference the real baseline (was previously left stale).
        self._yaw_target = yaw
        self._last_motion_at = now
        return self._command(yaw=yaw, speed=360, reason="audio_coarse")

    def _doa_led_sync(self, event, now):
        doa = float(event.payload.get("doa_deg", 0.0)) % 360
        corrected = (doa + self.doa_offset_deg) % 360
        if abs(((corrected - 180.0 + 180.0) % 360) - 180.0) <= self.rear_cone_deg:
            self.tracking_phase = "audio_rear_ignored"
            self._command_suppressed_reason = "rear_cone"
            return None

        target = self._doa_yaw(doa)
        if self._doa_led_ema is None:
            self._doa_led_ema = target
        else:
            self._doa_led_ema = self._doa_led_alpha * target + (1.0 - self._doa_led_alpha) * self._doa_led_ema

        yaw = self._doa_led_ema
        if self._doa_led_last_sent is not None:
            delta = yaw - self._doa_led_last_sent
            if abs(delta) < self._doa_led_dead_zone_deg:
                self.tracking_phase = "doa_led_sync"
                self._command_suppressed_reason = "doa_led_dead_zone"
                return None
            if now - self._doa_led_last_time < self._doa_led_cooldown_s:
                self.tracking_phase = "doa_led_sync"
                self._command_suppressed_reason = "doa_led_cooldown"
                return None
            if abs(delta) > self._doa_led_max_step_deg:
                yaw = self._doa_led_last_sent + (self._doa_led_max_step_deg if delta > 0 else -self._doa_led_max_step_deg)

        yaw = round(self._clamp(yaw, 1, 345), 1)
        self._doa_led_last_sent = yaw
        self._doa_led_last_time = now
        self._active_doa = doa
        self._speaker_seek = False
        self._speaker_confidence = 0.0
        self._seek_started_at = 0.0
        self._seek_to_lock_ms = None
        self._yaw_target = yaw
        self._last_motion_at = now
        self.tracking_phase = "doa_led_sync"
        self._command_suppressed_reason = ""
        self.fsm.transition(Event.make("audio", "speech_detected", "orchestrator"))
        return self._command(yaw=yaw, speed=360, reason="doa_led_sync")

    def _track(self, item, reason):
        cx, cy = self._norm(item.get("cx"), self.frame_width), self._norm(item.get("cy"), self.frame_height)
        box = item.get("bbox")
        track_box = self._normalize_bbox(item.get("track_bbox"))
        if track_box is None:
            track_box = self._normalize_bbox(box)
        if (cx is None or cy is None) and track_box is not None:
            x1, y1, x2, y2 = track_box
            cx = (x1 + x2) / 2 / self.frame_width
            cy = (y1 + (y2-y1) * (0.28 if reason == "body_align" else 0.5)) / self.frame_height
        if cx is None or cy is None:
            return None
        self._telemetry.update({
            "demo_stop_shake_mode": self._demo_mode(),
            "demo_zone": self._demo_zone(cx, cy, reason),
            "demo_hold_active": False,
            "demo_hold_reason": "",
            "body_align_suppressed": False,
            "person_visible": reason in {"body_align", "person_align", "face_reacquire_in_person"},
            "locked_person_bbox": self._locked_person_bbox,
            "face_search_roi": item.get("face_search_roi") or self._person_face_roi(item),
            "person_align_reason": "",
            "reacquire_reason": "person_anchor" if reason == "face_reacquire_in_person" else "",
            "motion_blocked_reason": "",
            "command_delta_yaw_deg": None,
            "command_delta_pitch_deg": None,
            "command_sent": False,
        })
        alpha_x = self._param("yaw_smoothing_alpha")
        alpha_y = self._param("pitch_smoothing_alpha")
        self._ema_x = cx if self._ema_x is None else alpha_x*cx + (1-alpha_x)*self._ema_x
        self._ema_y = cy if self._ema_y is None else alpha_y*cy + (1-alpha_y)*self._ema_y
        ex, ey = self._ema_x - self.target_x, self._ema_y - self.target_y
        if not self._demo_mode():
            ex, ey = self._edge_adjusted_error(ex, ey, track_box)
        self._tracking_error = {"x": round(ex, 4), "y": round(ey, 4)}
        person_reason = reason in {"body_align", "person_align", "face_reacquire_in_person"}
        if person_reason:
            enter, remain, centered_reason, centered_block_reason = self._person_centered_conditions(cx, cy)
        else:
            enter, remain, centered_reason, centered_block_reason = self._centered_conditions(cx, cy, track_box)
        self._update_target_telemetry(item, box, track_box, cx, cy, ex, ey, centered_reason, centered_block_reason)
        if self._demo_hold_condition(cx, cy, reason):
            self._centered = True
            self.lock_state = "centered" if self.locked_track_id is not None else self.lock_state
            self.tracking_phase = "speaker_centered" if "speaker" in reason else "locked_centered"
            self._outside_frames = 0
            self._command_suppressed_reason = "demo_hold"
            self._telemetry.update({
                "demo_hold_active": True,
                "demo_hold_reason": "face_inside_demo_hold_region",
                "centered_reason": "demo_hold_face_inside_frame",
                "centered_block_reason": "",
                "motion_blocked_reason": "demo_hold",
            })
            return None
        if enter or (self._centered and remain):
            self._centered = True
            self.lock_state = "centered" if self.locked_track_id is not None else self.lock_state
            if person_reason:
                self.tracking_phase = "face_search_in_person"
                self._telemetry["person_align_reason"] = "person_anchor_centered"
            else:
                self.tracking_phase = "speaker_centered" if "speaker" in reason else "locked_centered"
            self._outside_frames = 0
            self._command_suppressed_reason = "inside_deadzone"
            self._telemetry["centered_reason"] = centered_reason
            self._telemetry["centered_block_reason"] = ""
            return None
        self._centered = False
        self._outside_frames += 1
        if self._outside_frames < 2:
            self._command_suppressed_reason = "error_confirmation"
            return None
        now = time.monotonic()
        min_interval_s = self._param("min_command_interval_ms") / 1000.0
        if now - self._last_motion_at < min_interval_s:
            self._command_suppressed_reason = "command_interval"
            self._telemetry["motion_blocked_reason"] = "command_interval"
            return None
        elapsed_since_motion = max(min_interval_s, now - self._last_motion_at) if self._last_motion_at else min_interval_s
        yaw_limit = min(self._param("max_yaw_delta_deg_per_tick"), self._param("max_yaw_deg_per_sec") * elapsed_since_motion)
        pitch_limit = min(self._param("max_pitch_delta_deg_per_tick"), self._param("max_pitch_deg_per_sec") * elapsed_since_motion)
        if person_reason:
            yaw_limit = min(yaw_limit, 0.3 if self._demo_mode() else self._param("person_align_max_yaw_delta_deg"))
            pitch_limit = min(pitch_limit, 0.2 if self._demo_mode() else self._param("person_align_max_pitch_delta_deg"))
            self._telemetry["body_align_suppressed"] = True
            self._telemetry["person_align_reason"] = reason
        yaw_step = self._clamp(-ex*45.0, -yaw_limit, yaw_limit)
        pitch_step = self._clamp(ey*30.0, -pitch_limit, pitch_limit)
        pending_threshold = 0.5 if person_reason else 1.5
        yaw_pending = self._gimbal_yaw is not None and abs(self._gimbal_yaw-self._yaw_target) > pending_threshold
        pitch_pending = self._gimbal_pitch is not None and abs(self._gimbal_pitch-self._pitch_target) > pending_threshold
        if person_reason and (yaw_pending or pitch_pending):
            self._command_suppressed_reason = "pending_motion"
            self._telemetry["motion_blocked_reason"] = "pending_motion"
            return None
        yaw_step = self._damped_axis(yaw_step, "yaw", yaw_pending)
        pitch_step = self._damped_axis(pitch_step, "pitch", pitch_pending)
        if abs(yaw_step) < .01 and abs(pitch_step) < .01:
            self._command_suppressed_reason = "reverse_suppression"
            self._telemetry["motion_blocked_reason"] = "reverse_suppression"
            return None
        yaw = self._clamp(self._yaw_target + yaw_step, 1, 345)
        pitch = self._clamp(self._pitch_target + pitch_step, 30, 150)
        delta_yaw = abs(yaw - self._yaw_target)
        delta_pitch = abs(pitch - self._pitch_target)
        self._telemetry.update({
            "command_delta_yaw_deg": round(float(delta_yaw), 3),
            "command_delta_pitch_deg": round(float(delta_pitch), 3),
        })
        if self._demo_mode() and delta_yaw < self._param("min_yaw_command_delta_deg") and delta_pitch < self._param("min_pitch_command_delta_deg"):
            self._command_suppressed_reason = "min_command_delta"
            self._telemetry["motion_blocked_reason"] = "min_command_delta"
            return None
        mag = max(abs(ex), abs(ey))
        speed = 360 if mag > .25 else 240 if mag > .10 else 120
        if person_reason:
            speed = min(speed, 80 if self._demo_mode() else int(self._param("person_align_command_speed")))
        self._yaw_target, self._pitch_target = yaw, pitch
        self._last_motion_at = now
        self._command_suppressed_reason = ""
        self._telemetry.update({
            "target_yaw_deg": round(float(yaw), 3),
            "target_pitch_deg": round(float(pitch), 3),
            "command_yaw_deg": round(float(yaw), 3),
            "command_pitch_deg": round(float(pitch), 3),
            "yaw_cmd": round(float(yaw), 3),
            "pitch_cmd": round(float(pitch), 3),
            "command_sent": True,
        })
        return self._command(yaw=yaw, pitch=pitch, speed=speed, reason=reason)

    def _search(self, now):
        if self._search_exhausted:
            self.tracking_phase = "standby_stopped"
            return None
        if self._no_target_since is None:
            self._no_target_since = now
            self.tracking_phase = "search_grace"
            if self._demo_mode():
                self._telemetry.update({
                    "demo_zone": "NO_FACE",
                    "demo_hold_active": True,
                    "demo_hold_reason": "no_face_search_grace_hold",
                    "motion_blocked_reason": "demo_no_face_hold",
                    "command_sent": False,
                })
            return None
        search_after_ms = self._param("demo_search_after_ms") if self._demo_mode() else self._param("search_after_ms")
        if now - self._no_target_since < search_after_ms / 1000.0:
            self.tracking_phase = "search_grace"
            if self._demo_mode():
                self._telemetry.update({
                    "demo_zone": "NO_FACE",
                    "demo_hold_active": True,
                    "demo_hold_reason": "no_face_delayed_search_hold",
                    "motion_blocked_reason": "demo_no_face_hold",
                    "command_sent": False,
                })
            return None
        elapsed = now - self._no_target_since
        timeout_s = self._param("search_timeout_ms") / 1000.0
        if elapsed <= timeout_s:
            self.tracking_phase = "limited_search"
            sweep = self._param("demo_search_sweep_deg") if self._demo_mode() else self._param("search_sweep_deg")
            period_s = max(1.0, 2.0 * sweep / max(1.0, self._param("search_yaw_deg_per_sec")))
            yaw = self.center_yaw + sweep*math.sin(max(0.0, elapsed - search_after_ms / 1000.0) / period_s * math.tau)
            pitch_sweep = 0.0 if self._demo_mode() else self._param("search_pitch_sweep_deg")
            pitch = self.center_pitch + pitch_sweep * math.sin(max(0.0, elapsed - search_after_ms / 1000.0) / max(1.0, period_s * 1.7) * math.tau)
            if self._demo_mode():
                self._telemetry.update({
                    "demo_zone": "NO_FACE",
                    "demo_hold_active": False,
                    "motion_blocked_reason": "",
                    "command_delta_yaw_deg": round(abs(float(yaw - self._yaw_target)), 3),
                    "command_delta_pitch_deg": round(abs(float(pitch - self._pitch_target)), 3),
                    "command_sent": True,
                })
            speed = 180 if self._demo_mode() else int(self._param("search_command_speed"))
            return self._command(yaw=yaw, pitch=self._clamp(pitch, 30, 150), speed=speed, reason="limited_search")
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

    def _confirm_face(self, faces, *, reacquiring=False, weight=.6, seek=False):
        valid = [face for face in faces
                 if face.get("track_id") is not None
                 and float(face.get("confidence", face.get("conf", 0.0))) >= self.face_confidence_threshold
                 and self._face_geometry_valid(face)]
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
        if seek:
            # Speaker seek: lock fast — 1 frame at high confidence, else 2
            conf = float(face.get("confidence", face.get("conf", 0.0)))
            needed = self.seek_confirm_fast if conf >= self.seek_confidence else self.seek_confirm_required
        else:
            needed = self.lock_confirm_required
        if self._lock_candidate_frames < needed:
            return None
        self._lock(face)
        return face

    def _soft_face_candidate(self, faces):
        return any(
            face.get("track_id") is not None
            and float(face.get("confidence", face.get("conf", 0.0))) >= 0.45
            and self._face_geometry_valid(face, min_visible_ratio=0.35)
            for face in faces
        )

    def _face_in_frame(self, face):
        return self._face_geometry_valid(face, min_visible_ratio=0.35)

    def _face_geometry_valid(self, face, *, min_visible_ratio=0.55):
        cx = self._norm(face.get("cx"), self.frame_width)
        cy = self._norm(face.get("cy"), self.frame_height)
        if cx is not None and cy is not None and not (0.0 <= float(cx) <= 1.0 and 0.0 <= float(cy) <= 1.0):
            return False
        keypoints = face.get("keypoints") or []
        if keypoints:
            valid_points = 0
            for kp in keypoints:
                try:
                    x = float(kp.get("x"))
                    y = float(kp.get("y"))
                except (AttributeError, TypeError, ValueError):
                    continue
                if 0 <= x <= self.frame_width and 0 <= y <= self.frame_height:
                    valid_points += 1
            if valid_points < min(3, len(keypoints)):
                return False
        box = self._normalize_bbox(face.get("bbox"))
        if box is None:
            return cx is not None and cy is not None
        x1, y1, x2, y2 = box
        area = max(1.0, (x2 - x1) * (y2 - y1))
        visible_w = max(0.0, min(x2, self.frame_width) - max(x1, 0.0))
        visible_h = max(0.0, min(y2, self.frame_height) - max(y1, 0.0))
        return (visible_w * visible_h) / area >= min_visible_ratio

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

    def _remember_person(self, person):
        box = self._normalize_bbox(person.get("bbox"))
        if box is None:
            return
        self._locked_person_bbox = box
        self._last_person_seen_at = time.monotonic()
        self._person_align_frames += 1

    def _recent_person(self, now):
        return (
            self._locked_person_bbox is not None
            and self._last_person_seen_at > 0
            and now - self._last_person_seen_at <= self._param("person_hold_ms") / 1000.0
        )

    def _person_control_item(self, person):
        item = dict(person or {})
        box = self._normalize_bbox(item.get("bbox")) or self._locked_person_bbox
        if box is None:
            return item
        x1, y1, x2, y2 = box
        h = max(1.0, y2 - y1)
        item["bbox"] = box
        item["cx"] = (x1 + x2) / 2.0 / self.frame_width
        item["cy"] = (y1 + h * self._param("person_head_target_y_ratio")) / self.frame_height
        item["face_search_roi"] = self._person_face_roi(item)
        return item

    def _person_face_roi(self, person):
        box = self._normalize_bbox((person or {}).get("bbox"))
        if box is None:
            return None
        x1, y1, x2, y2 = box
        w, h = max(1.0, x2 - x1), max(1.0, y2 - y1)
        expand = self._param("person_face_roi_expand_ratio")
        top = max(0.0, y1)
        bottom = min(float(self.frame_height), y1 + h * self._param("person_face_roi_height_ratio"))
        left = max(0.0, x1 - w * expand)
        right = min(float(self.frame_width), x2 + w * expand)
        if right <= left or bottom <= top:
            return None
        return [round(left, 1), round(top, 1), round(right, 1), round(bottom, 1)]

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
        self._locked_person_bbox = None
        self._last_person_seen_at = 0.0
        self._person_align_frames = 0
        self._reset_search()
        self._doa_candidate = self._active_doa = None
        self._doa_led_ema = None
        self._doa_led_last_sent = None
        self._doa_led_last_time = 0.0
        self._speaker_seek = False
        self._speaker_confidence = 0.0
        self._seek_started_at = 0.0
        self._seek_to_lock_ms = None
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
        """Map a ReSpeaker DOA angle to an absolute gimbal yaw.

        Conventions: ReSpeaker DOA 0° = straight ahead, increasing clockwise
        when viewed from above (90° = right of the camera). Gimbal yaw 180 =
        mechanical center, usable range [1, 345]; the ~±30° zone around DOA
        180° (directly behind) is unreachable and filtered in _audio via
        rear_cone_deg. `doa_offset_deg` compensates rotational mounting offset
        between the mic array and the camera (set via 对准我 calibration);
        `doa_direction` (±1) flips handedness for mirrored mounts.
        """
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
                "speaker_seek":bool(self._speaker_seek),
                "seek_to_lock_ms":self._seek_to_lock_ms,
                "stop_state":self.stop_state,"last_observation_id":self._last_observation_id}
        runtime.update(self._phase1a_telemetry())
        return runtime

    def update_telemetry(self, **fields):
        for key, value in fields.items():
            if key in self._telemetry:
                self._telemetry[key] = value

    def _load_control_params(self):
        defaults = {
            "demo_stop_shake_mode": 0.0,
            "demo_hold_min_x_ratio": 0.30,
            "demo_hold_max_x_ratio": 0.70,
            "demo_hold_min_y_ratio": 0.25,
            "demo_hold_max_y_ratio": 0.75,
            "demo_search_sweep_deg": 3.0,
            "demo_search_after_ms": 2500.0,
            "min_command_interval_ms": 125.0,
            "min_yaw_command_delta_deg": 0.3,
            "min_pitch_command_delta_deg": 0.2,
            "center_deadband_x_ratio": 0.035,
            "center_deadband_y_ratio": 0.045,
            "safe_roi_width_ratio": 0.84,
            "safe_roi_height_ratio": 0.84,
            "edge_margin_x_ratio": 0.08,
            "edge_margin_y_ratio": 0.08,
            "max_yaw_deg_per_sec": 90.0,
            "max_pitch_deg_per_sec": 45.0,
            "max_yaw_delta_deg_per_tick": 6.0,
            "max_pitch_delta_deg_per_tick": 3.0,
            "yaw_smoothing_alpha": 0.55,
            "pitch_smoothing_alpha": 0.45,
            "require_stable_frames": 2.0,
            "search_after_ms": 500.0,
            "search_sweep_deg": 55.0,
            "search_yaw_deg_per_sec": 180.0,
            "search_pitch_sweep_deg": 8.0,
            "search_timeout_ms": 12000.0,
            "search_command_speed": 300.0,
            "person_confirm_frames": 1.0,
            "person_hold_ms": 2500.0,
            "person_head_target_y_ratio": 0.28,
            "person_align_deadband_x_ratio": 0.06,
            "person_align_deadband_y_ratio": 0.08,
            "person_align_max_yaw_delta_deg": 1.5,
            "person_align_max_pitch_delta_deg": 1.0,
            "person_align_command_speed": 140.0,
            "person_face_roi_expand_ratio": 0.12,
            "person_face_roi_height_ratio": 0.45,
        }
        configured = self._tracking_control_config.get("control") if isinstance(self._tracking_control_config, dict) else {}
        if not isinstance(configured, dict):
            return defaults
        params = dict(defaults)
        for key, default in defaults.items():
            try:
                if key == "demo_stop_shake_mode":
                    params[key] = 1.0 if bool(configured.get(key, default)) else 0.0
                else:
                    params[key] = float(configured.get(key, default))
            except (TypeError, ValueError):
                params[key] = default
        params["demo_stop_shake_mode"] = 1.0 if params["demo_stop_shake_mode"] else 0.0
        params["demo_hold_min_x_ratio"] = self._clamp(params["demo_hold_min_x_ratio"], 0.0, 1.0)
        params["demo_hold_max_x_ratio"] = self._clamp(params["demo_hold_max_x_ratio"], 0.0, 1.0)
        params["demo_hold_min_y_ratio"] = self._clamp(params["demo_hold_min_y_ratio"], 0.0, 1.0)
        params["demo_hold_max_y_ratio"] = self._clamp(params["demo_hold_max_y_ratio"], 0.0, 1.0)
        params["demo_search_sweep_deg"] = self._clamp(params["demo_search_sweep_deg"], 0.0, 3.0)
        params["demo_search_after_ms"] = self._clamp(params["demo_search_after_ms"], 2000.0, 10000.0)
        params["min_command_interval_ms"] = self._clamp(params["min_command_interval_ms"], 80.0, 1000.0)
        params["min_yaw_command_delta_deg"] = self._clamp(params["min_yaw_command_delta_deg"], 0.2, 5.0)
        params["min_pitch_command_delta_deg"] = self._clamp(params["min_pitch_command_delta_deg"], 0.15, 5.0)
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
        params["require_stable_frames"] = self._clamp(params["require_stable_frames"], 1.0, 5.0)
        params["search_after_ms"] = self._clamp(params["search_after_ms"], 0.0, 5000.0)
        params["search_sweep_deg"] = self._clamp(params["search_sweep_deg"], 3.0, 120.0)
        params["search_yaw_deg_per_sec"] = self._clamp(params["search_yaw_deg_per_sec"], 30.0, 720.0)
        params["search_pitch_sweep_deg"] = self._clamp(params["search_pitch_sweep_deg"], 0.0, 35.0)
        params["search_timeout_ms"] = self._clamp(params["search_timeout_ms"], 3000.0, 30000.0)
        params["search_command_speed"] = self._clamp(params["search_command_speed"], 60.0, 720.0)
        params["person_confirm_frames"] = self._clamp(params["person_confirm_frames"], 1.0, 5.0)
        params["person_hold_ms"] = self._clamp(params["person_hold_ms"], 250.0, 10000.0)
        params["person_head_target_y_ratio"] = self._clamp(params["person_head_target_y_ratio"], 0.1, 0.5)
        params["person_align_deadband_x_ratio"] = self._clamp(params["person_align_deadband_x_ratio"], 0.01, 0.3)
        params["person_align_deadband_y_ratio"] = self._clamp(params["person_align_deadband_y_ratio"], 0.01, 0.3)
        params["person_align_max_yaw_delta_deg"] = self._clamp(params["person_align_max_yaw_delta_deg"], 0.1, 10.0)
        params["person_align_max_pitch_delta_deg"] = self._clamp(params["person_align_max_pitch_delta_deg"], 0.1, 10.0)
        params["person_align_command_speed"] = self._clamp(params["person_align_command_speed"], 60.0, 360.0)
        params["person_face_roi_expand_ratio"] = self._clamp(params["person_face_roi_expand_ratio"], 0.0, 0.5)
        params["person_face_roi_height_ratio"] = self._clamp(params["person_face_roi_height_ratio"], 0.2, 0.8)
        return params

    def _param(self, name):
        return float(self._control_params[name])

    def _demo_mode(self):
        return bool(self._control_params.get("demo_stop_shake_mode", 0.0))

    def _demo_zone(self, cx, cy, reason):
        if not self._demo_mode():
            return ""
        if reason in {"body_align", "person_align", "face_reacquire_in_person"}:
            return "BODY_ALIGN_ONLY"
        if cx is None or cy is None:
            return "NO_FACE"
        x, y = float(cx), float(cy)
        if x < 0.15 or x > 0.85 or y < 0.15 or y > 0.85:
            return "EDGE"
        if (
            self._param("demo_hold_min_x_ratio") <= x <= self._param("demo_hold_max_x_ratio")
            and self._param("demo_hold_min_y_ratio") <= y <= self._param("demo_hold_max_y_ratio")
        ):
            return "HOLD"
        return "CORRECTION"

    def _demo_hold_condition(self, cx, cy, reason):
        if not self._demo_mode():
            return False
        return self._demo_zone(cx, cy, reason) == "HOLD"

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
        if in_deadband and safe and edge_clear:
            return True, remain_deadband and safe and edge_clear, "inside_deadband_safe_roi_edge_clear", ""
        reasons = []
        if not in_deadband:
            reasons.append("face_center_outside_deadband")
        if not safe:
            reasons.append("bbox_outside_safe_roi")
        if not edge_clear:
            reasons.append("bbox_too_close_to_edge")
        return False, remain_deadband and safe and edge_clear, "", ",".join(reasons) or "not_centered"

    def _person_centered_conditions(self, cx, cy):
        center_x = abs(float(cx) - self.target_x)
        center_y = abs(float(cy) - self.target_y)
        in_deadband = (
            center_x <= self._param("person_align_deadband_x_ratio")
            and center_y <= self._param("person_align_deadband_y_ratio")
        )
        remain = (
            center_x <= self._param("person_align_deadband_x_ratio") * 1.5
            and center_y <= self._param("person_align_deadband_y_ratio") * 1.5
        )
        if in_deadband:
            return True, remain, "person_anchor_inside_deadband", ""
        reasons = []
        if center_x > self._param("person_align_deadband_x_ratio"):
            reasons.append("person_x_outside_deadband")
        if center_y > self._param("person_align_deadband_y_ratio"):
            reasons.append("person_y_outside_deadband")
        return False, remain, "", ",".join(reasons) or "person_not_centered"

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

    def _geometry_telemetry(self):
        safe_x_margin = (1.0 - self._param("safe_roi_width_ratio")) / 2.0
        safe_y_margin = (1.0 - self._param("safe_roi_height_ratio")) / 2.0
        return {
            "deadband_x_px": round(float(self._param("center_deadband_x_ratio") * self.frame_width), 1),
            "deadband_y_px": round(float(self._param("center_deadband_y_ratio") * self.frame_height), 1),
            "safe_roi": {
                "left": round(float(safe_x_margin * self.frame_width), 1),
                "top": round(float(safe_y_margin * self.frame_height), 1),
                "right": round(float((1.0 - safe_x_margin) * self.frame_width), 1),
                "bottom": round(float((1.0 - safe_y_margin) * self.frame_height), 1),
            },
            "edge_margin": {
                "x_px": round(float(self._param("edge_margin_x_ratio") * self.frame_width), 1),
                "y_px": round(float(self._param("edge_margin_y_ratio") * self.frame_height), 1),
            },
        }

    def _phase1a_telemetry(self):
        telemetry = dict(self._telemetry)
        telemetry["tracking_state"] = self._tracking_state()
        telemetry["locked_track_id"] = self.locked_track_id
        telemetry["frame_center"] = {
            "x": round(self.frame_width / 2.0, 1),
            "y": round(self.frame_height / 2.0, 1),
        }
        telemetry.update(self._geometry_telemetry())
        telemetry["target_yaw_deg"] = round(float(self._yaw_target), 3)
        telemetry["target_pitch_deg"] = round(float(self._pitch_target), 3)
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

    def _update_target_telemetry(self, item, box, track_box, cx, cy, ex, ey, centered_reason, centered_block_reason):
        raw_box = self._normalize_bbox(item.get("raw_bbox"))
        if raw_box is None:
            raw_box = self._normalize_bbox(item.get("bbox_raw"))
        if raw_box is None:
            raw_box = self._normalize_bbox(box)
        if track_box is None:
            track_box = raw_box
        if track_box is not None:
            face_x = (track_box[0] + track_box[2]) / 2.0
            face_y = (track_box[1] + track_box[3]) / 2.0
        else:
            face_x, face_y = cx * self.frame_width, cy * self.frame_height
        target_x, target_y = (self.target_x + ex) * self.frame_width, (self.target_y + ey) * self.frame_height
        control_target = {"x": round(float(target_x), 1), "y": round(float(target_y), 1)}
        self._telemetry.update({
            "target_visible": True,
            "raw_bbox": raw_box,
            "track_bbox": track_box,
            "control_target": control_target,
            "last_control_target": dict(control_target),
            "face_center": {"x": round(float(face_x), 1), "y": round(float(face_y), 1)},
            "error_x_px": round(float(ex * self.frame_width), 1),
            "error_y_px": round(float(ey * self.frame_height), 1),
            "error_x_ratio": round(float(ex), 4),
            "error_y_ratio": round(float(ey), 4),
            "centered_reason": centered_reason,
            "centered_block_reason": centered_block_reason,
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
            "centered_reason": "",
            "centered_block_reason": "no_target",
            "demo_zone": "NO_FACE",
            "demo_hold_active": False,
            "demo_hold_reason": "",
            "body_align_suppressed": False,
            "person_visible": False,
            "locked_person_bbox": self._locked_person_bbox,
            "face_search_roi": self._person_face_roi({"bbox": self._locked_person_bbox}),
            "person_align_reason": "",
            "reacquire_reason": "",
            "motion_blocked_reason": "",
            "command_delta_yaw_deg": None,
            "command_delta_pitch_deg": None,
            "command_sent": False,
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
