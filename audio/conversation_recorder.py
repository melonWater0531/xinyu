"""
Conversation recorder for multi-person mode.

MVP responsibilities:
  - capture mono 16 kHz audio from the default/ReSpeaker input device
  - split speech turns with a lightweight VAD
  - persist segment wav files and timeline.jsonl
  - expose a JSON-serializable state for the frontend

Heavy ASR/diarization workers are intentionally optional so the recording
pipeline can run before WhisperX/pyannote are installed.
"""
from __future__ import annotations

import json
import math
import queue
import threading
import time
import wave
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Callable, Optional

import numpy as np

from utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class ConversationTurn:
    id: str
    session_id: str
    speaker: str
    speaker_hint: str
    start: float
    end: float
    text: str
    confidence: float
    doa_mean: Optional[float]
    doa_stability: float
    wav_path: str
    status: str
    created_at: float

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)

    def to_dict(self) -> dict:
        data = asdict(self)
        data["duration"] = round(self.duration, 2)
        data["start"] = round(self.start, 2)
        data["end"] = round(self.end, 2)
        data["created_at"] = round(self.created_at, 3)
        if data["doa_mean"] is not None:
            data["doa_mean"] = round(float(data["doa_mean"]), 1)
        data["doa_stability"] = round(float(data["doa_stability"]), 3)
        return data


