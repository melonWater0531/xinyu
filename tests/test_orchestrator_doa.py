"""Scripted-event tests for the DOA→gimbal pipeline (P0/P1 upgrades).

No hardware: the Orchestrator and SafetyLayer are driven directly with
synthetic audio/vision events and time is manipulated via monkeypatching
time.monotonic where holds matter.
"""

import time

import pytest

from core.event import ControlCommand, Event
from core.orchestrator_v2 import Orchestrator
from core.safety_layer import SafetyLayer


SESSION = "test-session"


def make_orchestrator(mode: str = "multi_sound_yaw") -> Orchestrator:
    orch = Orchestrator()
    orch.handle_event(Event.make("ui", "feature_start", "test",
                                 payload={"feature": mode, "session_id": SESSION}))
    return orch


def audio_event(doa: float, conf: float = 0.8, speech: bool = True) -> Event:
    return Event.make("audio", "speech_detected", "test", payload={
        "doa_deg": doa, "speech": speech, "session_id": SESSION,
        "vad_confidence": conf, "lip_motion": None,
    })


def drive_audio(orch: Orchestrator, doa: float, conf: float = 0.8,
                repeats: int = 12, step_s: float = 0.11):
    """Feed the same DOA repeatedly (like the 10Hz loop) and return commands."""
    commands = []
    for _ in range(repeats):
        cmd = orch.handle_event(audio_event(doa, conf))
        if cmd is not None:
            commands.append(cmd)
        time.sleep(step_s)
    return commands


class TestDoaToYaw:
    def test_commit_after_hold_single_command(self):
        orch = make_orchestrator()
        commands = drive_audio(orch, 60.0)
        assert len(commands) == 1, "one committed audio_coarse command expected"
        cmd = commands[0]
        assert cmd.reason == "audio_coarse"
        # DOA 60 (right of front) -> yaw 180 + 60 = 240
        assert cmd.yaw == pytest.approx(240.0)
        assert orch.tracking_phase == "audio_coarse"
        # audio_coarse must update the yaw baseline used by fine tracking
        assert orch._yaw_target == pytest.approx(240.0)

    def test_dict_speech_event_returns_yaw_command(self):
        orch = make_orchestrator()
        event = {
            "type": "audio:speech_detected",
            "doa_deg": 90.0,
            "speech": True,
            "session_id": SESSION,
            "vad_confidence": 0.8,
        }
        assert orch.handle_event(event) is None
        orch._doa_candidate_since -= 0.6
        command = orch.handle_event(event)
        assert command is not None
        assert command.reason == "audio_coarse"
        assert command.yaw == pytest.approx(270.0)
        assert command.session_id == SESSION

    def test_lip_motion_false_does_not_block_respeaker_doa(self):
        orch = make_orchestrator()
        event = Event.make("audio", "speech_detected", "test", payload={
            "doa_deg": 50.0,
            "speech": True,
            "session_id": SESSION,
            "vad_confidence": 0.82,
            "lip_motion": False,
        })
        assert orch.handle_event(event) is None
        orch._doa_candidate_since -= 0.6
        command = orch.handle_event(event)
        assert command is not None
        assert command.reason == "audio_coarse"
        assert command.yaw == pytest.approx(230.0)
        assert orch._command_suppressed_reason == "weak_lip_motion"

    def test_doa_candidate_tolerates_threshold_jitter(self):
        orch = make_orchestrator()
        assert orch.handle_event(audio_event(90.0)) is None
        orch._doa_candidate_since -= 0.6
        command = orch.handle_event(audio_event(103.0))
        assert command is not None
        assert command.reason == "audio_coarse"

    def test_jitter_within_dedupe_no_extra_commands(self):
        orch = make_orchestrator()
        drive_audio(orch, 60.0)
        # jitter +/-6 deg around the active DOA: inside the 20-deg dedupe
        extra = []
        for delta in (5.0, -6.0, 4.0, -3.0, 6.0, -5.0):
            cmd = orch.handle_event(audio_event(60.0 + delta))
            if cmd is not None:
                extra.append(cmd)
        assert extra == []

    def test_speaker_switch_requires_longer_hold(self):
        orch = make_orchestrator()
        drive_audio(orch, 40.0)
        # New speaker at 100 deg: no command before the 0.8s switch hold
        early = []
        for _ in range(6):  # ~0.66s
            cmd = orch.handle_event(audio_event(100.0))
            if cmd is not None:
                early.append(cmd)
            time.sleep(0.11)
        assert early == []
        late = drive_audio(orch, 100.0, repeats=4)
        assert len(late) == 1
        assert late[0].yaw == pytest.approx(280.0)

    def test_rear_cone_ignored(self):
        orch = make_orchestrator()
        commands = drive_audio(orch, 175.0)
        assert commands == []
        assert orch.tracking_phase == "audio_rear_ignored"

    def test_offset_applied(self):
        orch = make_orchestrator()
        orch.handle_event(Event.make("ui", "control_config", "test", payload={
            "session_id": SESSION, "doa_offset_deg": -30.0}))
        commands = drive_audio(orch, 60.0)
        assert len(commands) == 1
        # corrected = 60 - 30 = 30 -> yaw 210
        assert commands[0].yaw == pytest.approx(210.0)

    def test_motor_busy_defers_commit(self):
        orch = make_orchestrator()
        drive_audio(orch, 40.0)   # commits, yaw_target = 220
        # Gimbal readback far from target -> motion still in flight
        orch.update_gimbal_readback(180.0, 90.0)
        deferred = drive_audio(orch, 100.0, repeats=12)
        assert deferred == []
        assert orch._command_suppressed_reason == "motor_busy"
        # Motion completes -> readback matches target -> commit allowed
        orch.update_gimbal_readback(220.0, 90.0)
        late = drive_audio(orch, 100.0, repeats=4)
        assert len(late) == 1


