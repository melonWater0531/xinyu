from __future__ import annotations

import unittest
import tempfile
import asyncio
import time
import os
from types import SimpleNamespace
from pathlib import Path

import numpy as np
import recamera_fastapi as api
from audio.conversation_recorder import ConversationRecorder
from services.emotion_prompt import build_emotion_context
import services.llm_router as llm_router
import services.whisperx_final as whisperx_final
from services.speaker_mapper import SpeakerMapper
from vision.attention_engine import AttentionConfig, ScoringModule


class BackendContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_stop_requires_session_id(self) -> None:
        api._single_track_active = True
        single = await api.api_single_track_stop({})
        self.assertFalse(single["accepted"])
        self.assertEqual(single["reason"], "session_id_required")
        self.assertTrue(single["active"])

        api._multi_track_active = True
        multi = await api.api_multi_track_stop({"finalize": False})
        self.assertFalse(multi["accepted"])
        self.assertEqual(multi["reason"], "session_id_required")
        self.assertTrue(multi["active"])

    async def test_phase1a_endpoint_runtime_docs_contract(self) -> None:
        route_paths = {getattr(route, "path", "") for route in api.app.routes}
        for path in (
            "/api/single_track/start",
            "/api/single_track/stop",
            "/api/multi_track/start",
            "/api/multi_track/stop",
        ):
            self.assertIn(path, route_paths)

        runtime = (await api.api_control_runtime())["runtime"]
        for field in (
            "tracking_state", "target_visible", "locked_track_id", "raw_bbox", "track_bbox",
            "control_target", "last_control_target", "face_center", "frame_center", "error_x_px",
            "error_y_px", "error_x_ratio", "error_y_ratio", "frame_age_ms",
            "deadband_x_px", "deadband_y_px", "safe_roi", "edge_margin",
            "target_yaw_deg", "target_pitch_deg", "command_yaw_deg",
            "command_pitch_deg", "centered_reason", "centered_block_reason",
            "face_detection_ms", "embedding_ms", "tracker_update_ms",
            "control_loop_ms", "vision_hz", "control_hz", "telemetry_hz",
            "ui_push_hz", "tracking_config_loaded", "tracking_config_path",
            "tracking_config_error",
        ):
            self.assertIn(field, runtime)
        self.assertIn("perception_diagnostics", runtime)
        for field in (
            "video_connected", "face_tracker_available", "latest_face_count",
            "latest_person_count", "observation_faces", "observation_persons",
            "last_publish_ok", "last_publish_error", "face_period",
            "pose_period", "detail_period", "analysis_detail_enabled",
            "sscma_fail_count", "sscma_last_error",
        ):
            self.assertIn(field, runtime["perception_diagnostics"])

        root = Path(api.__file__).resolve().parent
        self.assertTrue((root / "config" / "tracking_control.json").exists())
        self.assertTrue((root / "docs" / "tracking_tuning_sop.md").exists())

    def test_perception_diagnostics_track_observation_candidates(self) -> None:
        class FakeVideo:
            connected = True
            fps = 12.5
            last_frame_age_ms = 42
            resolution = [1280, 720]
            boxes = [[640, 360, 300, 500, 88, 0]]

        old_video = api.video_client
        old_persons = list(api._latest_pose_persons)
        old_diag = dict(api._perception_diag)
        old_observation_id = api._observation_id
        old_runtime = dict(api._runtime_cache)
        try:
            api.video_client = FakeVideo()
            api._latest_pose_persons[:] = [
                SimpleNamespace(
                    _track_id=7,
                    _lost_frames=0,
                    _source="face_tracker_v2",
                    _is_primary=True,
                    bbox=(100, 80, 220, 260),
                    conf=0.93,
                    face_center=(160, 140),
                    face_conf=0.93,
                    keypoints=[],
                )
            ]
            api._runtime_cache = {
                **api._runtime_cache,
                "active_feature": "single_face_analysis",
                "session_id": "diag-session",
            }
            payload = api._build_vision_observation()
            diag = api._perception_diagnostics()
            self.assertEqual(payload["session_id"], "diag-session")
            self.assertEqual(len(payload["faces"]), 1)
            self.assertEqual(len(payload["persons"]), 1)
            self.assertEqual(diag["latest_face_count"], 1)
            self.assertEqual(diag["latest_person_count"], 1)
            self.assertEqual(diag["observation_faces"], 1)
            self.assertEqual(diag["observation_persons"], 1)
            self.assertIn("face_tracker_v2", diag["latest_sources"])
        finally:
            api.video_client = old_video
            api._latest_pose_persons[:] = old_persons
            api._perception_diag = old_diag
            api._observation_id = old_observation_id
            api._runtime_cache = old_runtime

    def test_voice_target_enum_accepts_recamera_aliases(self) -> None:
        self.assertEqual(api._normalize_voice_target("recamera_speaker"), "recamera_speaker")
        self.assertEqual(api._normalize_voice_target("recamera"), "recamera_speaker")
        self.assertEqual(api._normalize_voice_target("device"), "recamera_speaker")
        self.assertEqual(api._normalize_voice_target("browser"), "browser")

    def test_snapshot_exposes_home_compatibility_fields_and_valence(self) -> None:
        old_runtime = dict(api._runtime_cache)
        old_emotion = api._emotieff_result
        try:
            api._runtime_cache = {
                **api._runtime_cache,
                "locked_track_id": 42,
                "tracking_phase": "face_lock",
                "active_feature": "multi_sound_yaw",
            }
            api._emotieff_result = {
                "emotion": "Happiness",
                "confidence": 0.9,
                "probabilities": {"Happiness": 0.8, "Sadness": 0.1},
                "valence": api._emotion_valence("Happiness", {"Happiness": 0.8, "Sadness": 0.1}),
            }
            data = api.build_state_snapshot()["data"]
            self.assertTrue(data["face_lock"]["locked"])
            self.assertEqual(data["face_lock"]["track_id"], 42)
            self.assertIn("sound_follow", data)
            self.assertIn("valence", data["emotieff"])
        finally:
            api._runtime_cache = old_runtime
            api._emotieff_result = old_emotion

    def test_doa_status_exposes_motion_diagnostics(self) -> None:
        class FakeDoa:
            doa = 320.0
            has_speech = True
            age = 0.04

            def __init__(self) -> None:
                self.packet_count = 10

            def status(self) -> dict:
                return {"source": "usb", "connected": True, "packet_count": self.packet_count}

        old_reader = api._doa_reader
        old_diag = dict(api._doa_diag)
        fake = FakeDoa()
        try:
            api._doa_reader = fake
            api._doa_diag.update({"last_deg": None, "last_changed_at": 0.0, "last_seen_at": 0.0, "last_delta_deg": 0.0})
            first = api._doa_status()
            self.assertIn("diagnostics", first)
            self.assertEqual(first["diagnostics"]["packet_count"], 10)
            self.assertFalse(first["diagnostics"]["moving"])

            fake.packet_count = 11
            stable = api._doa_status()
            self.assertFalse(stable["diagnostics"]["moving"])
            self.assertIn("stable_sec", stable["diagnostics"])
            self.assertIn("last_changed_at", stable["diagnostics"])

            fake.doa = 330.0
            fake.packet_count = 12
            moving = api._doa_status()
            self.assertTrue(moving["diagnostics"]["moving"])
            self.assertEqual(moving["diagnostics"]["last_delta_deg"], 10.0)
        finally:
            api._doa_reader = old_reader
            api._doa_diag.clear()
            api._doa_diag.update(old_diag)

    def test_doa_status_prefers_auto_selected_beam_for_led_path(self) -> None:
        class FakeDoa:
            doa = 90.0
            raw_doa = 320.0
            led_doa = 90.0
            has_speech = True
            age = 0.04

            def status(self) -> dict:
                return {
                    "source": "usb",
                    "connected": True,
                    "doa_deg": 90.0,
                    "raw_doa_deg": 320.0,
                    "led_doa_deg": 90.0,
                    "doa_basis": "auto_selected_beam",
                    "packet_count": 3,
                }

        old_reader = api._doa_reader
        old_diag = dict(api._doa_diag)
        try:
            api._doa_reader = FakeDoa()
            api._doa_diag.update({"last_deg": None, "last_changed_at": 0.0, "last_seen_at": 0.0, "last_delta_deg": 0.0})
            status = api._doa_status()
            self.assertEqual(status["doa_deg"], 90.0)
            self.assertEqual(status["raw_doa_deg"], 320.0)
            self.assertEqual(status["led_doa_deg"], 90.0)
            self.assertEqual(status["doa_basis"], "auto_selected_beam")
            self.assertEqual(status["diagnostics"]["raw_doa_deg"], 320.0)
        finally:
            api._doa_reader = old_reader
            api._doa_diag.clear()
            api._doa_diag.update(old_diag)

    def test_emotion_context_handles_missing_and_probability_shapes(self) -> None:
        empty = build_emotion_context(None)
        self.assertIn("是否观察到人脸：否", empty)

        context = build_emotion_context({
            "emotieff": {
                "emotion": "Happiness",
                "confidence": 0.82,
                "probabilities": [["Happiness", 0.82], ["Sadness", 0.08], ["Fear", 0.02]],
                "valence": 0.5,
                "arousal": 0.2,
            },
            "attention": {"has_face": True, "score": 78, "state": "focused"},
            "eye_metrics": {"perclos": 0.04, "blink_rate": 12},
            "gaze": {"state": "center", "confidence": 0.9},
        })
        self.assertIn("Happiness(82%)", context)
        self.assertIn("Sadness(8%)", context)
        self.assertNotIn("Fear(2%)", context)
        self.assertIn("PERCLOS", context)

    async def test_llm_router_complete_with_provider_none_keeps_complete_compatible(self) -> None:
        old_deepseek = llm_router.DEEPSEEK_API_KEY
        old_zhipu = llm_router.ZHIPU_API_KEY
        try:
            llm_router.DEEPSEEK_API_KEY = ""
            llm_router.ZHIPU_API_KEY = ""
            result = await llm_router.router.complete_with_provider([{"role": "user", "content": "hello"}], 20)
            self.assertEqual(result["text"], "")
            self.assertEqual(result["provider"], "none")
            text = await llm_router.router.complete([{"role": "user", "content": "hello"}], 20)
            self.assertEqual(text, "")
        finally:
            llm_router.DEEPSEEK_API_KEY = old_deepseek
            llm_router.ZHIPU_API_KEY = old_zhipu

    async def test_emotion_infer_no_face_returns_local_unobserved(self) -> None:
        old_attn = api._attn_result
        old_emotion = api._emotieff_result
        try:
            api._attn_result = {"has_face": False, "score": 0, "state": "missing"}
            api._emotieff_result = None
            result = await api.api_emotion_infer()
            self.assertTrue(result["ok"])
            self.assertEqual(result["label"], "暂未观察到")
            self.assertEqual(result["intensity"], 0)
            self.assertEqual(result["provider"], "local")
        finally:
            api._attn_result = old_attn
            api._emotieff_result = old_emotion

    async def test_emotion_infer_parses_llm_json(self) -> None:
        old_attn = api._attn_result
        old_emotion = api._emotieff_result
        old_complete = api._cloud_llm_complete

        async def fake_complete(messages, max_tokens=None):
            return {
                "text": '{"label":"专注中的满足感","intensity":7,"explanation":"表情积极且专注度较高。"}',
                "provider": "zhipu",
            }

        try:
            api._attn_result = {"has_face": True, "score": 80, "state": "focused"}
            api._emotieff_result = {
                "emotion": "Happiness",
                "confidence": 0.8,
                "probabilities": {"Happiness": 0.8, "Neutral": 0.1},
                "valence": 0.6,
            }
            api._cloud_llm_complete = fake_complete
            result = await api.api_emotion_infer()
            self.assertEqual(result["label"], "专注中的满足感")
            self.assertEqual(result["intensity"], 7)
            self.assertEqual(result["provider"], "zhipu")
        finally:
            api._attn_result = old_attn
            api._emotieff_result = old_emotion
            api._cloud_llm_complete = old_complete

    async def test_emotion_infer_malformed_llm_uses_local_fallback(self) -> None:
        old_attn = api._attn_result
        old_emotion = api._emotieff_result
        old_complete = api._cloud_llm_complete

        async def fake_complete(messages, max_tokens=None):
            return {"text": "not json", "provider": "deepseek"}

        try:
            api._attn_result = {"has_face": True, "score": 50, "state": "mixed"}
            api._emotieff_result = {
                "emotion": "Neutral",
                "confidence": 0.5,
                "probabilities": {"Neutral": 0.5},
                "valence": -0.4,
            }
            api._cloud_llm_complete = fake_complete
            result = await api.api_emotion_infer()
            self.assertEqual(result["provider"], "local")
            self.assertEqual(result["label"], "平静中带一点低落")
            self.assertGreaterEqual(result["intensity"], 1)
        finally:
            api._attn_result = old_attn
            api._emotieff_result = old_emotion
            api._cloud_llm_complete = old_complete

    async def test_chat_and_reflect_contracts_stay_stable_without_cloud(self) -> None:
        old_complete = api._cloud_llm_complete

        async def no_cloud(messages, max_tokens=None):
            return {"text": "", "provider": "none"}

        try:
            api._cloud_llm_complete = no_cloud
            chat = await api.api_chat({"message": "hello", "context": "", "user_name": "test"})
            self.assertEqual(set(chat.keys()), {"reply", "source", "emotion"})
            self.assertEqual(chat["source"], "template")
            self.assertTrue(chat["reply"])

            reflect = await api.api_llm_reflect({"mode": "diary", "emotion": "Happiness", "attention": 80})
            self.assertEqual(set(reflect.keys()), {"diary", "reply", "text", "source", "time"})
            self.assertEqual(reflect["source"], "template")
            self.assertTrue(reflect["diary"])
        finally:
            api._cloud_llm_complete = old_complete

    def test_speaker_mapper_register_lookup_and_search_plan_are_non_executing(self) -> None:
        mapper = SpeakerMapper()
        self.assertIsNone(mapper.lookup(65))
        info = mapper.register(65, track_id=2, pitch=88.5)
        self.assertEqual(info["label"], "说话人A")
        self.assertEqual(mapper.lookup(70)["label"], "说话人A")
        self.assertIsNone(mapper.lookup(120))

        wrap = mapper.register(355, track_id=3, pitch=90)
        self.assertEqual(mapper.lookup(5)["label"], wrap["label"])

        direct = mapper.build_search_plan(66)
        self.assertEqual(direct["action"], "direct")
        self.assertFalse(direct["execute"])

        search = mapper.build_search_plan(180)
        self.assertEqual(search["action"], "search")
        self.assertFalse(search["execute"])

        mapper.reset()
        self.assertEqual(mapper.get_registered_speakers(), [])

    def test_conversation_turn_records_speaker_provider_label_and_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            recorder = ConversationRecorder(
                root=tmp,
                doa_provider=lambda: (65.0, True),
                speaker_provider=lambda doa: "说话人A",
            )
            recorder._session_id = "session_test"
            recorder._session_dir = recorder.root / recorder._session_id
            (recorder._session_dir / "audio" / "segments").mkdir(parents=True)
            recorder._started_at = 100.0
            chunk = np.zeros(1600, dtype=np.float32)
            recorder._finalize_segment([chunk], [65.0], 101.0, 102.0)
            turn = recorder.state()["timeline"][0]
            self.assertEqual(turn["speaker_label"], "说话人A")
            self.assertEqual(turn["speaker"], "SPEAKER_RIGHT")

        with tempfile.TemporaryDirectory() as tmp:
            def broken(_doa):
                raise RuntimeError("boom")

            recorder = ConversationRecorder(root=tmp, speaker_provider=broken)
            recorder._session_id = "session_test"
            recorder._session_dir = recorder.root / recorder._session_id
            (recorder._session_dir / "audio" / "segments").mkdir(parents=True)
            recorder._started_at = 100.0
            recorder._finalize_segment([np.zeros(1600, dtype=np.float32)], [10.0], 101.0, 102.0)
            self.assertEqual(recorder.state()["timeline"][0]["speaker_label"], "未知说话人")

    async def test_meeting_speakers_and_wake_word_state_defaults(self) -> None:
        from services.speaker_mapper import speaker_mapper

        speaker_mapper.reset()
        empty = await api.api_meeting_speakers()
        self.assertEqual(empty, {"ok": True, "speakers": [], "total": 0})
        speaker_mapper.register(65, track_id=2, pitch=88.5)
        speakers = await api.api_meeting_speakers()
        self.assertEqual(speakers["total"], 1)
        self.assertEqual(speakers["speakers"][0]["label"], "说话人A")

        old_wake = api._wake_word_service
        try:
            api._wake_word_service = None
            state = await api.api_wake_word_state()
            self.assertFalse(state["enabled"])
            self.assertFalse(state["available"])
        finally:
            api._wake_word_service = old_wake

    async def test_voice_state_say_and_stop_are_stable_without_wake_word(self) -> None:
        old_wake = api._wake_word_service
        sent = []
        old_broadcast = api.ws_mgr.broadcast

        async def fake_broadcast(data):
            sent.append(data)

        try:
            api._wake_word_service = None
            api.ws_mgr.broadcast = fake_broadcast
            state = await api.api_voice_state()
            self.assertIn("enabled", state)
            self.assertEqual(state["engine"], "browser_speech")

            said = await api.api_voice_say({"text": "小屿语音测试。", "reason": "manual", "source": "test"})
            self.assertTrue(said["ok"])
            self.assertEqual(sent[-1]["type"], "voice_utterance")
            self.assertEqual(sent[-1]["text"], "小屿语音测试。")

            stopped = await api.api_voice_stop({"reason": "test"})
            self.assertTrue(stopped["ok"])
            self.assertEqual(sent[-1]["type"], "voice_stop")
            self.assertFalse((await api.api_wake_word_state())["enabled"])
        finally:
            api._wake_word_service = old_wake
            api.ws_mgr.broadcast = old_broadcast

    async def test_meeting_summarize_uses_speaker_labels_and_preserves_errors(self) -> None:
        class FakeRecorder:
            def __init__(self, turns):
                self._turns = turns
                self.active = False

            def state(self):
                return {"timeline": self._turns, "stats": {"duration": 60}}

            def audio_processing_state(self):
                return {"noise_suppression": {"enabled": False}, "vad_mode": "rms"}

            def set_transcript(self, turn_id, text, confidence=0.0):
                for turn in self._turns:
                    if str(turn.get("id", "")) == str(turn_id):
                        turn["text"] = text
                return True

            def save_report(self, report):
                self.report = dict(report)
                return "/tmp/meeting_report.json"

        old_recorder = api._conversation_recorder
        old_chat = api._deepseek_chat
        captured = {}

        async def fake_chat(messages, max_tokens=None):
            captured["user"] = messages[-1]["content"]
            return '{"diary":"会议整理完成。","summary":"完成整理"}'

        try:
            api._conversation_recorder = FakeRecorder([])
            no_segments = await api.api_meeting_summarize({})
            self.assertEqual(no_segments["error_code"], "no_segments")

            with tempfile.NamedTemporaryFile(suffix=".wav") as wav:
                api._conversation_recorder = FakeRecorder([{
                    "id": "turn_1",
                    "wav_path": wav.name,
                    "speaker_label": "说话人A",
                    "doa_mean": 65.0,
                }])
                api._deepseek_chat = fake_chat
                import services.cloud_asr as cloud_asr_module
                old_transcribe = cloud_asr_module.cloud_asr.transcribe

                async def fake_transcribe(_path):
                    return "今天讨论了项目进展。"

                cloud_asr_module.cloud_asr.transcribe = fake_transcribe
                try:
                    result = await api.api_meeting_summarize({})
                finally:
                    cloud_asr_module.cloud_asr.transcribe = old_transcribe

            self.assertTrue(result["ok"])
            self.assertIn("[说话人A] 今天讨论了项目进展。", result["transcript"])
            self.assertIn("[说话人A] 今天讨论了项目进展。", captured["user"])
        finally:
            api._conversation_recorder = old_recorder
            api._deepseek_chat = old_chat

    def test_whisperx_final_formats_segments_with_existing_speaker_labels(self) -> None:
        turns = [
            {"start": 0.0, "end": 3.0, "speaker_label": "说话人A"},
            {"start": 3.0, "end": 6.0, "speaker_label": "说话人B"},
        ]
        segments = [
            {"start": 0.2, "end": 2.5, "text": "讨论书画展安排。"},
            {"start": 3.2, "end": 5.5, "text": "确认场地预约。"},
        ]
        lines = whisperx_final.format_transcript_segments(segments, turns)
        self.assertEqual(lines[0], "[0.2-2.5s][说话人A] 讨论书画展安排。")
        self.assertEqual(lines[1], "[3.2-5.5s][说话人B] 确认场地预约。")

    async def test_meeting_summarize_prefers_whisperx_final_transcript(self) -> None:
        class FakeRecorder:
            def __init__(self, turns):
                self._turns = turns
                self.active = False

            def state(self):
                return {"timeline": self._turns, "stats": {"duration": 60}}

            def set_transcript(self, turn_id, text, confidence=0.0):
                raise AssertionError("segment ASR fallback should not run")

            def save_report(self, report):
                self.report = dict(report)
                return "/tmp/meeting_report.json"

        old_recorder = api._conversation_recorder
        old_chat = api._deepseek_chat
        old_ensure = api._ensure_asr_worker
        old_transcribe = whisperx_final.transcribe_meeting_turns
        captured = {}

        async def fake_chat(messages, max_tokens=None):
            captured["user"] = messages[-1]["content"]
            return '{"diary":"会议整理完成。","summary":"完成整理"}'

        async def fake_whisperx(_turns):
            return whisperx_final.WhisperXFinalResult(
                ok=True,
                transcript="[0.0-2.0s][说话人A] WhisperX最终转写。",
                lines=["[0.0-2.0s][说话人A] WhisperX最终转写。"],
                segments=[{"start": 0.0, "end": 2.0, "text": "WhisperX最终转写。"}],
                input_wav="/tmp/final.wav",
                output_json="/tmp/whisperx.json",
                duration_seconds=1.2,
            )

        with tempfile.TemporaryDirectory() as td:
            session = Path(td) / "session_20260707_130000"
            seg_dir = session / "audio" / "segments"
            seg_dir.mkdir(parents=True)
            wav_path = seg_dir / "seg_000001.wav"
            wav_path.write_bytes(b"placeholder")
            try:
                api._conversation_recorder = FakeRecorder([{
                    "id": "turn_1",
                    "wav_path": str(wav_path),
                    "speaker_label": "说话人A",
                    "start": 0.0,
                    "end": 2.0,
                }])
                api._deepseek_chat = fake_chat
                api._ensure_asr_worker = lambda: None
                whisperx_final.transcribe_meeting_turns = fake_whisperx

                result = await api.api_meeting_summarize({})
            finally:
                api._conversation_recorder = old_recorder
                api._deepseek_chat = old_chat
                api._ensure_asr_worker = old_ensure
                whisperx_final.transcribe_meeting_turns = old_transcribe

        self.assertTrue(result["ok"])
        self.assertEqual(result["transcript_provider"], "whisperx")
        self.assertIn("WhisperX最终转写", result["transcript"])
        self.assertIn("WhisperX最终转写", captured["user"])

    async def test_meeting_complete_submits_background_stop_and_summary(self) -> None:
        old_stop = api.api_multi_track_stop
        old_summarize = api.api_meeting_summarize
        old_task = api._meeting_summary_task
        calls = []

        async def fake_stop(payload):
            calls.append(("stop", payload["session_id"]))
            return {"ok": True, "accepted": True, "active": False}

        async def fake_summarize(payload):
            calls.append(("summarize", payload["session_id"]))
            return {"ok": True, "summary": "完成", "minutes": "会议完成"}

        try:
            api._meeting_summary_task = None
            api.api_multi_track_stop = fake_stop
            api.api_meeting_summarize = fake_summarize
            result = await api.api_meeting_complete({"session_id": "meeting-1"})
            self.assertTrue(result["accepted"])
            self.assertTrue(result["submitted"])
            self.assertTrue(result["processing"])
            await api._meeting_summary_task
            self.assertEqual(calls, [("stop", "meeting-1"), ("summarize", "meeting-1")])
        finally:
            api.api_multi_track_stop = old_stop
            api.api_meeting_summarize = old_summarize
            api._meeting_summary_task = old_task

    async def test_multi_track_start_submits_recording_without_blocking(self) -> None:
        old_start_feature = api._start_feature
        old_ensure_doa = api._ensure_doa_reader
        old_start_recording = api._start_conversation_recording
        old_recorder = api._conversation_recorder
        old_requested = api._conversation_recording_requested
        old_report = dict(api._meeting_report)
        old_task = api._meeting_recording_task
        calls = []

        async def fake_start_feature(feature):
            calls.append(("feature", feature))
            return {"ok": True, "accepted": True, "session_id": "meeting-2"}

        try:
            api._start_feature = fake_start_feature
            api._ensure_doa_reader = lambda: True
            api._start_conversation_recording = lambda: calls.append(("recording", True)) or True
            api._conversation_recorder = None
            api._meeting_recording_task = None
            result = await api.api_multi_track_start({"save_audio": True})
            self.assertTrue(result["accepted"])
            self.assertEqual(result["recording_state"], "starting")
            self.assertTrue(api._conversation_recording_requested)
            self.assertEqual(api._meeting_report["status"], "recording_starting")
            self.assertEqual(calls[:1], [("feature", "multi_sound_yaw")])
            await api._meeting_recording_task
            self.assertIn(("recording", True), calls)
        finally:
            api._start_feature = old_start_feature
            api._ensure_doa_reader = old_ensure_doa
            api._start_conversation_recording = old_start_recording
            api._conversation_recorder = old_recorder
            api._conversation_recording_requested = old_requested
            api._meeting_report = old_report
            api._meeting_recording_task = old_task

    async def test_conversation_segment_enters_asr_queue_and_writes_transcript(self) -> None:
        import services.cloud_asr as cloud_asr_module

        old_recorder = api._conversation_recorder
        old_queue = api._asr_queue
        old_worker = api._asr_worker_task
        old_loop = api._asr_loop
        old_enqueued = set(api._asr_enqueued_turns)
        old_running = set(api._asr_running_turns)
        old_stats = dict(api._asr_stats)
        old_transcribe = cloud_asr_module.cloud_asr.transcribe

        async def fake_transcribe(_path):
            return "这是实时转写结果"

        try:
            if api._asr_worker_task is not None:
                api._asr_worker_task.cancel()
            api._asr_queue = None
            api._asr_worker_task = None
            api._asr_loop = None
            api._asr_enqueued_turns.clear()
            api._asr_running_turns.clear()
            api._asr_stats.update({"pending": 0, "running": 0, "done": 0, "failed": 0, "last_error": "", "last_error_at": 0.0})
            cloud_asr_module.cloud_asr.transcribe = fake_transcribe

            with tempfile.TemporaryDirectory() as tmp:
                api._ensure_asr_worker()
                recorder = ConversationRecorder(
                    root=tmp,
                    doa_provider=lambda: (20.0, True),
                    speaker_provider=lambda doa: {"label": "匿名说话人 1", "track_id": 7, "confidence": 0.72},
                    segment_callback=api._on_conversation_segment,
                )
                api._conversation_recorder = recorder
                recorder._session_id = "session_test"
                recorder._session_dir = recorder.root / recorder._session_id
                (recorder._session_dir / "audio" / "segments").mkdir(parents=True)
                recorder._started_at = 100.0
                recorder._finalize_segment([np.zeros(1600, dtype=np.float32)], [20.0], 101.0, 102.0)
                await asyncio.sleep(0)
                await asyncio.wait_for(api._asr_queue.join(), timeout=2.0)
                turn = recorder.state()["timeline"][0]
                self.assertEqual(turn["text"], "这是实时转写结果")
                self.assertEqual(turn["status"], "transcribed")
                self.assertEqual(turn["track_id"], 7)
                self.assertAlmostEqual(turn["association_confidence"], 0.72)
        finally:
            if api._asr_worker_task is not None:
                api._asr_worker_task.cancel()
                try:
                    await api._asr_worker_task
                except asyncio.CancelledError:
                    pass
            api._conversation_recorder = old_recorder
            api._asr_queue = old_queue
            api._asr_worker_task = old_worker
            api._asr_loop = old_loop
            api._asr_enqueued_turns.clear()
            api._asr_enqueued_turns.update(old_enqueued)
            api._asr_running_turns.clear()
            api._asr_running_turns.update(old_running)
            api._asr_stats.clear()
            api._asr_stats.update(old_stats)
            cloud_asr_module.cloud_asr.transcribe = old_transcribe

    async def test_control_heartbeat_times_out_without_blocking_ui(self) -> None:
        old_eventbus = api._eventbus
        old_timeout = api.HEARTBEAT_EVENTBUS_TIMEOUT_S
        old_future = api._heartbeat_future
        old_in_flight = api._heartbeat_eventbus_in_flight
        old_state = dict(api._heartbeat_state)

        class SlowEventBus:
            host = "127.0.0.1"
            port = 8765

            def emit(self, _event):
                time.sleep(0.2)
                return {"ok": True, "accepted": True, "runtime": {"active_feature": "multi_sound_yaw"}}

        try:
            api._eventbus = SlowEventBus()
            api.HEARTBEAT_EVENTBUS_TIMEOUT_S = 0.02
            api._heartbeat_future = None
            api._heartbeat_eventbus_in_flight = False
            started = time.monotonic()
            result = await api.api_control_heartbeat({"session_id": "meeting-1"})
            elapsed = time.monotonic() - started
            self.assertLess(elapsed, 0.15)
            self.assertFalse(result["accepted"])
            self.assertTrue(result["degraded"])
            self.assertEqual(result["reason"], "eventbus_timeout")

            busy = await api.api_control_heartbeat({"session_id": "meeting-1"})
            self.assertEqual(busy["reason"], "eventbus_busy")
            await asyncio.sleep(0.25)
        finally:
            api._eventbus = old_eventbus
            api.HEARTBEAT_EVENTBUS_TIMEOUT_S = old_timeout
            api._heartbeat_future = old_future
            api._heartbeat_eventbus_in_flight = old_in_flight
            api._heartbeat_state.clear()
            api._heartbeat_state.update(old_state)

    async def test_degraded_hardware_handlers_return_within_one_second(self) -> None:
        old_eventbus = api._eventbus
        old_start_feature = api._start_feature
        old_ensure_asr_worker = api._ensure_asr_worker
        old_runtime = dict(api._runtime_cache)
        old_ui_session = api._ui_session_id
        old_started = api._meeting_started_at
        old_ended = api._meeting_ended_at
        old_report = dict(api._meeting_report)
        old_requested = api._conversation_recording_requested
        old_task = api._meeting_recording_task
        old_heartbeat_future = api._heartbeat_future
        old_heartbeat_in_flight = api._heartbeat_eventbus_in_flight

        degraded_runtime = {
            "connected": True,
            "active_feature": "multi_sound_yaw",
            "session_id": "meeting-timeout",
            "lease_remaining_ms": 5000,
            "hardware_ready": False,
            "gimbal_bridge_status": "timeout",
            "gimbal_bridge_circuit_open": True,
            "gimbal_bridge_last_error": "bridge request timed out",
        }

        class DegradedEventBus:
            host = "127.0.0.1"
            port = 8765

            def emit(self, _event):
                return {"ok": True, "accepted": True, "authority": "main_phase3", "runtime": degraded_runtime}

        async def degraded_start(_feature):
            return {
                "ok": True, "accepted": True, "session_id": "meeting-timeout",
                "hardware_ready": False, **degraded_runtime,
            }

        try:
            api._eventbus = DegradedEventBus()
            api._start_feature = degraded_start
            api._ensure_asr_worker = lambda: None
            api._runtime_cache = api._runtime_with_telemetry_defaults(degraded_runtime)
            api._ui_session_id = ""
            api._heartbeat_future = None
            api._heartbeat_eventbus_in_flight = False

            handlers = [
                ("state", lambda: api.api_state()),
                ("runtime", lambda: api.api_control_runtime()),
                ("heartbeat", lambda: api.api_control_heartbeat({"session_id": "meeting-timeout"})),
                ("multi_start", lambda: api.api_multi_track_start({"save_audio": False})),
                ("control_page", lambda: api.serve_control()),
                ("video_feed", lambda: api.video_feed()),
                ("snapshot", lambda: api.snapshot()),
            ]
            results = {}
            for name, handler in handlers:
                started = time.monotonic()
                results[name] = await handler()
                self.assertLess(time.monotonic() - started, 1.0, name)

            self.assertTrue(results["heartbeat"]["accepted"])
            self.assertGreater(results["heartbeat"]["last_heartbeat_at"], 0)
            self.assertGreater(results["heartbeat"]["runtime"]["lease_remaining_ms"], 0)
            self.assertEqual(results["runtime"]["runtime"]["gimbal_bridge_status"], "timeout")
            self.assertTrue(results["runtime"]["runtime"]["gimbal_bridge_circuit_open"])
            self.assertEqual(results["multi_start"]["start_status"], "accepted_with_degraded_hardware")
            self.assertEqual(results["multi_start"]["recording_state"], "disabled")
        finally:
            api._eventbus = old_eventbus
            api._start_feature = old_start_feature
            api._ensure_asr_worker = old_ensure_asr_worker
            api._runtime_cache = old_runtime
            api._ui_session_id = old_ui_session
            api._meeting_started_at = old_started
            api._meeting_ended_at = old_ended
            api._meeting_report = old_report
            api._conversation_recording_requested = old_requested
            api._meeting_recording_task = old_task
            api._heartbeat_future = old_heartbeat_future
            api._heartbeat_eventbus_in_flight = old_heartbeat_in_flight

    async def test_meeting_duration_advances_without_audio_segments(self) -> None:
        old_recorder = api._conversation_recorder
        old_started = api._meeting_started_at
        old_ended = api._meeting_ended_at
        try:
            api._conversation_recorder = None
            api._meeting_started_at = time.monotonic() - 2.0
            api._meeting_ended_at = 0.0
            self.assertGreaterEqual(api._conversation_state()["stats"]["duration"], 1.9)
        finally:
            api._conversation_recorder = old_recorder
            api._meeting_started_at = old_started
            api._meeting_ended_at = old_ended

    async def test_system_health_reports_zhipu_unconfigured(self) -> None:
        old_key = os.environ.pop("ZHIPU_API_KEY", None)
        try:
            health = await api.api_system_health()
            self.assertIn("zhipu_llm", health["components"])
            self.assertIn("zhipu_asr", health["components"])
            self.assertFalse(health["components"]["zhipu_llm"]["configured"])
            self.assertEqual(health["components"]["zhipu_llm"]["status"], "offline")
        finally:
            if old_key is not None:
                os.environ["ZHIPU_API_KEY"] = old_key

    async def test_voice_tts_degrades_without_zhipu_key(self) -> None:
        old_key = os.environ.pop("ZHIPU_API_KEY", None)
        try:
            result = await api.api_voice_tts({"text": "测试一句话", "play": False})
            self.assertFalse(result["ok"])
            self.assertEqual(result["error"], "unconfigured")
            self.assertIn("playback", result["state"])
        finally:
            if old_key is not None:
                os.environ["ZHIPU_API_KEY"] = old_key

    async def test_voice_play_missing_audio_reports_error(self) -> None:
        result = await api.api_voice_play({"audio_id": "missing_audio_for_test", "target": "browser"})
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "audio_missing")

    async def test_voice_test_tone_generates_audio_without_zhipu_key(self) -> None:
        old_key = os.environ.pop("ZHIPU_API_KEY", None)
        try:
            result = await api.api_voice_test_tone({"play": False})
            self.assertTrue(result["ok"])
            self.assertEqual(result["provider"], "local_test_tone")
            path = Path(result["path"])
            self.assertTrue(path.is_file())
            self.assertEqual(path.read_bytes()[:4], b"RIFF")
            self.assertTrue(result["audio_url"].startswith("/api/voice/audio/"))
        finally:
            if old_key is not None:
                os.environ["ZHIPU_API_KEY"] = old_key

    async def test_voice_playback_status_reports_unreachable_bridge(self) -> None:
        class FakeAudioClient:
            def state(self):
                return {"configured": True, "last_error": "timeout", "last_result": {}}

            def status(self):
                return {"ok": False, "state": "unreachable", "error": "timeout"}

        old_client = api._voice_audio_client
        try:
            api._voice_audio_client = FakeAudioClient()
            result = await api.api_voice_playback_status()
            self.assertFalse(result["ok"])
            self.assertEqual(result["state"], "unreachable")
            self.assertEqual(result["playback"]["last_error"], "timeout")
        finally:
            api._voice_audio_client = old_client

    async def test_voice_announce_settings_persist_and_are_sanitized(self) -> None:
        settings_path = api._voice_announce_settings_path
        old_text = settings_path.read_text(encoding="utf-8") if settings_path.is_file() else None
        old_settings = dict(api._voice_announce_settings)
        try:
            if settings_path.is_file():
                settings_path.unlink()
            api._voice_announce_settings = dict(api._VOICE_ANNOUNCE_DEFAULTS)
            default_result = await api.api_voice_announce_settings()
            self.assertTrue(default_result["ok"])
            self.assertEqual(default_result["settings"]["sedentary_minutes"], 45)

            updated = await api.api_voice_announce_settings_update({
                "enabled": "true",
                "sedentary_minutes": 999,
                "snooze_minutes": 0,
                "target": "device",
                "eye_fatigue_enabled": "false",
            })
            self.assertTrue(updated["settings"]["enabled"])
            self.assertEqual(updated["settings"]["sedentary_minutes"], 240)
            self.assertEqual(updated["settings"]["snooze_minutes"], 1)
            self.assertEqual(updated["settings"]["target"], "recamera_speaker")
            self.assertFalse(updated["settings"]["eye_fatigue_enabled"])
            api._voice_announce_settings = dict(api._VOICE_ANNOUNCE_DEFAULTS)
            reloaded = api._load_voice_announce_settings()
            self.assertEqual(reloaded["sedentary_minutes"], 240)
            self.assertIn("announce", (await api.api_voice_state()))
        finally:
            api._voice_announce_settings = old_settings
            if old_text is None:
                try:
                    settings_path.unlink()
                except FileNotFoundError:
                    pass
            else:
                settings_path.parent.mkdir(parents=True, exist_ok=True)
                settings_path.write_text(old_text, encoding="utf-8")

    async def test_voice_announce_test_force_uses_tts_and_recamera_target(self) -> None:
        class FakeZhipuVoice:
            async def tts(self, text, out_path, options=None):
                Path(out_path).parent.mkdir(parents=True, exist_ok=True)
                Path(out_path).write_bytes(b"RIFF....WAVEfmt ")
                return {
                    "ok": True,
                    "path": out_path,
                    "provider": "fake",
                    "content_type": "audio/wav",
                    "format": "wav",
                    "model": "fake-tts",
                }

            def status(self):
                return {"configured": True, "tts_model": "fake-tts", "tts_format": "wav"}

        class FakeAudioClient:
            def __init__(self):
                self.calls = []

            def play(self, audio_path, audio_id="", content_type="audio/wav"):
                self.calls.append((audio_path, audio_id, content_type))
                return {"ok": True, "state": "done", "audio_id": audio_id}

            def state(self):
                return {"configured": True, "last_error": "", "last_result": {}}

        old_zhipu = api.zhipu_voice
        old_client = api._voice_audio_client
        old_settings = dict(api._voice_announce_settings)
        fake_client = FakeAudioClient()
        try:
            api.zhipu_voice = FakeZhipuVoice()
            api._voice_audio_client = fake_client
            api._voice_announce_settings = {**api._VOICE_ANNOUNCE_DEFAULTS, "enabled": False}
            result = await api.api_voice_announce_test({"reason": "sedentary", "force": True})
            self.assertTrue(result["ok"])
            self.assertEqual(result["reason"], "sedentary")
            self.assertEqual(result["text"], "你已经坐了挺久了，起来走两分钟，回来我还在。")
            self.assertEqual(result["playback"]["target"], "recamera_speaker")
            self.assertEqual(result["playback"]["bridge"]["state"], "done")
            self.assertTrue(fake_client.calls)
        finally:
            api.zhipu_voice = old_zhipu
            api._voice_audio_client = old_client
            api._voice_announce_settings = old_settings

    async def test_voice_announce_test_reports_bad_reason_and_tts_error(self) -> None:
        old_key = os.environ.pop("ZHIPU_API_KEY", None)
        try:
            missing = await api.api_voice_announce_test({"reason": "", "force": True})
            self.assertFalse(missing["ok"])
            self.assertEqual(missing["error"], "reason_required")
            result = await api.api_voice_announce_test({"reason": "eye_fatigue", "force": True})
            self.assertFalse(result["ok"])
            self.assertEqual(result["error"], "unconfigured")
            self.assertIn("providers", result)
        finally:
            if old_key is not None:
                os.environ["ZHIPU_API_KEY"] = old_key

    async def test_voice_announce_auto_triggers_sedentary_and_fatigue(self) -> None:
        old_settings = dict(api._voice_announce_settings)
        old_runtime = dict(api._voice_announce_runtime)
        old_pending = set(api._voice_announce_runtime.get("pending") or set())
        try:
            api._voice_announce_settings = {
                **api._VOICE_ANNOUNCE_DEFAULTS,
                "enabled": True,
                "sedentary_minutes": 45,
                "snooze_minutes": 10,
            }
            api._voice_announce_runtime.update({
                "presence_since": 0.0,
                "away_since": 0.0,
                "fatigue_since": 0.0,
                "last_triggered": {},
                "pending": set(),
            })
            self.assertEqual(api._evaluate_voice_announce_triggers(True, {}, now=1000.0), [])
            self.assertEqual(api._evaluate_voice_announce_triggers(True, {}, now=1000.0 + 45 * 60 + 1), ["sedentary"])
            self.assertEqual(api._evaluate_voice_announce_triggers(True, {}, now=1000.0 + 45 * 60 + 20), [])

            api._evaluate_voice_announce_triggers(False, {}, now=5000.0)
            api._evaluate_voice_announce_triggers(False, {}, now=5121.0)
            self.assertEqual(api._voice_announce_runtime["presence_since"], 0.0)

            self.assertEqual(api._evaluate_voice_announce_triggers(True, {"fatigue_level": "drowsy"}, now=6000.0), [])
            self.assertEqual(api._evaluate_voice_announce_triggers(True, {"fatigue_level": "drowsy"}, now=6061.0), ["eye_fatigue"])
        finally:
            api._voice_announce_settings = old_settings
            api._voice_announce_runtime.clear()
            api._voice_announce_runtime.update(old_runtime)
            api._voice_announce_runtime["pending"] = old_pending

    async def test_meeting_status_announcements_are_scheduled(self) -> None:
        old_schedule = api._schedule_voice_announce
        old_recorder = api._conversation_recorder
        old_requested = api._conversation_recording_requested
        scheduled = []
        try:
            api._schedule_voice_announce = lambda reason, source="auto_announce": scheduled.append((reason, source))
            api._conversation_recorder = None
            await api.api_conversation_start({"save_audio": False})
            await api.api_conversation_stop({})
            result = await api.api_meeting_summarize({})
            self.assertFalse(result["ok"])
            self.assertIn(("meeting_start", "conversation_start"), scheduled)
            self.assertIn(("meeting_stop", "conversation_stop"), scheduled)
            self.assertIn(("meeting_summary_error", "meeting_summarize"), scheduled)
        finally:
            api._schedule_voice_announce = old_schedule
            api._conversation_recorder = old_recorder
            api._conversation_recording_requested = old_requested

    async def test_voice_control_routes_registered(self) -> None:
        route_paths = {getattr(route, "path", "") for route in api.app.routes}
        for path in (
            "/api/voice/playback/status",
            "/api/voice/announce/settings",
            "/api/voice/announce/test",
            "/api/voice/test_tone",
            "/api/voice/chat",
            "/api/voice/tts",
            "/api/voice/play",
        ):
            self.assertIn(path, route_paths)

    async def test_doa_direction_endpoint_persists_and_returns_handedness(self) -> None:
        calib = Path(api.__file__).resolve().parent / "runtime" / "doa_calibration.json"
        old_text = calib.read_text(encoding="utf-8") if calib.is_file() else None
        old_ui_sid = api._ui_session_id
        try:
            api._ui_session_id = ""
            result = await api.api_control_doa_direction({"doa_direction": -1})
            self.assertTrue(result["ok"])
            self.assertEqual(result["doa_direction"], -1)
            saved = calib.read_text(encoding="utf-8")
            self.assertIn('"doa_direction": -1', saved)
        finally:
            api._ui_session_id = old_ui_sid
            if old_text is None:
                try:
                    calib.unlink()
                except FileNotFoundError:
                    pass
            else:
                calib.write_text(old_text, encoding="utf-8")


class AttentionScoringTests(unittest.TestCase):
    def test_update_raw_does_not_apply_orientation_stability_weights_again(self) -> None:
        cfg = AttentionConfig(window_size=0, orientation_weight=0.7, stability_weight=0.3)
        scoring = ScoringModule(cfg)
        self.assertEqual(scoring.update_raw(100), 100)

        weighted = ScoringModule(cfg)
        self.assertEqual(weighted.update(100, 0), 70)


if __name__ == "__main__":
    unittest.main()
