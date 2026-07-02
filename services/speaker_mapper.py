"""Non-blocking meeting speaker mapping.

This module maps ReSpeaker DOA zones to lightweight speaker labels. It never
commands the gimbal; search plans are data only for future control-runtime work.
"""

from __future__ import annotations

import math
import time
from typing import Any

from utils.logger import get_logger

logger = get_logger(__name__)

CAMERA_HFOV = 62.0
DOA_ZONE_TOLERANCE = 15.0
LIP_MOVEMENT_THRESHOLD = 2.0


class SpeakerMapper:
    def __init__(self) -> None:
        self.speaker_map: dict[str, dict[str, Any]] = {}
        self._speaker_counter = 0

    def reset(self) -> None:
        self.speaker_map = {}
        self._speaker_counter = 0
        logger.info("Meeting speaker map reset")

    def _zone_key(self, doa_deg: float) -> str:
        center = int(round((float(doa_deg) % 360.0) / 30.0) * 30) % 360
        return f"zone_{center}"

    @staticmethod
    def _angle_diff(a: float, b: float) -> float:
        diff = abs((float(a) % 360.0) - (float(b) % 360.0))
        return min(diff, 360.0 - diff)

    def _find_matching_zone(self, doa_deg: float) -> dict[str, Any] | None:
        for info in self.speaker_map.values():
            if self._angle_diff(doa_deg, info["doa_center"]) <= DOA_ZONE_TOLERANCE:
                return info
        return None

    def lookup(self, doa_deg: float | None) -> dict[str, Any] | None:
        if doa_deg is None:
            return None
        match = self._find_matching_zone(float(doa_deg))
        if match:
            match["last_seen"] = time.time()
            return match
        return None

    def register(
        self,
        doa_deg: float,
        track_id: int | None = None,
        pitch: float | None = None,
        face_embedding: list[float] | None = None,
    ) -> dict[str, Any]:
        existing = self._find_matching_zone(float(doa_deg))
        if existing:
            existing.update({
                "track_id": track_id if track_id is not None else existing.get("track_id"),
                "pitch": float(pitch) if pitch is not None else existing.get("pitch"),
                "last_seen": time.time(),
            })
            return existing

        self._speaker_counter += 1
        label = f"说话人{chr(64 + min(self._speaker_counter, 26))}"
        if self._speaker_counter > 26:
            label = f"说话人{self._speaker_counter}"
        info = {
            "doa_center": float(doa_deg) % 360.0,
            "track_id": track_id,
            "pitch": float(pitch) if pitch is not None else None,
            "label": label,
            "face_embedding": face_embedding,
            "last_seen": time.time(),
        }
        self.speaker_map[self._zone_key(doa_deg)] = info
        logger.info(
            "Registered meeting speaker %s @ DOA=%.0f track_id=%s pitch=%s",
            label,
            float(doa_deg) % 360.0,
            track_id,
            f"{float(pitch):.1f}" if pitch is not None else "unknown",
        )
        return info

    def get_registered_speakers(self) -> list[dict[str, Any]]:
        speakers = []
        for info in self.speaker_map.values():
            speakers.append({
                "label": info.get("label", "未知说话人"),
                "doa": round(float(info.get("doa_center", 0.0)), 1),
                "track_id": info.get("track_id"),
                "pitch": round(float(info["pitch"]), 1) if info.get("pitch") is not None else None,
                "last_seen": round(float(info.get("last_seen", 0.0)), 3),
            })
        return sorted(speakers, key=lambda item: item["label"])

    @staticmethod
    def estimate_face_azimuth(face_cx: float, frame_width: int, camera_yaw: float) -> float:
        width = max(1, int(frame_width))
        offset_ratio = (float(face_cx) / width) - 0.5
        return (float(camera_yaw) + offset_ratio * CAMERA_HFOV) % 360.0

    @classmethod
    def find_closest_face_to_doa(
        cls,
        faces: list[dict[str, Any]],
        doa_deg: float,
        frame_width: int,
        camera_yaw: float,
    ) -> dict[str, Any] | None:
        best = None
        best_diff = 999.0
        for face in faces:
            center = face.get("face_center")
            cx = face.get("cx")
            if center and isinstance(center, list):
                cx = center[0]
            if cx is None:
                continue
            azimuth = cls.estimate_face_azimuth(float(cx), frame_width, camera_yaw)
            diff = cls._angle_diff(azimuth, doa_deg)
            if diff < best_diff:
                best = face
                best_diff = diff
        return best if best is not None and best_diff <= DOA_ZONE_TOLERANCE * 2 else None

    @staticmethod
    def detect_lip_movement(landmarks_prev: list, landmarks_curr: list) -> bool:
        if not landmarks_prev or not landmarks_curr:
            return False
        try:
            lip_dist_curr = math.dist(landmarks_curr[13][:2], landmarks_curr[14][:2])
            lip_dist_prev = math.dist(landmarks_prev[13][:2], landmarks_prev[14][:2])
            return abs(lip_dist_curr - lip_dist_prev) > LIP_MOVEMENT_THRESHOLD
        except (IndexError, TypeError, ValueError):
            return False

    def build_search_plan(self, doa_deg: float) -> dict[str, Any]:
        match = self._find_matching_zone(float(doa_deg))
        if match:
            return {
                "action": "direct",
                "yaw": match["doa_center"],
                "pitch": match.get("pitch"),
                "track_id": match.get("track_id"),
                "label": match.get("label"),
                "execute": False,
            }
        return {
            "action": "search",
            "yaw": float(doa_deg) % 360.0,
            "pitch_default": 90.0,
            "pitch_sequence": [90, 100, 80, 110],
            "use_yolo_body": True,
            "verify_lip_movement": True,
            "execute": False,
        }


speaker_mapper = SpeakerMapper()
