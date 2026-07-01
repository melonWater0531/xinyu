"""Lightweight faster-whisper wrapper for meeting transcription."""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Optional

_model = None
_model_lock = asyncio.Lock()
DEFAULT_MODEL = os.getenv("RECAMERA_WHISPER_MODEL", "Systran/faster-whisper-tiny")


async def get_model():
    global _model
    async with _model_lock:
        if _model is None:
            try:
                from faster_whisper import WhisperModel
                _model = WhisperModel(DEFAULT_MODEL, device="cpu", compute_type="int8")
            except ImportError:
                pass  # faster-whisper not installed — transcribe_wav will return ""
    return _model


async def transcribe_wav(wav_path: str | Path) -> str:
    """Transcribe a WAV file. Returns text string or '' on failure / missing dependency."""
    model = await get_model()
    if model is None:
        return ""
    try:
        loop = asyncio.get_running_loop()
        segments, _ = await loop.run_in_executor(
            None, lambda: model.transcribe(str(wav_path), language="zh")
        )
        return " ".join(s.text.strip() for s in segments).strip()
    except Exception:
        return ""
