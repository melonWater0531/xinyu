from __future__ import annotations

import unittest
from pathlib import Path

import numpy as np

from vision.gesture_detector import GestureDetector


class _Cat:
    def __init__(self, category_name: str, score: float = 0.95) -> None:
        self.category_name = category_name
        self.score = score


class _Result:
    def __init__(self, name: str) -> None:
        self.gestures = [[_Cat(name)]]
        self.handedness = [[_Cat("Right")]]


class _Recognizer:
    def __init__(self, name: str) -> None:
        self.name = name

    def recognize(self, _image):
        return _Result(self.name)


class GestureDetectorTests(unittest.TestCase):
    def test_missing_model_reports_unavailable(self) -> None:
        detector = GestureDetector(model_path="/tmp/recamera_missing_gesture.task")
        state = detector.detect(np.zeros((32, 32, 3), dtype=np.uint8))
        self.assertFalse(state["available"])
        self.assertIn("model_missing", state["reason"])

    def test_downloaded_model_loads_when_present(self) -> None:
        model = Path(__file__).parents[1] / "models" / "gesture_recognizer.task"
        self.assertTrue(model.is_file(), "gesture_recognizer.task must be downloaded")
        detector = GestureDetector(model_path=str(model))
        state = detector.detect(np.zeros((64, 64, 3), dtype=np.uint8))
        self.assertTrue(state["available"])
        self.assertIn(state["reason"], {"no_gesture", ""})

    def test_closed_fist_is_recognition_only_without_intent(self) -> None:
        detector = GestureDetector(model_path="/tmp/unused.task", stable_frames=2, cooldown_sec=10.0)
        detector._loaded = True
        detector._recognizer = _Recognizer("Closed_Fist")

        first = detector.detect(np.zeros((32, 32, 3), dtype=np.uint8))
        second = detector.detect(np.zeros((32, 32, 3), dtype=np.uint8))

        self.assertEqual(first["name"], "Closed_Fist")
        self.assertEqual(second["name"], "Closed_Fist")
        self.assertEqual(second["stable_frames"], 2)
        self.assertGreaterEqual(second["progress"], 1.0)
        self.assertEqual(second["intent"], "")
        self.assertFalse(first["intent_ready"])
        self.assertFalse(second["intent_ready"])
        self.assertEqual(second["pending_confirm"], "")
        self.assertFalse(second["intent_confirmed"])


if __name__ == "__main__":
    unittest.main()
