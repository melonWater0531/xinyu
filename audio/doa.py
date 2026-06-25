"""
DOA (Direction of Arrival) reader — pluggable source layer.

Reads azimuth data from reSpeaker XVF3800 (or equivalent).
Perception ONLY — never imports gimbal or modifies state machine.

Source types:
  - subprocess : runs a command each cycle and parses its stdout
  - stdin      : reads lines from stdin (for pipe usage)
  - tcp        : listens on a TCP port, receives DOA lines from remote
  - mock       : generates simulated DOA for testing

The old `map_doa_to_gimbal()` function has been REMOVED.
Angle mapping is now the responsibility of fusion_controller.
"""

import json
import logging
import math
import random
import re
import select
import socket
import subprocess
import sys
import threading
import time
from abc import ABC, abstractmethod
from typing import Optional

from utils.logger import get_logger

logger = get_logger(__name__)


# ═══════════════════════════════════════════════════════════════
#  DOA line parser (shared across all text-line sources)
# ═══════════════════════════════════════════════════════════════

# Matches:  AUDIO_MGR_SELECTED_AZIMUTHS 0.12829 (7.35 deg) 5.26549 (301.69 deg)
_PARSE_RE = re.compile(
    r"AUDIO_MGR_SELECTED_AZIMUTHS\s+([\d.]+)\s+\(([\d.]+)\s*deg\)"
)

# Simpler format: just a number (degrees or radians), optionally signed
_SIMPLE_DEG_RE = re.compile(r"^\s*([+-]?\d+(?:\.\d+)?)\s*(?:deg|°)?\s*$")
_SIMPLE_RAD_RE = re.compile(r"^\s*([+-]?\d+(?:\.\d+)?)\s*rad\s*$")


def parse_doa_line(line: str) -> Optional[float]:
    """
    Parse one line of text into a DOA azimuth in degrees.

    Supports formats:
      - xvf_host full format:  AUDIO_MGR_SELECTED_AZIMUTHS <rad> (<deg> deg) ...
      - Simple degree:         7.35
      - Degree with unit:      7.35 deg  /  7.35°
      - Radian:                0.128 rad
      - JSON:                  {"azimuth_deg": 7.35}  /  {"doa": 0.128, "unit": "rad"}

    Returns:
        degrees (float) or None if unparseable.
        0° = front, positive = right, negative = left.
    """
    line = line.strip()
    if not line:
        return None

    # xvf_host format
    match = _PARSE_RE.search(line)
    if match:
        degrees = float(match.group(2))
        if degrees > 180:
            degrees -= 360
        return degrees

    # JSON format
    if line.startswith("{"):
        try:
            obj = json.loads(line)
            if "azimuth_deg" in obj:
                return float(obj["azimuth_deg"])
            if "azimuth" in obj:
                return float(obj["azimuth"])
            if "doa" in obj:
                val = float(obj["doa"])
                unit = obj.get("unit", "rad")
                if unit == "rad":
                    return math.degrees(val)
                return val
        except (json.JSONDecodeError, ValueError):
            pass

    # Radian with unit suffix
    match = _SIMPLE_RAD_RE.match(line)
    if match:
        return math.degrees(float(match.group(1)))

    # Plain number (treat as degrees)
    match = _SIMPLE_DEG_RE.match(line)
    if match:
        return float(match.group(1))

    return None


# ═══════════════════════════════════════════════════════════════
#  Abstract base
# ═══════════════════════════════════════════════════════════════

class DOASource(ABC):
    """Abstract DOA source. Subclass to add new sources."""

    @abstractmethod
    def read_azimuth_degrees(self) -> Optional[float]:
        """
        Read the current DOA azimuth in degrees.
        Returns None when no valid reading is available.
        0° = front, positive = right, negative = left.
        """
        ...

    def close(self) -> None:
        """Clean up resources."""
        pass


# ═══════════════════════════════════════════════════════════════
#  Subprocess DOA source
# ═══════════════════════════════════════════════════════════════

