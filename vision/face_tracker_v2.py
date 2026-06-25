"""
FaceTrackerV2 — High-stability multi-face tracker.
═══════════════════════════════════════════════════

Architecture:
  InsightFace SCRFD  →  face detection + ArcFace embedding
  KalmanFilter        →  motion prediction (8-state)
  ByteTrack matching  →  two-stage: IoU + cosine similarity
  Re-ID cache         →  30-frame retention for lost targets
  EMA smoothing       →  bbox jitter reduction

Usage:
    tracker = FaceTrackerV2()
    tracks = tracker.update(frame_bgr)
    # → [{id, bbox, embedding, landmarks_106, face_center, is_tracked}]
"""

from __future__ import annotations

import math
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np

from utils.logger import get_logger

logger = get_logger(__name__)

# ═══════════════════════════════════════════════════════════════
#  Constants
# ═══════════════════════════════════════════════════════════════

MAX_LOST_FRAMES = 30       # Frames before deleting a lost track
IOU_THRESHOLD = 0.45       # Minimum IoU for first-stage match
COSINE_THRESHOLD = 0.50    # Minimum cosine similarity for second-stage
HIGH_CONF = 0.60           # Detection confidence threshold for "high"
LOW_CONF = 0.30            # Minimum detection confidence
EMA_ALPHA = 0.35           # EMA smoothing factor for bbox
EMBEDDING_CACHE_SIZE = 10  # Number of embeddings to cache per track


# ═══════════════════════════════════════════════════════════════
#  Kalman Filter (8-state: x, y, w, h, vx, vy, vw, vh)
# ═══════════════════════════════════════════════════════════════

