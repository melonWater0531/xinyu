from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

import main_phase3
import recamera_fastapi
from core.event import Event
from core.event_bus import EventBusClient


ROOT = Path(__file__).resolve().parents[1]


class ControlPageResilienceTests(unittest.TestCase):
    def test_device_lease_failure_keeps_feature_active_and_recovers(self) -> None:
        class FlakyHardware:
            def __init__(self) -> None:
                self.starts = 0

            def start_session(self, _session_id, lease_ms=0):
                self.starts += 1
                return True

            def renew_session(self, _session_id, lease_ms=0):
                return False

            def stop_session(self, _session_id=""):
                return True

        runner = main_phase3.Phase3Runner(enable_control=False, max_cycles=0)
        runner._hw = FlakyHardware()
        start = runner.process_event(Event.make("ui", "feature_start", "test", payload={
            "feature": "multi_sound_yaw", "session_id": "multi-test", "lease_ms": 5000,
        }))
        self.assertTrue(start["accepted"])

        degraded = runner.process_event(Event.make("ui", "feature_heartbeat", "test", payload={
            "session_id": "multi-test", "lease_ms": 5000,
        }))
        self.assertTrue(degraded["accepted"])
        self.assertFalse(degraded["hardware_ready"])
        self.assertEqual(degraded["runtime"]["active_feature"], "multi_sound_yaw")
        self.assertEqual(degraded["runtime"]["stop_state"], "hardware_lease_degraded")

        recovered = runner.process_event(Event.make("ui", "feature_heartbeat", "test", payload={
            "session_id": "multi-test", "lease_ms": 5000,
        }))
        self.assertTrue(recovered["accepted"])
        self.assertTrue(recovered["hardware_ready"])
        self.assertEqual(recovered["runtime"]["active_feature"], "multi_sound_yaw")
        self.assertEqual(runner._hw.starts, 2)

    def test_lease_windows_allow_a_missed_heartbeat(self) -> None:
        self.assertEqual(recamera_fastapi.CONTROL_LEASE_MS, 5000)
        self.assertEqual(main_phase3.DEVICE_LEASE_MS, 2000)
        worst_case_seconds = (
            main_phase3.DEVICE_REQUEST_TIMEOUT_MS * main_phase3.DEVICE_REQUEST_RETRY / 1000
            + 0.05 * (main_phase3.DEVICE_REQUEST_RETRY - 1)
        )
        self.assertLess(worst_case_seconds, EventBusClient().timeout)

    def test_control_page_heartbeats_do_not_overlap_or_stop_on_hide(self) -> None:
        page = (ROOT / "dashboard" / "recamera_v2_live.html").read_text(encoding="utf-8")
        self.assertIn("heartbeatInFlight", page)
        self.assertIn("setInterval(heartbeat,1000)", page)
        self.assertNotIn("if(document.hidden)deactivatePage", page)
        self.assertIn("State render error", page)

    def test_unified_meeting_page_has_complete_dom_contract(self) -> None:
        page = (ROOT / "dashboard" / "recamera_v2_live.html").read_text(encoding="utf-8")
        ids = re.findall(r'\bid="([^"]+)"', page)
        references = set(re.findall(r"\$\('([^']+)'\)", page))
        self.assertEqual(len(ids), len(set(ids)), "duplicate element ids")
        self.assertEqual(references - set(ids), set(), "script references missing DOM ids")
        self.assertNotIn('id="page-meeting_recording"', page)
        self.assertIn('id="page-multi_sound_yaw"', page)
        self.assertIn("/api/meeting/complete", page)
        self.assertIn("说话人逐句记录", page)
        self.assertIn("LLM 会议纪要", page)

    def test_conversation_recorder_reads_doa_provider(self) -> None:
        from audio.conversation_recorder import ConversationRecorder

        recorder = ConversationRecorder(root=ROOT / "records" / "test-only", doa_provider=lambda: (65.0, True))
        self.assertEqual(recorder._read_doa(), (65.0, True))

    def test_overlay_never_draws_a_zero_sized_source_canvas(self) -> None:
        overlay = (ROOT / "dashboard" / "tracking_overlay.js").read_text(encoding="utf-8")
        self.assertIn("if(w<=0||h<=0)return", overlay)
        self.assertNotIn("drawImage", overlay)
        self.assertIn("drawScene($('multiOverlay')", overlay)

    def test_node_red_watchdog_matches_device_lease(self) -> None:
        flow = json.loads((ROOT / "deploy" / "node_red" / "recamera_control_bridge.json").read_text(encoding="utf-8"))
        status_node = next(node for node in flow if node.get("name") == "Build real readback")
        self.assertIn("watchdog_ms:2000", status_node["func"])


if __name__ == "__main__":
    unittest.main()
