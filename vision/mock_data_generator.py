"""
Mock bbox data generator — simulates person appearance, stay, movement, disappearance.

Scenarios (auto-cycle):
  A: Person enters   — None → bbox appears          (30 frames)
  B: Person idle     — bbox with ±jitter             (60 frames)
  C: Person moves    — bbox slowly pans left↔right  (90 frames)
  D: Person leaves   — bbox → None                   (30 frames)

Total cycle: 210 frames. Repeats indefinitely.

Usage:
    gen = MockDataGenerator()
    while True:
        bbox = gen.next()
        if bbox:  # target present
            ...
"""

import math
import random
from typing import Optional

from core.event import BBox
from utils.logger import get_logger

logger = get_logger(__name__)


# ═══════════════════════════════════════════════════════════════
#  Scenario timing (frames)
# ═══════════════════════════════════════════════════════════════

class Scenario:
    ENTER  = "enter"
    IDLE   = "idle"
    MOVE   = "move"
    LEAVE  = "leave"

# Configurable frame counts per scenario
SCENARIO_FRAMES = {
    Scenario.ENTER: 30,    # ~2 sec at 15fps
    Scenario.IDLE:  60,    # ~4 sec
    Scenario.MOVE:  90,    # ~6 sec
    Scenario.LEAVE: 30,    # ~2 sec
}

# BBox base parameters (normalized to 1920×1080 for now)
BBOX_BASE = {
    "x1": 400,
    "y1": 150,
    "x2": 560,   # width = 160
    "y2": 500,   # height = 350
    "class_id": 0,
    "class_name": "person",
    "confidence": 0.85,
}


# ═══════════════════════════════════════════════════════════════
#  Generator
# ═══════════════════════════════════════════════════════════════

