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

以下实时状态只能作为低置信度的背景线索，不能用来否定、覆盖或纠正用户的文字自述。
{context}

对话原则：
- 信息优先级严格为：用户本轮自述 > 用户日记和近期聊天 > 摄像头视觉线索。
- 用户文字和视觉线索冲突时，只跟随用户文字；不要提及冲突，也不要按视觉标签追问。
- 不直接复述视觉情绪标签、置信度、概率或任何模型字段，不说“我看出你很……”或“根据传感器……”。
- 像关心朋友一样回应，不做心理咨询师或医生式诊断。
- 先回应用户明确说出的感受，再给一个轻而具体的陪伴选择或开放问题。
- 只有用户没有表达感受时，才可以谨慎参考视觉线索，并使用“也许”“似乎”等不确定措辞。
- 不编造没有被用户或状态提到的事件。
- 回复 40-90 个中文字符，自然、克制，不使用列表或标题。"""


_ZH_EMOTION = {
    "Happiness": "快乐", "Happy": "快乐", "Neutral": "平静", "Calm": "平静",
    "Sadness": "低落", "Sad": "低落", "Anger": "愤怒", "Angry": "愤怒",
    "Fear": "不安", "Surprise": "惊讶", "Disgust": "不适", "Contempt": "轻蔑",
}


def describe_day_summary(day_summary: dict | None) -> str:
    """Compact one-line description of what the camera observed today,
    e.g. '上午平静为主, 15:02 情绪低点, 专注均值 72, 陪伴约 180 分钟'."""
    if not isinstance(day_summary, dict) or not day_summary.get("available"):
        return ""
    parts = []
    dom = str(day_summary.get("dominant_emotion") or "")
    if dom:
        parts.append(f"当日主要情绪为{_ZH_EMOTION.get(dom, dom)}")
    dips = day_summary.get("dips") or []
    if dips:
        ts = str(dips[0].get("ts") or "")
        if ts:
            parts.append(f"{ts} 有一次情绪低点")
    attn = day_summary.get("attention_avg")
    if attn is not None:
        parts.append(f"专注均值 {round(_as_float(attn))}")
    presence = _as_float(day_summary.get("presence_min"))
    if presence >= 1:
        parts.append(f"陪伴约 {round(presence)} 分钟")
    return "，".join(parts)


def build_weekly_report_prompt(weekly_data, options=None, user_name: str = "",
                               week_start: str = "", week_end: str = "") -> list[dict[str, str]]:
    """Build a short weekly reflection while preserving the legacy call shape."""
    if isinstance(weekly_data, dict):
        entries = weekly_data.get("entries") or []
        day_summaries = weekly_data.get("day_summaries") or []
        selfcare = weekly_data.get("selfcare") or []
        opts = options if isinstance(options, dict) else {}
        user_name = str(opts.get("user_name") or user_name)
        week_start = str(opts.get("week_start") or week_start)
        week_end = str(opts.get("week_end") or week_end)
    else:
        entries = weekly_data or []
        day_summaries = options if isinstance(options, list) else []
        opts = {}
        selfcare = next((e.get("selfcare_week") for e in entries if isinstance(e, dict) and e.get("selfcare_week")), [])
    name = (user_name or "").strip() or "用户"
    diary_lines = []
    for e in entries[:14]:
        if not isinstance(e, dict):
            continue
        emo = _ZH_EMOTION.get(str(e.get("emotion") or ""), str(e.get("emotion") or ""))
        obs = _ZH_EMOTION.get(str(e.get("observed_emotion") or ""), str(e.get("observed_emotion") or ""))
        line = f"{e.get('date','')}: 自评{emo or '未填'}"
        if obs:
            line += f"，视觉弱线索为{obs}"
        excerpt = str(e.get("content") or e.get("excerpt") or "")[:60]
        if excerpt:
            line += f"，摘录：{excerpt}"
        diary_lines.append(line)
    observed_lines = []
    for d in (day_summaries or [])[:7]:
        if isinstance(d, dict) and d.get("available"):
            desc = describe_day_summary(d)
            if desc:
                observed_lines.append(f"{d.get('date','')}: {desc}")
    record_days = len({str(e.get("date")) for e in entries if isinstance(e, dict) and e.get("date")})
    data_sufficiency = str(opts.get("data_sufficiency") or ("low" if record_days < 3 else "medium" if record_days <= 5 else "high"))
    demo_mode = bool(opts.get("demo_mode")) or bool(selfcare) and all(str(d.get("source")) == "demo" for d in selfcare if isinstance(d, dict))
    bounds = (80, 120) if data_sufficiency == "low" else (120, 180) if data_sufficiency == "medium" else (160, 220)
    if demo_mode:
        bounds = (120, 180)
    max_chars = max(bounds[0], min(int(opts.get("max_chars") or bounds[1]), bounds[1]))

    care_lines = []
    for day in selfcare[:7]:
        if not isinstance(day, dict):
            continue
        care_lines.append(
            f"{day.get('date','')}: 步数{(day.get('steps') or {}).get('value','无')}，"
            f"喝水{(day.get('water') or {}).get('cups','无')}杯，"
            f"运动{len((day.get('exercise') or {}).get('sessions') or [])}次，"
            f"呼吸{len((day.get('breathing') or {}).get('sessions') or [])}次，来源{day.get('source','未知')}"
        )
    system = f"""你是心屿，请给{name}写一段自然、温和、克制的本周简报。
信息优先级：用户日记、自述和聊天 > self-care 主动记录 > 摄像头视觉弱线索；冲突时必须跟随用户自述。
正文控制在 {bounds[0]}-{max_chars} 个中文字符。最终只输出一个自然短文，不要标题，不要列表。
内容顺序自然包含：一句趋势、一句肯定、一句需要照顾的地方、一个具体的下周建议。
禁止医学或心理诊断、说教、夸大趋势、编造数据、逐项罗列数字、暴露字段名或 JSON。
不要复述视觉模型标签或概率。演示数据必须明确称为演示预览，不能伪装成用户真实数据。"""
    user = (
        f"周期：{week_start} 至 {week_end}\n"
        f"数据充分度：{data_sufficiency}；演示模式：{'是' if demo_mode else '否'}\n"
        f"用户日记（{len(diary_lines)} 条）：\n" + ("\n".join(diary_lines) or "本周没有日记记录。") +
        "\n\n视觉弱线索：\n" + ("\n".join(observed_lines) or "本周没有观察数据。") +
        "\n\n自我照顾记录：\n" + ("\n".join(care_lines) or "本周没有自我照顾记录。")
    )
    return [{"role": "system", "content": system},
            {"role": "user", "content": user}]


def build_reflect_messages(diary_text: str, state: dict | None, user_name: str = "", payload: dict | None = None) -> list[dict[str, str]]:
    context = build_emotion_context(state)
    payload = payload or {}
    duration_min = _as_int(payload.get("duration_min"))
    observed = f"\n监测时长：{duration_min} 分钟" if duration_min else ""
    day_line = describe_day_summary(payload.get("day_summary"))
    if day_line:
        observed += f"\n今日观察摘要：{day_line}"
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
