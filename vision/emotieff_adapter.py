"""
EmotiEffLib Adapter — feeds the same face crop into EmotiEffLib for
emotion recognition + valence/arousal (via multi-task model).

Runs in parallel with the existing EmotionModel — does NOT replace it.

Usage:
    from vision.emotieff_adapter import EmotiEffAdapter

    adapter = EmotiEffAdapter()
    result = adapter.predict(face_crop_bgr)
    # → {
    #     "emotion": "Happiness",
    #     "confidence": 0.87,
    #     "probabilities": {...},
    #     "valence": 0.52,
    #     "arousal": 0.31,
    #     "action_units": None,
    #   }

Printing is handled externally via print_comparison() so that both the
existing EmotionModel and EmotiEffLib results appear in one block.
"""

from __future__ import annotations

import threading
from typing import Dict, List, Optional

import cv2
import numpy as np

from utils.logger import get_logger
from vision.emotiefflib import EmotiEffLibRecognizer

logger = get_logger(__name__)

# ═══════════════════════════════════════════════════════════════
#  Configuration
# ═══════════════════════════════════════════════════════════════

DEFAULT_MODEL = "enet_b0_8_va_mtl"  # multi-task: emotions + valence + arousal

# Available models (see EmotiEffLib.facial_analysis.get_model_list)
_AVAILABLE_MODELS = [
    "enet_b0_8_best_vgaf",
    "enet_b0_8_best_afew",
    "enet_b2_8",
    "enet_b0_8_va_mtl",
    "enet_b2_7",
    "mbf_va_mtl",
    "mobilevit_va_mtl",
]


# ═══════════════════════════════════════════════════════════════
#  EmotiEffAdapter
# ═══════════════════════════════════════════════════════════════

class EmotiEffAdapter:
    """
    Thin wrapper around EmotiEffLib that accepts the SAME face crop
    (BGR uint8 numpy array) used by the existing EmotionModel.

    Preprocessing:
      - Converts BGR → RGB (EmotiEffLib expects RGB)
      - Resize + normalize handled internally by EmotiEffLib

    Thread-safe — internal lock protects the inference session.
    """

    def __init__(
        self,
        model_name: str = DEFAULT_MODEL,
    ) -> None:
        """
        Args:
            model_name: One of the supported EmotiEffLib models.
                        Use a _mtl model for valence + arousal.
        """
        self._model_name = model_name
        self._is_mtl = "_mtl" in model_name

        self._recognizer = None
        self._lock = threading.Lock()
        self._loaded = False

        self._load()

    # ── Load ────────────────────────────────────────────────

    def _load(self) -> None:
        """Initialize the EmotiEffLib recognizer (ONNX, local model)."""
        try:
            self._recognizer = EmotiEffLibRecognizer(
                engine="onnx",
                model_name=self._model_name,
            )
            self._loaded = True
            logger.info(
                "✅ EmotiEffAdapter loaded: model=%s engine=onnx mtl=%s",
                self._model_name,
                self._is_mtl,
            )
        except Exception as e:
            logger.error("EmotiEffAdapter load failed: %s", e)
            self._loaded = False

    @property
    def loaded(self) -> bool:
        return self._loaded

    @property
    def model_name(self) -> str:
        return self._model_name

    # ── Predict ─────────────────────────────────────────────

    def predict(self, face_crop: np.ndarray) -> Optional[Dict]:
        """
        Run EmotiEffLib inference on a face crop.

        Args:
            face_crop: BGR uint8 image (the SAME image fed to EmotionModel.predict).

        Returns:
            Dict with keys:
              - emotion       (str)
              - confidence    (float)
              - probabilities (Dict[str, float])
              - valence       (float | None)   — if MTL model
              - arousal       (float | None)   — if MTL model
              - action_units  (None)           — not supported by EmotiEffLib
              - model         (str)            — model name used
            Or None if inference fails.
        """
        if not self._loaded:
            logger.warning("EmotiEffAdapter: not loaded")
            return None

        if face_crop is None or face_crop.size == 0:
            return None

        with self._lock:
            try:
                return self._run_inference(face_crop)
            except Exception as e:
                logger.error("EmotiEffAdapter inference error: %s", e)
                return None

    # ── Internal Inference ──────────────────────────────────

    def _run_inference(self, face_crop: np.ndarray) -> Dict:
        """
        Run inference and return a standardized dict.

        face_crop: BGR uint8 numpy array.
        """
        # EmotiEffLib expects RGB input
        face_rgb = cv2.cvtColor(face_crop, cv2.COLOR_BGR2RGB)

        # predict_emotions with logits=False → probabilities for emotion columns
        emotions, scores = self._recognizer.predict_emotions(face_rgb, logits=False)
        scores = np.atleast_2d(scores)  # ensure (1, N)

        if self._is_mtl:
            # scores shape: (1, num_emotions + 2)
            # First num_emotions columns: softmax probabilities
            # Last 2 columns: raw valence, arousal
            emotion_probs = scores[0, :-2]
            va_raw = scores[0, -2:]
        else:
            emotion_probs = scores[0]
            va_raw = None

        top_idx = int(np.argmax(emotion_probs))
        emotion_label = self._recognizer.idx_to_emotion_class[top_idx]
        confidence = float(emotion_probs[top_idx])

        # Build probabilities dict
        prob_dict = {
            self._recognizer.idx_to_emotion_class[i]: float(p)
            for i, p in enumerate(emotion_probs)
        }

        result: Dict = {
            "emotion": emotion_label,
            "confidence": round(confidence, 4),
            "probabilities": prob_dict,
            "model": self._model_name,
        }

        # Valence & arousal (MTL models only)
        if va_raw is not None:
            # MTL models typically output VA via tanh in [-1, 1]
            result["valence"] = round(float(np.clip(va_raw[0], -1.0, 1.0)), 4)
            result["arousal"] = round(float(np.clip(va_raw[1], -1.0, 1.0)), 4)
        else:
            result["valence"] = None
            result["arousal"] = None

        # Action units — not supported
        result["action_units"] = None

        return result

    # ── Debug Output ─────────────────────────────────────────

    # (printing is handled by the module-level print_comparison() function)


