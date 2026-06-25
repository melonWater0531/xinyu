"""
EmotiEffLib — Emotion Recognition (local ONNX deployment).

Simplified from https://github.com/sb-ai-lab/EmotiEffLib
for the reCamera multimodal pipeline.

Usage:
    from vision.emotiefflib import EmotiEffLibRecognizer

    recognizer = EmotiEffLibRecognizer(model_name="enet_b0_8_va_mtl")
    emotions, scores = recognizer.predict_emotions(face_crop)
"""

from .facial_analysis import (
    EmotiEffLibRecognizer,
    EmotiEffLibRecognizerOnnx,
    get_model_list,
    get_supported_engines,
)
