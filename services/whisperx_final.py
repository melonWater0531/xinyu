"""Final meeting transcript enhancement via an isolated WhisperX runtime."""
from __future__ import annotations

import asyncio
import json
import os
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from utils.logger import get_logger

logger = get_logger(__name__)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_WHISPERX_PY = Path(os.getenv("WHISPERX_PY", "/home/lintong_chen/.venvs/recamera-whisperx/bin/python"))
DEFAULT_FFMPEG_HOME = Path(
    os.getenv("WHISPERX_FFMPEG_HOME", "/home/lintong_chen/.local/opt/ffmpeg-n7.1-latest-linux64-gpl-shared-7.1")
)
DEFAULT_MODEL_PATH = os.getenv(
    "WHISPERX_MODEL_PATH",
    "/home/lintong_chen/.cache/huggingface/hub/models--Systran--faster-whisper-medium/"
    "snapshots/08e178d48790749d25932bbc082711ddcfdfbc4f",
)


@dataclass
class WhisperXFinalResult:
    ok: bool
    transcript: str = ""
    lines: list[str] | None = None
    segments: list[dict[str, Any]] | None = None
    input_wav: str = ""
    output_json: str = ""
    error: str = ""
    duration_seconds: float = 0.0
    provider: str = "whisperx"


def _session_dir_from_turns(turns: list[dict[str, Any]]) -> Path | None:
    for turn in turns:
        wav_path = Path(str(turn.get("wav_path") or ""))
        parts = wav_path.parts
        if len(parts) >= 3 and parts[-3:-1] == ("audio", "segments"):
            return wav_path.parent.parent.parent
    return None


def _read_segment_pcm(path: Path) -> tuple[int, bytes]:
    with wave.open(str(path), "rb") as wf:
        if wf.getnchannels() != 1 or wf.getsampwidth() != 2:
            raise ValueError(f"unsupported wav format: {path}")
        rate = int(wf.getframerate())
        return rate, wf.readframes(wf.getnframes())