class ConversationRecorder:
    def __init__(
        self,
        root: str | Path,
        doa_provider: Optional[Callable[[], tuple[Optional[float], bool]]] = None,
        sample_rate: int = 16000,
        block_ms: int = 100,
        device: Optional[int | str] = None,
    ) -> None:
        self.root = Path(root)
        self.sample_rate = int(sample_rate)
        self.block_ms = int(block_ms)
        self.block_size = max(160, int(self.sample_rate * self.block_ms / 1000))
        self.device = device
        self.doa_provider = doa_provider

        self._lock = threading.Lock()
        self._audio_q: queue.Queue[np.ndarray] = queue.Queue(maxsize=200)
        self._running = False
        self._stream = None
        self._worker: Optional[threading.Thread] = None
        self._session_id = ""
        self._session_dir: Optional[Path] = None
        self._started_at = 0.0
        self._turns: list[ConversationTurn] = []
        self._current = {
            "recording": False,
            "has_speech": False,
            "elapsed": 0.0,
            "doa_deg": None,
            "speaker_hint": "未知",
            "level": 0.0,
        }
        self._error = ""
        self._segment_idx = 0

    @property
    def active(self) -> bool:
        return self._running

    @property
    def session_id(self) -> str:
        return self._session_id

    def start(self) -> bool:
        if self._running:
            return True
        self.root.mkdir(parents=True, exist_ok=True)
        self._session_id = time.strftime("session_%Y%m%d_%H%M%S")
        self._session_dir = self.root / self._session_id
        (self._session_dir / "audio" / "segments").mkdir(parents=True, exist_ok=True)
        self._started_at = time.time()
        self._turns.clear()
        self._segment_idx = 0
        self._error = ""

        try:
            import sounddevice as sd
            self._stream = sd.InputStream(
                samplerate=self.sample_rate,
                channels=1,
                dtype="float32",
                blocksize=self.block_size,
                device=self.device,
                callback=self._audio_callback,
            )
            self._stream.start()
        except Exception as e:
            self._error = f"audio input unavailable: {str(e)[:120]}"
            logger.warning("Conversation recorder audio unavailable: %s", self._error)
            return False

        self._running = True
        self._worker = threading.Thread(target=self._segment_loop, daemon=True, name="conversation-recorder")
        self._worker.start()
        self._write_session_json(ended_at=None)
        logger.info("🎙️ Conversation recording started: %s", self._session_id)
        return True

    def stop(self, finalize: bool = True) -> None:
        if not self._running and self._stream is None:
            return
        self._running = False
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None
        if self._worker:
            self._worker.join(timeout=2.0)
            self._worker = None
        self._write_session_json(ended_at=time.time() if finalize else None)
        with self._lock:
            self._current.update({"recording": False, "has_speech": False, "elapsed": 0.0})
        logger.info("🎙️ Conversation recording stopped: %s", self._session_id)

    def state(self) -> dict:
        with self._lock:
            turns = [t.to_dict() for t in self._turns[-80:]]
            current = dict(self._current)
        return {
            "active": self._running,
            "available": not bool(self._error),
            "error": self._error,
            "session_id": self._session_id,
            "started_at": round(self._started_at, 3) if self._started_at else None,
            "recording": self._running,
            "current": current,
            "timeline": turns,
            "stats": {
                "turns": len(turns),
                "speakers": len({t["speaker"] for t in turns}) if turns else 0,
                "duration": round(max(0.0, time.time() - self._started_at), 1) if self._started_at else 0.0,
            },
        }

    def _audio_callback(self, indata, frames, time_info, status) -> None:
        if status:
            logger.debug("Audio input status: %s", status)
        try:
            mono = np.asarray(indata[:, 0], dtype=np.float32).copy()
            self._audio_q.put_nowait(mono)
        except queue.Full:
            pass

    def _segment_loop(self) -> None:
        pre_chunks: list[np.ndarray] = []
        speech_chunks: list[np.ndarray] = []
        doa_values: list[float] = []
        speech_start_ts = 0.0
        silence_blocks = 0
        in_speech = False

        pre_blocks = max(1, int(200 / self.block_ms))
        end_silence_blocks = max(2, int(700 / self.block_ms))
        min_speech_blocks = max(2, int(400 / self.block_ms))
        max_speech_blocks = max(min_speech_blocks, int(18000 / self.block_ms))

        noise_floor = 0.008

        while self._running or not self._audio_q.empty():
            try:
                chunk = self._audio_q.get(timeout=0.2)
            except queue.Empty:
                continue

            level = float(np.sqrt(np.mean(np.square(chunk))) + 1e-9)
            noise_floor = min(0.06, max(0.004, noise_floor * 0.98 + level * 0.02))
            threshold = max(0.014, noise_floor * 2.8)
            voiced = level >= threshold
            doa_deg, has_speech = self._read_doa()
            if has_speech:
                voiced = True

            now = time.time()
            if not in_speech:
                pre_chunks.append(chunk)
                pre_chunks = pre_chunks[-pre_blocks:]
                if voiced:
                    in_speech = True
                    speech_start_ts = now - (len(pre_chunks) * self.block_ms / 1000.0)
                    speech_chunks = list(pre_chunks)
                    doa_values = []
                    silence_blocks = 0
            else:
                speech_chunks.append(chunk)
                if doa_deg is not None:
                    doa_values.append(float(doa_deg))
                silence_blocks = 0 if voiced else silence_blocks + 1
                too_long = len(speech_chunks) >= max_speech_blocks
                ended = silence_blocks >= end_silence_blocks
                if (ended or too_long) and len(speech_chunks) >= min_speech_blocks:
                    speech_end_ts = now - (silence_blocks * self.block_ms / 1000.0)
                    self._finalize_segment(speech_chunks, doa_values, speech_start_ts, speech_end_ts)
                    in_speech = False
                    speech_chunks = []
                    doa_values = []
                    pre_chunks = []
                    silence_blocks = 0

            with self._lock:
                self._current.update({
                    "recording": True,
                    "has_speech": bool(in_speech),
                    "elapsed": round(max(0.0, now - speech_start_ts), 1) if in_speech else 0.0,
                    "doa_deg": round(float(doa_deg), 1) if doa_deg is not None else None,
                    "speaker_hint": self._speaker_hint(doa_deg),
                    "level": round(level, 4),
                })

    def _read_doa(self) -> tuple[Optional[float], bool]:
        if not self.doa_provider:
            return None, False
        try:
            return self.doa_provider()
        except Exception:
            return None, False

    def _finalize_segment(
        self,
        chunks: list[np.ndarray],
        doa_values: list[float],
        start_ts: float,
        end_ts: float,
    ) -> None:
        if self._session_dir is None or not chunks:
            return
        audio = np.concatenate(chunks)
        self._segment_idx += 1
        seg_id = f"seg_{self._segment_idx:06d}"
        wav_path = self._session_dir / "audio" / "segments" / f"{seg_id}.wav"
        self._write_wav(wav_path, audio)

        doa_mean = self._circular_mean(doa_values) if doa_values else None
        stability = self._doa_stability(doa_values) if doa_values else 0.0
        turn = ConversationTurn(
            id=f"turn_{self._segment_idx:06d}",
            session_id=self._session_id,
            speaker=self._speaker_label(doa_mean),
            speaker_hint=self._speaker_hint(doa_mean),
            start=start_ts - self._started_at,
            end=end_ts - self._started_at,
            text="",
            confidence=0.0,
            doa_mean=doa_mean,
            doa_stability=stability,
            wav_path=str(wav_path),
            status="audio_saved",
            created_at=time.time(),
        )
        with self._lock:
            self._turns.append(turn)
        self._append_timeline(turn)

    def _write_wav(self, path: Path, audio: np.ndarray) -> None:
        pcm = np.clip(audio, -1.0, 1.0)
        pcm16 = (pcm * 32767.0).astype(np.int16)
        with wave.open(str(path), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(self.sample_rate)
            wf.writeframes(pcm16.tobytes())

    def _append_timeline(self, turn: ConversationTurn) -> None:
        if self._session_dir is None:
            return
        line = json.dumps(turn.to_dict(), ensure_ascii=False)
        with (self._session_dir / "timeline.jsonl").open("a", encoding="utf-8") as f:
            f.write(line + "\n")

    def _write_session_json(self, ended_at: Optional[float]) -> None:
        if self._session_dir is None:
            return
        data = {
            "session_id": self._session_id,
            "started_at": self._started_at,
            "ended_at": ended_at,
            "sample_rate": self.sample_rate,
            "channels": 1,
            "turns": len(self._turns),
        }
        (self._session_dir / "session.json").write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    @staticmethod
    def _speaker_hint(doa: Optional[float]) -> str:
        if doa is None:
            return "未知"
        deg = float(doa) % 360
        if deg <= 35 or deg >= 325:
            return "正前方"
        if 35 < deg < 145:
            return "右侧"
        if 145 <= deg <= 215:
            return "后方"
        return "左侧"

    @staticmethod
    def _speaker_label(doa: Optional[float]) -> str:
        hint = ConversationRecorder._speaker_hint(doa)
        return {
            "正前方": "SPEAKER_FRONT",
            "右侧": "SPEAKER_RIGHT",
            "后方": "SPEAKER_BACK",
            "左侧": "SPEAKER_LEFT",
        }.get(hint, "SPEAKER_UNKNOWN")

    @staticmethod
    def _circular_mean(values: list[float]) -> float:
        if not values:
            return 0.0
        sin_sum = sum(math.sin(math.radians(v)) for v in values)
        cos_sum = sum(math.cos(math.radians(v)) for v in values)
        return (math.degrees(math.atan2(sin_sum, cos_sum)) + 360.0) % 360.0

    @staticmethod
    def _doa_stability(values: list[float]) -> float:
        if len(values) < 2:
            return 0.0
        sin_mean = sum(math.sin(math.radians(v)) for v in values) / len(values)
        cos_mean = sum(math.cos(math.radians(v)) for v in values) / len(values)
        return min(1.0, math.sqrt(sin_mean * sin_mean + cos_mean * cos_mean))
