"""Zhipu/OpenAI-compatible voice helpers for short interactive turns."""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path

import aiohttp

from services.cloud_asr import cloud_asr
from services.llm_router import router
from utils.logger import get_logger

logger = get_logger(__name__)

ZHIPU_API_KEY = os.getenv("ZHIPU_API_KEY", "")
ZHIPU_TTS_URL = os.getenv("ZHIPU_TTS_URL", "https://open.bigmodel.cn/api/paas/v4/audio/speech")
ZHIPU_TTS_MODEL = os.getenv("ZHIPU_TTS_MODEL", "glm-tts")
ZHIPU_TTS_VOICE = os.getenv("ZHIPU_TTS_VOICE", "")
ZHIPU_TTS_FORMAT = os.getenv("ZHIPU_TTS_FORMAT", "wav")
ZHIPU_TTS_SPEED = os.getenv("ZHIPU_TTS_SPEED", "")
ZHIPU_TTS_VOLUME = os.getenv("ZHIPU_TTS_VOLUME", "")

VOICE_PRESETS = {
    "gentle_female": {
        "label": "温柔女声",
        "voice": "gentle_female",
        "speed": 0.95,
        "volume": 1.0,
        "description": "陪伴、日记回应、情绪安抚优先。",
    },
    "neutral_natural": {
        "label": "中性自然声",
        "voice": "neutral_natural",
        "speed": 1.0,
        "volume": 1.0,
        "description": "默认对话和多数产品提示。",
    },
    "meeting_prompt": {
        "label": "会议提示声",
        "voice": "meeting_prompt",
        "speed": 1.08,
        "volume": 0.9,
        "description": "短促、克制，适合会议状态提示。",
    },
}

_TIMEOUT = aiohttp.ClientTimeout(total=45, connect=5)


