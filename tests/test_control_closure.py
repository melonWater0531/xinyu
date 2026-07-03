from __future__ import annotations

import time
import unittest
import socket
import json

import numpy as np

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
        self.orch._control_params["demo_stop_shake_mode"] = 0.0
        event = Event.make("vision", "target_detected", "test", payload={"cx": .25, "cy": .45, "conf": .9, "class_name": "face"})
        self.orch.handle_event(event)
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
        centered = {"track_id": 7, "cx": 640, "cy": 230.4, "confidence": .95, "lost_frames": 0}
        self.orch.handle_event(self.observation(1, faces=[centered]))
        self.orch.handle_event(self.observation(2, faces=[centered]))
        command = self.orch.handle_event(self.observation(3, faces=[centered]))
        self.assertIsNone(command)
        self.assertEqual(self.orch.locked_track_id, 7)
        self.assertEqual(self.orch.tracking_phase, "locked_centered")
        self.assertEqual(self.orch.lock_state, "centered")

    def test_stale_track_is_not_display_or_control_candidate(self) -> None:
        self.start("single_face_analysis")
        self.orch._control_params["demo_stop_shake_mode"] = 0.0
        stale = {"track_id": 1, "cx": .2, "cy": .4, "confidence": .99, "lost_frames": 2}
        current = {"track_id": 2, "cx": .7, "cy": .32, "confidence": .9, "lost_frames": 0}
        for oid in range(1, 4):
            self.assertIsNone(self.orch.handle_event(self.observation(oid, faces=[stale, current])))
        command = self.orch.handle_event(self.observation(4, faces=[stale, current]))
        self.assertIsNotNone(command)
        self.assertEqual(self.orch.locked_track_id, 2)

    def test_old_session_and_out_of_order_observations_are_ignored(self) -> None:
        self.start("single_face_analysis")
        self.orch._control_params["demo_stop_shake_mode"] = 0.0
        face = {"track_id": 3, "cx": .2, "cy": .32, "confidence": .9, "lost_frames": 0}
        self.assertIsNone(self.orch.handle_event(self.observation(1, faces=[face], session_id="old")))
        for oid in (2, 3, 4):
            self.assertIsNone(self.orch.handle_event(self.observation(oid, faces=[face])))
        self.assertIsNotNone(self.orch.handle_event(self.observation(5, faces=[face])))
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
        face = {"track_id": 9, "cx": .55, "cy": .32, "confidence": .9, "lost_frames": 0}
        self.orch.handle_event(self.observation(1, faces=[face]))
        self.orch.handle_event(self.observation(2, faces=[face]))
        self.orch.handle_event(self.observation(3, faces=[face]))
        self.assertEqual(self.orch.locked_track_id, 9)
        self.orch._last_speech_at -= 1.6
        self.assertIsNone(self.orch.handle_event(self.observation(4, faces=[face])))
        self.assertEqual(self.orch.tracking_phase, "speaker_hold")

    def test_face_requires_three_tracked_frames_and_rejects_untracked_fallback(self) -> None:
        self.start("single_face_analysis")
        tracked = {"track_id": 11, "cx": .5, "cy": .32, "confidence": .9, "lost_frames": 0}
        self.orch.handle_event(self.observation(1, faces=[tracked]))
        self.orch.handle_event(self.observation(2, faces=[tracked]))
        self.assertIsNone(self.orch.locked_track_id)
        untracked = {"track_id": None, "cx": .5, "cy": .32, "confidence": .99, "lost_frames": 0}
        self.orch.handle_event(self.observation(3, faces=[untracked]))
        self.assertIsNone(self.orch.locked_track_id)
        self.assertEqual(self.orch.lock_state, "acquiring")

    def test_locked_face_occlusion_does_not_fall_back_to_body(self) -> None:
        self.start("single_face_analysis")
        face = {"track_id": 12, "cx": .5, "cy": .32, "confidence": .9, "lost_frames": 0}
        for oid in range(1, 4):
            self.orch.handle_event(self.observation(oid, faces=[face]))
        person = {"bbox": [200, 100, 900, 700], "cx": .43, "cy": .3, "confidence": .9}
        self.assertIsNone(self.orch.handle_event(self.observation(4, persons=[person])))
        self.assertEqual(self.orch.locked_track_id, 12)
        self.assertEqual(self.orch.lock_state, "occlusion_hold")

    def test_candidate_detection_gap_does_not_trigger_body_correction(self) -> None:
        self.start("single_face_analysis")
        face = {"track_id": 16, "cx": .7, "cy": .4, "confidence": .9, "lost_frames": 0}
        person = {"bbox": [100, 50, 1000, 700], "cx": .2, "cy": .7, "confidence": .95}
        self.orch.handle_event(self.observation(1, faces=[face], persons=[person]))
        self.assertIsNone(self.orch.handle_event(self.observation(2, persons=[person])))
        self.assertEqual(self.orch.tracking_phase, "acquiring")
        self.assertEqual(self.orch.runtime_state()["command_suppressed_reason"], "candidate_gap_hold")

    def test_centered_face_jitter_stays_inside_deadzone(self) -> None:
        self.start("single_face_analysis")
        for oid in range(1, 9):
            face = {"track_id": 13, "cx": .5 + (.03 if oid % 2 else -.03),
                    "cy": .32 + (.025 if oid % 2 else -.025), "confidence": .9, "lost_frames": 0}
            self.assertIsNone(self.orch.handle_event(self.observation(oid, faces=[face])))
        self.assertEqual(self.orch.locked_track_id, 13)
        self.assertEqual(self.orch.lock_state, "centered")

    def test_phase1b_centered_safe_roi_suppresses_motion(self) -> None:
        self.start("single_face_analysis")
        self.orch._control_params["demo_stop_shake_mode"] = 0.0
        face = {
            "track_id": 31, "cx": .5, "cy": .32,
            "bbox": [560, 190, 720, 350],
            "confidence": .95, "lost_frames": 0,
        }
        for oid in range(1, 7):
            self.assertIsNone(self.orch.handle_event(self.observation(oid, faces=[face])))
        runtime = self.orch.runtime_state()
        self.assertEqual(self.orch.lock_state, "centered")
        self.assertEqual(runtime["tracking_state"], "CENTERED")
        self.assertTrue(runtime["target_visible"])
        self.assertEqual(runtime["command_suppressed_reason"], "inside_deadzone")

    def test_phase1b_bbox_near_edge_allows_correction_even_with_small_center_error(self) -> None:
        self.start("single_face_analysis")
        self.orch._control_params["demo_stop_shake_mode"] = 0.0
        edge_face = {
            "track_id": 32, "cx": .5, "cy": .32,
            "bbox": [10, 190, 170, 350],
            "confidence": .95, "lost_frames": 0,
        }
        for oid in range(1, 4):
            self.assertIsNone(self.orch.handle_event(self.observation(oid, faces=[edge_face])))
        command = self.orch.handle_event(self.observation(4, faces=[edge_face]))
        self.assertIsNotNone(command)
        self.assertIsNotNone(command.yaw)
        self.assertEqual(command.mode, "absolute")
        self.assertNotEqual(self.orch.lock_state, "centered")
        runtime = self.orch.runtime_state()
        self.assertTrue(runtime["target_visible"])
        self.assertLess(runtime["error_x_ratio"], 0)
        self.assertNotEqual(runtime["control_target"], runtime["face_center"])

    def test_phase1b_top_edge_reports_centered_block_reason(self) -> None:
        self.start("single_face_analysis")
        self.orch._control_params["demo_stop_shake_mode"] = 0.0
        edge_face = {
            "track_id": 33, "cx": .5, "cy": .32,
            "bbox": [560, 3, 720, 160],
            "confidence": .95, "lost_frames": 0,
        }
        for oid in range(1, 4):
            self.orch.handle_event(self.observation(oid, faces=[edge_face]))
        runtime = self.orch.runtime_state()
        self.assertNotEqual(runtime["tracking_state"], "CENTERED")
        self.assertRegex(runtime["centered_block_reason"], "safe_roi|edge")
        self.assertIn("safe_roi", runtime)
        self.assertIn("edge_margin", runtime)

    def test_demo_stop_shake_holds_face_inside_large_region(self) -> None:
        self.start("single_face_analysis")
        face = {
            "track_id": 51, "cx": .60, "cy": .55,
            "bbox": [20, 8, 1240, 705],
            "confidence": .95, "lost_frames": 0,
        }
        for oid in range(1, 8):
            self.assertIsNone(self.orch.handle_event(self.observation(oid, faces=[face])))
        runtime = self.orch.runtime_state()
        self.assertEqual(runtime["tracking_state"], "CENTERED")
        self.assertEqual(runtime["demo_zone"], "HOLD")
        self.assertTrue(runtime["demo_hold_active"])
        self.assertEqual(runtime["demo_hold_reason"], "face_inside_demo_hold_region")
        self.assertEqual(runtime["motion_blocked_reason"], "demo_hold")
        self.assertFalse(runtime["command_sent"])

    def test_demo_stop_shake_body_align_only_allows_very_small_correction(self) -> None:
        self.start("single_face_analysis")
        person = {"bbox": [0, 0, 1280, 720], "cx": .9, "cy": .8, "confidence": .95}
        command = None
        for oid in range(1, 4):
            command = self.orch.handle_event(self.observation(oid, persons=[person]))
            if command is not None:
                break
        runtime = self.orch.runtime_state()
        self.assertIsNotNone(command)
        self.assertLessEqual(abs(command.yaw - 180), 0.301)
        self.assertLessEqual(abs(command.pitch - 90), 0.201)
        self.assertEqual(runtime["demo_zone"], "BODY_ALIGN_ONLY")
        self.assertTrue(runtime["body_align_suppressed"])
        self.assertTrue(runtime["command_sent"])

    def test_demo_stop_shake_missing_face_holds_before_search(self) -> None:
        self.start("single_face_analysis")
        self.assertIsNone(self.orch.handle_event(self.observation(1)))
        runtime = self.orch.runtime_state()
        self.assertEqual(runtime["tracking_phase"], "search_grace")
        self.assertEqual(runtime["demo_zone"], "NO_FACE")
        self.assertTrue(runtime["demo_hold_active"])
        self.assertEqual(runtime["motion_blocked_reason"], "demo_no_face_hold")
        self.assertFalse(runtime["command_sent"])

    def test_demo_stop_shake_correction_zone_sends_small_motion(self) -> None:
        self.start("single_face_analysis")
        face = {
            "track_id": 52, "cx": .78, "cy": .55,
            "bbox": [940, 330, 1060, 450],
            "confidence": .95, "lost_frames": 0,
        }
        for oid in range(1, 4):
            self.orch.handle_event(self.observation(oid, faces=[face]))
        command = self.orch.handle_event(self.observation(4, faces=[face]))
        runtime = self.orch.runtime_state()
        self.assertIsNotNone(command)
        self.assertEqual(command.mode, "absolute")
        self.assertEqual(runtime["demo_zone"], "CORRECTION")
        self.assertTrue(runtime["command_sent"])
        self.assertLessEqual(runtime["command_delta_yaw_deg"], 1.2)
        self.assertLessEqual(runtime["command_delta_pitch_deg"], 0.8)

    def test_demo_stop_shake_edge_zone_sends_limited_motion(self) -> None:
        self.start("single_face_analysis")
        face = {
            "track_id": 53, "cx": .92, "cy": .82,
            "bbox": [1120, 540, 1240, 690],
            "confidence": .95, "lost_frames": 0,
        }
        for oid in range(1, 4):
            self.orch.handle_event(self.observation(oid, faces=[face]))
        command = self.orch.handle_event(self.observation(4, faces=[face]))
        runtime = self.orch.runtime_state()
        self.assertIsNotNone(command)
        self.assertEqual(command.mode, "absolute")
        self.assertEqual(runtime["demo_zone"], "EDGE")
        self.assertTrue(runtime["command_sent"])
        self.assertLessEqual(runtime["command_delta_yaw_deg"], 1.2)
        self.assertLessEqual(runtime["command_delta_pitch_deg"], 0.8)

    def test_tracking_step_is_bounded_for_upper_body_composition(self) -> None:
        self.start("single_face_analysis")
        self.orch._control_params["demo_stop_shake_mode"] = 0.0
        face = {"track_id": 14, "cx": .9, "cy": .8, "confidence": .95, "lost_frames": 0}
        for oid in range(1, 4):
            self.orch.handle_event(self.observation(oid, faces=[face]))
        command = self.orch.handle_event(self.observation(4, faces=[face]))
        self.assertIsNotNone(command)
        self.assertLessEqual(abs(command.yaw - 180), 5)
        self.assertLessEqual(abs(command.pitch - 90), 3)
        self.assertLessEqual(command.speed, 180)
        runtime = self.orch.runtime_state()
        self.assertEqual(runtime["target_point"]["y"], .32)
        self.assertEqual(runtime["lock_state"], "locked")

    def test_phase1a_runtime_telemetry_contract_fields_exist(self) -> None:
        self.start("single_face_analysis")
        self.orch._control_params["demo_stop_shake_mode"] = 0.0
        face = {"track_id": 21, "cx": .62, "cy": .37, "bbox": [700, 220, 860, 420], "confidence": .95, "lost_frames": 0}
        for oid in range(1, 4):
            self.orch.handle_event(self.observation(oid, faces=[face]))
        runtime = self.orch.runtime_state()
        for field in (
            "tracking_state", "target_visible", "locked_track_id", "raw_bbox", "track_bbox",
            "control_target", "last_control_target", "face_center", "frame_center", "error_x_px",
            "error_y_px", "error_x_ratio", "error_y_ratio", "frame_age_ms",
            "deadband_x_px", "deadband_y_px", "safe_roi", "edge_margin",
            "target_yaw_deg", "target_pitch_deg", "command_yaw_deg",
            "command_pitch_deg", "centered_reason", "centered_block_reason",
            "demo_zone", "command_sent",
            "face_detection_ms", "embedding_ms", "tracker_update_ms",
            "control_loop_ms", "vision_hz", "control_hz", "telemetry_hz",
            "ui_push_hz", "tracking_config_loaded", "tracking_config_path",
            "tracking_config_error",
        ):
            self.assertIn(field, runtime)
        self.assertEqual(runtime["tracking_state"], "LOCKED")
        self.assertTrue(runtime["target_visible"])
        self.assertEqual(runtime["locked_track_id"], 21)
        self.assertEqual(runtime["track_bbox"], [700.0, 220.0, 860.0, 420.0])
        self.assertEqual(runtime["face_center"], {"x": 780.0, "y": 320.0})
        self.assertEqual(runtime["control_hz"], None)
        json.dumps(runtime)

    def test_phase1b_track_bbox_center_matches_face_center_telemetry(self) -> None:
        self.start("single_face_analysis")
        face = {"track_id": 41, "cx": .1, "cy": .9, "bbox": [451.6, 3.1, 841.5, 544.4], "confidence": .95, "lost_frames": 0}
        for oid in range(1, 4):
            self.orch.handle_event(self.observation(oid, faces=[face]))
        runtime = self.orch.runtime_state()
        self.assertEqual(runtime["track_bbox"], [451.6, 3.1, 841.5, 544.4])
        self.assertEqual(runtime["face_center"]["x"], round((451.6 + 841.5) / 2.0, 1))
        self.assertEqual(runtime["face_center"]["y"], round((3.1 + 544.4) / 2.0, 1))
        json.dumps(runtime)

    def test_phase1a_clears_current_target_telemetry_when_target_missing(self) -> None:
        self.start("single_face_analysis")
        face = {"track_id": 22, "cx": .62, "cy": .37, "bbox": [700, 220, 860, 420], "confidence": .95, "lost_frames": 0}
        for oid in range(1, 4):
            self.orch.handle_event(self.observation(oid, faces=[face]))
        visible = self.orch.runtime_state()
        self.assertTrue(visible["target_visible"])
        self.assertIsNotNone(visible["control_target"])
        self.orch.handle_event(self.observation(4))
        missing = self.orch.runtime_state()
        self.assertFalse(missing["target_visible"])
        self.assertIsNone(missing["raw_bbox"])
        self.assertIsNone(missing["track_bbox"])
        self.assertIsNone(missing["control_target"])
        self.assertEqual(missing["last_control_target"], visible["control_target"])
        self.assertIsNone(missing["face_center"])
        self.assertIsNone(missing["error_x_px"])
        self.assertIsNone(missing["error_y_px"])
        self.assertIsNone(missing["error_x_ratio"])
        self.assertIsNone(missing["error_y_ratio"])

    def test_phase1a_bbox_normalization_accepts_json_safe_shapes(self) -> None:
        self.assertEqual(self.orch._normalize_bbox([1, 2, 3, 4]), [1.0, 2.0, 3.0, 4.0])
        self.assertEqual(self.orch._normalize_bbox((1, 2, 3, 4)), [1.0, 2.0, 3.0, 4.0])
        self.assertEqual(self.orch._normalize_bbox(np.array([1, 2, 3, 4], dtype=np.float32)), [1.0, 2.0, 3.0, 4.0])
        self.assertEqual(self.orch._normalize_bbox({"x1": 1, "y1": 2, "x2": 3, "y2": 4}), [1.0, 2.0, 3.0, 4.0])
        self.assertEqual(self.orch._normalize_bbox({"left": 1, "top": 2, "right": 3, "bottom": 4}), [1.0, 2.0, 3.0, 4.0])
        self.assertIsNone(self.orch._normalize_bbox({"x": 1}))
        json.dumps({"bbox": self.orch._normalize_bbox(np.array([1, 2, 3, 4], dtype=np.float32))})

    def test_delayed_readback_suppresses_two_reverse_frames(self) -> None:
        self.start("single_face_analysis")
        self.orch._control_params["demo_stop_shake_mode"] = 0.0
        self.orch._control_params["max_yaw_delta_deg_per_tick"] = 5.0
        self.orch._control_params["max_pitch_delta_deg_per_tick"] = 3.0
        face = {"track_id": 15, "cx": .9, "cy": .32, "confidence": .95, "lost_frames": 0}
        for oid in range(1, 4):
            self.orch.handle_event(self.observation(oid, faces=[face]))
        first = self.orch.handle_event(self.observation(4, faces=[face]))
        self.assertIsNotNone(first)
        self.orch.update_gimbal_readback(180, 90)
        opposite = {"track_id": 15, "cx": .1, "cy": .32, "confidence": .95, "lost_frames": 0}
        self.orch._ema_x = .1
        self.orch._last_motion_at -= .31
        self.assertIsNone(self.orch.handle_event(self.observation(5, faces=[opposite])))
        self.orch._ema_x = .1
        self.orch._last_motion_at -= .31
        self.assertIsNone(self.orch.handle_event(self.observation(6, faces=[opposite])))
        self.orch._ema_x = .1
        self.orch._last_motion_at -= .31
        reversed_command = self.orch.handle_event(self.observation(7, faces=[opposite]))
        self.assertIsNotNone(reversed_command)
        self.assertGreater(reversed_command.yaw, first.yaw)

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
