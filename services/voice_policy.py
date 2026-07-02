"""Lightweight voice orchestration for browser-based TTS.

This module does not synthesize or play audio. It only decides whether a short
utterance should be emitted to browser clients, where Web Speech can play it.
"""

from __future__ import annotations

import os
import time
import uuid
from dataclasses import dataclass, asdict
from typing import Any


DEFAULT_COOLDOWNS = {
    "manual": 0,
    "chat_reply": 0,
    "wake_word": 8,
    "meeting_start": 5,
    "meeting_stop": 5,
    "meeting_summary_ok": 8,
    "meeting_summary_error": 8,
    "low_focus": 30 * 60,
    "fatigue": 45 * 60,
    "emotion_care": 60 * 60,
}


@dataclass
class VoiceUtterance:
    id: str
    text: str
    display_text: str
    reason: str
    priority: str
    interrupt: bool
    source: str
    time: float

    def to_event(self) -> dict[str, Any]:
        data = asdict(self)
        data["type"] = "voice_utterance"
        data["time"] = round(float(self.time), 3)
        return data


class VoicePolicy:
    def __init__(self, enabled: bool | None = None) -> None:
        self.enabled = self._env_enabled() if enabled is None else bool(enabled)
        self.engine = "browser_speech"
        self.available = True
        self.speaking = False
        self.queue_len = 0
        self.last_utterance = ""
        self.last_reason = ""
        self.last_event: dict[str, Any] | None = None
        self.recent_events: list[dict[str, Any]] = []
        self._last_by_reason: dict[str, float] = {}

    @staticmethod
    def _env_enabled() -> bool:
        raw = os.getenv("ENABLE_TTS_VOICE", "true").strip().lower()
        return raw not in {"0", "false", "no", "off"}

    def state(self) -> dict[str, Any]:
        return {
            "enabled": bool(self.enabled),
            "available": bool(self.available),
            "speaking": bool(self.speaking),
            "queue_len": int(self.queue_len),
            "last_utterance": self.last_utterance,
            "last_reason": self.last_reason,
            "engine": self.engine,
            "cooldowns": {k: round(max(0.0, v - (time.time() - self._last_by_reason.get(k, 0.0))), 1)
                          for k, v in DEFAULT_COOLDOWNS.items()
                          if self._last_by_reason.get(k)},
            "recent_events": list(self.recent_events[-10:]),
        }

    def build(
        self,
        text: str,
        reason: str = "manual",
        priority: str = "normal",
        interrupt: bool = False,
        source: str = "api",
        force: bool = False,
    ) -> VoiceUtterance | None:
        cleaned = self._clean_text(text)
        if not cleaned or (not self.enabled and not force):
            return None
        now = time.time()
        cooldown = DEFAULT_COOLDOWNS.get(reason, 20)
        last = self._last_by_reason.get(reason, 0.0)
        if not force and cooldown > 0 and now - last < cooldown:
            self._remember_drop(reason, source, "cooldown")
            return None
        self._last_by_reason[reason] = now
        utterance = VoiceUtterance(
            id=str(uuid.uuid4()),
            text=cleaned,
            display_text=cleaned,
            reason=reason,
            priority=priority,
            interrupt=bool(interrupt),
            source=source,
            time=now,
        )
        self.last_utterance = cleaned
        self.last_reason = reason
        event = utterance.to_event()
        self.last_event = event
        self.recent_events.append(event)
        self.recent_events = self.recent_events[-20:]
        return utterance

    def stop_event(self, reason: str = "manual") -> dict[str, Any]:
        self.speaking = False
        event = {"type": "voice_stop", "reason": reason, "time": round(time.time(), 3)}
        self.last_reason = f"stop:{reason}"
        self.last_event = event
        self.recent_events.append(event)
        self.recent_events = self.recent_events[-20:]
        return event

    def short_text_for(self, reason: str, fallback: str = "") -> str:
        return {
            "wake_word": "我在，想聊什么？",
            "meeting_start": "会议记录已开始，我会整理可用的发言片段。",
            "meeting_stop": "会议记录已结束，可以让我整理摘要。",
            "meeting_summary_ok": "会议整理好了，重点已经放在记录里。",
            "meeting_summary_error": "这段声音太短或不够清楚，我还没有整理出来。",
            "low_focus": "注意力有点散，要不要先做十分钟轻专注？",
            "fatigue": "眼睛可能有点累，我们先看远一点，休息一分钟。",
            "emotion_care": "我在这儿。可以先把最想被理解的一件事说出来。",
        }.get(reason, fallback)

    def _remember_drop(self, reason: str, source: str, why: str) -> None:
        self.recent_events.append({
            "type": "voice_dropped",
            "reason": reason,
            "source": source,
            "why": why,
            "time": round(time.time(), 3),
        })
        self.recent_events = self.recent_events[-20:]

    @staticmethod
    def _clean_text(text: str) -> str:
        cleaned = " ".join(str(text or "").replace("\n", " ").split())
        return cleaned[:180]


voice_policy = VoicePolicy()