def _build_session_wav(turns: list[dict[str, Any]], out_path: Path) -> bool:
    sample_rate = 16000
    cursor_frames = 0
    wrote = False
    out_path.parent.mkdir(parents=True, exist_ok=True)
    ordered = sorted(turns, key=lambda t: float(t.get("start", 0.0) or 0.0))
    with wave.open(str(out_path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(sample_rate)
        for turn in ordered:
            wav_path = Path(str(turn.get("wav_path") or ""))
            if not wav_path.exists():
                continue
            rate, pcm = _read_segment_pcm(wav_path)
            if rate != sample_rate:
                raise ValueError(f"unexpected sample rate {rate}: {wav_path}")
            start = max(0.0, float(turn.get("start", 0.0) or 0.0))
            start_frame = int(start * sample_rate)
            if start_frame > cursor_frames:
                out.writeframes(b"\x00\x00" * (start_frame - cursor_frames))
                cursor_frames = start_frame
            out.writeframes(pcm)
            cursor_frames += len(pcm) // 2
            wrote = True
    return wrote


def _speaker_for_segment(segment: dict[str, Any], turns: list[dict[str, Any]]) -> str:
    start = float(segment.get("start", 0.0) or 0.0)
    end = float(segment.get("end", start) or start)
    mid = start + max(0.0, end - start) / 2.0
    best_turn: dict[str, Any] | None = None
    best_overlap = 0.0
    for turn in turns:
        t_start = float(turn.get("start", 0.0) or 0.0)
        t_end = float(turn.get("end", t_start) or t_start)
        if t_start <= mid <= t_end:
            return str(turn.get("speaker_label") or "未知说话人")
        overlap = max(0.0, min(end, t_end) - max(start, t_start))
        if overlap > best_overlap:
            best_overlap = overlap
            best_turn = turn
    if best_turn is not None:
        return str(best_turn.get("speaker_label") or "未知说话人")
    return "未知说话人"


def format_transcript_segments(segments: list[dict[str, Any]], turns: list[dict[str, Any]]) -> list[str]:
    lines: list[str] = []
    for segment in segments:
        text = str(segment.get("text") or "").strip()
        if not text:
            continue
        start = float(segment.get("start", 0.0) or 0.0)
        end = float(segment.get("end", start) or start)
        speaker = _speaker_for_segment(segment, turns)
        lines.append(f"[{start:.1f}-{end:.1f}s][{speaker}] {text}")
    return lines


async def transcribe_meeting_turns(turns: list[dict[str, Any]]) -> WhisperXFinalResult:
    """Return a final transcript for a meeting, or ok=False to use fallback."""
    if not turns:
        return WhisperXFinalResult(ok=False, error="no_turns")
    session_dir = _session_dir_from_turns(turns)
    if session_dir is None:
        return WhisperXFinalResult(ok=False, error="session_dir_unavailable")
    if not DEFAULT_WHISPERX_PY.exists():
        return WhisperXFinalResult(ok=False, error="whisperx_python_missing")
    if not Path(DEFAULT_MODEL_PATH).exists():
        return WhisperXFinalResult(ok=False, error="whisperx_model_missing")

    ffmpeg_bin = DEFAULT_FFMPEG_HOME / "bin"
    ffmpeg_lib = DEFAULT_FFMPEG_HOME / "lib"
    work_dir = session_dir / "audio" / "final"
    input_wav = work_dir / "whisperx_final_input.wav"
    request_json = work_dir / "whisperx_request.json"
    output_json = session_dir / "whisperx_transcript.json"

    try:
        if not _build_session_wav(turns, input_wav):
            return WhisperXFinalResult(ok=False, error="no_audio_segments")
        request_json.write_text(
            json.dumps(
                {
                    "audio_file": str(input_wav),
                    "model_path": DEFAULT_MODEL_PATH,
                    "device": os.getenv("WHISPERX_DEVICE", "cuda"),
                    "compute_type": os.getenv("WHISPERX_COMPUTE_TYPE", "float16"),
                    "batch_size": int(os.getenv("WHISPERX_BATCH_SIZE", "8")),
                    "language": os.getenv("WHISPERX_LANGUAGE", "zh"),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        env = dict(os.environ)
        if ffmpeg_bin.exists():
            env["PATH"] = f"{ffmpeg_bin}:{env.get('PATH', '')}"
        if ffmpeg_lib.exists():
            env["LD_LIBRARY_PATH"] = f"{ffmpeg_lib}:{env.get('LD_LIBRARY_PATH', '')}"
        env.setdefault("HF_HUB_DISABLE_XET", "1")

        proc = await asyncio.create_subprocess_exec(
            str(DEFAULT_WHISPERX_PY),
            str(ROOT / "services" / "whisperx_runner.py"),
            "--input-json",
            str(request_json),
            "--output-json",
            str(output_json),
            cwd=str(ROOT),
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        timeout_s = float(os.getenv("WHISPERX_TIMEOUT_S", "240"))
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
        if proc.returncode != 0:
            err = (stderr or stdout).decode("utf-8", errors="ignore")[-800:]
            logger.warning("WhisperX final transcript failed: %s", err)
            return WhisperXFinalResult(ok=False, error="whisperx_failed")

        data = json.loads(output_json.read_text(encoding="utf-8"))
        segments = data.get("segments") or []
        lines = format_transcript_segments(segments, turns)
        transcript = "\n".join(lines)
        if not transcript.strip():
            return WhisperXFinalResult(ok=False, error="whisperx_empty")
        return WhisperXFinalResult(
            ok=True,
            transcript=transcript,
            lines=lines,
            segments=segments,
            input_wav=str(input_wav),
            output_json=str(output_json),
            duration_seconds=float(data.get("duration_seconds", 0.0) or 0.0),
        )
    except asyncio.TimeoutError:
        logger.warning("WhisperX final transcript timed out")
        return WhisperXFinalResult(ok=False, error="whisperx_timeout")
    except Exception as exc:
        logger.warning("WhisperX final transcript unavailable: %s", str(exc)[:160])
        return WhisperXFinalResult(ok=False, error=str(exc)[:160])
