"""Conservative proactive emotion intervention policy."""

from __future__ import annotations

import time
from collections import deque


NEGATIVE_EMOTIONS = {"Sadness", "Anger", "Fear", "Disgust", "Contempt"}


class EmotionInterventionPolicy:
    def __init__(
        self,
        window_sec: float = 180.0,
        min_confidence: float = 0.65,
        min_negative_ratio: float = 0.6,
        cooldown_sec: float = 1800.0,
    ) -> None:
        self._window_sec = float(window_sec)
        self._min_confidence = float(min_confidence)
        self._min_negative_ratio = float(min_negative_ratio)
        self._cooldown_sec = float(cooldown_sec)
        self._samples: deque[tuple[float, str, float, int, float, str]] = deque()
        self._last_trigger_at = 0.0
        self._last_message = ""
        self._last_reason = ""

    def update(self, emotieff: dict | None, attention: dict | None, eye_metrics: dict | None, gaze: dict | None) -> dict:
        now = time.time()
        emotion = str((emotieff or {}).get("emotion") or "Neutral")
        confidence = float((emotieff or {}).get("confidence") or 0.0)
        score = int(float((attention or {}).get("score") or 50))
        perclos = float((eye_metrics or {}).get("perclos") or 0.0)
        gaze_state = str((gaze or {}).get("state") or "unknown")
        self._samples.append((now, emotion, confidence, score, perclos, gaze_state))

        cutoff = now - self._window_sec
        while self._samples and self._samples[0][0] < cutoff:
            self._samples.popleft()

        cooldown_remaining = max(0.0, self._cooldown_sec - (now - self._last_trigger_at))
        if cooldown_remaining > 0:
            return self._state(False, cooldown_remaining=cooldown_remaining)

        if len(self._samples) < 20:
            return self._state(False, reason="collecting")

        confident = [s for s in self._samples if s[2] >= self._min_confidence]
        if len(confident) < max(12, int(len(self._samples) * 0.45)):
            return self._state(False, reason="low_confidence")

        neg = [s for s in confident if s[1] in NEGATIVE_EMOTIONS]
        ratio = len(neg) / max(1, len(confident))
        avg_conf = sum(s[2] for s in neg) / max(1, len(neg))
        avg_score = sum(s[3] for s in self._samples) / len(self._samples)
        avg_perclos = sum(s[4] for s in self._samples) / len(self._samples)
        gaze_down_ratio = sum(1 for s in self._samples if s[5] == "down") / len(self._samples)

        if ratio >= self._min_negative_ratio and avg_conf >= self._min_confidence:
            reason = "negative_emotion_window"
        elif avg_score < 45 and (avg_perclos > 0.18 or gaze_down_ratio > 0.45):
            reason = "fatigue_attention_window"
        else:
            return self._state(False, reason="below_threshold")

        self._last_trigger_at = now
        self._last_reason = reason
        self._last_message = self._message(reason)
        return self._state(True, reason=reason, message=self._last_message)

    def _state(self, active: bool, reason: str = "", message: str = "", cooldown_remaining: float = 0.0) -> dict:
        return {
            "active": bool(active),
            "type": "emotion_care" if active or reason in {"negative_emotion_window", "cooldown"} else "",
            "reason": reason,
            "message": message,
            "cooldown_remaining_sec": int(round(cooldown_remaining)),
        }

    @staticmethod
    def _message(reason: str) -> str:
        if reason == "fatigue_attention_window":
            return "状态有点疲惫了，先休息两分钟也算前进。"
        return "我在。今天如果有点难，也可以先只做一小步。"

