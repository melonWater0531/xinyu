from __future__ import annotations

import time
import unittest
import socket

from core.control_session import ControlMode
from core.event import Event
from core.event_bus import EventBusClient, EventBusServer
from core.orchestrator import Orchestrator
from core.safety_layer import SafetyLayer


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
        command = self.orch.handle_event(Event.make("audio", "speech_detected", "test", payload={"doa_deg": 35, "speech": True}))
        self.assertIsNotNone(command)
        self.assertIsNotNone(command.yaw)
        self.assertIsNone(command.pitch)
        self.assertIsNone(self.orch.handle_event(Event.make("vision", "target_detected", "test", payload={"cx": .2, "cy": .5, "conf": .9})))

    def test_manual_requires_current_session(self) -> None:
        self.start("manual_gimbal_debug")
        self.assertIsNone(self.orch.handle_event(ui("dpad_move", session_id="old", pan=2, tilt=1)))
        command = self.orch.handle_event(ui("dpad_move", session_id="s1", pan=2, tilt=1))
        self.assertEqual(command.mode, "delta")

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
