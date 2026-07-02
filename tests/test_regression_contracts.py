from __future__ import annotations

import unittest

import recamera_fastapi as api
from services.emotion_prompt import build_emotion_context
import services.llm_router as llm_router
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
            self.assertEqual(result, {"text": "", "provider": "none"})
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


class AttentionScoringTests(unittest.TestCase):
    def test_update_raw_does_not_apply_orientation_stability_weights_again(self) -> None:
        cfg = AttentionConfig(window_size=0, orientation_weight=0.7, stability_weight=0.3)
        scoring = ScoringModule(cfg)
        self.assertEqual(scoring.update_raw(100), 100)

        weighted = ScoringModule(cfg)
        self.assertEqual(weighted.update(100, 0), 70)


if __name__ == "__main__":
    unittest.main()