class TestSafetySlewCap:
    def _layer(self):
        return SafetyLayer(safe_mode=False, enable_real_control=True,
                           rate_limit_hz=1000.0, max_abs_step_deg=45.0)

    def _cmd(self, yaw, speed=90):
        return ControlCommand.make("test", yaw=yaw, pitch=90.0, speed=speed)

    def test_large_absolute_jump_truncated(self):
        layer = self._layer()
        first = layer.filter(self._cmd(180.0))
        assert first is not None and first.yaw == pytest.approx(180.0)
        time.sleep(0.005)  # clear the 1ms rate limit window
        second = layer.filter(self._cmd(320.0))
        assert second is not None
        assert second.yaw == pytest.approx(225.0), "truncated to last + 45"

    def test_small_step_untouched(self):
        layer = self._layer()
        layer.filter(self._cmd(180.0))
        time.sleep(0.005)
        cmd = layer.filter(self._cmd(184.0))
        assert cmd is not None and cmd.yaw == pytest.approx(184.0)

    def test_delta_mode_step_limit_unchanged(self):
        layer = self._layer()
        blocked = layer.filter(ControlCommand.make("test", mode="delta", yaw=5.0, speed=90))
        assert blocked is None
        assert layer.last_block_reason == "yaw_delta_limit"


class TestSpeakerSeekFastLock:
    def _observation(self, oid, faces):
        return Event.make("vision", "observation", "test", payload={
            "observation_id": oid, "captured_at": time.time(),
            "session_id": SESSION,
            "frame_size": {"width": 1920, "height": 1080},
            "faces": faces, "persons": [],
        })

    def test_high_conf_face_locks_in_one_frame(self):
        orch = make_orchestrator()
        drive_audio(orch, 60.0)
        assert orch._speaker_seek
        face = {"track_id": 7, "cx": 900, "cy": 500,
                "bbox": [800, 400, 1000, 640], "confidence": 0.9, "lost_frames": 0}
        orch.handle_event(self._observation(1, [face]))
        assert orch.locked_track_id == 7
        assert not orch._speaker_seek
        assert orch.runtime_state()["seek_to_lock_ms"] is not None

    def test_low_conf_face_needs_two_frames(self):
        orch = make_orchestrator()
        drive_audio(orch, 60.0)
        face = {"track_id": 3, "cx": 900, "cy": 500,
                "bbox": [800, 400, 1000, 640], "confidence": 0.70, "lost_frames": 0}
        orch.handle_event(self._observation(1, [face]))
        assert orch.locked_track_id is None, "0.70 conf must not lock in 1 frame"
        orch.handle_event(self._observation(2, [face]))
        assert orch.locked_track_id == 3

    def test_meeting_recording_mode_receives_audio(self):
        orch = make_orchestrator(mode="meeting_recording")
        commands = drive_audio(orch, 60.0)
        assert len(commands) == 1, "meeting_recording must drive DOA coarse aim"


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
