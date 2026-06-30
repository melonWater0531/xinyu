"""MediaPipe Gesture Recognizer wrapper for low-risk companion intents."""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import cv2

from utils.logger import get_logger

logger = get_logger(__name__)

MODEL_PATH = "models/gesture_recognizer.task"

INTENTS = {
    "Open_Palm": "summon_xinyu",
    "Closed_Fist": "pause_or_mute",
    "Thumb_Up": "feedback_positive",
    "Thumb_Down": "feedback_negative",
    "Victory": "capture_positive_moment",
}


@dataclass
class GestureState:
    available: bool = False
    name: str = ""
    confidence: float = 0.0
    handedness: str = ""
    stable_frames: int = 0
    intent: str = ""
    intent_ready: bool = False
    updated_at: float = 0.0
    reason: str = ""

    def as_dict(self) -> dict:
        return {
            "available": self.available,
            "name": self.name,
            "confidence": round(float(self.confidence), 3),
            "handedness": self.handedness,
            "stable_frames": int(self.stable_frames),
            "intent": self.intent,
            "intent_ready": bool(self.intent_ready),
            "updated_at": round(float(self.updated_at), 3) if self.updated_at else 0.0,
            "reason": self.reason,
        }


class GestureDetector:
    """Detect gestures and map them to companion intents only.

    This class never emits control events. It only returns stable UI intents for
    FastAPI state snapshots.
    """

    def __init__(
        self,
        model_path: str = MODEL_PATH,
        min_confidence: float = 0.6,
        stable_frames: int = 4,
        cooldown_sec: float = 3.0,
    ) -> None:
        self._path = Path(model_path)
        self._min_conf = float(min_confidence)
        self._stable_needed = int(stable_frames)
        self._cooldown = float(cooldown_sec)
        self._recognizer = None
        self._loaded = False
        self._load_failed_reason = ""
        self._last_name = ""
        self._stable_count = 0
        self._last_intent_at: dict[str, float] = {}

    def _load(self) -> bool:
        if self._loaded:
            return True
        if self._load_failed_reason:
            return False
        if not self._path.is_file():
            self._load_failed_reason = f"model_missing:{self._path}"
            return False
        try:
            import mediapipe as mp
            from mediapipe.tasks import python
            from mediapipe.tasks.python import vision

            options = vision.GestureRecognizerOptions(
                base_options=python.BaseOptions(model_asset_path=str(self._path)),
                running_mode=vision.RunningMode.IMAGE,
                num_hands=1,
                min_hand_detection_confidence=0.5,
                min_hand_presence_confidence=0.5,
                min_tracking_confidence=0.5,
            )
            self._recognizer = vision.GestureRecognizer.create_from_options(options)
            self._loaded = True
            logger.info("GestureDetector loaded: %s", self._path)
        except Exception as exc:
            self._load_failed_reason = str(exc)[:120]
            logger.warning("GestureDetector unavailable: %s", self._load_failed_reason)
        return self._loaded

    def detect(self, frame_bgr) -> dict:
        if frame_bgr is None:
            return GestureState(reason="no_frame").as_dict()
        if not self._load():
            return GestureState(reason=self._load_failed_reason or "unavailable").as_dict()
        try:
            import mediapipe as mp

            frame_rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
            mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=frame_rgb)
            result = self._recognizer.recognize(mp_image)
        except Exception as exc:
            return GestureState(available=True, reason=f"detect_error:{str(exc)[:80]}").as_dict()

        if not result.gestures:
            self._last_name = ""
            self._stable_count = 0
            return GestureState(available=True, reason="no_gesture").as_dict()

        top = result.gestures[0][0]
        name = str(top.category_name or "")
        conf = float(top.score or 0.0)
        handedness = ""
        if result.handedness and result.handedness[0]:
            handedness = str(result.handedness[0][0].category_name or "")

        if name == self._last_name:
            self._stable_count += 1
        else:
            self._last_name = name
            self._stable_count = 1

        intent = INTENTS.get(name, "")
        now = time.time()
        ready = False
        if intent and conf >= self._min_conf and self._stable_count >= self._stable_needed:
            last = self._last_intent_at.get(intent, 0.0)
            if now - last >= self._cooldown:
                ready = True
                self._last_intent_at[intent] = now

        return GestureState(
            available=True,
            name=name,
            confidence=conf,
            handedness=handedness,
            stable_frames=self._stable_count,
            intent=intent,
            intent_ready=ready,
            updated_at=now,
        ).as_dict()

