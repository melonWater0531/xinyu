from __future__ import annotations

import unittest

import numpy as np

import recamera_fastapi as api
from audio.noise_suppressor import MeetingAudioProcessor
from vision.person_stabilizer import StablePersonCounter


class ExtensionFeatureTests(unittest.TestCase):
    def test_audio_processor_falls_back_without_optional_dependencies(self) -> None:
        processor = MeetingAudioProcessor(sample_rate=16000)
        state = processor.state()
        self.assertIn("noise_suppression", state)
        self.assertIn(state["vad_mode"], {"webrtcvad", "rms"})

        audio = np.zeros(1600, dtype=np.float32)
        cleaned = processor.process(audio)
        self.assertEqual(cleaned.shape, audio.shape)
        if state["vad_mode"] == "rms":
            self.assertIsNone(processor.is_voiced(audio))

    def test_webrtcvad_silence_path_with_fake_vad(self) -> None:
        class FakeVad:
            def is_speech(self, frame: bytes, sample_rate: int) -> bool:
                return any(frame)

        processor = MeetingAudioProcessor(sample_rate=16000)
        processor._vad = FakeVad()
        self.assertFalse(processor.is_voiced(np.zeros(1600, dtype=np.float32)))
        self.assertTrue(processor.is_voiced(np.ones(1600, dtype=np.float32) * 0.1))

    def test_stable_person_counter_resists_single_frame_spike(self) -> None:
        counter = StablePersonCounter(window_sec=10.0, min_samples=3, switch_ratio=0.65)
        now = 1000.0
        for idx, count in enumerate([1, 1, 2, 1, 1]):
            state = counter.update(count, now + idx)
        self.assertEqual(state["stable_count"], 1)

        for idx, count in enumerate([2, 2, 2], start=5):
            state = counter.update(count, now + idx)
        self.assertEqual(state["stable_count"], 2)

    def test_state_snapshot_exposes_extension_fields(self) -> None:
        old_persons = list(api._latest_pose_persons)
        try:
            api._latest_pose_persons.clear()
            data = api.build_state_snapshot()["data"]
            self.assertIn("audio_processing", data)
            self.assertIn("stable_count", data["pose"])
            self.assertIn("vad_mode", data["audio_processing"])
        finally:
            api._latest_pose_persons[:] = old_persons


if __name__ == "__main__":
    unittest.main()

