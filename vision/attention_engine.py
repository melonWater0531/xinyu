"""
Attention Monitor V1 — 个人基线校准版。

核心改动:
  1. 自动/手动基线校准: 采集 10s pitch/yaw 中位数作为"正常姿势"
  2. 基线相对判断: 偏离基线 >15° 才判分心, 而非固定阈值
  3. 状态机防抖: 连续 2 窗口正常才恢复, 连续 3 窗口分心才降级
  4. 基线持久化: attention_baseline.json
  5. 持续 3s 才触发分心(短暂偏移忽略)
"""
from __future__ import annotations

import json
import math
import os
import time
from collections import deque
from dataclasses import dataclass
from typing import List, Optional, Tuple

import cv2
import numpy as np

from utils.logger import get_logger

logger = get_logger(__name__)

# ═══════════════════════════════════════════
#  Config
# ═══════════════════════════════════════════

@dataclass
class AttentionConfig:
    # 基线采集
    calibrate_duration: float = 10.0       # 初始校准采集秒数
    calibrate_min_samples: int = 10         # 最少采集帧数
    rebaseline_interval: float = 300.0      # 每 5 分钟微调基线
    rebaseline_window: float = 60.0         # 微调用最近 60s 数据

    # 基线相对阈值
    pitch_normal: float = 15.0    # |pitch-baseline| ≤ 15° = 正常
    pitch_buffer: float = 25.0    # 15-25° = 缓冲
    yaw_normal: float = 15.0      # |yaw-baseline| ≤ 15° = 正常
    yaw_buffer: float = 25.0      # 15-25° = 缓冲

    # 持续分心
    distraction_trigger: float = 3.0     # 持续偏离 >3s 才判分心

    # 稳定性
    nose_dead_zone: float = 10.0

    # 状态机
    degrade_windows: int = 3   # 连续分心 N 个窗口才降级
    recover_windows: int = 2   # 连续正常 N 个窗口才恢复

    # 评分
    window_size: float = 30.0      # 30 秒窗口
    ema_alpha: float = 0.3
    orientation_weight: float = 0.7
    stability_weight: float = 0.3

    # 持久化
    baseline_file: str = "attention_baseline.json"


# ═══════════════════════════════════════════
#  3D 人脸模型 (solvePnP)
# ═══════════════════════════════════════════

FACE_MODEL_3D = np.array([
    [-30.0, -30.0, 0.0],   # left eye
    [ 30.0, -30.0, 0.0],   # right eye
    [  0.0,   0.0, 0.0],   # nose tip
    [-20.0,  40.0, 0.0],   # left mouth
    [ 20.0,  40.0, 0.0],   # right mouth
], dtype=np.float64)


# ═══════════════════════════════════════════
#  HeadPoseModule
# ═══════════════════════════════════════════

class HeadPoseModule:
    def __init__(self):
        self._model = FACE_MODEL_3D.copy()

    def estimate(self, landmarks: List[Tuple[float, float]],
                 img_w: int = 640, img_h: int = 640) -> Tuple[float, float, float]:
        if len(landmarks) < 5:
            return (0.0, 0.0, 0.0)
        pts_2d = np.array(landmarks, dtype=np.float64)
        focal = float(img_w)
        center = (img_w / 2.0, img_h / 2.0)
        camera_matrix = np.array([
            [focal, 0, center[0]], [0, focal, center[1]], [0, 0, 1]
        ], dtype=np.float64)
        try:
            success, rvec, tvec = cv2.solvePnP(
                self._model, pts_2d, camera_matrix, None, flags=cv2.SOLVEPNP_ITERATIVE)
            if not success: return (0.0, 0.0, 0.0)
            rmat, _ = cv2.Rodrigues(rvec)
            sy = math.sqrt(rmat[0,0]**2 + rmat[1,0]**2)
            singular = sy < 1e-6
            if not singular:
                pitch = math.atan2(-rmat[2,0], sy)
                yaw = math.atan2(rmat[1,0], rmat[0,0])
                roll = math.atan2(rmat[2,1], rmat[2,2])
            else:
                pitch = math.atan2(-rmat[2,0], sy)
                yaw = math.atan2(-rmat[0,1], rmat[1,1])
                roll = 0.0
            return (math.degrees(yaw), math.degrees(pitch), math.degrees(roll))
        except Exception:
            return (0.0, 0.0, 0.0)


# ═══════════════════════════════════════════
#  StabilityModule
# ═══════════════════════════════════════════

