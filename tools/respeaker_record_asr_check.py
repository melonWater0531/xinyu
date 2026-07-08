#!/usr/bin/env python3
"""ReSpeaker recording + ASR smoke test.

This script intentionally stays outside the FastAPI meeting path. It verifies
the minimum ReSpeaker chain:
  device -> short WAV -> audio stats/clock ratio -> faster-whisper text
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import importlib.util
import os
import sys
import time
import wave
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "records" / "reSpeaker_tests"
LOG_PATH = ROOT / "logs" / "respeaker_test.log"
sys.path.insert(0, str(ROOT))


def log(message: str = "") -> None:
    print(message, flush=True)


def banner(message: str) -> None:
    line = "=" * 72
    log(line)
    log(message)
    log(line)


def query_devices() -> list[dict]:
    import sounddevice as sd

    devices = sd.query_devices()
    result = []
    for index, device in enumerate(devices):
        info = dict(device)
        info["index"] = index
        result.append(info)
    return result


def choose_device(requested: str | None) -> int:
    if requested:
        return int(requested)
    env_value = os.environ.get("RECAMERA_AUDIO_DEVICE", "").strip()
    if env_value:
        return int(env_value)
    for device in query_devices():
        name = str(device.get("name") or "").lower()
        if (
            int(device.get("max_input_channels") or 0) > 0
            and ("respeaker" in name or "xvf3800" in name or "usb audio" in name)
        ):
            return int(device["index"])
    raise RuntimeError("No ReSpeaker-like input device found; set --device or RECAMERA_AUDIO_DEVICE.")


def write_wav(path: Path, mono: np.ndarray, sample_rate: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pcm = (np.clip(mono, -1.0, 1.0) * 32767.0).astype(np.int16)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm.tobytes())


def rewrite_wav_header_sample_rate(src: Path, dst: Path, sample_rate: int) -> None:
    with wave.open(str(src), "rb") as wf:
        raw = wf.readframes(wf.getnframes())
        channels = int(wf.getnchannels())
        sample_width = int(wf.getsampwidth())
    with wave.open(str(dst), "wb") as out:
        out.setnchannels(channels)
        out.setsampwidth(sample_width)
        out.setframerate(sample_rate)
        out.writeframes(raw)


def wav_stats(path: Path) -> dict:
    with wave.open(str(path), "rb") as wf:
        raw = wf.readframes(wf.getnframes())
        sample_rate = int(wf.getframerate())
        channels = int(wf.getnchannels())
        frames = int(wf.getnframes())
    data = np.frombuffer(raw, dtype=np.int16)
    if channels > 1:
        data = data.reshape(-1, channels).mean(axis=1).astype(np.int16)
    audio = data.astype(np.float32) / 32767.0
    return {
        "sample_rate": sample_rate,
        "channels": channels,
        "frames": frames,
        "duration_header": round(frames / max(sample_rate, 1), 3),
        "rms": round(float(np.sqrt(np.mean(audio * audio)) + 1e-9), 6),
        "peak": round(float(np.max(np.abs(audio))) if audio.size else 0.0, 6),
        "above_0.01": round(float(np.mean(np.abs(audio) > 0.01)) if audio.size else 0.0, 4),
        "vad_voiced_ratio": vad_ratio(data, sample_rate),
    }


def vad_ratio(pcm16: np.ndarray, sample_rate: int) -> float | None:
    if importlib.util.find_spec("webrtcvad") is None:
        return None
    import webrtcvad

    vad = webrtcvad.Vad(2)
    frame_samples = int(sample_rate * 0.03)
    if frame_samples <= 0:
        return None
    total = 0
    voiced = 0
    for start in range(0, len(pcm16) - frame_samples + 1, frame_samples):
        frame = pcm16[start : start + frame_samples].astype(np.int16)
        with contextlib.suppress(Exception):
            voiced += int(vad.is_speech(frame.tobytes(), sample_rate))
            total += 1
    if total <= 0:
        return None
    return round(voiced / total, 4)


async def transcribe(path: Path, model_name: str) -> str:
    os.environ["RECAMERA_WHISPER_MODEL"] = model_name
    from audio.transcriber import transcribe_wav

    return await transcribe_wav(path)


def record_sounddevice(device: int, seconds: float, sample_rate: int, channels: int, out_path: Path) -> dict:
    import sounddevice as sd

    banner("!!! 请现在对着 ReSpeaker 连续说话，正在录音 !!!")
    log("建议测试句：测试会议记录，今天我们讨论项目进度和下一步安排。")
    for remaining in range(3, 0, -1):
        log(f"{remaining} ...")
        time.sleep(1)
    log(f"开始录音：device={device}, seconds={seconds}, sample_rate={sample_rate}, channels={channels}")
    started = time.perf_counter()
    data = sd.rec(
        int(sample_rate * seconds),
        samplerate=sample_rate,
        channels=channels,
        dtype="float32",
        device=device,
    )
    sd.wait()
    elapsed = time.perf_counter() - started
    if channels > 1:
        mono = np.asarray(data, dtype=np.float32).mean(axis=1)
    else:
        mono = np.asarray(data[:, 0], dtype=np.float32)
    write_wav(out_path, mono, sample_rate)
    ratio = elapsed / max(seconds, 0.001)
    effective_rate = int(round(sample_rate / ratio)) if ratio > 0 else sample_rate
    return {
        "path": str(out_path),
        "expected_sec": seconds,
        "actual_sec": round(elapsed, 3),
        "clock_ratio": round(ratio, 3),
        "effective_sample_rate_estimate": effective_rate,
        "warning": "clock_slow_or_usb_audio_issue" if ratio > 1.5 else "",
        "stats": wav_stats(out_path),
    }


def print_devices() -> None:
    banner("ReSpeaker / sounddevice 输入设备枚举")
    for device in query_devices():
        if int(device.get("max_input_channels") or 0) <= 0:
            continue
        log(
            f"[{device['index']}] {device.get('name')} | "
            f"inputs={device.get('max_input_channels')} | "
            f"default_sr={device.get('default_samplerate')}"
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--device", default="")
    parser.add_argument("--seconds", type=float, default=8.0)
    parser.add_argument("--sample-rate", type=int, default=16000)
    parser.add_argument("--channels", type=int, default=1)
    parser.add_argument("--model", default=os.environ.get("RECAMERA_WHISPER_MODEL") or "Systran/faster-whisper-small")
    parser.add_argument("--skip-asr", action="store_true")
    args = parser.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)

    print_devices()
    device = choose_device(args.device or None)
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    wav_path = OUT_DIR / f"respeaker_check_{timestamp}.wav"

    banner("ReSpeaker 录音 + 转文字自检开始")
    log(f"device={device}")
    log(f"model={args.model}")
    result = record_sounddevice(device, args.seconds, args.sample_rate, args.channels, wav_path)
    banner("录音结果")
    for key, value in result.items():
        log(f"{key}: {value}")

    if not args.skip_asr:
        candidates = [("original", wav_path)]
        ratio = float(result.get("clock_ratio") or 1.0)
        if ratio > 1.5:
            fixed_rate = int(result.get("effective_sample_rate_estimate") or 8000)
            fixed_rate = max(4000, min(args.sample_rate, fixed_rate))
            fixed_path = wav_path.with_name(f"{wav_path.stem}.asr_header_{fixed_rate}.wav")
            rewrite_wav_header_sample_rate(wav_path, fixed_path, fixed_rate)
            candidates.append((f"header_fixed_{fixed_rate}", fixed_path))
            banner("ASR 修正版音频已生成")
            log(f"fixed_path: {fixed_path}")
            log(f"fixed_stats: {wav_stats(fixed_path)}")

        for label, candidate_path in candidates:
            banner(f"ASR 转文字开始：{label}")
            started = time.perf_counter()
            text = asyncio.run(transcribe(candidate_path, args.model))
            elapsed = time.perf_counter() - started
            log(f"asr_file: {candidate_path}")
            log(f"asr_elapsed_sec: {round(elapsed, 3)}")
            log("TEXT_START")
            log(text)
            log("TEXT_END")
            if not text.strip():
                log("ASR_WARNING: 转写为空，请确认现场人声足够清楚，或切换 medium/云端 ASR。")

    banner("自检完成")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
