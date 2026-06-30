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

    @property
    def state(self):
        return self.fsm.state

    def update_gimbal_readback(self, yaw, pitch):
        if yaw is not None:
            self._gimbal_yaw = self._yaw_target = float(yaw)
        if pitch is not None:
            self._gimbal_pitch = self._pitch_target = float(pitch)

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
        if face is None and self.locked_track_id is not None and now - self._last_lock_seen <= 0.6:
            self.tracking_phase = "occlusion_hold"
            return None
        if face is None:
            self._unlock()
            face = self._best_face(faces)
            if face:
                self._lock(face)
        if face:
            self._vision_lost_frames = 0
            self._reset_search()
            self.tracking_phase = "face_lock"
            self.fsm.transition(Event.make("vision", "target_detected", "orchestrator"))
            return self._track(face, "face_lock")
        person = self._best_person(persons)
        if person:
            self._vision_lost_frames = 0
            self._reset_search()
            self.tracking_phase = "body_align"
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
            return self._track(face, "speaker_face_lock")
        if self.locked_track_id is not None and time.monotonic() - self._last_lock_seen <= 0.6:
            self.tracking_phase = "speaker_occlusion_hold"
            return None
        self._unlock()
        if self._speaker_seek and faces:
            face = self._best_face(faces, 1.4)
            self._lock(face)
            self._speaker_seek = False
            self.tracking_phase = "speaker_face_lock"
            return self._track(face, "speaker_face_lock")
        self.tracking_phase = "speaker_reacquire" if self._speaker_seek else "audio_wait"
        return None

    def _audio(self, event):
        now = time.monotonic()
        if event.name == "timeout" or not bool(event.payload.get("speech", True)):
            if now - self._last_speech_at > 1.5 and self.locked_track_id is not None:
                self.tracking_phase = "speaker_hold"
            return None
        doa = float(event.payload.get("doa_deg", 0.0)) % 360
        self._last_speech_at = now
        if self._active_doa is not None and self._angle(doa, self._active_doa) <= 20:
            self._doa_candidate = None
            return None
        if self._doa_candidate is None or self._angle(doa, self._doa_candidate) > 8:
            self._doa_candidate, self._doa_candidate_since = doa, now
            return None
        if now - self._doa_candidate_since < 0.5:
            return None
        self._active_doa, self._doa_candidate, self._speaker_seek = doa, None, True
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
        alpha = 0.45
        self._ema_x = cx if self._ema_x is None else alpha*cx + (1-alpha)*self._ema_x
        self._ema_y = cy if self._ema_y is None else alpha*cy + (1-alpha)*self._ema_y
        ex, ey = self._ema_x - .5, self._ema_y - .5
        enter, remain = abs(ex) <= .03 and abs(ey) <= .04, abs(ex) <= .05 and abs(ey) <= .06
        if enter or (self._centered and remain):
            self._centered = True
            self.tracking_phase = "speaker_centered" if "speaker" in reason else "locked_centered"
            return None
        self._centered = False
        base_y = self._gimbal_yaw if self._gimbal_yaw is not None else self._yaw_target
        base_p = self._gimbal_pitch if self._gimbal_pitch is not None else self._pitch_target
        yaw = self._clamp(base_y + self._clamp(-ex*self.vision_yaw_gain, -12, 12), 1, 345)
        pitch = self._clamp(base_p + self._clamp(ey*self.vision_pitch_gain, -8, 8), 30, 150)
        mag = max(abs(ex), abs(ey))
        speed = 360 if mag > .25 else 180 if mag > .10 else 90
        self._yaw_target, self._pitch_target = yaw, pitch
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
            if self.locked_track_id is not None and int(face.get("track_id", -1)) == self.locked_track_id:
                self._last_lock_seen = time.monotonic()
                return face
        return None

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
        self._last_lock_seen = time.monotonic()
        self._ema_x = self._ema_y = None
        self._centered = False

    def _unlock(self):
        self.locked_track_id = None
        self._ema_x = self._ema_y = None
        self._centered = False

    def _reset_search(self):
        self._no_target_since, self._home_sent_at, self._search_exhausted = None, 0.0, False

    def _reset(self):
        self.fsm.transition(Event.make("system", "control_reset", "orchestrator"))
        self._last_observation_id, self._vision_lost_frames = -1, 0
        self._unlock()
        self._reset_search()
        self._doa_candidate = self._active_doa = None
        self._speaker_seek = False

    def _ui_allowed(self, event):
        if not self.session.matches(str(event.payload.get("session_id", ""))):
            return False
        return self.session.mode is ControlMode.MANUAL_GIMBAL_DEBUG if event.name == "dpad_move" else event.name in {"gimbal_home","gimbal_sleep","gimbal_stop"}

    def _ui(self, event):
        if event.name == "dpad_move":
            return self._command(mode="delta", yaw=self._clamp(float(event.payload.get("pan",0)),-2.5,2.5), pitch=self._clamp(float(event.payload.get("tilt",0)),-2.5,2.5), speed=self.default_speed, reason="ui_dpad_move")
        if event.name == "gimbal_home":
            return self._command(yaw=self.center_yaw,pitch=self.center_pitch,speed=self.default_speed,reason="standby")
        if event.name == "gimbal_sleep":
            return self._command(yaw=self.center_yaw,pitch=180,speed=self.default_speed,reason="sleep")
        if event.name == "gimbal_stop":
            return self._command(stop=True,reason="ui_stop")
        return None

    def _doa_yaw(self, doa):
        corrected=(float(doa)+self.doa_offset_deg)%360
        signed=corrected if corrected<=180 else corrected-360
        return self._clamp(self.center_yaw+signed*self.doa_direction,1,345)

    def _command(self, *, session_id=None, **kwargs):
        self._command_sequence += 1
        return ControlCommand.make("orchestrator",session_id=self.session.session_id if session_id is None else session_id,sequence=self._command_sequence,ttl_s=.75,**kwargs)

    def runtime_state(self):
        return {**self.session.snapshot(),"fsm_state":self.state.value,"speed":self.default_speed,
                "doa_offset_deg":self.doa_offset_deg,"doa_direction":int(self.doa_direction),
                "locked_track_id":self.locked_track_id,"tracking_phase":self.tracking_phase,
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
