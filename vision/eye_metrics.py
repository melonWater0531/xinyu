"""
Eye/Mouth metrics for focus estimation — EAR, blink rate, PERCLOS.

Uses MediaPipe 478 face landmarks to compute eye openness and blink patterns.
These metrics serve as inverse indicators of focus (not "fatigue" labels).
"""
from __future__ import annotations

import json
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

import numpy as np

from utils.logger import get_logger

logger = get_logger(__name__)

# MediaPipe landmark indices for left/right eye contours
LEFT_EYE_IDX  = [33, 160, 158, 133, 153, 144]   # 6 points around left eye
RIGHT_EYE_IDX = [362, 385, 387, 263, 373, 380]   # 6 points around right eye

# EAR threshold: below this = eye closing (defaults until per-user calibration)
EAR_CLOSED = 0.18
EAR_NORMAL = 0.28

EAR_BASELINE_FILE = "runtime/ear_baseline.json"


class EarCalibrator:
    """Per-user EAR calibration, mirroring attention_engine.BaselineCalibrator.

    Collects ~30s of open-eye samples (blinks filtered out), takes the 75th
    percentile as the personal open-eye baseline, derives thresholds from it
    and persists them. Slowly re-baselines afterwards (0.8 old / 0.2 new).
    """

    CALIB_DURATION_S = 30.0
    MIN_SAMPLES = 40
    REBASELINE_INTERVAL_S = 600.0

    def __init__(self, path: str = EAR_BASELINE_FILE):
        self._path = Path(path)
        self.baseline: Optional[float] = None
        self._samples: list = []
        self._started_at = 0.0
        self._last_rebaseline = 0.0
        self._load()

    @property
    def calibrated(self) -> bool:
        return self.baseline is not None

    @property
    def ear_closed(self) -> float:
        return 0.55 * self.baseline if self.baseline else EAR_CLOSED

    @property
    def ear_normal(self) -> float:
        return 0.90 * self.baseline if self.baseline else EAR_NORMAL

    def _load(self):
        try:
            if self._path.is_file():
                data = json.loads(self._path.read_text(encoding="utf-8"))
                b = float(data.get("baseline", 0.0))
                if 0.1 <= b <= 0.6:
                    self.baseline = b
                    logger.info("EAR baseline loaded: %.3f", b)
        except Exception as exc:
            logger.warning("EAR baseline load failed: %s", str(exc)[:80])

    def _save(self):
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(json.dumps({
                "baseline": round(float(self.baseline), 4),
                "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            }), encoding="utf-8")
        except Exception as exc:
            logger.warning("EAR baseline save failed: %s", str(exc)[:80])

    def feed(self, ear: float) -> None:
        """Feed one EAR sample; blink frames are filtered by threshold."""
        now = time.time()
        # Exclude closing/blink frames from the open-eye baseline
        if ear <= (self.ear_closed if self.calibrated else EAR_CLOSED) * 1.3:
            return
        if not self._samples:
            self._started_at = now
        self._samples.append(ear)
        window_done = (now - self._started_at >= self.CALIB_DURATION_S
                       and len(self._samples) >= self.MIN_SAMPLES)
        if not window_done:
            return
        new_baseline = float(np.percentile(self._samples, 75))
        if not self.calibrated:
            self.baseline = new_baseline
            logger.info("EAR calibrated: baseline=%.3f closed=%.3f normal=%.3f",
                        self.baseline, self.ear_closed, self.ear_normal)
            self._save()
        elif now - self._last_rebaseline >= self.REBASELINE_INTERVAL_S:
            self.baseline = 0.8 * self.baseline + 0.2 * new_baseline
            self._save()
            self._last_rebaseline = now
        self._samples = []
        self._started_at = 0.0


def classify_fatigue(perclos: float, blink_rate: float, warmed_up: bool = True) -> str:
    """PERCLOS-based fatigue level, bumped one level by abnormal blink rate."""
    levels = ["alert", "mild", "drowsy", "severe"]
    if perclos < 0.08:
        idx = 0
    elif perclos < 0.15:
        idx = 1
    elif perclos < 0.25:
        idx = 2
    else:
        idx = 3
    if warmed_up and (blink_rate > 30 or blink_rate < 4):
        idx = min(idx + 1, 3)
    return levels[idx]


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
    fatigue_level: str = "alert"   # alert | mild | drowsy | severe
    calibrated: bool = False       # per-user EAR baseline is active


class EyeMetricTracker:
    """
    Track EAR, blink rate, PERCLOS over time.
    Outputs a focus_score (0-100) where:
      - High EAR + normal blink + low PERCLOS → 100 (focused)
      - Low EAR + abnormal blink + high PERCLOS → 0 (unfocused)
    """

    def __init__(self, window_sec: float = 30.0,
                 ear_threshold: float = EAR_CLOSED,
                 calibrator: Optional[EarCalibrator] = None):
        self._window_sec = window_sec
        self._ear_threshold = ear_threshold
        self._calibrator = calibrator if calibrator is not None else EarCalibrator()

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
            ear_left = ear_right = ear_avg = ear_override
        elif landmarks is not None and landmarks.shape[0] >= 478:
            ear_left, ear_right, ear_avg = self._compute_ear(landmarks)
        else:
            ear_left = ear_right = ear_avg = EAR_NORMAL  # default normal

        # Per-user calibration (samples above blink threshold only)
        cal = self._calibrator
        cal.feed(ear_avg)
        ear_closed_thr = cal.ear_closed
        ear_normal_thr = cal.ear_normal

        # Track
        self._ear_history.append((now, ear_avg))
        closed = ear_avg < ear_closed_thr
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

        # Blink rate per minute over the sliding window (warmup-guarded:
        # before a full window has elapsed, scale by actual observed time)
        observed_sec = min(now - self._session_start, self._window_sec)
        blink_rate = len(self._blink_events) / max(observed_sec / 60.0, 0.5)

        # Focus score from eye metrics (thresholds personalised when calibrated)
        # High EAR=good, low PERCLOS=good, normal blink rate=good
        thr_span = max(1e-3, ear_normal_thr - ear_closed_thr)
        ear_score = min(100, max(0, (current_ear - ear_closed_thr) / thr_span * 100))
        blink_score = 100 if 5 <= blink_rate <= 25 else max(0, 100 - abs(blink_rate - 15) * 4)
        perclos_score = max(0, 100 - perclos * 400)  # perclos 0.25 → 0
        focus_score = int(0.4 * ear_score + 0.3 * blink_score + 0.3 * perclos_score)

        # Fatigue level: PERCLOS bands + abnormal blink-rate bump (needs a full window)
        warmed_up = (now - self._session_start) >= self._window_sec
        fatigue_level = classify_fatigue(perclos, blink_rate, warmed_up)

        return EyeMetrics(
            ear_left=round(float(ear_left), 4), ear_right=round(float(ear_right), 4),
            ear_avg=current_ear,
            blink_count=self._total_blinks,
            blink_rate=round(blink_rate, 1),
            perclos=round(perclos, 3),
            eye_open=not closed,
            focus_score=focus_score,
            fatigue_level=fatigue_level,
            calibrated=cal.calibrated,
        )

    @staticmethod
    def _compute_ear(landmarks: np.ndarray) -> Tuple[float, float, float]:
        """Compute per-eye and average Eye Aspect Ratio from MediaPipe 478 landmarks."""
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
        return float(ear_l), float(ear_r), (ear_l + ear_r) / 2.0
