"""Central Event -> ControlCommand decision engine."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional, Sequence

from core.event import BBox, ControlCommand, Event
from core.control_session import ControlMode, ControlSession
from core.fsm import FSM, SystemState


@dataclass
class TargetState:
    audio_doa_deg: Optional[float] = None
    audio_speech: bool = False
    audio_ts: float = 0.0
    vision_cx: Optional[float] = None
    vision_cy: Optional[float] = None
    vision_conf: float = 0.0
    vision_ts: float = 0.0
    yaw_target: float = 180.0
    pitch_target: float = 90.0
    vision_class: str = ""


class Orchestrator:
    """Only module allowed to decide automatic gimbal commands."""

    def __init__(
        self,
        *,
        center_yaw: float = 180.0,
        center_pitch: float = 90.0,
        audio_max_step: float = 12.0,
        vision_yaw_gain: float = 60.0,
        vision_pitch_gain: float = 30.0,
        audio_stale_s: float = 1.0,
        vision_stale_s: float = 0.8,
        frame_width: int = 1920,
        frame_height: int = 1080,
        default_speed: int = 180,
        lease_ms: int = 2500,
    ) -> None:
        self.fsm = FSM()
        self.target = TargetState(yaw_target=center_yaw, pitch_target=center_pitch)
        self.center_yaw = float(center_yaw)
        self.center_pitch = float(center_pitch)
        self.audio_max_step = float(audio_max_step)
        self.vision_yaw_gain = float(vision_yaw_gain)
        self.vision_pitch_gain = float(vision_pitch_gain)
        self.audio_stale_s = float(audio_stale_s)
        self.vision_stale_s = float(vision_stale_s)
        self.frame_width = max(1, int(frame_width))
        self.frame_height = max(1, int(frame_height))
        self.default_speed = max(1, min(720, int(default_speed)))
        self.doa_offset_deg = 0.0
        self.doa_direction = 1.0
        self.session = ControlSession(default_lease_ms=lease_ms)
        self._vision_lost_frames = 0
        self._frame_count = 0

        # Tilt search (Stage 2): pitch up/down to find face when person visible
        self._tilt_dir: int = -1                  # -1=tilt up (decrease pitch), +1=tilt down
        self._tilt_step: float = 1.0              # °/frame = 5°/s @ 5Hz
        self._tilt_conf_history: list = []        # last 5 frames confidence
        self._tilt_reverse_count: int = 0         # consecutive "confidence dropping" frames

        # Sweep scan (Stage 3): pan when no target is visible
        self._sweep_idle_frames: int = 0          # consecutive frames with no target
        self._sweep_start_delay: int = 30         # frames before sweep begins (~3s @ 10fps)
        self._sweep_yaw: float = float(center_yaw)
        self._sweep_step: float = 1.5             # °/frame = 7.5°/s @ 5Hz command rate
        self._sweep_dir: int = 1                  # +1 = right, -1 = left
        self._sweep_min: float = float(center_yaw) - 50.0
        self._sweep_max: float = float(center_yaw) + 50.0

    @property
    def state(self) -> SystemState:
        return self.fsm.state

    def handle_event(self, event: Event) -> Optional[ControlCommand]:
        lifecycle = self._handle_lifecycle(event)
        if lifecycle is not _NOT_LIFECYCLE:
            return lifecycle

        mode = self.session.mode
        if mode is ControlMode.INACTIVE:
            return None
        if event.type == "vision" and mode is not ControlMode.SINGLE_FACE_ANALYSIS:
            return None
        if event.type == "audio" and mode not in {ControlMode.MULTI_SOUND_YAW, ControlMode.MEETING_SOUND_YAW}:
            return None
        if event.type == "ui" and not self._ui_event_allowed(event):
            return None

        self._ingest(event)
        state = self.fsm.transition(event)

        if event.type == "ui":
            return self._ui_command(event)
        if event.type == "system":
            return self._system_command(event)
        if state == SystemState.AUDIO_SEARCH:
            return self._audio_command(event)
        if event.type == "vision" and event.name == "target_lost":
            return self._sweep_scan()
        if state == SystemState.VISION_TRACK:
            if self.target.vision_class == "face":
                self._reset_tilt_search()
                return self._vision_command("vision_track")
            return self._tilt_search(self.target.vision_conf)
        if state == SystemState.FUSED_TRACK:
            return self._fused_command()
        if state == SystemState.LOST and event.name == "timeout":
            return ControlCommand.make("orchestrator", yaw=self.center_yaw, pitch=self.center_pitch, reason="lost_timeout")
        return None

    def _handle_lifecycle(self, event: Event):
        if event.type == "ui" and event.name == "feature_start":
            accepted = self.session.start(
                str(event.payload.get("feature", "")),
                str(event.payload.get("session_id", "")),
                event.payload.get("lease_ms"),
            )
            if accepted:
                self._reset_control_context()
            return None
        if event.type == "ui" and event.name == "feature_heartbeat":
            self.session.heartbeat(str(event.payload.get("session_id", "")), event.payload.get("lease_ms"))
            return None
        if event.type == "ui" and event.name == "feature_mode_update":
            accepted = self.session.update_mode(
                str(event.payload.get("feature", "")),
                str(event.payload.get("session_id", "")),
                event.payload.get("lease_ms"),
            )
            if accepted:
                self._reset_control_context()
            return None
        if event.type == "ui" and event.name == "feature_stop":
            if self.session.stop(str(event.payload.get("session_id", ""))):
                self._reset_control_context()
                return ControlCommand.make("orchestrator", stop=True, reason="feature_stop")
            return None
        if event.type == "ui" and event.name == "control_config":
            if not self.session.matches(str(event.payload.get("session_id", ""))):
                return None
            if "speed" in event.payload:
                self.default_speed = max(1, min(720, int(event.payload["speed"])))
            if "doa_offset_deg" in event.payload:
                self.doa_offset_deg = max(-180.0, min(180.0, float(event.payload["doa_offset_deg"])))
            if "doa_direction" in event.payload:
                self.doa_direction = -1.0 if float(event.payload["doa_direction"]) < 0 else 1.0
            return None
        if event.type == "system" and event.name in {"lease_expired", "shutdown", "emergency_stop"}:
            self.session.clear()
            self._reset_control_context()
            return ControlCommand.make("orchestrator", stop=True, reason=event.name)
        return _NOT_LIFECYCLE

    def _ui_event_allowed(self, event: Event) -> bool:
        session_id = str(event.payload.get("session_id", ""))
        if not self.session.matches(session_id):
            return False
        if event.name == "dpad_move":
            return self.session.mode is ControlMode.MANUAL_GIMBAL_DEBUG
        return event.name in {"gimbal_home", "gimbal_sleep", "gimbal_stop"}

    def _reset_control_context(self) -> None:
        self.fsm.transition(Event.make("system", "control_reset", "orchestrator"))
        self.target.audio_speech = False
        self.target.vision_conf = 0.0
        self.target.vision_class = ""
        self._vision_lost_frames = 0
        self._sweep_idle_frames = 0
        self._reset_tilt_search()

    def runtime_state(self) -> dict:
        return {
            **self.session.snapshot(),
            "fsm_state": self.state.value,
            "speed": self.default_speed,
            "doa_offset_deg": self.doa_offset_deg,
            "doa_direction": int(self.doa_direction),
        }

    def handle(self, event: Event) -> Optional[ControlCommand]:
        """Compatibility alias: all callers should move to handle_event."""
        return self.handle_event(event)

    def handle_vision(self, bboxes: Sequence[BBox], *, source: str = "vision") -> Optional[ControlCommand]:
        self._frame_count += 1
        if bboxes:
            primary = bboxes[0]
            self._vision_lost_frames = 0
            self._sweep_idle_frames = 0
            self._sweep_yaw = self.target.yaw_target  # anchor sweep to current position
            event = Event.make(
                "vision", "target_detected", source,
                {
                    "cx": primary.center_x / self.frame_width,
                    "cy": primary.center_y / self.frame_height,
                    "conf": primary.confidence,
                },
            )
            has_face = primary.class_name == "face"
            if has_face:
                # Stage 1: face visible — normal proportional tracking
                self._reset_tilt_search()
                return self.handle_event(event)
            else:
                # Stage 2: person visible but no face — tilt to search
                # Still update FSM (stays VISION_TRACK) but override motion command
                base_cmd = self.handle_event(event)
                if self.fsm.state in (SystemState.AUDIO_SEARCH, SystemState.FUSED_TRACK):
                    return base_cmd  # audio is driving, let fusion handle
                return self._tilt_search(primary.confidence)
        else:
            self._vision_lost_frames += 1
            event = Event.make("vision", "target_lost", source, {"conf": 0.0})
            base_cmd = self.handle_event(event)
            # Stage 3: no person — pan sweep (unless audio is driving)
            if self.fsm.state in (SystemState.AUDIO_SEARCH, SystemState.FUSED_TRACK):
                return base_cmd
            return self._sweep_scan() or base_cmd

    @property
    def vision_lost_frames(self) -> int:
        return self._vision_lost_frames

    @property
    def frame_count(self) -> int:
        return self._frame_count

    def _ingest(self, event: Event) -> None:
        if event.type == "audio":
            if "doa_deg" in event.payload:
                self.target.audio_doa_deg = float(event.payload["doa_deg"])
            self.target.audio_speech = bool(event.payload.get("speech", event.name == "speech_detected"))
            self.target.audio_ts = event.ts
        elif event.type == "vision":
            if event.name == "target_lost":
                self.target.vision_conf = 0.0
                return
            self.target.vision_cx = self._clamp(float(event.payload.get("cx", 0.5)), 0.0, 1.0)
            self.target.vision_cy = self._clamp(float(event.payload.get("cy", 0.5)), 0.0, 1.0)
            self.target.vision_conf = float(event.payload.get("conf", 0.0))
            self.target.vision_class = str(event.payload.get("class_name", "target"))
            self.target.vision_ts = event.ts

    def _ui_command(self, event: Event) -> Optional[ControlCommand]:
        if event.name == "dpad_move":
            pan = self._clamp(float(event.payload.get("pan", 0.0)), -2.5, 2.5)
            tilt = self._clamp(float(event.payload.get("tilt", 0.0)), -2.5, 2.5)
            return ControlCommand.make("orchestrator", mode="delta", yaw=pan, pitch=tilt, speed=self.default_speed, reason="ui_dpad_move")
        if event.name == "gimbal_home":
            return ControlCommand.make("orchestrator", yaw=self.center_yaw, pitch=self.center_pitch, speed=self.default_speed, reason="standby")
        if event.name == "gimbal_sleep":
            return ControlCommand.make("orchestrator", yaw=self.center_yaw, pitch=180.0, speed=self.default_speed, reason="sleep")
        if event.name == "gimbal_stop":
            return ControlCommand.make("orchestrator", stop=True, reason="ui_stop")
        return None

    def _system_command(self, event: Event) -> Optional[ControlCommand]:
        if event.name in {"shutdown", "emergency_stop"}:
            return ControlCommand.make("orchestrator", stop=True, reason=event.name)
        return None

    def _audio_command(self, event: Event) -> Optional[ControlCommand]:
        if not self._audio_fresh(event.ts):
            return None
        yaw = self._doa_to_yaw(float(self.target.audio_doa_deg or 0.0))
        self.target.yaw_target = yaw
        return ControlCommand.make("orchestrator", yaw=yaw, speed=self.default_speed, reason="audio_only_loop")

    def _vision_command(self, reason: str) -> Optional[ControlCommand]:
        if self.target.vision_cx is None or self.target.vision_cy is None:
            return None
        err_x = self.target.vision_cx - 0.5
        err_y = self.target.vision_cy - 0.5
        yaw = self._clamp(self.target.yaw_target - err_x * self.vision_yaw_gain, 1.0, 345.0)
        pitch = self._clamp(self.target.pitch_target + err_y * self.vision_pitch_gain, 30.0, 150.0)
        self.target.yaw_target = yaw
        self.target.pitch_target = pitch
        return ControlCommand.make("orchestrator", yaw=yaw, pitch=pitch, speed=self.default_speed, reason=reason)

    def _fused_command(self) -> Optional[ControlCommand]:
        now = time.time()
        if self._vision_fresh(now):
            cmd = self._vision_command("fusion_loop")
            if cmd and self._audio_fresh(now) and self.target.audio_doa_deg is not None:
                audio_yaw = self._doa_to_yaw(self.target.audio_doa_deg)
                yaw = 0.85 * float(cmd.yaw or self.target.yaw_target) + 0.15 * audio_yaw
                yaw = self._clamp(yaw, 1.0, 345.0)
                self.target.yaw_target = yaw
                return ControlCommand.make("orchestrator", yaw=yaw, pitch=cmd.pitch, speed=self.default_speed, reason="fusion_loop")
            return cmd
        if self._audio_fresh(now):
            return self._audio_command(Event.make("audio", "speech_detected", "orchestrator", {"doa_deg": self.target.audio_doa_deg, "speech": True}))
        return None

    def _audio_fresh(self, now: float) -> bool:
        return self.target.audio_doa_deg is not None and (now - self.target.audio_ts) <= self.audio_stale_s

    def _vision_fresh(self, now: float) -> bool:
        return self.target.vision_conf > 0.0 and (now - self.target.vision_ts) <= self.vision_stale_s

    def _doa_to_yaw(self, doa_deg: float) -> float:
        corrected = (float(doa_deg) + self.doa_offset_deg) % 360.0
        signed = corrected if corrected <= 180.0 else corrected - 360.0
        signed *= self.doa_direction
        target = self._clamp(self.center_yaw + signed, 1.0, 345.0)
        delta = target - self.target.yaw_target
        if abs(delta) > self.audio_max_step:
            target = self.target.yaw_target + (self.audio_max_step if delta > 0 else -self.audio_max_step)
        return self._clamp(target, 1.0, 345.0)

    def _tilt_search(self, current_conf: float) -> Optional[ControlCommand]:
        """Stage 2: person visible, tilt up/down to find face."""
        self._tilt_conf_history.append(current_conf)
        if len(self._tilt_conf_history) > 5:
            self._tilt_conf_history.pop(0)

        # Reverse direction if confidence has been dropping for 3 consecutive frames
        if len(self._tilt_conf_history) >= 5:
            recent_avg = sum(self._tilt_conf_history[-3:]) / 3
            baseline_avg = sum(self._tilt_conf_history[:2]) / 2
            if recent_avg < baseline_avg - 0.02:
                self._tilt_reverse_count += 1
            else:
                self._tilt_reverse_count = 0
            if self._tilt_reverse_count >= 3:
                self._tilt_dir *= -1
                self._tilt_reverse_count = 0
                self._tilt_conf_history.clear()

        new_pitch = self._clamp(
            self.target.pitch_target + self._tilt_step * self._tilt_dir,
            30.0, 150.0,
        )
        self.target.pitch_target = new_pitch
        return ControlCommand.make(
            "orchestrator",
            yaw=self.target.yaw_target,
            pitch=new_pitch,
            speed=self.default_speed,
            reason="tilt_search",
        )

    def _reset_tilt_search(self) -> None:
        """Reset Stage 2 state when face is found."""
        self._tilt_dir = -1  # default: tilt up next time
        self._tilt_conf_history = []
        self._tilt_reverse_count = 0

    def _sweep_scan(self) -> Optional[ControlCommand]:
        """Stage 3: pan left-right when no target is visible."""
        self._sweep_idle_frames += 1
        if self._sweep_idle_frames < self._sweep_start_delay:
            # Still in grace period — hold center before starting
            return ControlCommand.make(
                "orchestrator",
                yaw=self.center_yaw,
                pitch=self.center_pitch,
                speed=self.default_speed,
                reason="return_center",
            )
        # Advance sweep position
        self._sweep_yaw += self._sweep_step * self._sweep_dir
        if self._sweep_yaw >= self._sweep_max:
            self._sweep_yaw = self._sweep_max
            self._sweep_dir = -1
        elif self._sweep_yaw <= self._sweep_min:
            self._sweep_yaw = self._sweep_min
            self._sweep_dir = 1
        return ControlCommand.make(
            "orchestrator",
            yaw=self._clamp(self._sweep_yaw, 1.0, 345.0),
            pitch=self.center_pitch,
            speed=self.default_speed,
            reason="idle_scan",
        )

    @staticmethod
    def _clamp(value: float, low: float, high: float) -> float:
        return max(low, min(high, float(value)))


def make_system_command(name: str, source: str = "system") -> Optional[ControlCommand]:
    """Create system commands through the orchestrator module."""
    return Orchestrator().handle_event(Event.make("system", name, source))


_NOT_LIFECYCLE = object()

# The event-driven implementation consumes FastAPI vision/observation events.
# Keep this module path stable for existing imports and deployments.
from core.orchestrator_v2 import Orchestrator as EventDrivenOrchestrator
Orchestrator = EventDrivenOrchestrator
