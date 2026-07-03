from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path

from audio.conversation_recorder import ConversationRecorder
from core.device_config_store import DeviceConfigStore
from core.event import ControlCommand
from hardware.control_worker import HardwareControlWorker


class FakeBridgeClient:
    def __init__(self) -> None:
        self.calls = []

    def get_status(self):
        return {"connected": True, "yaw": 180, "pitch": 90}

    def apply_command(self, command):
        self.calls.append(("command", command.sequence))
        return True

    def start_session(self, session_id, lease_ms):
        self.calls.append(("start", session_id, lease_ms))
        return True

    def renew_session(self, session_id, lease_ms):
        self.calls.append(("renew", session_id, lease_ms))
        return True

    def stop_session(self, session_id):
        self.calls.append(("stop", session_id))
        return True

    def emergency_stop(self, session_id):
        self.calls.append(("emergency", session_id))
        return True


class ReliabilityUpgradeTests(unittest.TestCase):
    def test_motion_queue_keeps_only_latest_command(self) -> None:
        client = FakeBridgeClient()
        worker = HardwareControlWorker(client, telemetry_interval=10)
        for sequence in (1, 2, 3):
            worker.submit(ControlCommand.make(
                "test", yaw=180 + sequence, pitch=90, speed=180,
                session_id="s", sequence=sequence, ttl_s=5,
            ))
        worker.start()
        deadline = time.time() + 1
        while not any(call[0] == "command" for call in client.calls) and time.time() < deadline:
            time.sleep(0.01)
        worker.close()
        self.assertEqual([call for call in client.calls if call[0] == "command"], [("command", 3)])

    def test_stop_has_priority_over_pending_motion(self) -> None:
        client = FakeBridgeClient()
        worker = HardwareControlWorker(client, telemetry_interval=10)
        worker.submit(ControlCommand.make("test", yaw=190, session_id="s", sequence=1, ttl_s=5))
        worker.stop_session("s")
        worker.start()
        deadline = time.time() + 1
        while len(client.calls) < 2 and time.time() < deadline:
            time.sleep(0.01)
        worker.close()
        self.assertEqual(client.calls[0], ("stop", "s"))

    def test_shared_device_config_is_atomic_and_versioned(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = DeviceConfigStore(Path(tmp) / "device.json")
            first = store.write("192.168.42.1")
            second = store.write("10.7.172.99")
            self.assertEqual(first["version"], 1)
            self.assertEqual(second["version"], 2)
            self.assertEqual(store.read()["device_ip"], "10.7.172.99")

    def test_stale_recording_is_closed_during_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            session = root / "session_old"
            session.mkdir()
            path = session / "session.json"
            path.write_text(json.dumps({"session_id": "session_old", "ended_at": None, "turns": 0}), encoding="utf-8")
            ConversationRecorder(root=root)
            recovered = json.loads(path.read_text(encoding="utf-8"))
            self.assertIsNotNone(recovered["ended_at"])
            self.assertEqual(recovered["end_reason"], "recovered_after_unclean_shutdown")


if __name__ == "__main__":
    unittest.main()
