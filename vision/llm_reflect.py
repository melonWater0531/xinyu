"""
XINYU lightweight reflection engine.

This module intentionally keeps the old LLMReflect/get_llm interface so the
FastAPI layer and frontend data flow continue to work after removing the local
the removed local 0.5B model files. It provides deterministic, low-latency templates for:
  - emotion diary prompts
  - short quotes
  - daily reports
  - fallback chat replies
"""
from __future__ import annotations

import time
from typing import Optional


class LLMReflect:
    """Template-backed replacement for the former local model engine."""

    def __init__(self, model_path: str = None):
        self._path = model_path
        self._loaded = True
        self._last_time: float = 0.0

    def _load(self):
        self._loaded = True

    @property
    def loaded(self) -> bool:
        return True

    def _done(self, text: str, t0: float) -> str:
        self._last_time = max(0.0, time.time() - t0)
        return text.strip()[:120]

    def diary(self, emotion: str, attn_score: int = 0,
              prev_emotion: str = "", user_name: str = "") -> str:
        t0 = time.time()
        attn = "很专注" if attn_score >= 70 else "有些起伏" if attn_score >= 40 else "需要一点休息"
        name = f"{user_name}，" if user_name else ""
        templates = {
            "Happiness": f"{name}我感觉心里亮了一下，刚才是什么让这份开心出现的呢？",
            "Sadness": f"{name}我感觉心里有点沉，也许可以慢慢说说让你难过的地方。",
            "Anger": f"{name}我感觉有些紧绷，刚才是不是遇到了让你不舒服的事？",
            "Fear": f"{name}我感觉有一点不安，担心的事情可以先放到纸上。",
            "Surprise": f"{name}我感觉被什么轻轻撞了一下，发生了什么意想不到的事？",
            "Disgust": f"{name}我感觉想和某些东西保持距离，这种不适值得被看见。",
            "Contempt": f"{name}我感觉有些疏离，也许你正在重新判断一件事。",
            "Neutral": f"{name}我现在{attn}，此刻脑海里最先浮现的是什么？",
        }
        return self._done(templates.get(emotion, templates["Neutral"]), t0)

    def quote(self, emotion: str, attn_level: str) -> str:
        t0 = time.time()
        quotes = {
            "Happiness": "把快乐留一点光，照亮接下来的路。",
            "Sadness": "难过会经过你，但不会定义你。",
            "Anger": "先让呼吸回来，再让答案出现。",
            "Fear": "害怕时，也可以慢慢往前一步。",
            "Surprise": "意外抵达时，心也在扩展边界。",
            "Neutral": "平静不是停下，是稳稳地在场。",
        }
        return self._done(quotes.get(emotion, "此刻被看见，就已经很好。"), t0)

    def report(self, total_min: int, focused_pct: int,
               top_emotion: str, avg_attn: int) -> str:
        t0 = time.time()
        if focused_pct >= 70:
            text = "今天你保持了很好的投入感，记得也给身体一点温柔的休息。"
        elif focused_pct >= 40:
            text = "今天状态有些起伏，但你一直在尝试回来，这本身就很珍贵。"
        else:
            text = "今天可能有些疲惫，先照顾好呼吸、饮水和休息，再继续也不迟。"
        return self._done(text, t0)

    def respond_to_user(self, user_msg: str, emotion: str,
                        user_name: str = "", history: list = None,
                        profile: dict = None, context: str = "") -> str:
        t0 = time.time()
        msg = (user_msg or "").strip()
        if any(w in msg for w in ["累", "困", "疲惫", "没睡"]):
            text = "听起来你真的有些累了。先把自己照顾好，我们可以慢慢来。"
        elif any(w in msg for w in ["开心", "高兴", "顺利", "喜欢"]):
            text = "真好，这份开心值得被认真收藏。愿它多停留一会儿。"
        elif any(w in msg for w in ["难过", "伤心", "委屈"]):
            text = "我听见这份难过了。你不用急着变好，先让它被安放。"
        elif any(w in msg for w in ["生气", "烦", "讨厌"]):
            text = "这种烦躁一定有它的原因。先别责怪自己，慢慢说也可以。"
        else:
            text = "谢谢你愿意说出来。我在这里听着，也陪你一起整理。"
        return self._done(text, t0)


_llm_reflect: Optional[LLMReflect] = None


def get_llm() -> LLMReflect:
    global _llm_reflect
    if _llm_reflect is None:
        _llm_reflect = LLMReflect()
    return _llm_reflect
