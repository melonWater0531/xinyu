"""Optional meeting audio preprocessing.

The recorder must keep working when optional DSP dependencies are missing.
This module exposes one small processor that reports its own fallback state.
"""
from __future__ import annotations

import importlib
from dataclasses import dataclass
from typing import Optional

import numpy as np


@dataclass
class AudioProcessingState:
    noise_suppression: dict
    vad_mode: str
    fallback_reason: str


class MeetingAudioProcessor:
    def __init__(self, sample_rate: int = 16000, vad_aggressiveness: int = 2) -> None:
        self.sample_rate = int(sample_rate)
        self._noise_reduce = None
        self._vad = None
        self._fallback_reason = ""

        try:
            self._noise_reduce = importlib.import_module("noisereduce")
        except Exception as e:
            self._fallback_reason = f"noisereduce_unavailable:{type(e).__name__}"

        try:
            webrtcvad = importlib.import_module("webrtcvad")
            self._vad = webrtcvad.Vad(int(vad_aggressiveness))
        except Exception as e:
            reason = f"webrtcvad_unavailable:{type(e).__name__}"
            self._fallback_reason = ";".join([r for r in [self._fallback_reason, reason] if r])

    @property
    def noise_available(self) -> bool:
        return self._noise_reduce is not None

    @property
    def vad_available(self) -> bool:
        return self._vad is not None

    def process(self, mono: np.ndarray) -> np.ndarray:
        audio = np.asarray(mono, dtype=np.float32)
        if self._noise_reduce is None or audio.size == 0:
            return audio
        try:
            cleaned = self._noise_reduce.reduce_noise(y=audio, sr=self.sample_rate, stationary=False)
            return np.asarray(cleaned, dtype=np.float32)
        except Exception as e:
            self._fallback_reason = f"noise_reduce_failed:{type(e).__name__}"
            return audio

    def is_voiced(self, mono: np.ndarray) -> Optional[bool]:
        if self._vad is None:
            return None
        audio = np.asarray(mono, dtype=np.float32)
        if audio.size == 0:
            return False
        pcm = np.clip(audio, -1.0, 1.0)
        pcm16 = (pcm * 32767.0).astype(np.int16)
        frame_samples = int(self.sample_rate * 0.03)
        if frame_samples <= 0:
            return None
        voiced = 0
        total = 0
        for start in range(0, len(pcm16) - frame_samples + 1, frame_samples):
            frame = pcm16[start:start + frame_samples]
            try:
                if self._vad.is_speech(frame.tobytes(), self.sample_rate):
                    voiced += 1
                total += 1
            except Exception as e:
                self._fallback_reason = f"webrtcvad_failed:{type(e).__name__}"
                return None
        if total == 0:
            return None
        return voiced > 0

    def state(self) -> dict:
        return {
            "noise_suppression": {
                "available": self.noise_available,
                "enabled": self.noise_available,
            },
            "vad_mode": "webrtcvad" if self.vad_available else "rms",
            "fallback_reason": self._fallback_reason,
        }

