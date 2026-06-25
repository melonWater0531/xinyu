"""
Facial emotion recognition — ONNX-only, local deployment.

Based on EmotiEffLib (https://github.com/sb-ai-lab/EmotiEffLib),
simplified for reCamera multimodal pipeline.
"""

from __future__ import absolute_import, division, print_function

from typing import List, Tuple, Union

import cv2
import numpy as np
import onnx
import onnxruntime as ort
from onnx import TensorProto, helper, numpy_helper

from .utils import get_model_path_onnx


def get_model_list() -> List[str]:
    """Available ONNX model names."""
    return [
        "enet_b0_8_best_vgaf",
        "enet_b0_8_best_afew",
        "enet_b2_8",
        "enet_b0_8_va_mtl",
        "enet_b2_7",
        "mbf_va_mtl",
        "mobilevit_va_mtl",
    ]


def get_supported_engines() -> List[str]:
    """Supported inference engines (ONNX only in local deployment)."""
    return ["onnx"]


class EmotiEffLibRecognizerBase:
    """Abstract base for emotion recognizers."""

    def __init__(self, model_name: str) -> None:
        self.is_mtl = "_mtl" in model_name

        if "_7" in model_name:
            self.idx_to_emotion_class = {
                0: "Anger",
                1: "Disgust",
                2: "Fear",
                3: "Happiness",
                4: "Neutral",
                5: "Sadness",
                6: "Surprise",
            }
        else:
            self.idx_to_emotion_class = {
                0: "Anger",
                1: "Contempt",
                2: "Disgust",
                3: "Fear",
                4: "Happiness",
                5: "Neutral",
                6: "Sadness",
                7: "Surprise",
            }

        if "mbf_" in model_name:
            self.mean = [0.5, 0.5, 0.5]
            self.std = [0.5, 0.5, 0.5]
            self.img_size = 112
        else:
            self.mean = [0.485, 0.456, 0.406]
            self.std = [0.229, 0.224, 0.225]
            if "_b2_" in model_name:
                self.img_size = 260
            elif "ddamfnet" in model_name:
                self.img_size = 112
            else:
                self.img_size = 224

        self.classifier_weights = None
        self.classifier_bias = None

    # ── Scoring ──────────────────────────────────────────

    def _get_probab(self, features: np.ndarray) -> np.ndarray:
        """Compute classification scores from feature vectors."""
        x = np.dot(features, np.transpose(self.classifier_weights)) + self.classifier_bias
        return x

    # ── Emotion Classification ───────────────────────────

    def classify_emotions(
        self, features: np.ndarray, logits: bool = True
    ) -> Tuple[List[str], np.ndarray]:
        """
        Classify emotions from extracted features.

        Args:
            features: Feature vectors (N, D).
            logits: If True, return raw scores. If False, apply softmax.

        Returns:
            (list of emotion labels, scores array).
        """
        scores = self._get_probab(features)
        if self.is_mtl:
            x = scores[:, :-2]
        else:
            x = scores
        preds = np.argmax(x, axis=1)

        if not logits:
            e_x = np.exp(x - np.max(x, axis=1)[:, np.newaxis])
            e_x = e_x / e_x.sum(axis=1)[:, None]
            if self.is_mtl:
                scores[:, :-2] = e_x
            else:
                scores = e_x

        return [self.idx_to_emotion_class[pred] for pred in preds], scores

    # ── Predict Emotions ─────────────────────────────────

    def predict_emotions(
        self, face_img: Union[np.ndarray, List[np.ndarray]], logits: bool = True
    ) -> Tuple[List[str], np.ndarray]:
        """
        Predict emotions from a face image or list of images.

        Args:
            face_img: Single face crop (H,W,3) or list of crops.
            logits: If True, return raw scores.

        Returns:
            (list of emotion labels, scores array).
        """
        features = self.extract_features(face_img)
        return self.classify_emotions(features, logits)


