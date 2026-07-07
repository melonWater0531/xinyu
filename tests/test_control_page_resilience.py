from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

import main_phase3
import recamera_fastapi
from core.event import BBox, Event
from core.event_bus import EventBusClient


ROOT = Path(__file__).resolve().parents[1]


class ControlPageResilienceTests(unittest.TestCase):
    def test_hardware_io_is_queued_without_clearing_feature_session(self) -> None:
        runner = main_phase3.Phase3Runner(enable_control=False, max_cycles=0)
        start = runner.process_event(Event.make("ui", "feature_start", "test", payload={
            "feature": "multi_sound_yaw", "session_id": "multi-test", "lease_ms": 5000,
        }))
        self.assertTrue(start["accepted"])
        runner._device_session_degraded = True
        heartbeat = runner.process_event(Event.make("ui", "feature_heartbeat", "test", payload={
            "session_id": "multi-test", "lease_ms": 5000,
        }))
        self.assertTrue(heartbeat["accepted"])
        self.assertFalse(heartbeat["hardware_ready"])
        self.assertGreater(heartbeat["runtime"]["lease_remaining_ms"], 0)
        self.assertEqual(heartbeat["runtime"]["active_feature"], "multi_sound_yaw")
        self.assertIn(heartbeat["runtime"]["hardware_io"]["queue_state"], {"pending", "executing", "idle"})
        runner._hardware_worker.close()

    def test_multi_audio_event_produces_session_bound_yaw_command(self) -> None:
        runner = main_phase3.Phase3Runner(enable_control=False, max_cycles=0)
        try:
            start = runner.process_event(Event.make("ui", "feature_start", "test", payload={
                "feature": "multi_sound_yaw", "session_id": "multi-audio", "lease_ms": 5000,
            }))
            self.assertTrue(start["accepted"])
            event = Event.make("audio", "speech_detected", "test", payload={
                "doa_deg": 90.0,
                "speech": True,
                "session_id": "multi-audio",
                "vad_confidence": 0.8,
                "lip_motion": False,
            })
            first = runner.process_event(event)
            self.assertFalse(first["applied"])
            runner._orchestrator._doa_candidate_since -= 0.6
            result = runner.process_event(event)
            self.assertIsNotNone(result["command"])
            self.assertEqual(result["command"]["reason"], "audio_coarse")
            self.assertEqual(result["command"]["session_id"], "multi-audio")
            self.assertAlmostEqual(result["command"]["yaw"], 270.0)
        finally:
            runner._hardware_worker.close()

    def test_local_single_vision_uses_structured_observation(self) -> None:
        runner = main_phase3.Phase3Runner(enable_control=False, max_cycles=0)
        try:
            start = runner.process_event(Event.make("ui", "feature_start", "test", payload={
                "feature": "single_face_analysis", "session_id": "single-local", "lease_ms": 5000,
            }))
            self.assertTrue(start["accepted"])
            runner._last_frame_size = {"width": 1280, "height": 720}
            runner._last_face_tracks = [{
                "track_id": 4,
                "cx": 0.90,
                "cy": 0.70,
                "bbox": [1040, 430, 1220, 620],
                "confidence": 0.95,
                "lost_frames": 0,
            }]
            box = BBox(1040, 430, 1220, 620, class_name="face", confidence=0.95)
            self.assertIsNone(runner._handle_vision_event([box]))
            runner._last_face_tracks = [dict(runner._last_face_tracks[0])]
            self.assertIsNone(runner._handle_vision_event([box]))
            runner._last_face_tracks = [dict(runner._last_face_tracks[0])]
            runner._handle_vision_event([box])
            self.assertEqual(runner._last_event.name, "observation")
            self.assertEqual(runner._last_event.payload["faces"][0]["track_id"], 4)
            self.assertEqual(runner._orchestrator.locked_track_id, 4)
            self.assertTrue(runner._orchestrator.runtime_state()["command_sent"])
        finally:
            runner._hardware_worker.close()

    def test_lease_windows_allow_a_missed_heartbeat(self) -> None:
        self.assertEqual(recamera_fastapi.CONTROL_LEASE_MS, 5000)
        self.assertEqual(main_phase3.DEVICE_LEASE_MS, 5000)
        worst_case_seconds = (
            main_phase3.DEVICE_REQUEST_TIMEOUT_MS * main_phase3.DEVICE_REQUEST_RETRY / 1000
            + 0.05 * (main_phase3.DEVICE_REQUEST_RETRY - 1)
        )
        self.assertLess(worst_case_seconds, EventBusClient().timeout)

    def test_control_page_heartbeats_do_not_overlap_or_stop_on_hide(self) -> None:
        page = (ROOT / "dashboard" / "recamera_v2_live.html").read_text(encoding="utf-8")
        self.assertIn("heartbeatInFlight", page)
        self.assertIn("setInterval(heartbeat,1000)", page)
        self.assertIn("clearInterval(heartbeatTimer)", page)
        self.assertIn("startHeartbeatTimer();", page)
        self.assertIn("stopHeartbeatTimer();", page)
        self.assertNotIn("if(document.hidden)deactivatePage", page)
        self.assertIn("State render error", page)
        self.assertIn("tracking_overlay.js?v=20260703-1", page)

    def test_control_page_exposes_announce_voice_tests(self) -> None:
        page = (ROOT / "dashboard" / "recamera_v2_live.html").read_text(encoding="utf-8")
        self.assertIn("voiceAnnounceTest", page)
        self.assertIn("/api/voice/announce/test", page)
        for reason in (
            "sedentary",
            "eye_fatigue",
            "meeting_start",
            "meeting_stop",
            "meeting_summary_ok",
            "meeting_summary_error",
        ):
            self.assertIn(f"voiceAnnounceTest('{reason}')", page)

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

    def test_all_control_videos_preserve_original_frame(self) -> None:
        page = (ROOT / "dashboard" / "recamera_v2_live.html").read_text(encoding="utf-8")
        self.assertIn(".video-wrap img", page)
        self.assertIn("object-fit:contain", page)
        self.assertIn("object-position:center", page)
        self.assertNotIn("object-fit:cover", page)
        self.assertIn("--video-aspect", page)
        self.assertIn('id="singleFrameInfo"', page)
        self.assertIn('id="multiFrameInfo"', page)
        self.assertIn("last_frame_age_ms", page)
        self.assertIn("now-lastVideoReconnectAt<3000", page)
        self.assertIn("/video_feed?ts=${now}", page)
        self.assertNotIn("videoEl.src='/api/snapshot'", page)

    def test_meeting_frontend_requests_timeout_and_release_buttons(self) -> None:
        page = (ROOT / "dashboard" / "recamera_v2_live.html").read_text(encoding="utf-8")
        self.assertIn("AbortController", page)
        self.assertIn("clearTimeout(timer)", page)
        complete = page.split("async function completeMeetingWorkflow()", 1)[1].split("async function startLocalFeature", 1)[0]
        self.assertIn("finally", complete)
        self.assertIn("meetingCompleting=false", complete)
        self.assertIn("timeout:e.name==='AbortError'", page)

    def test_heartbeat_is_short_degraded_and_does_not_clear_session(self) -> None:
        page = (ROOT / "dashboard" / "recamera_v2_live.html").read_text(encoding="utf-8")
        heartbeat = page.split("async function heartbeat()", 1)[1].split("async function recoverMultiSessionIfExpired", 1)[0]
        self.assertIn("timeoutMs:1200", heartbeat)
        self.assertIn("silent:true", heartbeat)
        self.assertIn("控制心跳降级", heartbeat)
        self.assertNotIn("r.degraded||", heartbeat)
        self.assertNotIn("heartbeatFailures>=3", heartbeat)
        self.assertNotIn("clearSession();", heartbeat)

    def test_multi_heartbeat_can_recover_expired_session(self) -> None:
        page = (ROOT / "dashboard" / "recamera_v2_live.html").read_text(encoding="utf-8")
        self.assertIn("recoveringSession=false", page)
        heartbeat = page.split("async function heartbeat()", 1)[1].split("function toggleLandmarks", 1)[0]
        recover = page.split("async function recoverMultiSessionIfExpired", 1)[1].split("function toggleLandmarks", 1)[0]
        self.assertIn("sessionExpired=/no_command|heartbeat_rejected|session_id_required|lease_expired/i.test(reason)", heartbeat)
        self.assertIn("if(sessionExpired||!transient)await recoverMultiSessionIfExpired(sessionId)", heartbeat)
        self.assertIn("activePage!=='multi_sound_yaw'", recover)
        self.assertIn("/api/control/runtime", recover)
        self.assertIn("last.reason==='lease_expired'", recover)
        self.assertIn("clearSession();", recover)
        self.assertIn("/api/multi_track/start", recover)
        self.assertIn("await applyControlConfig()", recover)

    def test_multi_heartbeat_keeps_timeout_and_busy_transient(self) -> None:
        page = (ROOT / "dashboard" / "recamera_v2_live.html").read_text(encoding="utf-8")
        heartbeat = page.split("async function heartbeat()", 1)[1].split("async function recoverMultiSessionIfExpired", 1)[0]
        self.assertIn("r.timeout", heartbeat)
        self.assertIn("r.authority==='unreachable'", heartbeat)
        self.assertIn("eventbus_timeout", heartbeat)
        self.assertIn("eventbus_busy", heartbeat)

    def test_control_page_shows_doa_motion_diagnostics(self) -> None:
        page = (ROOT / "dashboard" / "recamera_v2_live.html").read_text(encoding="utf-8")
        self.assertIn('id="doaMotion"', page)
        self.assertIn("doa.diagnostics||rs.diagnostics", page)
        self.assertIn("auto beam", page)
        self.assertIn("rawDeg", page)
        self.assertIn("ledDeg", page)
        self.assertIn("diag.moving", page)
        self.assertIn("diag.packet_count", page)

    def test_meeting_frontend_shows_realtime_asr_lifecycle(self) -> None:
        page = (ROOT / "dashboard" / "recamera_v2_live.html").read_text(encoding="utf-8")
        for token in ("asr_pending", "transcribing", "transcribed", "asr_failed", "recording_state", "association_confidence"):
            self.assertIn(token, page)
        self.assertIn("audioEnabled=conv.requested!==false", page)
        self.assertIn("Meeting render error", page)
        self.assertIn("img.naturalWidth", page)

    def test_service_worker_refreshes_static_assets_before_cache_fallback(self) -> None:
        sw = (ROOT / "dashboard" / "sw.js").read_text(encoding="utf-8")
        self.assertIn('CACHE_NAME = "xinyu-pwa-v12"', sw)
        for asset in (
            "/static/product_home/home.css",
            "/static/product_home/home.js",
            "/static/product_home/seed_data.js",
            "/home-old",
        ):
            self.assertIn(asset, sw)
        static_branch = sw.split('url.pathname.startsWith("/static/")', 1)[1]
        self.assertLess(static_branch.index("fetch(request)"), static_branch.index("caches.match(request)"))

    def test_node_red_watchdog_matches_device_lease(self) -> None:
        flow = json.loads((ROOT / "deploy" / "node_red" / "recamera_control_bridge.json").read_text(encoding="utf-8"))
        status_node = next(node for node in flow if node.get("name") == "Build real readback")
        self.assertIn("watchdog_ms:5000", status_node["func"])
        self.assertIn("last_command", status_node["func"])
        self.assertIn("verified", status_node["func"])

    def test_multi_mode_throttles_nonessential_perception(self) -> None:
        backend = (ROOT / "recamera_fastapi.py").read_text(encoding="utf-8")
        self.assertIn("run_companion_detail = not multi_mode", backend)
        self.assertIn("pose_frame_count % 15", backend)
        self.assertIn("adapter.predict", backend)
        self.assertIn("run_in_executor(_slow_pool", backend)


if __name__ == "__main__":
    unittest.main()
