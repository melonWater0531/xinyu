"""Single-session target selection and gimbal command orchestration."""
from __future__ import annotations
import math
import time
from typing import Optional, Sequence
from core.control_session import ControlMode, ControlSession
from core.event import BBox, ControlCommand, Event
from core.fsm import FSM, SystemState


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
        oid = int(event.payload.get("observation_id", -1))
        captured = float(event.payload.get("captured_at", event.timestamp))
        captured = captured * 1000 if captured < 10_000_000_000 else captured
        if oid <= self._last_observation_id or time.time() * 1000 - captured > 600:
            return None
        self._last_observation_id, self._frame_count = oid, self._frame_count + 1
        size = event.payload.get("frame_size") or {}
        self.frame_width = max(1, int(size.get("width", self.frame_width)))
        self.frame_height = max(1, int(size.get("height", self.frame_height)))
        faces = [x for x in event.payload.get("faces", []) if int(x.get("lost_frames", 0) or 0) == 0]
        persons = event.payload.get("persons", [])
        return self._single(faces, persons) if self.session.mode is ControlMode.SINGLE_FACE_ANALYSIS else self._multi(faces)

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
        if (cx is None or cy is None) and len(box) >= 4:
            x1, y1, x2, y2 = map(float, box[:4])
            cx = (x1 + x2) / 2 / self.frame_width
            cy = (y1 + (y2-y1) * (0.28 if reason == "body_align" else 0.5)) / self.frame_height
        if cx is None or cy is None:
            return None
        alpha = 0.20
        self._ema_x = cx if self._ema_x is None else alpha*cx + (1-alpha)*self._ema_x
        self._ema_y = cy if self._ema_y is None else alpha*cy + (1-alpha)*self._ema_y
        ex, ey = self._ema_x - self.target_x, self._ema_y - self.target_y
        self._tracking_error = {"x": round(ex, 4), "y": round(ey, 4)}
        enter, remain = abs(ex) <= .05 and abs(ey) <= .06, abs(ex) <= .08 and abs(ey) <= .10
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
        yaw_step = self._clamp(-ex*45.0, -5, 5)
        pitch_step = self._clamp(ey*30.0, -3, 3)
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
        return {**self.session.snapshot(),"fsm_state":self.state.value,"speed":self.default_speed,
                "doa_offset_deg":self.doa_offset_deg,"doa_direction":int(self.doa_direction),
                "locked_track_id":self.locked_track_id,"tracking_phase":self.tracking_phase,
                "lock_state":self.lock_state,"lock_candidate_id":self._lock_candidate_id,
                "lock_confirm_frames":self._lock_candidate_frames,
                "target_point":{"x":round(self.target_x,3),"y":round(self.target_y,3),"framing_mode":self.framing_mode},
                "tracking_error":dict(self._tracking_error),
                "command_suppressed_reason":self._command_suppressed_reason,
                "speaker_confidence":round(self._speaker_confidence,3),
                "stop_state":self.stop_state,"last_observation_id":self._last_observation_id}

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
