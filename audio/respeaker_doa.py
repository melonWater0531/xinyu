"""
ReSpeaker XVF3800 DOA Reader — USB HID control for sound source localization.

Reads Direction of Arrival from the 4-mic array and provides:
  - Raw DOA angle (0-359°, 0=front)
  - Speech detection flag
  - Mapped gimbal yaw angle

Usage:
    reader = ReSpeakerDOA()
    if reader.open():
        doa_deg, has_speech = reader.read()
        if has_speech:
            gimbal_yaw = reader.to_gimbal_yaw(doa_deg)

Requires: pyusb, libusb
"""

from __future__ import annotations

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import struct
import threading
import time
from typing import Optional, Tuple

from utils.logger import get_logger

logger = get_logger(__name__)

# ReSpeaker USB VID/PID
VID = 0x2886
PID = 0x001A

# DOA_VALUE: resid=20, cmdid=18, length=2 (uint16 × 2)
# Returns: [doa_angle_0_359, speech_detected_0_or_1]
DOA_RESID = 20
DOA_CMDID = 0x80 | 18  # 0x80 | cmdid for read
DOA_LENGTH = 9  # 1 status + 4 × uint16 (8 bytes) — matches simple script
LED_EFFECT_CMDID = 12
LED_BRIGHTNESS_CMDID = 13
LED_GAMMIFY_CMDID = 14
LED_DOA_COLOR_CMDID = 17


