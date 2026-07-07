"""Subprocess runner for WhisperX final meeting transcription.

This module is executed with the isolated WhisperX virtualenv. Keep imports
local to this process so the FastAPI runtime does not need WhisperX installed.
"""
from __future__ import annotations

import argparse
import json
import time


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-json", required=True)
    parser.add_argument("--output-json", required=True)
    args = parser.parse_args()

    started = time.time()
    with open(args.input_json, "r", encoding="utf-8") as f:
        cfg = json.load(f)

    import whisperx

    audio_file = str(cfg["audio_file"])
    model_path = str(cfg["model_path"])
    device = str(cfg.get("device") or "cuda")
    compute_type = str(cfg.get("compute_type") or "float16")
    language = str(cfg.get("language") or "zh")
    batch_size = int(cfg.get("batch_size") or 8)

    model = whisperx.load_model(
        model_path,
        device,
        compute_type=compute_type,
        language=language,
    )
    audio = whisperx.load_audio(audio_file)
    result = model.transcribe(audio, batch_size=batch_size)

    model_a, metadata = whisperx.load_align_model(language_code=language, device=device)
    aligned = whisperx.align(
        result.get("segments", []),
        model_a,
        metadata,
        audio,
        device,
        return_char_alignments=False,
    )
    payload = {
        "ok": True,
        "audio_file": audio_file,
        "model_path": model_path,
        "device": device,
        "compute_type": compute_type,
        "batch_size": batch_size,
        "language": result.get("language") or language,
        "duration_seconds": round(time.time() - started, 2),
        "segments": aligned.get("segments", []),
    }
    with open(args.output_json, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