class KalmanBoxTracker:
    """
    8-state Kalman filter for bounding box tracking.

    State vector:  [cx, cy, w, h, vx, vy, vw, vh]
    Measurement:  [cx, cy, w, h]

    Motion model: constant velocity (linear)

    The Kalman filter predicts where the bounding box SHOULD be
    based on its velocity history. This prediction is used to:
      1. Compute IoU with new detections (even if the face moved)
      2. Fill in missing frames when detection fails temporarily
      3. Smooth the trajectory by fusing prediction + measurement
    """

    def __init__(self, bbox: Tuple[float, float, float, float]):
        # bbox: (x1, y1, x2, y2)
        x1, y1, x2, y2 = bbox
        cx = (x1 + x2) / 2.0
        cy = (y1 + y2) / 2.0
        w = x2 - x1
        h = y2 - y1

        self.kf = cv2.KalmanFilter(8, 4)  # 8 state, 4 measurement

        # Transition matrix (constant velocity)
        # x(t+1) = x(t) + vx(t)
        self.kf.transitionMatrix = np.array([
            [1, 0, 0, 0, 1, 0, 0, 0],  # cx = cx + vx
            [0, 1, 0, 0, 0, 1, 0, 0],  # cy = cy + vy
            [0, 0, 1, 0, 0, 0, 1, 0],  # w  = w  + vw
            [0, 0, 0, 1, 0, 0, 0, 1],  # h  = h  + vh
            [0, 0, 0, 0, 1, 0, 0, 0],  # vx = vx
            [0, 0, 0, 0, 0, 1, 0, 0],  # vy = vy
            [0, 0, 0, 0, 0, 0, 1, 0],  # vw = vw
            [0, 0, 0, 0, 0, 0, 0, 1],  # vh = vh
        ], np.float32)

        # Measurement matrix (we only observe position, not velocity)
        self.kf.measurementMatrix = np.array([
            [1, 0, 0, 0, 0, 0, 0, 0],
            [0, 1, 0, 0, 0, 0, 0, 0],
            [0, 0, 1, 0, 0, 0, 0, 0],
            [0, 0, 0, 1, 0, 0, 0, 0],
        ], np.float32)

        # Process noise: ByteTrack-tuned
        # Position noise higher → trust measurement more for position
        # Velocity noise lower → smooth velocity estimates
        self.kf.processNoiseCov = np.eye(8, dtype=np.float32) * 0.01
        self.kf.processNoiseCov[:4, :4] *= 1.0   # position process noise = 0.01
        self.kf.processNoiseCov[4:, 4:] *= 0.01  # velocity process noise = 0.0001

        # Measurement noise: standard for face detection
        self.kf.measurementNoiseCov = np.eye(4, dtype=np.float32) * 1.0

        # Initial error covariance
        # Position: initially somewhat uncertain (×10). Velocity: initially very uncertain (×1000).
        self.kf.errorCovPost = np.eye(8, dtype=np.float32)
        self.kf.errorCovPost[:4, :4] *= 10.0
        self.kf.errorCovPost[4:, 4:] *= 1000.0

        # Initial state
        self.kf.statePost = np.array(
            [[cx], [cy], [w], [h], [0], [0], [0], [0]], np.float32
        )

        self._prediction = self.kf.statePost[:4].copy()
        self._time_since_update = 0

    def predict(self) -> np.ndarray:
        """Predict next state. Returns [cx, cy, w, h]."""
        self._prediction = self.kf.predict()[:4]
        self._time_since_update += 1
        return self._prediction.copy()

    def update(self, bbox: Tuple[float, float, float, float]) -> np.ndarray:
        """Update with new measurement. Returns corrected [cx, cy, w, h]."""
        x1, y1, x2, y2 = bbox
        measurement = np.array([
            [(x1 + x2) / 2.0], [(y1 + y2) / 2.0],
            [x2 - x1], [y2 - y1],
        ], np.float32)
        corrected = self.kf.correct(measurement)[:4]
        self._time_since_update = 0
        return corrected

    @property
    def state(self) -> np.ndarray:
        """Current state [cx, cy, w, h]."""
        return self.kf.statePost[:4].copy()

    @property
    def prediction(self) -> np.ndarray:
        """Latest prediction [cx, cy, w, h]."""
        return self._prediction.copy()

    @property
    def time_since_update(self) -> int:
        return self._time_since_update

    def get_bbox(self) -> Tuple[float, float, float, float]:
        """Get predicted bbox as (x1, y1, x2, y2)."""
        cx, cy, w, h = self._prediction.flatten()
        return (cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2)


# ═══════════════════════════════════════════════════════════════
#  Tracklet (single tracked face)
# ═══════════════════════════════════════════════════════════════