# ═══════════════════════════════════════════════════════════════
#  ONNX Recognizer
# ═══════════════════════════════════════════════════════════════

class EmotiEffLibRecognizerOnnx(EmotiEffLibRecognizerBase):
    """ONNX implementation of EmotiEffLib emotion recognizer."""

    def __init__(self, model_name: str = "enet_b0_8_best_vgaf") -> None:
        super().__init__(model_name)

        path = get_model_path_onnx(model_name)
        model = onnx.load(path)
        graph = model.graph
        nodes = graph.node
        gemm_node = nodes[-1]

        if gemm_node is None or len(gemm_node.input) < 3:
            raise RuntimeError("Unexpected ONNX graph: last node is not GEMM")

        weight_name = gemm_node.input[1]
        bias_name = gemm_node.input[2]
        weight_tensor = next((t for t in graph.initializer if t.name == weight_name), None)
        bias_tensor = next((t for t in graph.initializer if t.name == bias_name), None)
        self.classifier_weights = numpy_helper.to_array(weight_tensor) if weight_tensor else None
        self.classifier_bias = numpy_helper.to_array(bias_tensor) if bias_tensor else None

        if self.classifier_weights is None or self.classifier_bias is None:
            raise RuntimeError(
                f"Failed to extract classifier weights/bias from ONNX model '{model_name}'. "
                f"GEMM node found but weight/bias tensor missing from graph initializer."
            )

        # Remove the classifier head → model outputs features
        new_output_name = gemm_node.input[0]
        graph.node.remove(gemm_node)
        graph.output.remove(graph.output[0])
        new_output_shape = [None, self.classifier_weights.shape[1]]
        new_output = helper.make_tensor_value_info(
            new_output_name, TensorProto.FLOAT, new_output_shape
        )
        graph.output.append(new_output)

        model_bytes = model.SerializeToString()
        ort.set_default_logger_severity(3)
        self.ort_session = ort.InferenceSession(
            model_bytes, providers=["CPUExecutionProvider"]
        )

    def _preprocess(self, img: np.ndarray) -> np.ndarray:
        """Preprocess face image for ONNX input."""
        x = cv2.resize(img, (self.img_size, self.img_size)) / 255
        for i in range(3):
            x[..., i] = (x[..., i] - self.mean[i]) / self.std[i]
        return x.transpose(2, 0, 1).astype("float32")[np.newaxis, ...]

    def extract_features(
        self, face_img: Union[np.ndarray, List[np.ndarray]]
    ) -> np.ndarray:
        """
        Extract visual features from a face image or list of images.

        Args:
            face_img: (H,W,3) numpy array or list of arrays.

        Returns:
            Feature vectors (N, D).
        """
        if isinstance(face_img, np.ndarray):
            img_tensor = self._preprocess(face_img)
        elif isinstance(face_img, list) and all(isinstance(i, np.ndarray) for i in face_img):
            img_tensor = np.concatenate(
                [self._preprocess(img) for img in face_img], axis=0
            )
        else:
            raise TypeError("Expected np.ndarray or List[np.ndarray]")
        features = self.ort_session.run(None, {"input": img_tensor})[0]
        return features


# ═══════════════════════════════════════════════════════════════
#  Factory
# ═══════════════════════════════════════════════════════════════

def EmotiEffLibRecognizer(
    engine: str = "onnx",
    model_name: str = "enet_b0_8_best_vgaf",
    device: str = "cpu",
) -> EmotiEffLibRecognizerOnnx:
    """
    Create an EmotiEffLib recognizer instance.

    Args:
        engine: "onnx" (only option in local deployment).
        model_name: Model name from get_model_list().
        device: Ignored (CPU only).

    Returns:
        EmotiEffLibRecognizerOnnx instance.
    """
    if engine not in get_supported_engines():
        raise ValueError(
            f"Unsupported engine: {engine}. "
            f"Supported: {get_supported_engines()}"
        )
    return EmotiEffLibRecognizerOnnx(model_name)
