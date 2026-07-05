from __future__ import annotations

import asyncio
import threading
import time
import unittest
from pathlib import Path

import numpy as np

import recamera_fastapi as api
from audio.conversation_recorder import ConversationRecorder


class RecorderCallbackTests(unittest.TestCase):
    def test_portaudio_callback_only_enqueues_raw_audio(self) -> None:
        recorder = ConversationRecorder(root="/tmp/xinyu-recorder-callback-test")
        recorder._audio_processor.process = lambda _audio: (_ for _ in ()).throw(
            AssertionError("DSP must not run in the PortAudio callback")
        )
        recorder._audio_callback(np.zeros((1600, 1), dtype=np.float32), 1600, None, None)
        self.assertEqual(recorder._audio_q.qsize(), 1)
        self.assertTrue(recorder._worker is None or recorder._worker.daemon)

    def test_control_page_defaults_to_tracking_only_mode(self) -> None:
        page = (Path(__file__).resolve().parents[1] / "dashboard" / "recamera_v2_live.html").read_text(encoding="utf-8")
        start = page.split("async function startMeetingWorkflow()", 1)[1].split("async function completeMeetingWorkflow()", 1)[0]
        self.assertIn("{save_audio:false}", start)
        self.assertNotIn("{save_audio:true}", start)
        self.assertIn("真实录音未启用", start)
        self.assertIn("录音未启用", page)


class RecorderFreezeIsolationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        names = (
            "_start_feature", "_ensure_doa_reader", "_ensure_asr_worker",
            "_start_conversation_recording", "_conversation_recorder",
            "_conversation_recording_requested", "_meeting_save_audio",
            "_meeting_report", "_meeting_recording_task", "_meeting_started_at",
            "_meeting_ended_at", "_recording_start_requested_at",
            "RECORDING_START_TIMEOUT_S", "RECORDING_STOP_TIMEOUT_S",
            "_recorder_start_future", "_recorder_start_thread", "_recorder_start_token",
            "_runtime_cache", "_ui_session_id", "_eventbus", "_heartbeat_future",
            "_heartbeat_eventbus_in_flight", "_heartbeat_state", "_multi_track_active",
            "_single_track_active", "_tracking_mode", "_asr_stats",
        )
        self.saved = {name: getattr(api, name) for name in names}
        self.saved["_meeting_report"] = dict(api._meeting_report)
        self.saved["_runtime_cache"] = dict(api._runtime_cache)
        self.saved["_heartbeat_state"] = dict(api._heartbeat_state)
        self.saved["_asr_stats"] = dict(api._asr_stats)

        async def start_feature(feature):
            return {
                "ok": True, "accepted": True, "feature": feature,
                "session_id": "recorder-freeze-test", "hardware_ready": False,
            }

        api._start_feature = start_feature
        api._ensure_doa_reader = lambda: True
        api._ensure_asr_worker = lambda: None
        api._conversation_recorder = None
        api._conversation_recording_requested = False
        api._meeting_save_audio = False
        api._meeting_report = {"status": "idle", "error": "", "progress": 0}
        api._meeting_recording_task = None
        api._meeting_started_at = 0.0
        api._meeting_ended_at = 0.0
        api._recording_start_requested_at = 0.0
        api.RECORDING_START_TIMEOUT_S = 0.05
        api.RECORDING_STOP_TIMEOUT_S = 0.05
        api._recorder_start_future = None
        api._recorder_start_thread = None
        api._recorder_start_token = 0
        api._runtime_cache = api._runtime_with_telemetry_defaults({
            "connected": True, "active_feature": "inactive", "session_id": "",
            "lease_remaining_ms": 0,
        })
        api._ui_session_id = ""
        api._heartbeat_future = None
        api._heartbeat_eventbus_in_flight = False
        api._multi_track_active = False
        api._single_track_active = False
        api._tracking_mode = "single"
        api._asr_stats.update({
            "pending": 0, "running": 0, "done": 0, "failed": 0,
            "last_error": "", "last_error_at": 0.0,
        })

    async def asyncTearDown(self) -> None:
        task = api._meeting_recording_task
        if task is not None and task is not self.saved["_meeting_recording_task"] and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        for name, value in self.saved.items():
            target = getattr(api, name)
            if isinstance(value, dict) and isinstance(target, dict):
                target.clear()
                target.update(value)
            else:
                setattr(api, name, value)

    async def test_default_multi_start_is_tracking_only_without_recorder(self) -> None:
        api._start_conversation_recording = lambda: (_ for _ in ()).throw(
            AssertionError("save_audio=false must not start a recorder")
        )
        started = time.monotonic()
        result = await api.api_multi_track_start({})
        self.assertLess(time.monotonic() - started, 1.0)
        self.assertTrue(result["accepted"])
        state = result["state"]
        self.assertEqual(state["recording_status"], "disabled")
        self.assertEqual(state["status"], "tracking_only")
        self.assertFalse(state["save_audio"])
        self.assertFalse(state["recording_requested"])
        self.assertFalse(state["recorder_active"])
        await asyncio.sleep(0.02)
        self.assertGreater(api._conversation_state()["stats"]["duration"], 0)

    async def test_stuck_recorder_degrades_without_blocking_cached_endpoints(self) -> None:
        release = threading.Event()
        api._start_conversation_recording = lambda: release.wait(2.0) or True

        class EventBus:
            host = "127.0.0.1"
            port = 8765

            def emit(self, _event):
                return {"ok": True, "accepted": True, "runtime": {
                    "connected": True, "active_feature": "multi_sound_yaw",
                    "session_id": "recorder-freeze-test", "lease_remaining_ms": 5000,
                }}

        api._eventbus = EventBus()
        try:
            started = time.monotonic()
            result = await api.api_multi_track_start({"save_audio": True})
            self.assertLess(time.monotonic() - started, 1.0)
            self.assertTrue(result["accepted"])
            await asyncio.sleep(0)
            self.assertIsNotNone(api._recorder_start_thread)
            await asyncio.wait_for(api._meeting_recording_task, timeout=0.5)
            state = api._conversation_state()
            self.assertEqual(state["recording_status"], "degraded")
            self.assertEqual(state["last_recording_error"], "recording_start_timeout")
            self.assertTrue(api._recorder_start_thread.daemon)

            handlers = [
                ("health", api.api_system_health()),
                ("state", api.api_state()),
                ("runtime", api.api_control_runtime()),
                ("conversation", api.api_conversation_state()),
                ("debug", api.api_conversation_debug()),
                ("heartbeat", api.api_control_heartbeat({"session_id": "recorder-freeze-test"})),
                ("video", api.video_feed()),
                ("snapshot", api.snapshot()),
            ]
            responses = {}
            for name, awaitable in handlers:
                before = time.monotonic()
                responses[name] = await awaitable
                self.assertLess(time.monotonic() - before, 1.0, name)
            self.assertTrue(responses["heartbeat"]["accepted"])
            self.assertGreater(responses["heartbeat"]["runtime"]["lease_remaining_ms"], 0)
        finally:
            release.set()
            await asyncio.sleep(0.02)

    async def test_recorder_exception_and_asr_failure_are_degraded(self) -> None:
        api._ensure_asr_worker = lambda: (_ for _ in ()).throw(RuntimeError("ASR init failed"))
        api._start_conversation_recording = lambda: (_ for _ in ()).throw(RuntimeError("PortAudio open failed"))
        await api.api_multi_track_start({"save_audio": True})
        await asyncio.wait_for(api._meeting_recording_task, timeout=0.5)
        state = api._conversation_state()
        self.assertEqual(state["recording_status"], "degraded")
        self.assertIn("PortAudio open failed", state["last_recording_error"])
        self.assertEqual(state["asr_status"], "degraded")
        self.assertIn("ASR init failed", state["last_asr_error"])

    async def test_watchdog_and_stop_timeout_never_join_stuck_audio(self) -> None:
        api._meeting_save_audio = True
        api._conversation_recording_requested = True
        api._meeting_report = {"status": "recording_starting", "error": ""}
        api._recording_start_requested_at = time.time() - 6.0
        watchdog = asyncio.create_task(api.recorder_watchdog_loop())
        await asyncio.sleep(0.55)
        watchdog.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await watchdog
        self.assertEqual(api._meeting_report["status"], "recording_degraded")
        self.assertEqual(api._meeting_report["error"], "recording_start_timeout")

        release = threading.Event()

        class StuckRecorder:
            def stop(self, finalize=True):
                release.wait(2.0)

        api._conversation_recorder = StuckRecorder()
        before = time.monotonic()
        try:
            self.assertFalse(await api._stop_conversation_recording_async())
            self.assertLess(time.monotonic() - before, 0.5)
            threads = [thread for thread in threading.enumerate() if thread.name == "recorder-stop-daemon"]
            self.assertTrue(threads and all(thread.daemon for thread in threads))
        finally:
            release.set()


if __name__ == "__main__":
    unittest.main()
