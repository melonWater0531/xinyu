"""Optional wake word service.

The service is disabled unless ENABLE_WAKE_WORD=true. Missing openWakeWord or
audio-device errors are reported in state() and never block FastAPI startup.
"""

from __future__ import annotations

import os
import threading
import time
from typing import Callable

from utils.logger import get_logger

logger = get_logger(__name__)


class WakeWordService:
    def __init__(self, audio_device_index=None, enabled: bool | None = None) -> None:
        self.audio_device_index = audio_device_index
        self.enabled = self._env_enabled() if enabled is None else bool(enabled)
        self.available = False
        self.is_listening = False
        self.is_paused = False
        self.error = ""
        self._thread: threading.Thread | None = None
        self._callbacks: list[Callable[[str, float], None]] = []
        self._detector = None

    @staticmethod
    def _env_enabled() -> bool:
        return os.getenv("ENABLE_WAKE_WORD", "").strip().lower() in {"1", "true", "yes", "on"}

    def on_wake(self, callback: Callable[[str, float], None]) -> None:
        self._callbacks.append(callback)

    def start(self) -> None:
        if not self.enabled:
            self.error = ""
            return
        if self.is_listening:
            return
        try:
            from openwakeword.model import Model
            self._detector = Model(wakeword_models=["hey_jarvis"], inference_framework="onnx")
            self.available = True
            self.error = ""
        except Exception as exc:
            self.available = False
            self.error = f"openwakeword_unavailable:{type(exc).__name__}"
            logger.warning("Wake word unavailable: %s", str(exc)[:120])
            return
        self.is_listening = True
        self._thread = threading.Thread(target=self._listen_loop, daemon=True, name="wake-word-service")
        self._thread.start()
        logger.info("Wake word service started")

    def pause(self) -> None:
        if self.enabled:
            self.is_paused = True

    def resume(self) -> None:
        if self.enabled:
            self.is_paused = False

    def stop(self) -> None:
        self.is_listening = False
        if self._thread:
            self._thread.join(timeout=3.0)
            self._thread = None

    def state(self) -> dict:
        return {
            "enabled": bool(self.enabled),
            "available": bool(self.available),
            "listening": bool(self.is_listening),
            "paused": bool(self.is_paused),
            "error": self.error,
        }

    def _emit(self, name: str, score: float) -> None:
        for callback in list(self._callbacks):
            try:
                callback(name, score)
            except Exception as exc:
                logger.debug("wake callback failed: %s", str(exc)[:80])

    def _listen_loop(self) -> None:
        try:
            import sounddevice as sd
        except Exception as exc:
            self.available = False
            self.error = f"sounddevice_unavailable:{type(exc).__name__}"
            self.is_listening = False
            logger.warning("Wake word audio unavailable: %s", str(exc)[:120])
            return

        chunk = 1280
        while self.is_listening:
            if self.is_paused:
                time.sleep(0.25)
                continue
            try:
                audio = sd.rec(
                    frames=chunk,
                    samplerate=16000,
                    channels=1,
                    dtype="int16",
                    device=self.audio_device_index,
                )
                sd.wait()
                scores = self._detector.predict(audio.reshape(-1))
                for name, score in scores.items():
                    score_f = float(score)
                    if score_f > 0.5:
                        logger.info("Wake word detected: %s %.3f", name, score_f)
                        self._emit(str(name), score_f)
                        time.sleep(3.0)
                        break
            except Exception as exc:
                self.error = f"listen_failed:{type(exc).__name__}"
                logger.warning("Wake word listen failed: %s", str(exc)[:120])
                time.sleep(1.0)
