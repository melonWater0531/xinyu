from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from unittest import mock
from pathlib import Path

from audio.conversation_recorder import ConversationRecorder
from core.device_config_store import DeviceConfigStore
from core.device_config import bypass_proxy_for_device
from core.event import ControlCommand
from hardware.control_worker import HardwareControlWorker
from hardware.recamera_client import RecameraClient


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
    def test_gimbal_bridge_circuit_opens_and_half_open_probe_recovers(self) -> None:
        class TimeoutOpener:
            def open(self, *_args, **_kwargs):
                raise TimeoutError("bridge request timed out")

        class JsonResponse:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return b'{"connected":true,"timestamp":9999999999999}'

        class SuccessOpener:
            def open(self, *_args, **_kwargs):
                return JsonResponse()

        client = RecameraClient(base_url="http://127.0.0.1:1880", timeout_ms=50, retry=1)
        client._dry_run = False
        client._direct_opener = TimeoutOpener()
        for _ in range(3):
            self.assertIsNone(client.get_status())
        opened = client.bridge_state()
        self.assertEqual(opened["gimbal_bridge_status"], "timeout")
        self.assertTrue(opened["gimbal_bridge_circuit_open"])
        requests_at_open = client.request_count
        self.assertIsNone(client.get_status())
        self.assertEqual(client.request_count, requests_at_open, "open circuit must skip bridge I/O")

        client._circuit_open_until = time.monotonic() - 0.01
        client._direct_opener = SuccessOpener()
        self.assertIsNotNone(client.get_status())
        recovered = client.bridge_state()
        self.assertEqual(recovered["gimbal_bridge_status"], "ok")
        self.assertFalse(recovered["gimbal_bridge_circuit_open"])

        client._direct_opener = TimeoutOpener()
        for _ in range(3):
            self.assertIsNone(client.get_status())
        client._circuit_open_until = time.monotonic() - 0.01
        self.assertIsNone(client.get_status())
        failed_probe = client.bridge_state()
        self.assertEqual(failed_probe["gimbal_bridge_status"], "timeout")
        self.assertTrue(failed_probe["gimbal_bridge_circuit_open"])

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

    def test_proxy_bypass_merges_both_env_variants_and_exact_host(self) -> None:
        with mock.patch.dict(os.environ, {
            "NO_PROXY": "localhost,upper.example",
            "no_proxy": "127.0.0.1,lower.example,192.168.*",
        }, clear=False):
            bypass_proxy_for_device("192.168.225.84")
            upper = os.environ["NO_PROXY"].split(",")
            lower = os.environ["no_proxy"].split(",")
            self.assertEqual(upper, lower)
            self.assertIn("192.168.225.84", upper)
            self.assertIn("upper.example", upper)
            self.assertIn("lower.example", upper)

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