# ═══════════════════════════════════════════════════════════════
#  Comparison Printer — prints both models side by side
# ═══════════════════════════════════════════════════════════════

def print_comparison(
    frame_count: int,
    current_result: Optional[Dict],
    emotieff_result: Optional[Dict],
) -> None:
    """
    Print a side-by-side comparison of the current EmotionModel
    and EmotiEffLib results.  Called every 30 frames by the caller.
    """
    logger.info("=" * 60)

    # ── Current Model ──
    logger.info("Current Model (EmotionModel)")
    if current_result:
        logger.info("  emotion:    %s", current_result.get("emotion", "?"))
        logger.info("  confidence: %.4f", current_result.get("confidence", 0.0))
    else:
        logger.info("  (no result)")

    # ── EmotiEffLib ──
    logger.info("EmotiEffLib")
    if emotieff_result:
        logger.info("  emotion:      %s", emotieff_result.get("emotion", "?"))
        logger.info("  confidence:   %.4f", emotieff_result.get("confidence", 0.0))
        if emotieff_result.get("valence") is not None:
            logger.info("  valence:      %+.4f", emotieff_result["valence"])
        if emotieff_result.get("arousal") is not None:
            logger.info("  arousal:      %+.4f", emotieff_result["arousal"])
        au = emotieff_result.get("action_units")
        logger.info("  action_units: %s", au if au is not None else "N/A")
    else:
        logger.info("  (no result)")

    logger.info("=" * 60)


# ═══════════════════════════════════════════════════════════════
#  Singleton
# ═══════════════════════════════════════════════════════════════

_emotieff_adapter: Optional[EmotiEffAdapter] = None


def get_emotieff_adapter(
    model_name: str = DEFAULT_MODEL,
) -> EmotiEffAdapter:
    """
    Get or create the singleton EmotiEffAdapter instance.

    Args:
        model_name: EmotiEffLib model name (default: enet_b0_8_va_mtl).

    Returns:
        EmotiEffAdapter instance.
    """
    global _emotieff_adapter
    if _emotieff_adapter is None:
        _emotieff_adapter = EmotiEffAdapter(model_name=model_name)
    return _emotieff_adapter


# ═══════════════════════════════════════════════════════════════
#  Quick test
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("Testing EmotiEffAdapter...")
    adapter = EmotiEffAdapter()

    # Create a dummy face crop (224x224 BGR)
    dummy_face = np.random.randint(0, 255, (224, 224, 3), dtype=np.uint8)

    result = adapter.predict(dummy_face)
    if result:
        print(f"\nResult: {result['emotion']} (conf={result['confidence']:.4f})")
        if result.get("valence") is not None:
            print(f"Valence: {result['valence']:+.4f}, Arousal: {result['arousal']:+.4f}")
    else:
        print("Inference failed.")
