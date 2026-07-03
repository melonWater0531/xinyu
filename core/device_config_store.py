"""Process-shared runtime device configuration.

The dashboard and the control runtime are separate processes.  This small,
atomic JSON store keeps them on the same reCamera address without making the
FastAPI process a hardware owner.
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from pathlib import Path

from core.device_config import normalize_device_ip


DEFAULT_PATH = Path(__file__).resolve().parents[1] / "runtime" / "device_config.json"


class DeviceConfigStore:
    def __init__(self, path: str | Path = DEFAULT_PATH) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()

    def read(self) -> dict:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return {"device_ip": "", "version": 0, "updated_at": None}
        return {
            "device_ip": normalize_device_ip(str(raw.get("device_ip", ""))),
            "version": int(raw.get("version", 0) or 0),
            "updated_at": raw.get("updated_at"),
        }

    def write(self, device_ip: str) -> dict:
        ip = normalize_device_ip(device_ip, required=True)
        with self._lock:
            previous = self.read()
            data = {
                "device_ip": ip,
                "version": int(previous.get("version", 0)) + 1,
                "updated_at": round(time.time(), 3),
            }
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, temp_name = tempfile.mkstemp(prefix="device-config-", suffix=".json", dir=self.path.parent)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    json.dump(data, handle, ensure_ascii=False, indent=2)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temp_name, self.path)
            finally:
                try:
                    os.unlink(temp_name)
                except FileNotFoundError:
                    pass
            return data


device_config_store = DeviceConfigStore()
