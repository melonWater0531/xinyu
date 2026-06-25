"""
Eye/Mouth metrics for focus estimation — EAR, blink rate, PERCLOS.

Uses MediaPipe 478 face landmarks to compute eye openness and blink patterns.
These metrics serve as inverse indicators of focus (not "fatigue" labels).
"""
from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass
from typing import Optional, Tuple

import numpy as np

from utils.logger import get_logger

logger = get_logger(__name__)

# MediaPipe landmark indices for left/right eye contours
LEFT_EYE_IDX  = [33, 160, 158, 133, 153, 144]   # 6 points around left eye
RIGHT_EYE_IDX = [362, 385, 387, 263, 373, 380]   # 6 points around right eye

# EAR threshold: below this = eye closing
EAR_CLOSED = 0.18
EAR_NORMAL = 0.28


@dataclass
class EyeMetrics:
    ear_left: float = 0.0
    ear_right: float = 0.0
    ear_avg: float = 0.0
    blink_count: int = 0          # total blinks this session
    blink_rate: float = 0.0        # blinks per minute
    perclos: float = 0.0           # % frames with eyes closed
    eye_open: bool = True
    focus_score: int = 100         # 0-100 derived from eye metrics


class EyeMetricTracker:
    """
    Track EAR, blink rate, PERCLOS over time.
    Outputs a focus_score (0-100) where:
      - High EAR + normal blink + low PERCLOS → 100 (focused)
      - Low EAR + abnormal blink + high PERCLOS → 0 (unfocused)
    """

    def __init__(self, window_sec: float = 30.0,
                 ear_threshold: float = EAR_CLOSED):
        self._window_sec = window_sec
        self._ear_threshold = ear_threshold

        self._ear_history: deque = deque()           # (ts, ear_avg)
        self._closed_history: deque = deque()         # (ts, bool)
        self._blink_events: deque = deque()           # blink timestamps
        self._prev_closed = False
        self._total_blinks = 0
        self._session_start = time.time()

    def update(self, landmarks: Optional[np.ndarray] = None,
               ear_override: Optional[float] = None) -> EyeMetrics:
        """
        Call every frame with MediaPipe landmarks (478, 3) or pre-computed EAR.
        Returns EyeMetrics with current focus_score.
        """
        now = time.time()

        if ear_override is not None:
            ear_avg = ear_override
        elif landmarks is not None and landmarks.shape[0] >= 478:
            ear_avg = self._compute_ear(landmarks)
        else:
            ear_avg = EAR_NORMAL  # default normal

        # Track
        self._ear_history.append((now, ear_avg))
        closed = ear_avg < self._ear_threshold
        self._closed_history.append((now, closed))

        # Blink detection: closed → open transition
        if self._prev_closed and not closed:
            self._blink_events.append(now)
            self._total_blinks += 1
        self._prev_closed = closed

        # Clean old data
        cutoff = now - self._window_sec
        while self._ear_history and self._ear_history[0][0] < cutoff:
            self._ear_history.popleft()
        while self._closed_history and self._closed_history[0][0] < cutoff:
            self._closed_history.popleft()
        while self._blink_events and self._blink_events[0] < cutoff:
            self._blink_events.popleft()

        # Compute metrics
        ear_vals = [e[1] for e in self._ear_history]
        current_ear = ear_vals[-1] if ear_vals else EAR_NORMAL

        # PERCLOS: % time eyes closed in window
        closed_count = sum(1 for c in self._closed_history if c[1])
        total_count = max(1, len(self._closed_history))
        perclos = closed_count / total_count

        # Blink rate per minute
        elapsed = now - self._session_start
        blink_rate = (self._total_blinks / max(elapsed / 60, 0.5))

        # Focus score from eye metrics
        # High EAR=good, low PERCLOS=good, normal blink rate=good
        ear_score = min(100, max(0, (current_ear - EAR_CLOSED) / (EAR_NORMAL - EAR_CLOSED) * 100))
        blink_score = 100 if 5 <= blink_rate <= 25 else max(0, 100 - abs(blink_rate - 15) * 4)
        perclos_score = max(0, 100 - perclos * 400)  # perclos 0.25 → 0
        focus_score = int(0.4 * ear_score + 0.3 * blink_score + 0.3 * perclos_score)

        return EyeMetrics(
            ear_left=current_ear, ear_right=current_ear,
            ear_avg=current_ear,
            blink_count=self._total_blinks,
            blink_rate=round(blink_rate, 1),
            perclos=round(perclos, 3),
            eye_open=not closed,
            focus_score=focus_score,
        )

    @staticmethod
    def _compute_ear(landmarks: np.ndarray) -> float:
        """Compute Eye Aspect Ratio from MediaPipe 478 landmarks."""
        def ear_eye(pts_idx):
            p = [landmarks[i][:2] for i in pts_idx]  # 6 points (x,y)
            # EAR = (|p1-p5| + |p2-p4|) / (2 * |p0-p3|)
            v1 = np.linalg.norm(p[1] - p[5])
            v2 = np.linalg.norm(p[2] - p[4])
            h  = np.linalg.norm(p[0] - p[3])
            if h < 1e-6: return 0.3
            return (v1 + v2) / (2.0 * h)

        ear_l = ear_eye(LEFT_EYE_IDX)
        ear_r = ear_eye(RIGHT_EYE_IDX)
        return (ear_l + ear_r) / 2.0
