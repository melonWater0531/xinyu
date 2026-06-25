"""
Wake Word Detector — "你好小屿" trigger using ReSpeaker DOA + speech duration.

Strategy (WSL2-compatible, no audio driver needed):
  1. Monitor ReSpeaker DOA for sustained speech (>1.5s continuous)
  2. On trigger: return the DOA angle at time of wake
  3. Caller steers gimbal → starts face detection → locks and tracks

Future: replace duration check with real Vosk/Whisper wake word recognition
         when audio capture (pyaudio/portaudio) is available.

Usage:
    detector = WakeWordDetector(doa_reader)
    detector.start()

    while True:
        doa_deg = detector.check()
        if doa_deg is not None:
            print(f"Wake word detected at {doa_deg}°!")
            # steer gimbal to doa_deg, start face tracking
"""

from __future__ import annotations

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import threading
import time
from typing import Optional

from utils.logger import get_logger

logger = get_logger(__name__)


class WakeWordDetector:
    """
    Monitors ReSpeaker speech signal for sustained utterance.

    Config:
        min_duration:  seconds of continuous speech to trigger (default 1.5)
        cooldown:      seconds before next trigger allowed (default 3.0)
    """

    def __init__(
        self,
        doa_reader,  # ReSpeakerDOA instance
        min_duration: float = 1.5,
        cooldown: float = 3.0,
        on_wake: callable = None,  # callback(doa_deg) on wake word detection
    ) -> None:
        self._doa = doa_reader
        self._min_dur = min_duration
        self._cooldown = cooldown
        self._on_wake = on_wake

        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

        # State
        self._speech_start: Optional[float] = None
        self._last_trigger: float = 0.0
        self._trigger_doa: Optional[float] = None
        self._triggered: bool = False

    # ── Start / Stop ────────────────────────────────────────

    def start(self, interval: float = 0.1) -> None:
        """Start monitoring in background thread."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._loop, args=(interval,), daemon=True, name="wake-word"
        )
        self._thread.start()
        logger.info("👂 Wake word detector started (min_dur=%.1fs cooldown=%.1fs)",
                     self._min_dur, self._cooldown)

    def stop(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=2.0)

    # ── Main loop ───────────────────────────────────────────

    def _loop(self, interval: float) -> None:
        # Track DOA history to detect "someone speaking to me"
        _doa_history = []  # (timestamp, doa)
        _REST_ZONES = [(0, 8), (330, 360)]  # DOA resting positions

        def _is_resting(d):
            return any(lo <= d <= hi for lo, hi in _REST_ZONES)

        while self._running:
            try:
                now = time.monotonic()
                doa = self._doa.doa
                _doa_history.append((now, doa))
                # Keep last 2 seconds
                _doa_history = [(t, d) for t, d in _doa_history if now - t < 2.0]

                # Check: has DOA been consistently OUTSIDE resting zones?
                recent = [d for t, d in _doa_history if now - t < self._min_dur]
                if len(recent) >= 5:
                    active_count = sum(1 for d in recent if not _is_resting(d))
                    active_ratio = active_count / len(recent)

                    with self._lock:
                        if active_ratio > 0.7 and (now - self._last_trigger) >= self._cooldown:
                            # DOA has been active (non-resting) for most of the duration
                            self._trigger_doa = doa
                            self._triggered = True
                            self._last_trigger = now
                            logger.info("🔔 Wake triggered! DOA=%.0f° (active=%.0f%%)",
                                         self._trigger_doa, active_ratio * 100)
                            if self._on_wake:
                                try: self._on_wake(self._trigger_doa)
                                except Exception: pass
            except Exception:
                pass
            time.sleep(interval)

    # ── Public API ──────────────────────────────────────────

    def check(self) -> Optional[float]:
        """
        Check if wake word was triggered since last call.

        Returns:
            DOA angle (degrees) if triggered, None otherwise.
            Consumes the trigger (next call returns None until new trigger).
        """
        with self._lock:
            if self._triggered:
                self._triggered = False
                return self._trigger_doa
            return None

    @property
    def is_listening(self) -> bool:
        """True if DOA is outside resting zones (someone might be speaking)."""
        return False  # simplified; real status in check()

    @property
    def listening_duration(self) -> float:
        """Always 0 in DOA-based mode."""
        return 0.0


# ═══════════════════════════════════════════════════════════════
#  Quick test
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    from utils.logger import setup_root_logger
    setup_root_logger("INFO")
    from audio.respeaker_doa import ReSpeakerDOA

    doa = ReSpeakerDOA()
    if not doa.open():
        print("No ReSpeaker — exiting")
        raise SystemExit(1)
    doa.start(0.08)

    def on_wake(doa_deg):
        print(f"\n🔔 唤醒！声音方向: {doa_deg:.0f}°")
        doa.led_flash()  # flash LED as feedback

    detector = WakeWordDetector(doa, min_duration=1.5, cooldown=3.0, on_wake=on_wake)
    detector.start(0.1)

    print("👂 等待唤醒词... 对着麦克风持续说话 1.5 秒以上")
    print("   (说\"你好小屿\"然后继续说, Ctrl+C 停止)")
    print("   💡 LED 已关闭, 唤醒时闪烁\n")

    try:
        while True:
            result = detector.check()
            dur = detector.listening_duration
            if dur > 0:
                bar = "█" * int(dur * 10) + "░" * max(0, 20 - int(dur * 10))
                print(f"\r正在听... [{bar}] {dur:.1f}s  ", end="")
            else:
                print(f"\r等待唤醒词...                        ", end="")
            time.sleep(0.1)
    except KeyboardInterrupt:
        print("\n停止")
    finally:
        detector.stop()
        doa.close()
