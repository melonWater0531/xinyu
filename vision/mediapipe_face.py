"""
MediaPipe Face Landmarker wrapper — 478 landmarks + head pose + eye/mouth metrics.

Usage:
    detector = MPFaceDetector()
    result = detector.detect(frame_bgr)
    # result.landmarks (478,3), result.ear, result.eye_open, result.head_yaw
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Optional, Tuple

import cv2
import numpy as np

from utils.logger import get_logger

logger = get_logger(__name__)

MODEL_PATH = "models/face_landmarker.task"


@dataclass
class MPFaceResult:
    success: bool = False
    landmarks: Optional[np.ndarray] = None   # (478, 3) xyz
    landmarks5: Optional[np.ndarray] = None  # left_eye, right_eye, nose, left_mouth, right_mouth
    ear_avg: float = 0.0
    eye_open: bool = True


class MPFaceDetector:
    """MediaPipe Face Landmarker. Thread-safe, lazy-loads model."""

    def __init__(self, model_path: str = None):
        self._path = model_path or MODEL_PATH
        self._detector = None
        self._lock = threading.Lock()
        self._loaded = False
        self._last_time: float = 0.0

    def _load(self):
        if self._loaded: return
        with self._lock:
            if self._loaded: return
            try:
                import mediapipe as mp
                from mediapipe.tasks import python
                from mediapipe.tasks.python import vision

                base = python.BaseOptions(model_asset_path=self._path)
                opts = vision.FaceLandmarkerOptions(
                    base_options=base,
                    output_face_blendshapes=True,
                    output_facial_transformation_matrixes=True,
                    running_mode=vision.RunningMode.IMAGE,
                    num_faces=1,
                )
                self._detector = vision.FaceLandmarker.create_from_options(opts)
                self._loaded = True
                logger.info("✅ MPFaceDetector loaded: %s", self._path)
            except Exception as e:
                logger.error("MPFaceDetector: %s", e)

    def detect(self, frame_bgr: np.ndarray) -> MPFaceResult:
        """
        Detect face landmarks in BGR frame.

        Args:
            frame_bgr: 640x640 BGR uint8 image.

        Returns:
            MPFaceResult with 478 landmarks (xyz) + head pose.
        """
        self._load()
        if not self._loaded:
            return MPFaceResult()

        try:
            import mediapipe as mp
            img_rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
            mp_img = mp.Image(image_format=mp.ImageFormat.SRGB, data=img_rgb)
        except Exception:
            return MPFaceResult()

        t0 = time.time()
        with self._lock:
            try:
                result = self._detector.detect(mp_img)
            except Exception:
                return MPFaceResult()
        self._last_time = time.time() - t0

        if not result.face_landmarks:
            return MPFaceResult()

        # Extract landmarks
        lm = result.face_landmarks[0]
        pts = np.array([[l.x * frame_bgr.shape[1],
                         l.y * frame_bgr.shape[0],
                         l.z * frame_bgr.shape[1]] for l in lm], dtype=np.float32)

        # Head pose: use existing solvePnP from attention engine (no duplication)

        # Quick EAR computation from eye landmarks
        ear = self._quick_ear(pts)
        landmarks5 = self._five_points(pts)

        return MPFaceResult(
            success=True,
            landmarks=pts,
            landmarks5=landmarks5,
            ear_avg=round(float(ear), 3),
            eye_open=ear > 0.18,
        )

    @staticmethod
    def _quick_ear(pts: np.ndarray) -> float:
        """Compute EAR from MediaPipe 478 landmarks."""
        # Left eye: 33,160,158,133,153,144  Right eye: 362,385,387,263,373,380
        def ear_6(ids):
            p = pts[ids, :2]
            v = np.linalg.norm(p[1]-p[5]) + np.linalg.norm(p[2]-p[4])
            h = np.linalg.norm(p[0]-p[3])
            return v / (2.0 * h) if h > 1e-6 else 0.3
        return (ear_6([33,160,158,133,153,144]) + ear_6([362,385,387,263,373,380])) / 2.0

    @staticmethod
    def _five_points(pts: np.ndarray) -> np.ndarray:
        """Extract stable 5-point landmarks from MediaPipe FaceMesh."""
        left_eye = np.mean(pts[[33, 133], :2], axis=0)
        right_eye = np.mean(pts[[362, 263], :2], axis=0)
        nose = pts[1, :2]
        left_mouth = pts[61, :2]
        right_mouth = pts[291, :2]
        return np.array([left_eye, right_eye, nose, left_mouth, right_mouth], dtype=np.float32)

    @property
    def loaded(self) -> bool: return self._loaded
    @property
    def last_time_ms(self) -> float: return self._last_time * 1000
