from __future__ import annotations

import unittest
from pathlib import Path

import numpy as np

from vision.gesture_detector import GestureDetector


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


if __name__ == "__main__":
    unittest.main()