class MockDataGenerator:
    """
    Generates mock bbox data cycling through ENTER→IDLE→MOVE→LEAVE→ENTER...

    Features:
      - ±2-5px random jitter per frame
      - Smooth left↔right movement during MOVE scenario
      - Confidence fluctuations
      - Returns Optional[BBox] each frame (None = no person)
    """

    def __init__(
        self,
        frames_enter: int = 30,
        frames_idle:  int = 60,
        frames_move:  int = 90,
        frames_leave: int = 30,
        jitter_range: int = 5,
        move_speed:   float = 2.0,    # pixels per frame
        move_range:   float = 150.0,  # max pixels from center
        seed:         Optional[int] = None,
    ) -> None:
        self._frames_enter  = frames_enter
        self._frames_idle   = frames_idle
        self._frames_move   = frames_move
        self._frames_leave  = frames_leave
        self._jitter_range  = jitter_range
        self._move_speed    = move_speed
        self._move_range    = move_range

        self._frame: int = 0
        self._total_frames: int = 0

        # Base bbox (center position — will be jittered each frame)
        self._base_x1: float = float(BBOX_BASE["x1"])
        self._base_y1: float = float(BBOX_BASE["y1"])
        self._base_x2: float = float(BBOX_BASE["x2"])
        self._base_y2: float = float(BBOX_BASE["y2"])

        # Movement tracker
        self._move_offset: float = 0.0
        self._move_dir:    float = 1.0  # +1 = right, -1 = left

        # RNG
        if seed is not None:
            random.seed(seed)

        logger.info(
            "MockDataGenerator: enter=%d idle=%d move=%d leave=%d "
            "jitter=±%dpx move=±%.0fpx",
            frames_enter, frames_idle, frames_move, frames_leave,
            jitter_range, move_range,
        )

    # ── public ─────────────────────────────────────────────

    def next(self) -> Optional[BBox]:
        """
        Return the bbox for the current frame, or None if no person.

        Call once per main loop iteration.
        """
        self._total_frames += 1
        scenario, phase_frame = self._get_scenario(self._frame)
        self._frame += 1

        if scenario == Scenario.ENTER:
            return self._gen_enter(phase_frame)
        elif scenario == Scenario.IDLE:
            return self._gen_idle()
        elif scenario == Scenario.MOVE:
            return self._gen_move()
        elif scenario == Scenario.LEAVE:
            return self._gen_leave(phase_frame)
        return None

    def reset(self) -> None:
        """Reset generator to frame 0."""
        self._frame = 0
        self._total_frames = 0
        self._move_offset = 0.0
        self._move_dir = 1.0

    @property
    def frame(self) -> int:
        return self._frame

    @property
    def total_frames(self) -> int:
        return self._total_frames

    def current_scenario(self) -> str:
        scenario, _ = self._get_scenario(self._frame)
        return scenario

    @property
    def cycle_length(self) -> int:
        return sum(SCENARIO_FRAMES.values())

    # ── scenario dispatch ──────────────────────────────────

    def _get_scenario(self, frame: int):
        """Determine (scenario, frame_within_scenario) for a global frame index."""
        cycle = self.cycle_length
        f = frame % cycle

        offset = 0
        for name in [Scenario.ENTER, Scenario.IDLE, Scenario.MOVE, Scenario.LEAVE]:
            length = SCENARIO_FRAMES[name]
            if f < offset + length:
                return name, f - offset
            offset += length

        return Scenario.ENTER, 0  # fallback

    # ── scenario generators ────────────────────────────────

    def _gen_enter(self, phase_frame: int) -> Optional[BBox]:
        """
        Scenario A: person enters.
        Phase 0-4: None (no person); Phase 5-29: bbox fades in (confidence ramps).
        """
        total = self._frames_enter
        # First 5 frames: no person
        if phase_frame < 5:
            return None

        # Frame 5-29: person appears with rising confidence
        progress = min(1.0, (phase_frame - 5) / max(1, total - 10))
        confidence = 0.3 + progress * 0.6  # ramps 0.3 → 0.9
        return self._make_bbox(confidence=confidence)

    def _gen_idle(self) -> BBox:
        """
        Scenario B: person stationary with small jitter.
        """
        return self._make_bbox(confidence=0.8)

    def _gen_move(self) -> BBox:
        """
        Scenario C: person moves left↔right across frame.
        """
        self._move_offset += self._move_speed * self._move_dir

        # Bounce at boundaries
        if abs(self._move_offset) >= self._move_range:
            self._move_offset = (
                self._move_range if self._move_offset > 0 else -self._move_range
            )
            self._move_dir *= -1

        return self._make_bbox(
            offset_x=self._move_offset,
            confidence=0.75,
        )

    def _gen_leave(self, phase_frame: int) -> Optional[BBox]:
        """
        Scenario D: person leaves.
        First 5 frames: bbox visible (fading confidence).
        Remaining 25 frames: person gone (None).
        This ensures 30 consecutive None frames across the LEAVE→ENTER boundary
        for TRACK→LOST debounce (30 frames).
        """
        total = self._frames_leave

        # First 5 frames: person still visible, fading
        if phase_frame < 5:
            progress = phase_frame / 5.0
            confidence = 0.8 - progress * 0.5  # fades 0.8 → 0.3
            return self._make_bbox(confidence=confidence)

        # Remaining 25 frames: person gone
        return None

    # ── helpers ────────────────────────────────────────────

    def _make_bbox(
        self,
        offset_x: float = 0.0,
        confidence: float = 0.85,
    ) -> BBox:
        """Build a BBox with jitter applied to all corners."""
        jx1 = random.randint(-self._jitter_range, self._jitter_range)
        jy1 = random.randint(-self._jitter_range, self._jitter_range)
        jx2 = random.randint(-self._jitter_range, self._jitter_range)
        jy2 = random.randint(-self._jitter_range, self._jitter_range)

        confidence_jitter = random.uniform(-0.03, 0.03)
        conf = max(0.0, min(1.0, confidence + confidence_jitter))

        x1 = int(self._base_x1 + offset_x + jx1)
        y1 = int(self._base_y1 + jy1)
        x2 = int(self._base_x2 + offset_x + jx2)
        y2 = int(self._base_y2 + jy2)

        return BBox(
            x1=x1, y1=y1, x2=x2, y2=y2,
            class_id=BBOX_BASE["class_id"],
            class_name=BBOX_BASE["class_name"],
            confidence=round(conf, 3),
        )


# ═══════════════════════════════════════════════════════════════
#  Quick test
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    gen = MockDataGenerator()
    cycle_len = gen.cycle_length
    print(f"Cycle length: {cycle_len} frames")

    for i in range(cycle_len + 10):
        bbox = gen.next()
        scenario = gen.current_scenario()
        if bbox:
            print(f"  frame={i:4d}  scenario={scenario:6s}  "
                  f"bbox=[{bbox.x1},{bbox.y1},{bbox.x2},{bbox.y2}]  "
                  f"center=({bbox.center_x:6.1f},{bbox.center_y:5.1f})  "
                  f"conf={bbox.confidence:.2f}")
        else:
            print(f"  frame={i:4d}  scenario={scenario:6s}  bbox=None")