class SubprocessDOASource(DOASource):
    """
    Runs an external command periodically and parses its stdout.

    Example:
        command: "xvf_host.exe AUDIO_MGR_SELECTED_AZIMUTHS"
    """

    def __init__(self, command: str, shell: bool = True,
                 interval: float = 0.1, encoding: str = "utf-8") -> None:
        self._command = command
        self._shell = shell
        self._interval = interval
        self._encoding = encoding
        self._last_value: Optional[float] = None

    def read_azimuth_degrees(self) -> Optional[float]:
        try:
            result = subprocess.run(
                self._command,
                shell=self._shell,
                capture_output=True,
                text=True,
                timeout=self._interval + 2.0,
                encoding=self._encoding,
            )
            output = result.stdout.strip()
            if not output:
                return self._last_value

            degrees = parse_doa_line(output)
            if degrees is not None:
                self._last_value = degrees
                return degrees

        except subprocess.TimeoutExpired:
            logger.warning("DOA subprocess timed out")
        except FileNotFoundError:
            logger.error("DOA command not found: %s", self._command.split()[0])
        except Exception:
            logger.exception("DOA subprocess error")

        return self._last_value


# ═══════════════════════════════════════════════════════════════
#  Stdin DOA source (for pipe usage)
# ═══════════════════════════════════════════════════════════════

class StdinDOASource(DOASource):
    """
    Reads DOA lines from stdin. Use with shell pipes.

    Examples:
        ssh user@pc "xvf_host.exe AUDIO_MGR_SELECTED_AZIMUTHS" | python3 app.py
        tail -f doa_log.txt | python3 app.py
    """

    def __init__(self, timeout: float = 0.1) -> None:
        self._timeout = timeout
        self._last_value: Optional[float] = None
        self._buffer = ""
        logger.info("DOA source: stdin — waiting for piped input...")

    def read_azimuth_degrees(self) -> Optional[float]:
        try:
            ready, _, _ = select.select([sys.stdin], [], [], self._timeout)
            if ready:
                chunk = sys.stdin.read(4096)
                if not chunk:
                    time.sleep(0.5)
                    return self._last_value
                self._buffer += chunk

                while "\n" in self._buffer:
                    line, self._buffer = self._buffer.split("\n", 1)
                    degrees = parse_doa_line(line)
                    if degrees is not None:
                        self._last_value = degrees
        except Exception:
            pass

        return self._last_value


# ═══════════════════════════════════════════════════════════════
#  TCP server DOA source
# ═══════════════════════════════════════════════════════════════

class TcpDOASource(DOASource):
    """
    Listens on a TCP port and receives DOA lines from remote senders.

    Usage:
        echo "7.35 deg" | nc <recamera-ip> 9999
    """

    def __init__(self, host: str = "0.0.0.0", port: int = 9999) -> None:
        self._host = host
        self._port = port
        self._last_value: Optional[float] = None
        self._server: Optional[socket.socket] = None
        self._client: Optional[socket.socket] = None
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

        self._start_server()

    def _start_server(self) -> None:
        self._server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server.bind((self._host, self._port))
        self._server.listen(1)
        self._server.settimeout(1.0)
        self._running = True
        self._thread = threading.Thread(target=self._accept_loop, daemon=True)
        self._thread.start()
        logger.info("DOA source: tcp — listening on %s:%d", self._host, self._port)

    def _accept_loop(self) -> None:
        buf = ""
        while self._running:
            try:
                if self._client is None and self._server:
                    try:
                        conn, addr = self._server.accept()
                        logger.info("TCP DOA client connected from %s:%d", *addr)
                        conn.settimeout(0.5)
                        self._client = conn
                        buf = ""
                    except socket.timeout:
                        continue

                if self._client:
                    try:
                        data = self._client.recv(4096)
                        if not data:
                            logger.info("TCP DOA client disconnected")
                            self._client.close()
                            self._client = None
                            continue

                        buf += data.decode("utf-8", errors="replace")
                        while "\n" in buf:
                            line, buf = buf.split("\n", 1)
                            degrees = parse_doa_line(line)
                            if degrees is not None:
                                with self._lock:
                                    self._last_value = degrees
                    except socket.timeout:
                        continue
                    except Exception:
                        if self._client:
                            self._client.close()
                        self._client = None
            except Exception:
                time.sleep(0.5)

    def read_azimuth_degrees(self) -> Optional[float]:
        with self._lock:
            return self._last_value

    def close(self) -> None:
        self._running = False
        if self._client:
            self._client.close()
            self._client = None
        if self._server:
            self._server.close()
            self._server = None
        if self._thread:
            self._thread.join(timeout=2.0)


