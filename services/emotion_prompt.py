"""Prompt builders for open-vocabulary emotion inference.

The real-time layer remains EmotiEffLib's 8-class classifier. This module
turns its output plus attention, eye, and gaze signals into low-frequency LLM
prompts for richer semantic labels and emotion-aware replies.
"""

from __future__ import annotations

EMOTION_INFERENCE_SYSTEM_PROMPT = """你是心屿的情绪语义分析模块。请根据多维传感器线索，推理用户当前更细腻的情绪状态。

要求：
1. 不要只是复述 8 类表情分类；请综合表情概率、效价、唤醒度、专注、疲劳和视线线索。
2. 输出一个自然、开放词汇的中文情绪标签，例如“专注中的满足感”“平静里带一点疲惫”“有些心不在焉的倦意”。
3. 给出 1-10 的情绪强度。没有观察到人脸时强度为 0。
4. 用一句话解释判断依据，语气谨慎，不做医学或心理诊断。
5. 严格输出 JSON，不要输出其他内容。

JSON 格式：
{"label":"细腻情绪标签","intensity":7,"explanation":"一句话解释"}"""


def _data(state: dict | None) -> dict:
    if not isinstance(state, dict):
        return {}
    data = state.get("data")
    if isinstance(data, dict):
        return data
    return state


def _as_float(value, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_int(value, default: int = 0) -> int:
    try:
        if value is None:
            return default
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _probabilities(raw) -> dict[str, float]:
    if isinstance(raw, dict):
        return {str(k): _as_float(v) for k, v in raw.items()}
    if isinstance(raw, list):
        names = ["Anger", "Contempt", "Disgust", "Fear", "Happiness", "Neutral", "Sadness", "Surprise"]
        pairs = raw
        if raw and all(isinstance(item, (int, float)) for item in raw):
            pairs = list(zip(names, raw))
        out: dict[str, float] = {}
        for item in pairs:
            if isinstance(item, (list, tuple)) and len(item) >= 2:
                out[str(item[0])] = _as_float(item[1])
            elif isinstance(item, dict):
                name = item.get("emotion") or item.get("label") or item.get("name")
                value = item.get("probability", item.get("score", item.get("confidence")))
                if name:
                    out[str(name)] = _as_float(value)
        return out
    return {}


def _top_probabilities(probs: dict[str, float]) -> str:
    items = sorted(probs.items(), key=lambda item: -item[1])
    kept = [f"{name}({value:.0%})" for name, value in items if value >= 0.05]
    return ", ".join(kept[:5]) if kept else "暂无高置信分类"


def build_emotion_context(state: dict | None) -> str:
    """Build compact sensor context for LLM prompts."""
    data = _data(state)
    emotieff = data.get("emotieff") or data.get("emotion") or {}
    attention = data.get("attention") or {}
    eye = data.get("eye_metrics") or {}
    gaze = data.get("gaze") or {}

    probs = _probabilities(emotieff.get("probabilities"))
    emotion = str(emotieff.get("emotion") or "Unknown")
    confidence = _as_float(emotieff.get("confidence"))
    valence = emotieff.get("valence")
    arousal = emotieff.get("arousal")
    has_face = bool(attention.get("has_face") or emotion not in {"", "Unknown", "None"})

    lines = [
        f"是否观察到人脸：{'是' if has_face else '否'}",
        f"主要表情分类：{emotion}（置信度 {confidence:.0%}）",
        f"分类概率分布：{_top_probabilities(probs)}",
        f"情绪效价：{_as_float(valence):+.2f}（-1 消极，+1 积极）" if valence is not None else "情绪效价：未知",
        f"唤醒度：{_as_float(arousal):+.2f}" if arousal is not None else "唤醒度：未知",
        f"专注度：{_as_int(attention.get('score'))}/100（{attention.get('state', '未知')}）",
        f"PERCLOS 疲劳指标：{_as_float(eye.get('perclos')):.3f}",
        f"眨眼率：{_as_float(eye.get('blink_rate')):.1f} 次/分钟",
        f"视线状态：{gaze.get('state', '未知')}（置信度 {_as_float(gaze.get('confidence')):.0%}）",
    ]
    return "\n".join(lines)


def build_emotion_inference_messages(state: dict | None) -> list[dict[str, str]]:
    context = build_emotion_context(state)
    return [
        {"role": "system", "content": EMOTION_INFERENCE_SYSTEM_PROMPT},
        {"role": "user", "content": f"当前传感器线索：\n{context}"},
    ]


def build_chat_system_prompt(state: dict | None, user_name: str = "") -> str:
    context = build_emotion_context(state)
    name = user_name.strip() or "用户"
    return f"""你是心屿（XINYU），一个温柔陪伴型 AI，正在和{name}对话。

你可以参考以下实时状态，但不要主动报数字，也不要说“根据传感器数据”。请把这些线索自然地融入关心里。
{context}

对话原则：
- 像关心朋友一样回应，不做心理咨询师或医生式诊断。
- 如果疲劳线索明显，温柔建议休息、喝水或放慢节奏。
- 如果积极线索明显，给予肯定；如果线索矛盾，承认“看起来有点复杂”。
- 不编造没有被用户或状态提到的事件。
- 回复不超过 80 字，中文，自然段落，不使用列表或标题。"""


def build_reflect_messages(diary_text: str, state: dict | None, user_name: str = "", payload: dict | None = None) -> list[dict[str, str]]:
    context = build_emotion_context(state)
    payload = payload or {}
    duration_min = _as_int(payload.get("duration_min"))
    observed = f"\n监测时长：{duration_min} 分钟" if duration_min else ""
    name = user_name.strip() or "用户"
    system = """你是心屿，请以用户视角（“我”）生成今日日记条目，并给出一句温柔回应。

要求：
- 日记不超过 60 字，回应不超过 40 字。
- 用户自写内容优先，传感器状态只作为辅助线索。
- 不复述原文，不编造未提及的事件。
- 当文字和状态线索冲突时，用温和、不确定的语气处理。
- 输出严格 JSON，只有两个字段：{"diary":"...","reply":"..."}"""
    user = f"""用户：{name}
用户自写内容：
{diary_text.strip() or "用户未填写文字。"}

当前状态线索：
{context}{observed}"""
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
