"""
Face crop from YuNet 5-point landmarks — optimized for emotion recognition.

Target: face occupies 70-90% of 224x224 crop, with forehead + chin + cheeks.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Tuple

import cv2
import numpy as np

from utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class FaceCropResult:
    crop: Optional[np.ndarray] = None        # 224x224 BGR
    roi: Optional[Tuple[int,int,int,int]] = None  # (x1,y1,x2,y2) in frame
    crop_size: Optional[Tuple[int,int]] = None    # (w,h) of crop before resize
    face_ratio: float = 0.0                  # estimated face/total area ratio


def extract_face_crop(
    frame: np.ndarray,
    landmarks: Optional[List[Tuple[float, float]]] = None,
    bbox: Optional[Tuple[float, float, float, float]] = None,
    output_size: int = 224,
) -> FaceCropResult:
    """
    Extract square face crop from frame. Uses landmarks only (bbox as last resort).

    Landmarks-based algorithm:
      1. Compute face center = mean of all 5 landmarks
      2. Compute face span = max distance between any two landmarks
      3. Add padding: top(forehead) + bottom(chin) + left/right(cheeks)
      4. Crop square, resize to 224x224

    Args:
        frame:      BGR uint8 image.
        landmarks:  5 points [(lx,ly),(rx,ry),(nx,ny),(lmx,lmy),(rmx,rmy)].
        bbox:       (x1,y1,x2,y2) fallback if no landmarks.
        output_size: output square size.

    Returns:
        FaceCropResult with .crop (224x224 BGR), .roi, .face_ratio.
    """
    if frame is None or frame.size == 0:
        return FaceCropResult()

    h, w = frame.shape[:2]

    # ── Method 1: Landmarks (preferred) ──
    if landmarks and len(landmarks) >= 5:
        return _crop_from_landmarks(frame, landmarks, output_size, w, h)

    # ── Method 2: Bbox fallback ──
    if bbox:
        return _crop_from_bbox(frame, bbox, output_size, w, h)

    return FaceCropResult()


def _crop_from_landmarks(
    frame: np.ndarray, landmarks: List[Tuple[float, float]],
    out_sz: int, fw: int, fh: int,
) -> FaceCropResult:
    """Crop using 5-point landmarks with forehead+chin+cheek padding."""
    pts = np.array(landmarks, dtype=np.float32)

    # Face center
    cx = float(np.mean(pts[:, 0]))
    cy = float(np.mean(pts[:, 1]))

    # Eye distance (primary scale reference)
    lx, ly = landmarks[0]  # left_eye
    rx, ry = landmarks[1]  # right_eye
    eye_dist = np.sqrt((rx - lx) ** 2 + (ry - ly) ** 2)

    # Face span: max distance between any two landmarks
    span = max(
        np.linalg.norm(pts[i] - pts[j])
        for i in range(len(pts)) for j in range(i + 1, len(pts))
    ) if len(pts) >= 3 else eye_dist * 1.5

    # Tight crop: face ~80% of 224x224
    crop_size = max(eye_dist * 3.0, span * 1.3, 64.0)

    eye_y = (ly + ry) / 2
    face_cy = eye_y + eye_dist * 0.25

    half = crop_size / 2
    x1 = int(max(0, cx - half))    # tight width
    y1 = int(max(0, face_cy - half * 0.75))  # forehead
    x2 = int(min(fw, cx + half))
    y2 = int(min(fh, face_cy + half * 1.25))  # chin

    if x2 <= x1 or y2 <= y1:
        return FaceCropResult()

    # Compute face ratio (face area / crop area)
    crop_area = (x2 - x1) * (y2 - y1)
    face_area = span * span * 0.8  # rough estimate
    face_ratio = min(1.0, face_area / crop_area)

    crop = frame[y1:y2, x1:x2]
    crop_resized = cv2.resize(crop, (out_sz, out_sz))

    return FaceCropResult(
        crop=crop_resized,
        roi=(x1, y1, x2, y2),
        crop_size=(x2 - x1, y2 - y1),
        face_ratio=round(face_ratio, 2),
    )


def _crop_from_bbox(
    frame: np.ndarray, bbox: Tuple[float, float, float, float],
    out_sz: int, fw: int, fh: int,
) -> FaceCropResult:
    """Bbox fallback: crop top 40% of bbox (face area)."""
    bx1, by1, bx2, by2 = bbox
    bw, bh = bx2 - bx1, by2 - by1
    if bw <= 0 or bh <= 0:
        return FaceCropResult()

    # Face ≈ top portion of person bbox
    cx = (bx1 + bx2) / 2
    face_top = by1 - bh * 0.05   # slight above bbox top (forehead)
    face_h = bh * 0.45           # top 45% of person = head+face
    crop_sz = max(bw * 1.1, face_h, 64.0)

    half = crop_sz / 2
    x1 = int(max(0, cx - half))
    y1 = int(max(0, face_top - crop_sz * 0.05))
    x2 = int(min(fw, cx + half))
    y2 = int(min(fh, y1 + crop_sz))

    if x2 <= x1 or y2 <= y1:
        return FaceCropResult()

    crop = frame[y1:y2, x1:x2]
    crop_resized = cv2.resize(crop, (out_sz, out_sz))

    return FaceCropResult(
        crop=crop_resized,
        roi=(x1, y1, x2, y2),
        crop_size=(x2 - x1, y2 - y1),
        face_ratio=0.5,  # rough estimate
    )


def draw_emotion_overlay(
    frame: np.ndarray,
    roi: Optional[Tuple[int, int, int, int]],
    emotion: str,
    confidence: float,
) -> np.ndarray:
    """Draw face crop box + emotion label on frame (in-place)."""
    if roi:
        x1, y1, x2, y2 = roi
        cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 200), 2)
        label = f"{emotion} {confidence:.0%}"
        cv2.putText(frame, label, (x1, y1 - 8),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 200), 2)
    return frame