# ═══════════════════════════════════════════════════════════════
#  Mock DOA source
# ═══════════════════════════════════════════════════════════════

class MockDOASource(DOASource):
    """
    Simulates DOA readings for testing without hardware.

    Patterns:
      - "sweep":  sweeps left → center → right → center → left ...
      - "random": random direction each read
      - "static": fixed angle
    """

    def __init__(self, interval: float = 1.0, pattern: str = "sweep",
                 static_angle: float = 90.0) -> None:
        self._interval = interval
        self._pattern = pattern
        self._static_angle = static_angle
        self._sweep_angle = -90.0
        self._sweep_dir = 1
        self._sweep_step = 8.0

    def read_azimuth_degrees(self) -> Optional[float]:
        time.sleep(self._interval)

        if self._pattern == "static":
            return self._static_angle

        if self._pattern == "random":
            return random.uniform(-90, 90)

        if self._pattern == "sweep":
            self._sweep_angle += self._sweep_dir * self._sweep_step
            if self._sweep_angle >= 90:
                self._sweep_angle = 90
                self._sweep_dir = -1
            elif self._sweep_angle <= -90:
                self._sweep_angle = -90
                self._sweep_dir = 1
            return self._sweep_angle

        return 0.0


# ═══════════════════════════════════════════════════════════════
#  DOA Reader — unified high-level interface
# ═══════════════════════════════════════════════════════════════

class DOAReader:
    """
    High-level DOA reader used by app.py.

    Perception ONLY — outputs angles, never controls gimbal.

    Usage:
        reader = DOAReader(config.doa)
        reader.start()
        angle = reader.get_angle()  # None = no valid reading
        reader.stop()
    """

    def __init__(self, config=None) -> None:
        self._config = config
        self._source: Optional[DOASource] = None

    def start(self) -> None:
        """Initialize the configured DOA source."""
        if self._config is None:
            logger.warning("No DOA config — using mock source")
            self._source = MockDOASource()
            return

        source_type = self._config.source

        if source_type == "subprocess":
            sc = self._config.subprocess
            logger.info("DOA source: subprocess → %s", sc.command)
            self._source = SubprocessDOASource(
                command=sc.command,
                shell=sc.shell,
                interval=sc.interval,
                encoding=sc.encoding,
            )

        elif source_type == "stdin":
            logger.info("DOA source: stdin (pipe mode)")
            self._source = StdinDOASource(timeout=0.1)

        elif source_type == "tcp":
            tc = self._config.tcp
            logger.info("DOA source: tcp → %s:%d", tc.host, tc.port)
            self._source = TcpDOASource(host=tc.host, port=tc.port)

        elif source_type == "mock":
            mc = self._config.mock
            logger.info("DOA source: mock (pattern=%s)", mc.pattern)
            self._source = MockDOASource(
                interval=mc.interval,
                pattern=mc.pattern,
                static_angle=mc.static_angle,
            )

        else:
            raise ValueError(f"Unknown DOA source type: {source_type}")

    def get_angle(self) -> Optional[float]:
        """
        Get the current DOA azimuth angle in degrees.

        Returns:
            float — azimuth angle (0°=front, +right, -left), or
            None  — no valid reading available.
        """
        if self._source is None:
            return None
        return self._source.read_azimuth_degrees()

    def stop(self) -> None:
        """Clean up resources."""
        if self._source:
            self._source.close()
            self._source = None
        logger.info("DOA reader stopped")