@dataclass
class Tracklet:
    """A single tracked face identity."""

    track_id: int
    kalman: KalmanBoxTracker
    bbox: Tuple[float, float, float, float]  # current smoothed bbox
    bbox_raw: Tuple[float, float, float, float]  # raw detection bbox
    confidence: float = 0.0
    embedding: Optional[np.ndarray] = None       # latest ArcFace embedding
    embedding_cache: deque = field(default_factory=lambda: deque(maxlen=EMBEDDING_CACHE_SIZE))
    landmarks_5: Optional[np.ndarray] = None     # 5-point facial landmarks
    landmarks_106: Optional[np.ndarray] = None   # 106-point facial landmarks
    is_active: bool = True                        # currently being tracked
    is_confirmed: bool = False                     # confirmed once a high-conf face is detected
    confirm_frames: int = 0                        # consecutive high-conf detections
    lost_frames: int = 0                          # consecutive frames without match
    total_frames: int = 0                         # total frames this track existed
    _interp_bbox: Optional[Tuple] = None           # interpolated bbox during brief loss
    head_yaw: float = 0.0
    head_pitch: float = 0.0
    head_roll: float = 0.0

    def update(self, bbox, confidence, embedding, landmarks, landmarks_5=None):
        """Update tracklet with new detection."""
        self.bbox_raw = bbox
        self.confidence = confidence

        # Kalman update
        corrected = self.kalman.update(bbox)
        cx, cy, w, h = corrected.flatten()

        # EMA smoothing on bbox for stability
        if self.total_frames > 0:
            alpha = EMA_ALPHA
            prev = self.bbox
            new_x1 = alpha * (cx - w / 2) + (1 - alpha) * prev[0]
            new_y1 = alpha * (cy - h / 2) + (1 - alpha) * prev[1]
            new_x2 = alpha * (cx + w / 2) + (1 - alpha) * prev[2]
            new_y2 = alpha * (cy + h / 2) + (1 - alpha) * prev[3]
            self.bbox = (new_x1, new_y1, new_x2, new_y2)
        else:
            self.bbox = (cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2)

        if embedding is not None and len(embedding) > 0:
            self.embedding = embedding
            self.embedding_cache.append(embedding)

        if landmarks_5 is not None and landmarks_5.shape[0] >= 5:
            self.landmarks_5 = landmarks_5

        if landmarks is not None and landmarks.shape[0] >= 60:
            self.landmarks_106 = landmarks

        self.lost_frames = 0
        self.is_active = True
        self.total_frames += 1
        self.confirm_frames += 1
        # Confirmed means detector-stable only.
        if self.confirm_frames >= 1:
            self.is_confirmed = True

    def predict(self) -> Tuple[float, float, float, float]:
        """Kalman-predicted bbox for matching."""
        return self.kalman.get_bbox()

    def mark_lost(self, predicted_already: bool = False):
        """Mark as lost this frame."""
        self.lost_frames += 1
        # update() already predicts once for every active track before matching.
        # Re-predicting on the same frame pushes the bbox too far.
        pred = self.kalman.prediction if predicted_already else self.kalman.predict()
        # Interpolate bbox during brief loss for display continuity.
        cx, cy, w, h = pred.flatten()
        self._interp_bbox = (cx - w/2, cy - h/2, cx + w/2, cy + h/2)
        self.bbox = self._interp_bbox
        if self.lost_frames > MAX_LOST_FRAMES:
            self.is_active = False
            self.is_confirmed = False

    @property
    def face_center(self) -> Tuple[float, float]:
        """Center of the face bbox."""
        x1, y1, x2, y2 = self.bbox
        return ((x1 + x2) / 2.0, (y1 + y2) / 2.0)

    @property
    def avg_embedding(self) -> Optional[np.ndarray]:
        """Average of cached embeddings for robust matching."""
        if not self.embedding_cache:
            return self.embedding
        return np.mean(list(self.embedding_cache), axis=0)


# ═══════════════════════════════════════════════════════════════
#  Matcher (ByteTrack two-stage)
# ═══════════════════════════════════════════════════════════════