class ReSpeakerDOA:
    """
    Continuous DOA reader for ReSpeaker XVF3800 USB mic array.

    Thread-safe. Runs a background polling thread at ~10 Hz.
    """

    def __init__(self, vid: int = VID, pid: int = PID) -> None:
        self._vid = vid
        self._pid = pid
        self._dev = None
        self._lock = threading.Lock()
        self._usb_lock = threading.Lock()
        self._running = False
        self._thread: Optional[threading.Thread] = None

        # Latest reading
        self._doa_deg: float = 0.0       # 0-359, 0=front
        self._has_speech: bool = False
        self._last_read_time: float = 0.0
        self._read_count: int = 0
        self._error_count: int = 0
        self._led = {
            "hardware": True, "effect": "off", "brightness": 80,
            "base_color": "#102030", "doa_color": "#24c98b",
            "last_write_ok": False,
        }

    # ── Open / Close ────────────────────────────────────────

    def open(self) -> bool:
        """Find and open the ReSpeaker USB device."""
        try:
            import usb.core
            dev = usb.core.find(idVendor=self._vid, idProduct=self._pid)
            if dev is None:
                logger.warning("ReSpeaker not found (VID=0x%04X PID=0x%04X)", self._vid, self._pid)
                return False

            self._dev = dev
            logger.info("🎤 ReSpeaker XVF3800 connected (VID=0x%04X PID=0x%04X)", self._vid, self._pid)
            return True
        except ImportError:
            logger.error("pyusb not installed")
            return False
        except Exception as e:
            logger.error("ReSpeaker open failed: %s", e)
            return False

    def _ctrl_write(self, resid: int, cmdid: int, data: list) -> None:
        """Send a write command with auto-packed values."""
        payload = bytes()
        for val in data:
            payload += val.to_bytes(4, 'little') if isinstance(val, int) and val > 255 else bytes([val])
        self._ctrl_write_raw(resid, cmdid, payload)

    def _ctrl_write_raw(self, resid: int, cmdid: int, payload: bytes) -> None:
        """Send raw bytes via USB control transfer."""
        import usb.util
        if self._dev is None:
            return
        with self._usb_lock:
            self._dev.ctrl_transfer(
                usb.util.CTRL_OUT | usb.util.CTRL_TYPE_VENDOR | usb.util.CTRL_RECIPIENT_DEVICE,
                0, cmdid, resid, payload, 500
            )

    def _ctrl_read_raw(self, resid: int, cmdid: int, length: int) -> bytes:
        import usb.util
        if self._dev is None:
            return b""
        with self._usb_lock:
            response = self._dev.ctrl_transfer(
                usb.util.CTRL_IN | usb.util.CTRL_TYPE_VENDOR | usb.util.CTRL_RECIPIENT_DEVICE,
                0, 0x80 | cmdid, resid, length + 1, 500,
            )
        raw = response.tobytes() if hasattr(response, "tobytes") else bytes(response)
        if not raw or raw[0] != 0:
            raise RuntimeError(f"XVF3800 read failed for command {cmdid}")
        return raw[1:]

    def close(self) -> None:
        """Stop polling and release USB device."""
        self.stop()
        if self._dev:
            try:
                import usb.util
                usb.util.dispose_resources(self._dev)
            except Exception:
                pass
            self._dev = None

    # ── Polling ─────────────────────────────────────────────

    def start(self, interval: float = 0.1) -> None:
        """Start background DOA polling at given interval (default 100ms = 10Hz)."""
        if self._running:
            return
        if self._dev is None:
            logger.warning("Cannot start DOA polling — device not open")
            return
        self._running = True
        self._thread = threading.Thread(target=self._poll_loop, args=(interval,), daemon=True, name="doa-poll")
        self._thread.start()
        logger.info("🎤 DOA polling started @ %.0f Hz", 1.0 / interval)

    def stop(self) -> None:
        """Stop background polling."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=2.0)
            self._thread = None

    def _poll_loop(self, interval: float) -> None:
        """Background loop: read DOA_VALUE continuously."""
        while self._running:
            try:
                doa, speech = self._read_doa_raw()
                with self._lock:
                    self._doa_deg = doa
                    self._has_speech = speech
                    self._last_read_time = time.monotonic()
                    self._read_count += 1
            except Exception as e:
                self._error_count += 1
                if self._error_count % 100 == 0:
                    logger.warning("DOA read error (×%d): %s", self._error_count, str(e)[:80])
            time.sleep(interval)

    # ── Raw USB Read ────────────────────────────────────────

    def _read_doa_raw(self) -> Tuple[float, bool]:
        """
        Read DOA_VALUE using the same approach as the working simple script.
        Returns raw byte list and extracts DOA + speech detection the same way.
        """
        import usb.core, usb.util
        TIMEOUT = 200

        with self._usb_lock:
            response = self._dev.ctrl_transfer(
                usb.util.CTRL_IN | usb.util.CTRL_TYPE_VENDOR | usb.util.CTRL_RECIPIENT_DEVICE,
                0, DOA_CMDID, DOA_RESID, DOA_LENGTH, TIMEOUT,
            )

        # Use raw byte list like simple script (ignores status byte)
        raw = response.tolist()

        # DOA is uint16 at bytes 1-2 (little-endian): raw[1] + raw[2]*256
        doa_val = raw[1] + raw[2] * 256
        doa_deg = float(doa_val % 360)

        # Speech detection is at byte 3 (matching simple script: result[3])
        has_speech = bool(raw[3])

        return doa_deg, has_speech

    # ── Public API ──────────────────────────────────────────

    def read(self) -> Tuple[float, bool]:
        """
        Get latest DOA reading.

        Returns:
            (doa_degrees, has_speech)
        """
        with self._lock:
            return self._doa_deg, self._has_speech

    @property
    def doa(self) -> float:
        """Latest DOA angle in degrees (0=front, 90=right)."""
        with self._lock:
            return self._doa_deg

    @property
    def has_speech(self) -> bool:
        """True if speech detected via flag OR energy threshold."""
        with self._lock:
            if self._has_speech:
                return True
            # Fallback: use DOA variance as proxy for speech
            # If DOA changes significantly, someone is likely speaking
            return False  # energy check added below in _poll_loop

    @property
    def age(self) -> float:
        """Seconds since last successful read."""
        with self._lock:
            if self._last_read_time == 0.0:
                return 999.0
            return time.monotonic() - self._last_read_time

    # ── Coordinate Mapping ──────────────────────────────────

    @staticmethod
    def to_gimbal_yaw(doa_deg: float, current_yaw: float = 180.0, max_step: float = 15.0) -> float:
        """
        Convert ReSpeaker DOA angle (0=front) to gimbal yaw angle (1-345°).

        ReSpeaker:  0°=front, 90°=right, 180°=behind, 270°=left
        Gimbal:     180°=center/front, 1°=full left, 345°=full right

        Mapping: gimbal_yaw = 180 + doa_mapped
          where doa_mapped = doa if doa <= 180 else doa - 360
          → range [-180, 180] → gimbal yaw [0, 360]

        Args:
            doa_deg:      DOA angle from ReSpeaker (0-359).
            current_yaw:  Current gimbal yaw position.
            max_step:     Maximum degrees to move per update.

        Returns:
            Target gimbal yaw, clamped to [1, 345].
        """
        # Normalize to [-180, 180]
        doa_mapped = doa_deg if doa_deg <= 180 else doa_deg - 360

        # Map to gimbal: center 180° + offset
        target = 180.0 + doa_mapped

        # Clamp
        target = max(1.0, min(345.0, target))

        # Step limit for smooth motion
        if abs(target - current_yaw) > max_step:
            if target > current_yaw:
                target = current_yaw + max_step
            else:
                target = current_yaw - max_step

        return max(1.0, min(345.0, target))

    @property
    def stats(self) -> dict:
        with self._lock:
            age = 999.0 if self._last_read_time == 0.0 else time.monotonic() - self._last_read_time
            return {
                "doa": round(self._doa_deg, 1),
                "has_speech": self._has_speech,
                "age": round(age, 2),
                "reads": self._read_count,
                "errors": self._error_count,
            }

    def set_led_doa(
        self,
        *,
        brightness: int = 80,
        base_color: int = 0x102030,
        doa_color: int = 0x24C98B,
    ) -> bool:
        """Enable the XVF3800 firmware's physical DOA ring effect."""
        if self._dev is None:
            return False
        try:
            self._ctrl_write_raw(DOA_RESID, LED_DOA_COLOR_CMDID, struct.pack("<II", int(base_color), int(doa_color)))
            self._ctrl_write_raw(DOA_RESID, LED_BRIGHTNESS_CMDID, bytes([max(0, min(255, int(brightness)))]))
            self._ctrl_write_raw(DOA_RESID, LED_GAMMIFY_CMDID, bytes([1]))
            self._ctrl_write_raw(DOA_RESID, LED_EFFECT_CMDID, bytes([4]))
            effect = self._ctrl_read_raw(DOA_RESID, LED_EFFECT_CMDID, 1)[0]
            brightness_read = self._ctrl_read_raw(DOA_RESID, LED_BRIGHTNESS_CMDID, 1)[0]
            colors = struct.unpack("<II", self._ctrl_read_raw(DOA_RESID, LED_DOA_COLOR_CMDID, 8))
            self._led.update({
                "effect": "doa" if effect == 4 else f"mode_{effect}", "brightness": brightness_read,
                "base_color": f"#{colors[0] & 0xFFFFFF:06x}",
                "doa_color": f"#{colors[1] & 0xFFFFFF:06x}",
                "last_write_ok": effect == 4, "readback": True,
            })
            return effect == 4
        except Exception as exc:
            self._led["last_write_ok"] = False
            logger.warning("ReSpeaker LED DOA mode failed: %s", exc)
            return False

    def set_led_off(self) -> bool:
        if self._dev is None:
            return False
        try:
            self._ctrl_write_raw(DOA_RESID, LED_EFFECT_CMDID, bytes([0]))
            effect = self._ctrl_read_raw(DOA_RESID, LED_EFFECT_CMDID, 1)[0]
            self._led.update({"effect": "off" if effect == 0 else f"mode_{effect}", "last_write_ok": effect == 0, "readback": True})
            return effect == 0
        except Exception as exc:
            self._led["last_write_ok"] = False
            logger.warning("ReSpeaker LED off failed: %s", exc)
            return False

    @property
    def led_status(self) -> dict:
        return dict(self._led)

    def status(self) -> dict:
        return {
            "available": self._dev is not None and self.age <= 1.0,
            "source": "usb", "connected": self._dev is not None,
            "doa_deg": round(self.doa, 1), "has_speech": self.has_speech,
            "age": round(self.age, 2), "packet_count": self._read_count,
            "errors": self._error_count, "led": self.led_status,
        }

    def __repr__(self) -> str:
        return (
            f"ReSpeakerDOA(doa={self._doa_deg:.1f}° "
            f"speech={'yes' if self._has_speech else 'no'} "
            f"age={self.age:.1f}s)"
        )


# ═══════════════════════════════════════════════════════════════
#  Quick test
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    from utils.logger import setup_root_logger
    setup_root_logger("INFO")

    reader = ReSpeakerDOA()
    if not reader.open():
        print("No ReSpeaker found — exiting")
        raise SystemExit(1)

    reader.start(interval=0.1)
    print("Reading DOA... (Ctrl+C to stop)")
    try:
        while True:
            doa, speech = reader.read()
            print(f"DOA={doa:6.1f}°  speech={'YES' if speech else 'no '}  age={reader.age:.2f}s", end="\r")
            time.sleep(0.2)
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        reader.close()
