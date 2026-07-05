"""
Control Filter — computes smooth gimbal delta commands from bbox position.

Phase 3: This is the bridge between "what the state machine wants"
and "what the safety layer allows."

Pipeline (per frame):
  bbox center → error (vs frame center) → normalize → dead zone
  → EMA filter → clamp → (delta_pan, delta_tilt)

Key design:
  - RELATIVE control (delta angles, not absolute positions)
  - Dead zone: ±10% of frame center → no movement
  - EMA smoothing: output = 0.3*current + 0.7*previous
  - Clamp: max ±2.5° per frame per axis
  - Configurable Kp gain

Usage:
    cf = ControlFilter(frame_width=1920, frame_height=1080)
    delta_pan, delta_tilt = cf.update(bbox_center_x, bbox_center_y)
    # The Orchestrator consumes this result and remains the only component
    # allowed to construct a ControlCommand.
"""

from typing import Optional, Tuple

from utils.logger import get_logger

logger = get_logger(__name__)


# ═══════════════════════════════════════════════════════════════
#  ControlFilter
# ═══════════════════════════════════════════════════════════════

class ControlFilter:
    """
    Converts bbox position to smooth, safe delta commands.

    Algorithm:
      1. error = bbox_center - frame_center
      2. norm  = error / frame_dimension   (→ [-1, 1])
      3. if |norm| < dead_zone: return None  (do nothing)
      4. raw  = Kp * norm * max_step
      5. filtered = EMA(raw, previous_output)
      6. clamp(filtered, ±max_step)
      7. return (delta_pan, delta_tilt)
    """

    def __init__(
        self,
        frame_width: int = 1920,
        frame_height: int = 1080,
        kp: float = 1.0,
        dead_zone: float = 0.10,
        max_step_deg: float = 2.5,
        ema_alpha: float = 0.3,
    ) -> None:
        """
        Args:
            frame_width:   Frame width in pixels.
            frame_height:  Frame height in pixels.
            kp:            Proportional gain.
            dead_zone:     Fraction of frame dimension [0-1].
                           ±10% default → no control if within ±192px (width).
            max_step_deg:  Maximum delta per frame in degrees.
            ema_alpha:     EMA smoothing factor (0-1).
                           Lower = smoother, slower.
                           output = alpha * current + (1-alpha) * previous
        """
        self._frame_w = frame_width
        self._frame_h = frame_height
        self._frame_cx = frame_width / 2.0
        self._frame_cy = frame_height / 2.0
        self._kp = kp
        self._dead_zone = dead_zone
        self._max_step = max_step_deg
        self._alpha = ema_alpha

        # Dead zone in pixels
        self._dead_px = dead_zone  # stored as fraction, computed per-axis

        # EMA state
        self._prev_pan: float = 0.0
        self._prev_tilt: float = 0.0

        # Stats
        self._update_count: int = 0
        self._move_count: int = 0
        self._skip_count: int = 0  # dead zone skips

        # Debug: last computed values
        self._last_error_x: float = 0.0
        self._last_error_y: float = 0.0
        self._last_norm_x: float = 0.0
        self._last_norm_y: float = 0.0
        self._last_raw_pan: float = 0.0
        self._last_raw_tilt: float = 0.0

        logger.info(
            "ControlFilter: Kp=%.1f dead_zone=±%.0f%% max_step=%.1f° "
            "ema_alpha=%.2f",
            kp, dead_zone * 100, max_step_deg, ema_alpha,
        )

    # ── Main update (called every frame) ───────────

    def update(
        self,
        bbox_center_x: float,
        bbox_center_y: float,
    ) -> Tuple[Optional[float], Optional[float]]:
        """
        Compute delta pan/tilt for this frame.

        Args:
            bbox_center_x: BBox center X in pixels.
            bbox_center_y: BBox center Y in pixels.

        Returns:
            (delta_pan, delta_tilt) in degrees, or (None, None) if no move needed.
            None return means "hold position" (dead zone active).
        """
        self._update_count += 1

        # ── Step 1: Calculate error ──────────────
        error_x = bbox_center_x - self._frame_cx
        error_y = bbox_center_y - self._frame_cy
        self._last_error_x = error_x
        self._last_error_y = error_y

        # ── Step 2: Normalize ────────────────────
        norm_x = error_x / max(1, self._frame_w)
        norm_y = error_y / max(1, self._frame_h)
        self._last_norm_x = norm_x
        self._last_norm_y = norm_y

        # ── Step 3: Per-axis dead zone ───────────
        # Each axis independently: within dead zone → clamp to zero
        # This prevents one-axis-close-to-center from dragging the other axis
        norm_x_dead = 0.0 if abs(norm_x) < self._dead_zone else norm_x
        norm_y_dead = 0.0 if abs(norm_y) < self._dead_zone else norm_y

        if norm_x_dead == 0.0 and norm_y_dead == 0.0:
            self._skip_count += 1
            # Decay previous values toward zero
            self._prev_pan *= 0.5
            self._prev_tilt *= 0.5
            return None, None

        # ── Step 4: Raw control output ───────────
        # sign convention: +error_x = bbox right → pan right (+pan)
        #                  +error_y = bbox down  → tilt down  (+tilt)
        # If an axis is in dead zone, use raw=0 and let EMA decay it naturally
        raw_pan = self._kp * norm_x_dead * self._max_step
        raw_tilt = self._kp * norm_y_dead * self._max_step
        self._last_raw_pan = raw_pan
        self._last_raw_tilt = raw_tilt

        # ── Step 5: EMA filter ───────────────────
        # output = 0.3 * current + 0.7 * previous
        # If only one axis is in dead zone, the dead axis's raw=0 and its
        # EMA will decay toward zero naturally — no residual drift.
        filtered_pan = self._alpha * raw_pan + (1 - self._alpha) * self._prev_pan
        filtered_tilt = self._alpha * raw_tilt + (1 - self._alpha) * self._prev_tilt

        # ── Step 6: Clamp ────────────────────────
        delta_pan = max(-self._max_step, min(self._max_step, filtered_pan))
        delta_tilt = max(-self._max_step, min(self._max_step, filtered_tilt))

        # Round small values to zero to avoid micro-movements
        if abs(delta_pan) < 0.05:
            delta_pan = 0.0
        if abs(delta_tilt) < 0.05:
            delta_tilt = 0.0

        # ── Store for next frame ────────────────
        self._prev_pan = delta_pan
        self._prev_tilt = delta_tilt
        self._move_count += 1

        return delta_pan, delta_tilt

    # ── Reset ────────────────────────────────────

    def reset(self) -> None:
        """Reset EMA state (e.g., after LOST→TRACK transition)."""
        self._prev_pan = 0.0
        self._prev_tilt = 0.0
        logger.debug("ControlFilter reset")

    # ── Public info for debug ────────────────────

    @property
    def debug_info(self) -> dict:
        """Return last-frame debug values for console output."""
        return {
            "error_x": self._last_error_x,
            "error_y": self._last_error_y,
            "norm_x": self._last_norm_x,
            "norm_y": self._last_norm_y,
            "raw_pan": self._last_raw_pan,
            "raw_tilt": self._last_raw_tilt,
            "filtered_pan": self._prev_pan,
            "filtered_tilt": self._prev_tilt,
        }

    @property
    def stats(self) -> dict:
        """Return cumulative statistics."""
        return {
            "updates": self._update_count,
            "moves": self._move_count,
            "skips": self._skip_count,
            "skip_ratio": (
                self._skip_count / max(1, self._update_count)
            ),
        }

    @property
    def prev_output(self) -> Tuple[float, float]:
        return (self._prev_pan, self._prev_tilt)