class ByteTrackMatcher:
    """
    Two-stage matching as in ByteTrack.

    Stage 1: High-confidence detections matched to tracks via IoU.
    Stage 2: Remaining low-confidence detections matched via cosine similarity
             of ArcFace embeddings with unmatched track embeddings.
    """

    @staticmethod
    def iou(a: Tuple, b: Tuple) -> float:
        """Intersection over Union."""
        x1 = max(a[0], b[0])
        y1 = max(a[1], b[1])
        x2 = min(a[2], b[2])
        y2 = min(a[3], b[3])
        if x2 <= x1 or y2 <= y1:
            return 0.0
        inter = (x2 - x1) * (y2 - y1)
        area_a = (a[2] - a[0]) * (a[3] - a[1])
        area_b = (b[2] - b[0]) * (b[3] - b[1])
        return inter / (area_a + area_b - inter + 1e-8)

    @staticmethod
    def cosine_sim(a: np.ndarray, b: np.ndarray) -> float:
        """Cosine similarity between two normalized vectors."""
        if a is None or b is None or len(a) == 0 or len(b) == 0:
            return 0.0
        a_norm = a / (np.linalg.norm(a) + 1e-8)
        b_norm = b / (np.linalg.norm(b) + 1e-8)
        return float(np.dot(a_norm, b_norm))

    @staticmethod
    def hungarian_match(cost_matrix: np.ndarray) -> List[Tuple[int, int]]:
        """
        Hungarian algorithm for optimal assignment.
        Returns list of (track_idx, det_idx) pairs.
        """
        if cost_matrix.size == 0:
            return []

        # Negate IoU for cost (Hungarian minimizes, we want max IoU)
        from scipy.optimize import linear_sum_assignment
        try:
            row_ind, col_ind = linear_sum_assignment(-cost_matrix)
            return list(zip(row_ind, col_ind))
        except Exception:
            # Fallback: greedy matching
            matches = []
            used_cols = set()
            for i in range(cost_matrix.shape[0]):
                best_j, best_val = -1, -1
                for j in range(cost_matrix.shape[1]):
                    if j not in used_cols and cost_matrix[i, j] > best_val:
                        best_val = cost_matrix[i, j]
                        best_j = j
                if best_j >= 0 and best_val > 0:
                    matches.append((i, best_j))
                    used_cols.add(best_j)
            return matches

    def match_stage1(
        self,
        tracks: List[Tracklet],
        detections: List[Dict],
    ) -> Tuple[List[Tuple[int, int]], List[int], List[int]]:
        """
        Stage 1: IoU matching for high-confidence detections.

        Returns:
            matches: [(track_idx, det_idx)]
            unmatched_tracks: [track_idx]
            unmatched_dets: [det_idx]
        """
        if not tracks or not detections:
            return [], list(range(len(tracks))), list(range(len(detections)))

        n_tracks = len(tracks)
        n_dets = len(detections)
        cost = np.zeros((n_tracks, n_dets), dtype=np.float32)

        for i, track in enumerate(tracks):
            pred_bbox = track.predict()
            for j, det in enumerate(detections):
                cost[i, j] = self.iou(pred_bbox, det['bbox'])

        # Only match pairs above threshold
        valid = cost > IOU_THRESHOLD
        if not valid.any():
            return [], list(range(n_tracks)), list(range(n_dets))

        # Mask invalid pairs with 0 (won't be matched)
        cost[~valid] = 0.0

        raw_matches = self.hungarian_match(cost)
        matches = [(i, j) for i, j in raw_matches if cost[i, j] > IOU_THRESHOLD]

        matched_track_idx = {i for i, _ in matches}
        matched_det_idx = {j for _, j in matches}
        unmatched_tracks = [i for i in range(n_tracks) if i not in matched_track_idx]
        unmatched_dets = [j for j in range(n_dets) if j not in matched_det_idx]

        return matches, unmatched_tracks, unmatched_dets

    def match_stage2(
        self,
        tracks: List[Tracklet],
        unmatched_track_indices: List[int],
        low_conf_detections: List[Dict],
    ) -> List[Tuple[int, int]]:
        """
        Stage 2: Cosine similarity matching for low-confidence detections.

        Uses the average cached embedding of each track for robustness
        against single-frame embedding noise.
        """
        if not unmatched_track_indices or not low_conf_detections:
            return []

        n = len(unmatched_track_indices)
        m = len(low_conf_detections)
        cost = np.zeros((n, m), dtype=np.float32)

        for i, ti in enumerate(unmatched_track_indices):
            track = tracks[ti]
            emb = track.avg_embedding
            if emb is None:
                continue
            for j, det in enumerate(low_conf_detections):
                det_emb = det.get('embedding')
                if det_emb is not None:
                    cost[i, j] = self.cosine_sim(emb, det_emb)

        valid = cost > COSINE_THRESHOLD
        if not valid.any():
            return []

        cost[~valid] = 0.0
        raw_matches = self.hungarian_match(cost)
        return [(unmatched_track_indices[i], low_conf_detections[j]['_orig_idx'])
                for i, j in raw_matches if cost[i, j] > COSINE_THRESHOLD]


