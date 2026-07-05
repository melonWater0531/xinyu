"""Single-threaded hardware I/O scheduler for the Node-RED bridge."""
from __future__ import annotations

import threading
import time
from collections import deque
from typing import Callable, Optional

from core.event import ControlCommand
from core.device_config import device_http_url
from hardware.recamera_client import RecameraClient


class HardwareControlWorker:
    """Serialize bridge I/O and coalesce motion commands.

    Stop/lifecycle work is FIFO and has priority.  Motion is a one-slot queue,
    so slow hardware can never build a backlog of stale face positions.
    """

    def __init__(self, client: RecameraClient, *, lease_ms: int = 5000,
                 telemetry_interval: float = 1.0,
                 on_status: Optional[Callable[[dict], None]] = None) -> None:
        self.client = client
        self.lease_ms = int(lease_ms)
        self.telemetry_interval = float(telemetry_interval)
        self.on_status = on_status
        self._priority: deque[tuple[str, tuple]] = deque()
        self._motion: Optional[ControlCommand] = None
        self._cv = threading.Condition()
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._state = {
            "queue_state": "idle", "command_state": "idle", "command_id": 0,
            "last_error": "", "last_result_at": None, "command_started_at": None,
            "consecutive_failures": 0,
            "last_hardware_command_error": "", "hardware_command_queue_size": 0,
        }

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True, name="gimbal-io")
        self._thread.start()

    def close(self) -> None:
        deadline = time.monotonic() + 1.5
        with self._cv:
            self._motion = None
            while self._running and (self._priority or self._state["queue_state"] == "executing") and time.monotonic() < deadline:
                self._cv.wait(timeout=0.05)
            self._running = False
            self._cv.notify_all()
        if self._thread:
            self._thread.join(timeout=2.0)
        self._thread = None

    def start_session(self, session_id: str) -> None:
        self._enqueue("start", session_id)

    def renew_session(self, session_id: str) -> None:
        self._enqueue("renew", session_id)

    def stop_session(self, session_id: str) -> None:
        self._enqueue("stop", session_id, front=True)

    def emergency_stop(self, session_id: str) -> None:
        self._enqueue("emergency", session_id, front=True)

    def reconfigure(self, device_ip: str) -> None:
        self._enqueue("reconfigure", device_ip, front=True)

    def submit(self, command: ControlCommand) -> None:
        with self._cv:
            if command.stop or command.action == "calibrate":
                self._priority.appendleft(("command", (command,)))
            else:
                self._motion = command
            self._state["queue_state"] = "pending"
            self._state["command_state"] = "accepted"
            self._state["command_started_at"] = round(time.time(), 3)
            self._state["command_id"] = max(self._state["command_id"] + 1, int(command.sequence or 0))
            self._cv.notify()

    def snapshot(self) -> dict:
        bridge_state = self._bridge_state()
        with self._cv:
            return {
                **self._state,
                **bridge_state,
                "hardware_command_queue_size": len(self._priority) + (1 if self._motion is not None else 0),
            }

    def _enqueue(self, kind: str, session_id: str, front: bool = False) -> None:
        with self._cv:
            if kind in {"start", "renew"}:
                self._priority = deque(
                    item for item in self._priority
                    if not (item[0] in {"start", "renew"} and item[1] == (str(session_id),))
                )
            item = (kind, (str(session_id),))
            (self._priority.appendleft if front else self._priority.append)(item)
            self._state["queue_state"] = "pending"
            self._cv.notify()

    def _run(self) -> None:
        next_telemetry = 0.0
        while True:
            item = None
            with self._cv:
                if not self._running:
                    return
                if self._priority:
                    item = self._priority.popleft()
                elif self._motion is not None:
                    item, self._motion = ("command", (self._motion,)), None
                elif time.monotonic() < next_telemetry:
                    self._cv.wait(timeout=min(0.25, next_telemetry - time.monotonic()))
                    continue
            if item is None:
                status = self.client.get_status()
                next_telemetry = time.monotonic() + self.telemetry_interval
                if self.on_status:
                    self.on_status({
                        **(status or {"connected": False, "source": "bridge_unavailable", "last_error": "status unavailable"}),
                        **self._bridge_state(),
                    })
                with self._cv:
                    if status and status.get("verified"):
                        self._state["command_state"] = "verified"
                    elif status and (status.get("last_command") or {}).get("state") == "failed":
                        self._state["command_state"] = "failed"
                        self._state["last_error"] = str(status.get("last_error") or "motor command failed")
                    elif (self._state["command_state"] == "executing" and self._state["command_started_at"]
                          and time.time() - float(self._state["command_started_at"]) > 3.0):
                        self._state["command_state"] = "actuation_unverified"
                self._record(bool(status), "status unavailable" if not status else "", telemetry=True)
                continue
            kind, args = item
            self._state["queue_state"] = "executing"
            self._state["command_state"] = "executing"
            try:
                if kind == "start":
                    ok = self.client.start_session(args[0], self.lease_ms)
                elif kind == "renew":
                    ok = self.client.renew_session(args[0], self.lease_ms)
                elif kind == "stop":
                    ok = self.client.stop_session(args[0])
                elif kind == "emergency":
                    ok = self.client.emergency_stop(args[0])
                elif kind == "reconfigure":
                    ok = self.client.reconfigure(device_http_url(args[0], required=True))
                else:
                    ok = self.client.apply_command(args[0])
                self._record(bool(ok), "bridge request failed" if not ok else "", accepted_only=(kind == "command"))
            except Exception as exc:
                self._record(False, str(exc)[:160])

    def _record(self, ok: bool, error: str, telemetry: bool = False, accepted_only: bool = False) -> None:
        bridge_state = self._bridge_state()
        with self._cv:
            self._state["queue_state"] = "idle"
            if not telemetry:
                self._state["command_state"] = ("executing" if accepted_only else "verified") if ok else "failed"
            if not (telemetry and ok and self._state["command_state"] in {"failed", "actuation_unverified"}):
                self._state["last_error"] = "" if ok else error
            self._state["last_result_at"] = round(time.time(), 3)
            self._state["consecutive_failures"] = 0 if ok else self._state["consecutive_failures"] + 1
            if not telemetry:
                self._state["last_hardware_command_error"] = "" if ok else error
            self._state.update(bridge_state)
            self._state["hardware_command_queue_size"] = len(self._priority) + (1 if self._motion is not None else 0)
            self._cv.notify_all()

    def _bridge_state(self) -> dict:
        snapshot = getattr(self.client, "bridge_state", None)
        return snapshot() if callable(snapshot) else {}