class ZhipuVoiceService:
    def __init__(self) -> None:
        self.last_tts_error = ""
        self.last_tts_error_at = 0.0
        self.last_tts_success_at = 0.0

    def configured(self) -> bool:
        return bool(os.getenv("ZHIPU_API_KEY", ""))

    def status(self) -> dict:
        return {
            "configured": self.configured(),
            "tts_url": os.getenv("ZHIPU_TTS_URL", ZHIPU_TTS_URL),
            "tts_model": os.getenv("ZHIPU_TTS_MODEL", ZHIPU_TTS_MODEL),
            "tts_format": os.getenv("ZHIPU_TTS_FORMAT", ZHIPU_TTS_FORMAT),
            "last_tts_error": self.last_tts_error,
            "last_tts_error_at": self.last_tts_error_at,
            "last_tts_success_at": self.last_tts_success_at,
            "presets": VOICE_PRESETS,
        }

    async def transcribe(self, audio_path: str) -> dict:
        text = await cloud_asr.transcribe(audio_path)
        return {
            "text": text,
            "provider": "zhipu" if text and not getattr(cloud_asr, "last_error", "") else "fallback",
            "last_error": getattr(cloud_asr, "last_error", ""),
        }

    async def chat(self, transcript: str, context: str = "", user_name: str = "") -> dict:
        if not transcript.strip():
            return {"text": "", "provider": "none", "error": "empty_transcript"}
        system = (
            "你是心屿小屿，回应要短、自然、温柔。"
            "不要做医疗诊断；如果用户明显求助，建议联系可信的人或专业支持。"
        )
        user = transcript.strip()
        if context:
            user = f"{user}\n\n上下文：{context[:600]}"
        if user_name:
            user = f"用户称呼：{user_name[:40]}\n{user}"
        result = await router.complete_with_provider(
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            max_tokens=180,
            temperature=0.7,
        )
        text = (result.get("text") or "").strip()
        if not text:
            text = "我听到了。你可以慢慢说，我会陪你把这件事一点点理清楚。"
        return {"text": text, "provider": result.get("provider", "none")}

    async def tts(self, text: str, out_path: str, options: dict | None = None) -> dict:
        text = str(text or "").strip()
        if not text:
            self._record_tts_error("empty_text")
            return {"ok": False, "error": "empty_text"}
        key = os.getenv("ZHIPU_API_KEY", "")
        if not key:
            self._record_tts_error("unconfigured")
            return {"ok": False, "error": "unconfigured"}

        options = dict(options or {})
        preset = VOICE_PRESETS.get(str(options.get("preset") or ""), {})
        fmt = str(options.get("format") or os.getenv("ZHIPU_TTS_FORMAT", ZHIPU_TTS_FORMAT) or "wav").lower()
        payload = {
            "model": str(options.get("model") or os.getenv("ZHIPU_TTS_MODEL", ZHIPU_TTS_MODEL)),
            "input": text,
            "response_format": fmt,
        }
        voice = str(options.get("voice") or os.getenv("ZHIPU_TTS_VOICE", ZHIPU_TTS_VOICE) or preset.get("voice") or "")
        if voice:
            payload["voice"] = voice
        speed = _first_number(options.get("speed"), os.getenv("ZHIPU_TTS_SPEED", ZHIPU_TTS_SPEED), preset.get("speed"))
        if speed is not None:
            payload["speed"] = speed
        volume = _first_number(options.get("volume"), os.getenv("ZHIPU_TTS_VOLUME", ZHIPU_TTS_VOLUME), preset.get("volume"))
        if volume is not None:
            payload["volume"] = volume

        url = str(options.get("url") or os.getenv("ZHIPU_TTS_URL", ZHIPU_TTS_URL))
        headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
        try:
            async with aiohttp.ClientSession(timeout=_TIMEOUT) as session:
                async with session.post(url, json=payload, headers=headers) as resp:
                    raw = await resp.read()
                    if resp.status < 200 or resp.status >= 300:
                        err = "auth" if resp.status in (401, 403) else "quota" if resp.status == 429 else "bad_response"
                        self._record_tts_error(err)
                        logger.warning("Zhipu TTS => %d (%s): %s", resp.status, err, raw[:120])
                        return {"ok": False, "error": err, "status": resp.status}
                    content_type = resp.headers.get("Content-Type", "audio/wav")
                    data = _extract_audio_bytes(raw, content_type)
                    if not data:
                        self._record_tts_error("bad_response")
                        return {"ok": False, "error": "bad_response"}
                    path = Path(out_path)
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(data)
                    self.last_tts_error = ""
                    self.last_tts_success_at = time.time()
                    return {
                        "ok": True,
                        "path": str(path),
                        "bytes": len(data),
                        "provider": "zhipu",
                        "content_type": "audio/wav" if fmt == "wav" else content_type,
                        "format": fmt,
                        "voice": voice,
                        "model": payload["model"],
                    }
        except asyncio.TimeoutError:
            self._record_tts_error("timeout")
        except aiohttp.ClientError as exc:
            self._record_tts_error("network")
            logger.warning("Zhipu TTS network error: %s", str(exc)[:100])
        except Exception as exc:
            self._record_tts_error("network")
            logger.warning("Zhipu TTS error: %s", str(exc)[:100])
        return {"ok": False, "error": self.last_tts_error or "network"}

    def _record_tts_error(self, error: str) -> None:
        self.last_tts_error = str(error or "error")
        self.last_tts_error_at = time.time()


def _first_number(*values):
    for value in values:
        if value in ("", None):
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


def _extract_audio_bytes(raw: bytes, content_type: str) -> bytes:
    if not raw:
        return b""
    if content_type.startswith("audio/") or raw[:4] == b"RIFF":
        return raw
    try:
        import base64
        import json

        data = json.loads(raw.decode("utf-8"))
        audio = data.get("audio") or data.get("data") or data.get("audio_base64")
        if isinstance(audio, str):
            return base64.b64decode(audio)
    except Exception:
        return b""
    return b""


zhipu_voice = ZhipuVoiceService()