# ═══════════════════════════════════════════════════════════════
#  FaceTrackerV2 (main class)
# ═══════════════════════════════════════════════════════════════

class FaceTrackerV2:
    """
    High-stability multi-face tracker.

    Pipeline per frame:
      1. SCRFD detection → bounding boxes + confidence
      2. ArcFace → 512-d embeddings
      3. 2D106 → 106-point facial landmarks
      4. Kalman predict → where SHOULD each track be?
      5. ByteTrack Stage 1 → IoU match high-conf dets to tracks
      6. ByteTrack Stage 2 → cosine match low-conf dets to unmatched tracks
      7. Kalman update → corrected positions
      8. New tracks → unmatched high-conf detections
      9. Lost track cleanup → remove after MAX_LOST_FRAMES
      10. Output → structured track data

    Usage:
        tracker = FaceTrackerV2()
        while True:
            frame = camera.read()
            tracks = tracker.update(frame)
            for t in tracks:
                print(f"ID={t['id']} bbox={t['bbox']}")
    """

    def __init__(
        self,
        detection_size: Tuple[int, int] = (640, 640),
        det_thresh: float = 0.35,
        track_thresh: float = 0.55,
        max_lost: int = MAX_LOST_FRAMES,
    ) -> None:
        self._det_size = detection_size
        self._det_thresh = det_thresh
        self._track_thresh = track_thresh
        self._max_lost = max_lost

        # InsightFace
        self._app = None
        self._available = False
        self._loaded = False

        # Tracking state
        self._tracks: Dict[int, Tracklet] = {}  # active tracks
        self._reid_cache: Dict[int, Tracklet] = {}  # lost but remembered
        self._next_id: int = 0
        self._matcher = ByteTrackMatcher()

        # Stats
        self._frame_count: int = 0
        self._total_time: float = 0.0
        self._detect_time: float = 0.0

        # Display-selected track metadata only.
        self._primary_id: Optional[int] = None

        self._initialize()

    # ── Initialize ────────────────────────────────────────────

    def _initialize(self) -> None:
        """Load InsightFace models."""
        try:
            import insightface
            from insightface.app import FaceAnalysis

            self._app = FaceAnalysis(
                name="buffalo_l",
                providers=["CPUExecutionProvider"],
                allowed_modules=["detection", "landmark_2d_106", "recognition"],
            )
            self._app.prepare(ctx_id=-1, det_size=self._det_size,
                              det_thresh=self._det_thresh)
            self._available = True
            self._loaded = True
            logger.info(
                "✅ FaceTrackerV2: SCRFD + 2D106 + ArcFace loaded "
                "(det_size=%s, det_thresh=%.2f, track_thresh=%.2f)",
                self._det_size, self._det_thresh, self._track_thresh
            )
        except ImportError:
            logger.warning("⚠️  InsightFace not installed. FaceTrackerV2 unavailable.")
            self._available = False
        except Exception as e:
            logger.error("FaceTrackerV2 init failed: %s", e)
            self._available = False

    # ── Properties ────────────────────────────────────────────

    @property
    def available(self) -> bool:
        return self._available

    @property
    def loaded(self) -> bool:
        return self._loaded

    @property
    def active_tracks(self) -> List[Tracklet]:
        return [t for t in self._tracks.values() if t.is_active]

    @property
    def primary_track(self) -> Optional[Tracklet]:
        if self._primary_id is not None:
            return self._tracks.get(self._primary_id)
        return None

    @property
    def avg_time_ms(self) -> float:
        if self._frame_count == 0:
            return 0.0
        return self._total_time / self._frame_count * 1000

    # ── Main update ───────────────────────────────────────────

    def update(self, frame_bgr: np.ndarray) -> List[Dict]:
        """
        Process one frame. Returns list of tracked faces.

        Each result dict:
            id: int              — unique track ID
            bbox: (x1,y1,x2,y2) — smoothed bounding box
            confidence: float    — detection confidence
            embedding: ndarray   — ArcFace 512-d embedding
            landmarks_106: ndarray — 106 facial landmarks
            face_center: (cx,cy) — center of face
            is_primary: bool     — is this the gimbal target?
            lost_frames: int      — consecutive frames lost
            head_yaw/pitch/roll  — head pose
        """
        if not self._available:
            return []

        self._frame_count += 1
        t0 = time.monotonic()

        # ── Step 1: Detection ─────────────────────
        try:
            raw_faces = self._app.get(frame_bgr)
        except Exception as e:
            logger.debug("Detection error: %s", str(e)[:80])
            return self._predict_only()

        t1 = time.monotonic()
        self._detect_time += t1 - t0

        # ── Step 2: Parse detections ─────────────
        high_dets, low_dets = [], []
        for f in raw_faces:
            bbox = tuple(float(v) for v in f.bbox)
            conf = float(f.det_score) if hasattr(f, 'det_score') else 0.9
            emb = None
            if hasattr(f, 'embedding') and f.embedding is not None:
                emb = f.embedding.copy()
            lm5 = None
            if hasattr(f, 'kps') and f.kps is not None:
                kps = np.asarray(f.kps, dtype=np.float32)
                if kps.ndim == 2 and kps.shape[0] >= 5 and kps.shape[1] >= 2:
                    lm5 = kps[:, :2].copy()
            lm = None
            if hasattr(f, 'landmark_2d_106') and f.landmark_2d_106 is not None:
                lm_2d = f.landmark_2d_106
                if lm_2d.shape[1] == 2:
                    z_col = np.zeros((lm_2d.shape[0], 1), dtype=lm_2d.dtype)
                    lm = np.hstack([lm_2d, z_col])
                else:
                    lm = lm_2d

            det = {'bbox': bbox, 'confidence': conf, 'embedding': emb,
                   'landmarks': lm, 'landmarks_5': lm5}
            if conf >= self._track_thresh:
                high_dets.append(det)
            elif conf >= self._det_thresh:
                det['_orig_idx'] = len(low_dets)
                low_dets.append(det)

        # ── Step 3: Kalman predict all tracks ────
        for track in self._tracks.values():
            track.kalman.predict()

        # ── Step 4: ByteTrack Stage 1 (IoU) ──────
        active_tracks = [t for t in self._tracks.values() if t.is_active]
        stage1_matches, unmatched_track_indices, unmatched_high_dets = \
            self._matcher.match_stage1(active_tracks, high_dets)

        # Apply matches
        for ti, di in stage1_matches:
            track = active_tracks[ti]
            det = high_dets[di]
            track.update(
                det['bbox'], det['confidence'],
                det['embedding'], det['landmarks'], det.get('landmarks_5'),
            )

        # ── Step 5: ByteTrack Stage 2 (Cosine) ──
        # match_stage2 returns di as the _orig_idx into low_dets
        stage2_matches = self._matcher.match_stage2(
            active_tracks, unmatched_track_indices, low_dets
        )
        for ti, di in stage2_matches:
            track = active_tracks[ti]
            det = low_dets[di] if 0 <= di < len(low_dets) else None
            if det:
                track.update(
                    det['bbox'], det['confidence'],
                    det['embedding'], det['landmarks'], det.get('landmarks_5'),
                )

        # ── Step 6: New tracks ──────────────────
        matched_high_idx = {di for _, di in stage1_matches}
        new_track_ids = set()
        for j in unmatched_high_dets:
            if j not in matched_high_idx:
                det = high_dets[j]
                kalman = KalmanBoxTracker(det['bbox'])
                track = Tracklet(
                    track_id=self._next_id, kalman=kalman,
                    bbox=det['bbox'], bbox_raw=det['bbox'],
                    confidence=det['confidence'],
                    embedding=det['embedding'],
                    landmarks_5=det['landmarks_5'],
                    landmarks_106=det['landmarks'],
                    is_confirmed=True,
                    confirm_frames=1,
                    total_frames=1,
                )
                self._tracks[self._next_id] = track
                new_track_ids.add(track.track_id)
                self._next_id += 1

        # ── Step 7: Mark lost tracks ────────────
        matched_track_global = set()
        for ti, _ in stage1_matches:
            matched_track_global.add(active_tracks[ti].track_id)
        for ti, _ in stage2_matches:
            matched_track_global.add(active_tracks[ti].track_id)

        for track in self._tracks.values():
            if track.track_id in new_track_ids:
                continue
            if track.is_active and track.track_id not in matched_track_global:
                track.mark_lost(predicted_already=True)
                if not track.is_active:
                    # Move to Re-ID cache
                    self._reid_cache[track.track_id] = track
                    if self._primary_id == track.track_id:
                        self._primary_id = None

        # ── Step 8: Cleanup expired Re-ID cache ──
        expired = [tid for tid, t in self._reid_cache.items()
                   if t.lost_frames > self._max_lost]
        for tid in expired:
            del self._reid_cache[tid]
            if tid in self._tracks:
                del self._tracks[tid]

        # ── Step 9: Build output ────────────────
        t2 = time.monotonic()
        self._total_time += t2 - t0

        results = []
        for track in self._tracks.values():
            if not track.is_active:
                continue
            if not track.is_confirmed:
                continue  # skip unconfirmed tracks — only output confirmed ones
            has_current_detection = track.lost_frames == 0
            results.append({
                'id': track.track_id,
                'bbox': track.bbox,
                'bbox_raw': track.bbox_raw,
                'confidence': track.confidence,
                'embedding': track.embedding,
                'landmarks_5': track.landmarks_5 if has_current_detection else None,
                'landmarks_106': track.landmarks_106 if has_current_detection else None,
                'face_center': track.face_center,
                'is_primary': False,
                'lost_frames': track.lost_frames,
                'total_frames': track.total_frames,
                'head_yaw': track.head_yaw,
                'head_pitch': track.head_pitch,
                'head_roll': track.head_roll,
            })

        return results

    # ── Fallback: predict-only (no detections available) ──────

    def _predict_only(self) -> List[Dict]:
        """Return Kalman-predicted positions when detection fails."""
        results = []
        for track in self._tracks.values():
            track.mark_lost()
            if track.is_active:
                pred_bbox = track.kalman.get_bbox()
                results.append({
                    'id': track.track_id,
                    'bbox': pred_bbox,
                    'bbox_raw': pred_bbox,
                    'confidence': 0.0,
                    'embedding': track.embedding,
                    'landmarks_5': None,
                    'landmarks_106': None,
                    'face_center': track.face_center,
                    'is_primary': False,
                    'lost_frames': track.lost_frames,
                    'total_frames': track.total_frames,
                    'head_yaw': 0.0,
                    'head_pitch': 0.0,
                    'head_roll': 0.0,
                })
        return results

    # ── Public helpers ────────────────────────────────────────

    def set_primary(self, track_id: int) -> None:
        """No-op: vision does not select control targets."""
        return None

    def reset(self) -> None:
        """Clear all tracks."""
        self._tracks.clear()
        self._reid_cache.clear()
        self._next_id = 0
        self._primary_id = None


# ═══════════════════════════════════════════════════════════════
#  Singleton
# ═══════════════════════════════════════════════════════════════

_tracker_v2: Optional[FaceTrackerV2] = None


def get_face_tracker_v2() -> FaceTrackerV2:
    """Get or create the singleton FaceTrackerV2 instance."""
    global _tracker_v2
    if _tracker_v2 is None:
        _tracker_v2 = FaceTrackerV2()
    return _tracker_v2
