from __future__ import annotations

import unittest

import recamera_fastapi as api
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


class AttentionScoringTests(unittest.TestCase):
    def test_update_raw_does_not_apply_orientation_stability_weights_again(self) -> None:
        cfg = AttentionConfig(window_size=0, orientation_weight=0.7, stability_weight=0.3)
        scoring = ScoringModule(cfg)
        self.assertEqual(scoring.update_raw(100), 100)

        weighted = ScoringModule(cfg)
        self.assertEqual(weighted.update(100, 0), 70)


if __name__ == "__main__":
    unittest.main()
