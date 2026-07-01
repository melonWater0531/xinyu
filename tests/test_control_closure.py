from __future__ import annotations

import time
import unittest
import socket

from core.control_session import ControlMode
from core.event import Event
from core.event_bus import EventBusClient, EventBusServer
from core.orchestrator import Orchestrator
from core.safety_layer import SafetyLayer
from vision.data_source import RealVisionSource


def ui(name: str, **payload) -> Event:
    return Event.make("ui", name, "test", payload=payload)


class ControlClosureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.orch = Orchestrator()

    def start(self, feature: str, session_id: str = "s1") -> None:
        self.orch.handle_event(ui("feature_start", feature=feature, session_id=session_id, lease_ms=2500))

    def test_inactive_ignores_perception(self) -> None:
        self.assertIsNone(self.orch.handle_event(Event.make("vision", "target_detected", "test", payload={"cx": .2, "cy": .5, "conf": .9, "class_name": "face"})))
        self.assertIsNone(self.orch.handle_event(Event.make("audio", "speech_detected", "test", payload={"doa_deg": 35, "speech": True})))

    def test_single_accepts_vision_and_ignores_audio(self) -> None:
        self.start("single_face_analysis")
        event = Event.make("vision", "target_detected", "test", payload={"cx": .25, "cy": .45, "conf": .9, "class_name": "face"})
        self.orch.handle_event(event)
        self.orch.handle_event(event)
        command = self.orch.handle_event(event)
        self.assertIsNotNone(command)
        self.assertIsNotNone(command.pitch)
        self.assertIsNone(self.orch.handle_event(Event.make("audio", "speech_detected", "test", payload={"doa_deg": 35, "speech": True})))

    def test_multi_accepts_audio_as_yaw_only(self) -> None:
        self.start("multi_sound_yaw")
        payload = {"doa_deg": 35, "speech": True, "session_id": "s1"}
        self.assertIsNone(self.orch.handle_event(Event.make("audio", "speech_detected", "test", payload=payload)))
        self.orch._doa_candidate_since -= .6
        command = self.orch.handle_event(Event.make("audio", "speech_detected", "test", payload=payload))
        self.assertIsNotNone(command)
        self.assertIsNotNone(command.yaw)
        self.assertIsNone(command.pitch)
        self.assertIsNone(self.orch.handle_event(Event.make("vision", "target_detected", "test", payload={"cx": .2, "cy": .5, "conf": .9})))

    def test_manual_requires_current_session(self) -> None:
        self.start("manual_gimbal_debug")
        self.assertIsNone(self.orch.handle_event(ui("dpad_move", session_id="old", pan=2, tilt=1)))
        command = self.orch.handle_event(ui("dpad_move", session_id="s1", pan=2, tilt=1))
        self.assertEqual(command.mode, "delta")

    def test_official_standby_sleep_and_calibrate_require_session(self) -> None:
        self.start("manual_gimbal_debug")
        self.assertIsNone(self.orch.handle_event(ui("gimbal_sleep", session_id="old")))
        standby = self.orch.handle_event(ui("gimbal_standby", session_id="s1"))
        self.assertEqual(standby.reason, "standby")
        self.assertEqual((standby.yaw, standby.pitch, standby.speed), (180.0, 90.0, 360))
        sleep = self.orch.handle_event(ui("gimbal_sleep", session_id="s1"))
        self.assertEqual(sleep.reason, "sleep")
        self.assertEqual((sleep.yaw, sleep.pitch, sleep.speed), (180.0, 175.0, 360))
        calibrate = self.orch.handle_event(ui("gimbal_calibrate", session_id="s1"))
        self.assertEqual(calibrate.action, "calibrate")
        self.assertEqual(calibrate.reason, "calibrate")
        self.assertEqual(self.orch.session.mode, ControlMode.INACTIVE)

    def test_new_session_takes_over_and_old_stop_is_ignored(self) -> None:
        self.start("single_face_analysis", "old")
        self.start("multi_sound_yaw", "new")
        self.assertIsNone(self.orch.handle_event(ui("feature_stop", session_id="old")))
        self.assertEqual(self.orch.session.mode, ControlMode.MULTI_SOUND_YAW)
        command = self.orch.handle_event(ui("feature_stop", session_id="new"))
        self.assertTrue(command.stop)
        self.assertEqual(self.orch.session.mode, ControlMode.INACTIVE)

    def test_expired_session_can_be_stopped_by_system_event(self) -> None:
        self.start("single_face_analysis")
        self.orch.session._deadline = time.monotonic() - .1
        self.assertTrue(self.orch.session.expired())
        command = self.orch.handle_event(Event.make("system", "lease_expired", "test"))
        self.assertTrue(command.stop)
        self.assertEqual(self.orch.session.mode, ControlMode.INACTIVE)

    def test_safety_speed_is_hard_gate(self) -> None:
        layer = SafetyLayer(safe_mode=False, enable_real_control=True, rate_limit_hz=1000)
        self.start("manual_gimbal_debug")
        valid = self.orch.handle_event(ui("dpad_move", session_id="s1", pan=1, tilt=1))
        self.assertIs(layer.filter(valid), valid)
        invalid = type(valid).make("test", mode="delta", yaw=1, pitch=1, speed=900)
        invalid_layer = SafetyLayer(safe_mode=False, enable_real_control=True, rate_limit_hz=1000)
        self.assertIsNone(invalid_layer.filter(invalid))
        self.assertEqual(invalid_layer.last_block_reason, "speed_range")

    def observation(self, oid: int, *, faces=None, persons=None, session_id="s1") -> Event:
        return Event.make("vision", "observation", "test", payload={
            "session_id": session_id,
            "observation_id": oid,
            "captured_at": time.time() * 1000,
            "frame_size": {"width": 1280, "height": 720},
            "faces": faces or [],
            "persons": persons or [],
        })

    def test_observation_uses_normalized_dynamic_resolution(self) -> None:
        self.start("single_face_analysis")
        centered = {"track_id": 7, "cx": 640, "cy": 360, "confidence": .95, "lost_frames": 0}
        command = self.orch.handle_event(self.observation(1, faces=[centered]))
        self.assertIsNone(command)
        self.assertEqual(self.orch.locked_track_id, 7)
        self.assertEqual(self.orch.tracking_phase, "locked_centered")

    def test_stale_track_is_not_display_or_control_candidate(self) -> None:
        self.start("single_face_analysis")
        stale = {"track_id": 1, "cx": .2, "cy": .4, "confidence": .99, "lost_frames": 2}
        current = {"track_id": 2, "cx": .7, "cy": .5, "confidence": .9, "lost_frames": 0}
        command = self.orch.handle_event(self.observation(1, faces=[stale, current]))
        self.assertIsNotNone(command)
        self.assertEqual(self.orch.locked_track_id, 2)

    def test_old_session_and_out_of_order_observations_are_ignored(self) -> None:
        self.start("single_face_analysis")
        face = {"track_id": 3, "cx": .2, "cy": .5, "confidence": .9, "lost_frames": 0}
        self.assertIsNone(self.orch.handle_event(self.observation(1, faces=[face], session_id="old")))
        self.assertIsNotNone(self.orch.handle_event(self.observation(2, faces=[face])))
        self.assertIsNone(self.orch.handle_event(self.observation(1, faces=[face])))

    def test_single_search_times_out_to_standby(self) -> None:
        self.start("single_face_analysis")
        self.orch.handle_event(self.observation(1))
        self.orch._no_target_since -= 8.1
        home = self.orch.handle_event(self.observation(2))
        self.assertEqual(home.reason, "search_timeout_home")
        self.orch.update_gimbal_readback(180, 90)
        self.assertIsNone(self.orch.handle_event(self.observation(3)))
        self.assertEqual(self.orch.tracking_phase, "standby_stopped")

    def test_multi_stable_doa_then_visual_lock(self) -> None:
        self.start("multi_sound_yaw")
        payload = {"doa_deg": 40, "speech": True, "session_id": "s1"}
        self.orch.handle_event(Event.make("audio", "speech_detected", "test", payload=payload))
        self.orch._doa_candidate_since -= .6
        coarse = self.orch.handle_event(Event.make("audio", "speech_detected", "test", payload=payload))
        self.assertEqual(coarse.reason, "audio_coarse")
        face = {"track_id": 9, "cx": .55, "cy": .5, "confidence": .9, "lost_frames": 0}
        self.orch.handle_event(self.observation(1, faces=[face]))
        self.assertEqual(self.orch.locked_track_id, 9)
        self.orch._last_speech_at -= 1.6
        self.assertIsNone(self.orch.handle_event(self.observation(2, faces=[face])))
        self.assertEqual(self.orch.tracking_phase, "speaker_hold")

    def test_sscma_center_size_box_conversion(self) -> None:
        class Stream:
            boxes = [[640, 360, 400, 600, 90, 0]]
        source = RealVisionSource.__new__(RealVisionSource)
        source._stream = Stream()
        source._conf_thresh = .1
        source._frame_count = 0
        box = source.get_bboxes()[0]
        self.assertEqual((box.x1, box.y1, box.x2, box.y2), (440, 60, 840, 660))
        self.assertEqual((box.center_x / 1280, box.center_y / 720), (.5, .5))

    def test_eventbus_receives_runtime_snapshot_larger_than_one_packet(self) -> None:
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        expected = "x" * 12000
        server = EventBusServer(lambda _event: {"ok": True, "runtime": {"trace": expected}}, port=port)
        self.assertTrue(server.start())
        try:
            result = EventBusClient(port=port).emit(Event.make("system", "runtime_snapshot_request", "test"))
            self.assertEqual(result["runtime"]["trace"], expected)
        finally:
            server.close()


if __name__ == "__main__":
    unittest.main()