class StabilityModule:
    def __init__(self, dead_zone: float = 10.0):
        self._dead_zone = dead_zone
        self._prev_nose: Optional[Tuple[float, float]] = None
        self._velocity_history: deque = deque(maxlen=30)

    def update(self, nose_xy: Optional[Tuple[float, float]]) -> float:
        if nose_xy is None: return 0.5
        if self._prev_nose is None:
            self._prev_nose = nose_xy; return 0.0
        dx = nose_xy[0] - self._prev_nose[0]
        dy = nose_xy[1] - self._prev_nose[1]
        dist = math.sqrt(dx*dx + dy*dy)
        if dist < self._dead_zone: dist = 0.0
        self._prev_nose = nose_xy
        self._velocity_history.append(dist)
        if len(self._velocity_history) < 2: return 0.0
        avg_vel = sum(self._velocity_history) / len(self._velocity_history)
        return max(0.0, 1.0 - avg_vel / 30.0)


# ═══════════════════════════════════════════
#  Baseline Calibrator
# ═══════════════════════════════════════════

class BaselineCalibrator:
    """采集 pitch/yaw 基线, 支持自动校准和持久化."""

    def __init__(self, config: AttentionConfig):
        self._cfg = config
        self._samples: deque = deque()           # (ts, yaw, pitch)
        self._calibrated = False
        self._baseline_yaw: float = 0.0
        self._baseline_pitch: float = 0.0
        self._calibrate_start: Optional[float] = None
        self._last_rebaseline: float = 0.0
        self._load()

    @property
    def calibrated(self) -> bool: return self._calibrated

    @property
    def baseline_yaw(self) -> float: return self._baseline_yaw

    @property
    def baseline_pitch(self) -> float: return self._baseline_pitch

    def add_sample(self, yaw: float, pitch: float):
        now = time.time()
        self._samples.append((now, yaw, pitch))

        # 初始校准: 采集 cfg.calibrate_duration 秒
        if not self._calibrated:
            if self._calibrate_start is None:
                self._calibrate_start = now
            elapsed = now - self._calibrate_start
            if elapsed >= self._cfg.calibrate_duration and len(self._samples) >= self._cfg.calibrate_min_samples:
                self._compute_baseline()
                self._calibrated = True
                self._save()
                logger.info("✅ Baseline calibrated: yaw=%.1f° pitch=%.1f°",
                            self._baseline_yaw, self._baseline_pitch)
            return

        # 定期微调 (每 rebaseline_interval 秒)
        if now - self._last_rebaseline > self._cfg.rebaseline_interval:
            self._rebaseline(now)

    def _compute_baseline(self):
        """取中位数作为基线."""
        if len(self._samples) == 0: return
        yaws = sorted(s[1] for s in self._samples)
        pitches = sorted(s[2] for s in self._samples)
        n = len(yaws)
        self._baseline_yaw = yaws[n // 2]
        self._baseline_pitch = pitches[n // 2]

    def _rebaseline(self, now: float):
        """用最近 rebaseline_window 秒数据微调基线."""
        cutoff = now - self._cfg.rebaseline_window
        recent = [s for s in self._samples if s[0] > cutoff]
        if len(recent) < 5: return
        yaws = sorted(s[1] for s in recent)
        pitches = sorted(s[2] for s in recent)
        n = len(yaws)
        new_yaw = yaws[n // 2]
        new_pitch = pitches[n // 2]
        # 慢速融合, 避免突变
        self._baseline_yaw = 0.8 * self._baseline_yaw + 0.2 * new_yaw
        self._baseline_pitch = 0.8 * self._baseline_pitch + 0.2 * new_pitch
        self._last_rebaseline = now
        logger.debug("Rebaseline: yaw=%.1f° pitch=%.1f°",
                     self._baseline_yaw, self._baseline_pitch)

    def force_calibrate(self):
        """手动校准: 用最近 10s 数据立即计算基线."""
        self._calibrated = False
        self._calibrate_start = 0  # 立即触发
        self._compute_baseline()
        if len(self._samples) >= 3:
            self._calibrated = True
            self._save()

    def _save(self):
        """只保存正脸附近的基线，避免错误关键点污染后续判断."""
        if abs(self._baseline_yaw) > 60 or abs(self._baseline_pitch) > 45:
            logger.warning("Baseline out of range (yaw=%.1f pitch=%.1f) — not saving",
                           self._baseline_yaw, self._baseline_pitch)
            return
        try:
            data = {"yaw": self._baseline_yaw, "pitch": self._baseline_pitch}
            with open(self._cfg.baseline_file, "w") as f:
                json.dump(data, f)
        except Exception: pass

    def _load(self):
        try:
            if os.path.exists(self._cfg.baseline_file):
                with open(self._cfg.baseline_file) as f:
                    data = json.load(f)
                y = float(data.get("yaw", 0))
                p = float(data.get("pitch", 0))
                if abs(y) <= 60 and abs(p) <= 45:
                    self._baseline_yaw = y
                    self._baseline_pitch = p
                    self._calibrated = True
                    logger.info("📂 Loaded baseline: yaw=%.1f° pitch=%.1f°", y, p)
                else:
                    logger.warning("Baseline file corrupt (yaw=%.1f pitch=%.1f) — ignored", y, p)
        except Exception: pass


# ═══════════════════════════════════════════
#  EvidenceModule (基线相对版)
# ═══════════════════════════════════════════

class EvidenceModule:
    def __init__(self, config: AttentionConfig):
        self._cfg = config
        self._distraction_start: Optional[float] = None

    def evaluate(self, yaw: float, pitch: float, stability: float,
                 baseline_yaw: float, baseline_pitch: float,
                 calibrated: bool) -> Tuple[float, float, List[str], List[str]]:
        evidence = []
        warnings = []
        orient_score = 100.0

        if not calibrated:
            evidence.append("Calibrating...")
            return (100.0, 100.0, evidence, warnings)

        # 基线相对偏差
        dyaw = abs(yaw - baseline_yaw)
        dpitch = abs(pitch - baseline_pitch)

        # Yaw
        if dyaw <= self._cfg.yaw_normal:
            evidence.append("Looking Forward")
        elif dyaw <= self._cfg.yaw_buffer:
            evidence.append("Head Slightly Turned")
        else:
            orient_score -= 25.0
            warnings.append("Looking Sideways")

        # Pitch
        if dpitch <= self._cfg.pitch_normal:
            evidence.append("Normal Viewing Angle")
        elif dpitch <= self._cfg.pitch_buffer:
            evidence.append("Slight Head Tilt")
        else:
            orient_score -= 25.0
            warnings.append("Head Tilted Away")

        # 持续分心检测
        is_distracted = dyaw > self._cfg.yaw_buffer or dpitch > self._cfg.pitch_buffer
        now = time.time()
        if is_distracted:
            if self._distraction_start is None:
                self._distraction_start = now
            elif (now - self._distraction_start) < self._cfg.distraction_trigger:
                pass  # 不到 3s, 不罚
            else:
                orient_score = max(30, orient_score - 20)
        else:
            self._distraction_start = None

        # Stability
        if stability > 0.7:
            evidence.append("Stable Head")
            stability_score = 100.0
        elif stability > 0.4:
            evidence.append("Slight Movement")
            stability_score = 70.0
        else:
            warnings.append("Frequent Movement")
            stability_score = 40.0

        return (max(0, orient_score), stability_score, evidence, warnings)


# ═══════════════════════════════════════════
#  ScoringModule (状态机防抖)
# ═══════════════════════════════════════════

class ScoringModule:
    def __init__(self, config: AttentionConfig):
        self._cfg = config
        self._window: deque = deque()  # (ts, raw_score)
        self._smoothed: Optional[float] = None
        self._last_update: float = 0.0
        self._display_score = 80.0
        self._display_state = "Focused"
        # 状态机
        self._degrade_count: int = 0
        self._recover_count: int = 0
        self._current_tier: int = 1  # 0=high,1=focused,2=slight,3=distracted,4=highly

    def update(self, orient_score: float, stability_score: float) -> float:
        now = time.time()
        raw = (self._cfg.orientation_weight * orient_score +
               self._cfg.stability_weight * stability_score)
        return self.update_raw(raw, now=now)

    def update_raw(self, raw_score: float, now: Optional[float] = None) -> float:
        now = time.time() if now is None else float(now)
        raw = max(0.0, min(100.0, float(raw_score)))
        self._window.append((now, raw))

        cutoff = now - self._cfg.window_size
        while self._window and self._window[0][0] < cutoff:
            self._window.popleft()

        if now - self._last_update >= self._cfg.window_size and len(self._window) > 0:
            avg = sum(s[1] for s in self._window) / len(self._window)
            if self._smoothed is None:
                self._smoothed = avg
            else:
                self._smoothed = (self._cfg.ema_alpha * avg +
                                  (1 - self._cfg.ema_alpha) * self._smoothed)

            # 状态机防抖
            new_tier = self._score_to_tier(self._smoothed)
            if new_tier > self._current_tier:
                self._degrade_count += 1
                self._recover_count = 0
                if self._degrade_count >= self._cfg.degrade_windows:
                    self._current_tier = new_tier
                    self._degrade_count = 0
            elif new_tier < self._current_tier:
                self._recover_count += 1
                self._degrade_count = 0
                if self._recover_count >= self._cfg.recover_windows:
                    self._current_tier = new_tier
                    self._recover_count = 0
            else:
                self._degrade_count = 0
                self._recover_count = 0

            self._display_score = round(self._smoothed)
            self._display_state = self._tier_to_state(self._current_tier)
            self._last_update = now

        return self._display_score

    @property
    def display_state(self) -> str: return self._display_state

    @staticmethod
    def _score_to_tier(s: float) -> int:
        if s >= 90: return 0
        if s >= 70: return 1
        if s >= 50: return 2
        if s >= 30: return 3
        return 4

    @staticmethod
    def _tier_to_state(t: int) -> str:
        return ["Highly Focused", "Focused", "Slightly Distracted",
                "Distracted", "Highly Distracted"][t]


# ═══════════════════════════════════════════
#  AttentionEngine
# ═══════════════════════════════════════════

class AttentionEngine:
    def __init__(self, config: AttentionConfig = None):
        self._cfg = config or AttentionConfig()
        self._pose = HeadPoseModule()
        self._stability = StabilityModule(dead_zone=self._cfg.nose_dead_zone)
        self._baseline = BaselineCalibrator(self._cfg)
        self._evidence = EvidenceModule(self._cfg)
        self._scoring = ScoringModule(self._cfg)

    def update(self, landmarks: Optional[List[Tuple[float, float]]],
               nose_xy: Optional[Tuple[float, float]] = None,
               img_w: int = 640, img_h: int = 640,
               eye_metrics: dict = None,
               gaze: dict = None) -> dict:
        if landmarks is None or len(landmarks) < 5:
            return {"has_face": False}

        yaw, pitch, roll = self._pose.estimate(landmarks, img_w, img_h)
        stability = self._stability.update(nose_xy)
        self._baseline.add_sample(yaw, pitch)

        orient_score, stability_score, evidence, warnings = self._evidence.evaluate(
            yaw, pitch, stability,
            self._baseline.baseline_yaw, self._baseline.baseline_pitch,
            self._baseline.calibrated)

        # Fuse complementary visual cues:
        # head orientation (VFOA proxy), eye alertness (EAR/PERCLOS/blink),
        # and short-term motion stability. Eye metrics are fatigue-related,
        # but here they are only one attention cue, not the whole decision.
        eye_raw = 70.0
        if eye_metrics:
            ear = eye_metrics.get("ear_avg", 0.3)
            ef = eye_metrics.get("focus_score", 50)
            perclos = eye_metrics.get("perclos", 0)
            blink_rate = eye_metrics.get("blink_rate", 15)
            p = 1.0
            if ear < 0.18:
                p *= 0.35
                warnings.append("Eyes Mostly Closed")
            elif ear < 0.23:
                p *= 0.75
                evidence.append("Eyes Partly Open")
            else:
                evidence.append("Eyes Open")
            if perclos > 0.2:
                p *= 0.5
                warnings.append("High PERCLOS")
            if blink_rate < 3 or blink_rate > 30:
                p *= 0.7
                warnings.append("Abnormal Blink Rate")
            eye_raw = ef * p
        gaze_raw = 70.0
        if gaze and gaze.get("available"):
            state = str(gaze.get("state") or "unknown")
            conf = float(gaze.get("confidence") or 0.0)
            if state == "center":
                gaze_raw = 100.0
                evidence.append("Gaze Centered")
            elif state in {"left", "right"}:
                gaze_raw = 68.0
                evidence.append("Gaze Slightly Aside")
            elif state == "down":
                gaze_raw = 45.0
                warnings.append("Gaze Down")
            elif state == "away":
                gaze_raw = 50.0
                warnings.append("Gaze Away")
            gaze_raw = 70.0 * (1.0 - conf) + gaze_raw * conf

        fused = 0.40 * orient_score + 0.30 * eye_raw + 0.15 * stability_score + 0.15 * gaze_raw
        if eye_metrics and eye_metrics.get("ear_avg", 0.3) < 0.15:
            fused = min(fused, 40)

        score = self._scoring.update_raw(fused)
        state = self._scoring.display_state

        return {
            "has_face": True, "score": score, "state": state,
            "evidence": evidence, "warnings": warnings,
            "head_yaw": round(float(yaw), 1),
            "head_pitch": round(float(pitch), 1),
            "head_roll": round(float(roll), 1),
            "components": {
                "orientation": int(round(orient_score)),
                "eye": int(round(eye_raw)),
                "stability": int(round(stability_score)),
                "gaze": int(round(gaze_raw)),
                "weights": {"orientation": 0.40, "eye": 0.30, "stability": 0.15, "gaze": 0.15},
            },
            "calibrated": self._baseline.calibrated,
            "baseline_yaw": round(self._baseline.baseline_yaw, 1),
            "baseline_pitch": round(self._baseline.baseline_pitch, 1),
        }
