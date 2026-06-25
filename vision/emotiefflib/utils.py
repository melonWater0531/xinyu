"""
Local model path utilities — no network download.

All ONNX models live in:  recamera_multimodal/models/emotiefflib/
"""

import os
from pathlib import Path

_MODEL_DIR = Path(__file__).resolve().parents[2] / "models" / "emotiefflib"


def get_model_path_onnx(model_name: str) -> str:
    """
    Return the local file path of an ONNX model.

    Args:
        model_name: e.g. "enet_b0_8_va_mtl"

    Returns:
        Full path to the .onnx file.
    """
    fpath = _MODEL_DIR / f"{model_name}.onnx"
    if not fpath.is_file():
        raise FileNotFoundError(
            f"Model not found: {fpath}\n"
            f"Please place {model_name}.onnx in {_MODEL_DIR}"
        )
    return str(fpath)


def get_model_path_torch(model_name: str) -> str:
    """Torch models not bundled — raise clear error."""
    raise NotImplementedError(
        "Torch engine not supported in local deployment. "
        "Use engine='onnx' instead."
    )


def get_engagement_classification_weights() -> str:
    """Engagement classification not bundled."""
    raise NotImplementedError("Engagement model not included.")
