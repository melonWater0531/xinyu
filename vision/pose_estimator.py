"""
YOLO11n-Pose Estimator via ONNX Runtime.

Runs pose estimation on JPEG frames using yolo11n-pose.onnx.
Outputs person bounding boxes + 17 COCO keypoints per person.

Model expects: RGB image, 0-255 uint8, shape (1, 3, 640, 640).
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import cv2
import numpy as np

from utils.logger import get_logger

logger = get_logger(__name__)

# COCO 17 keypoints
KEYPOINT_NAMES = [
    "nose", "left_eye", "right_eye", "left_ear", "right_ear",
    "left_shoulder", "right_shoulder", "left_elbow", "right_elbow",
    "left_wrist", "right_wrist", "left_hip", "right_hip",
    "left_knee", "right_knee", "left_ankle", "right_ankle",
]

# Skeleton connections for visualization
SKELETON = [
    (5, 6), (5, 7), (7, 9), (6, 8), (8, 10),
    (5, 11), (6, 12), (11, 12),
    (11, 13), (13, 15), (12, 14), (14, 16),
    (0, 1), (0, 2), (1, 3), (2, 4),
    (0, 5), (0, 6),
]

# Face keypoint indices
FACE_INDICES = [0, 1, 2]  # nose, left_eye, right_eye


@dataclass
class Keypoint:
    x: float
    y: float
    conf: float
    name: str = ""


@dataclass
class PersonPose:
    bbox: Tuple[float, float, float, float]  # x1, y1, x2, y2
    conf: float
    keypoints: List[Keypoint] = field(default_factory=list)
    face_center: Optional[Tuple[float, float]] = None
    face_conf: float = 0.0


class PoseEstimator:
    """YOLO11n-pose via ONNX Runtime. Thread-safe."""

    def __init__(
        self,
        model_path: str = "models/yolo11n-pose.onnx",
        conf_threshold: float = 0.4,
        kp_conf_threshold: float = 0.3,
        input_size: int = 640,
    ):
        self._conf_threshold = conf_threshold
        self._kp_conf_threshold = kp_conf_threshold
        self._input_size = input_size
        self._model_path = model_path
        self._session: Optional["ort.InferenceSession"] = None
        self._lock = threading.Lock()
        self._initialized = False
        self._frame_count = 0
        self._total_time = 0.0
        self._initialize()

    def _initialize(self):
        try:
            import onnxruntime as ort
            self._session = ort.InferenceSession(
                self._model_path,
                providers=['CPUExecutionProvider'],
            )
            # Warmup
            dummy = np.zeros((1, 3, 640, 640), dtype=np.float32)
            self._session.run(None, {"images": dummy})
            self._initialized = True
            logger.info("✅ PoseEstimator: %s loaded (ONNX Runtime)", self._model_path)
        except Exception as e:
            logger.error("PoseEstimator init failed: %s", e)

    @property
    def initialized(self) -> bool:
        return self._initialized

    @property
    def avg_time_ms(self) -> float:
        if self._frame_count == 0:
            return 0.0
        return self._total_time / self._frame_count * 1000

    def detect(self, jpeg_bytes: bytes) -> List[PersonPose]:
        """Run pose estimation on a JPEG frame."""
        if not self._initialized:
            return []

        arr = np.frombuffer(jpeg_bytes, dtype=np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if img is None:
            return []

        h, w = img.shape[:2]

        # Convert BGR→RGB, resize, keep 0-255
        img_rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        if img_rgb.shape[:2] != (self._input_size, self._input_size):
            img_rgb = cv2.resize(img_rgb, (self._input_size, self._input_size))
        img_chw = np.transpose(img_rgb.astype(np.float32), (2, 0, 1))
        img_batch = img_chw[np.newaxis, ...]

        t0 = time.monotonic()
        with self._lock:
            out = self._session.run(None, {"images": img_batch})
        dt = time.monotonic() - t0
        self._frame_count += 1
        self._total_time += dt

        return self._parse_output(out[0], w, h)

    def _parse_output(self, output: np.ndarray, img_w: int, img_h: int) -> List[PersonPose]:
        """Parse YOLO11n-pose output: (1, 56, 8400)."""
        output = output[0].T  # → (8400, 56)

        boxes_xywh = output[:, :4]
        scores = output[:, 4]
        kps_flat_all = output[:, 5:]  # (8400, 51)

        # Filter by confidence
        mask = scores > self._conf_threshold
        if not mask.any():
            return []

        boxes_xywh = boxes_xywh[mask]
        scores = scores[mask]
        kps_flat_all = kps_flat_all[mask]

        # Convert cx,cy,w,h → x1,y1,x2,y2 (normalized to 0-640)
        boxes_xyxy = np.zeros_like(boxes_xywh)
        boxes_xyxy[:, 0] = np.maximum(0, boxes_xywh[:, 0] - boxes_xywh[:, 2] / 2)
        boxes_xyxy[:, 1] = np.maximum(0, boxes_xywh[:, 1] - boxes_xywh[:, 3] / 2)
        boxes_xyxy[:, 2] = np.minimum(self._input_size, boxes_xywh[:, 0] + boxes_xywh[:, 2] / 2)
        boxes_xyxy[:, 3] = np.minimum(self._input_size, boxes_xywh[:, 1] + boxes_xywh[:, 3] / 2)

        # NMS — keep boxes with area > 500px² (filter tiny false positives)
        areas = (boxes_xyxy[:, 2] - boxes_xyxy[:, 0]) * (boxes_xyxy[:, 3] - boxes_xyxy[:, 1])
        big_enough = areas > 500
        if not big_enough.any():
            return []

        boxes_xyxy = boxes_xyxy[big_enough]
        scores = scores[big_enough]
        kps_flat_all = kps_flat_all[big_enough]

        # Sort by confidence, keep top-10
        sort_idx = np.argsort(scores)[::-1][:10]
        boxes_xyxy = boxes_xyxy[sort_idx]
        scores = scores[sort_idx]
        kps_flat_all = kps_flat_all[sort_idx]

        # NMS on top candidates
        try:
            nms_indices = cv2.dnn.NMSBoxes(
                boxes_xyxy.tolist(), scores.tolist(),
                self._conf_threshold, 0.5,
            )
        except Exception:
            nms_indices = list(range(len(scores)))[:3]

        if len(nms_indices) == 0:
            nms_indices = [[0]] if len(scores) > 0 else []

        scale_x = img_w / self._input_size
        scale_y = img_h / self._input_size
        persons = []

        for idx in nms_indices[:5]:  # max 5 persons
            idx = int(idx[0]) if hasattr(idx, '__iter__') else int(idx)
            if idx >= len(scores):
                continue

            conf = float(scores[idx])
            box = boxes_xyxy[idx]

            # Denormalize bbox
            x1 = box[0] * scale_x
            y1 = box[1] * scale_y
            x2 = box[2] * scale_x
            y2 = box[3] * scale_y

            # Parse keypoints
            kps_row = kps_flat_all[idx].reshape(17, 3)
            keypoints = []
            face_points = []

            for i in range(17):
                kx = kps_row[i, 0] * scale_x
                ky = kps_row[i, 1] * scale_y
                kc = float(kps_row[i, 2])

                if kc > self._kp_conf_threshold:
                    keypoints.append(Keypoint(x=kx, y=ky, conf=kc, name=KEYPOINT_NAMES[i]))
                    if i in FACE_INDICES:
                        face_points.append((kx, ky))

            # Face center
            face_center = None
            face_conf = 0.0
            if face_points:
                face_center = (
                    sum(p[0] for p in face_points) / len(face_points),
                    sum(p[1] for p in face_points) / len(face_points),
                )
                face_confs = [kp.conf for kp in keypoints
                              if KEYPOINT_NAMES.index(kp.name) in FACE_INDICES]
                face_conf = sum(face_confs) / len(face_confs) if face_confs else 0.0

            persons.append(PersonPose(
                bbox=(x1, y1, x2, y2),
                conf=conf,
                keypoints=keypoints,
                face_center=face_center,
                face_conf=face_conf,
            ))

        return persons


# Singleton
_pose_estimator: Optional[PoseEstimator] = None


def get_pose_estimator() -> PoseEstimator:
    global _pose_estimator
    if _pose_estimator is None:
        _pose_estimator = PoseEstimator()
    return _pose_estimator
