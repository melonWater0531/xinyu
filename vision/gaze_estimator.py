"""Lightweight gaze trend estimation from MediaPipe face landmarks."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np


@dataclass
class GazeResult:
    available: bool = False
    state: str = "unknown"
    x_offset: float = 0.0
    y_offset: float = 0.0
    confidence: float = 0.0

    def as_dict(self) -> dict:
        return {
            "available": self.available,
            "state": self.state,
            "x_offset": round(float(self.x_offset), 3),
            "y_offset": round(float(self.y_offset), 3),
            "confidence": round(float(self.confidence), 3),
        }


class GazeEstimator:
    """Estimate coarse gaze trend, not precise eye tracking.

    MediaPipe Face Landmarker returns 478 points when iris landmarks are present.
    The estimator compares iris centers against eye-corner centers and reports a
    stable coarse trend for attention hints.
    """

    LEFT_EYE_CORNERS = (33, 133)
    RIGHT_EYE_CORNERS = (362, 263)
    LEFT_IRIS = (468, 469, 470, 471)
    RIGHT_IRIS = (473, 474, 475, 476)

    def update(self, landmarks: Optional[np.ndarray]) -> dict:
        if landmarks is None:
            return GazeResult().as_dict()
        pts = np.asarray(landmarks, dtype=np.float32)
        if pts.ndim != 2 or pts.shape[0] < 477 or pts.shape[1] < 2:
            return GazeResult().as_dict()

        try:
            left = self._eye_offset(pts, self.LEFT_EYE_CORNERS, self.LEFT_IRIS)
            right = self._eye_offset(pts, self.RIGHT_EYE_CORNERS, self.RIGHT_IRIS)
        except Exception:
            return GazeResult().as_dict()

        x_off = float((left[0] + right[0]) / 2.0)
        y_off = float((left[1] + right[1]) / 2.0)
        mag = min(1.0, (abs(x_off) + abs(y_off)) / 0.8)
        confidence = max(0.2, min(1.0, 1.0 - mag * 0.35))

        if y_off > 0.22:
            state = "down"
        elif abs(x_off) <= 0.18 and abs(y_off) <= 0.22:
            state = "center"
        elif x_off < -0.18:
            state = "left"
        elif x_off > 0.18:
            state = "right"
        else:
            state = "away"

        return GazeResult(True, state, x_off, y_off, confidence).as_dict()

    @staticmethod
    def _eye_offset(pts: np.ndarray, corners: tuple[int, int], iris_ids: tuple[int, ...]) -> tuple[float, float]:
        c0 = pts[corners[0], :2]
        c1 = pts[corners[1], :2]
        iris = np.mean(pts[list(iris_ids), :2], axis=0)
        center = (c0 + c1) / 2.0
        width = max(1.0, float(np.linalg.norm(c0 - c1)))
        return float((iris[0] - center[0]) / width), float((iris[1] - center[1]) / width)

