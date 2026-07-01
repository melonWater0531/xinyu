"""Sliding-window person count stabilizer for product UI."""
from __future__ import annotations

import time
from collections import deque


class StablePersonCounter:
    def __init__(self, window_sec: float = 2.0, min_samples: int = 3, switch_ratio: float = 0.65) -> None:
        self.window_sec = float(window_sec)
        self.min_samples = int(min_samples)
        self.switch_ratio = float(switch_ratio)
        self._samples: deque[tuple[float, int]] = deque()
        self._stable_count = 0
        self._last_changed_at = 0.0
        self._confidence = 0.0

    def update(self, count: int, now: float | None = None) -> dict:
        now = time.time() if now is None else float(now)
        count = max(0, int(count))
        self._samples.append((now, count))
        cutoff = now - self.window_sec
        while self._samples and self._samples[0][0] < cutoff:
            self._samples.popleft()

        total = len(self._samples)
        if total >= self.min_samples:
            recent = list(self._samples)[-self.min_samples:]
            recent_values = [value for _, value in recent]
            if len(set(recent_values)) == 1 and recent_values[-1] != self._stable_count:
                self._stable_count = recent_values[-1]
                self._last_changed_at = now
                self._confidence = 1.0
                return self.snapshot()
            votes: dict[int, int] = {}
            for _, value in self._samples:
                votes[value] = votes.get(value, 0) + 1
            candidate, candidate_votes = max(votes.items(), key=lambda item: (item[1], -abs(item[0] - self._stable_count)))
            confidence = candidate_votes / total
            self._confidence = confidence
            if candidate != self._stable_count and confidence >= self.switch_ratio:
                self._stable_count = candidate
                self._last_changed_at = now
        elif total:
            self._confidence = sum(1 for _, value in self._samples if value == self._stable_count) / total

        return self.snapshot()

    def snapshot(self) -> dict:
        return {
            "stable_count": int(self._stable_count),
            "confidence": round(float(self._confidence), 3),
            "window_sec": round(float(self.window_sec), 2),
            "last_changed_at": round(float(self._last_changed_at), 3) if self._last_changed_at else None,
        }
