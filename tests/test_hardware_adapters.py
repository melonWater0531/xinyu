from __future__ import annotations

import json
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from audio.respeaker_doa import LED_EFFECT_CMDID, ReSpeakerDOA
from core.event import ControlCommand
from hardware.recamera_client import RecameraClient


class FakeUsb:
    def __init__(self) -> None:
        self.writes = []
        self.values = {12: b"\x00", 13: b"\x50", 17: b"\x30\x20\x10\x00\x8b\xc9\x24\x00"}

    def ctrl_transfer(self, request_type, request, value, index, payload, timeout):
        if value & 0x80:
            data = self.values.get(value & 0x7F, b"\x00" * (int(payload) - 1))
            return bytes([0]) + data
        self.writes.append((value, index, bytes(payload)))
        self.values[value] = bytes(payload)
        return []


class BridgeHandler(BaseHTTPRequestHandler):
    commands = []

    def do_GET(self):
        if self.path.endswith("/status"):
            self._json(200, {"connected": True, "yaw": 181.2, "pitch": 88.5, "yaw_speed": 180, "pitch_speed": 160, "timestamp": int(time.time() * 1000), "source": "motor_readback"})
        else:
            self._json(404, {})

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        payload = json.loads(self.rfile.read(length) or b"{}")
        self.commands.append((self.path, payload))
        self._json(202, {"ok": True, "accepted": True})

    def _json(self, status, payload):
        raw = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *_args):
        pass


class HardwareAdapterTests(unittest.TestCase):
    def test_respeaker_led_uses_hardware_doa_effect(self) -> None:
        reader = ReSpeakerDOA()
        reader._dev = FakeUsb()
        self.assertTrue(reader.set_led_doa())
        self.assertEqual(reader.led_status["effect"], "doa")
        self.assertTrue(any(cmd == LED_EFFECT_CMDID and payload == b"\x04" for cmd, _resid, payload in reader._dev.writes))
        self.assertTrue(reader.set_led_off())
        self.assertEqual(reader.led_status["effect"], "off")

    def test_node_red_bridge_command_and_readback(self) -> None:
        server = ThreadingHTTPServer(("127.0.0.1", 0), BridgeHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            client = RecameraClient(base_url=f"http://127.0.0.1:{server.server_port}")
            self.assertTrue(client.connect())
            self.assertTrue(client.start_session("hardware-test"))
            command = ControlCommand.make("test", yaw=190, pitch=95, speed=180,
                                          session_id="hardware-test", sequence=1)
            self.assertTrue(client.apply_command(command))
            status = client.get_status()
            self.assertEqual(status["source"], "motor_readback")
            self.assertAlmostEqual(status["yaw"], 181.2)
            self.assertTrue(client.emergency_stop())
            self.assertTrue(client.stop_session("hardware-test"))
            self.assertTrue(any(path.endswith("/command") for path, _ in BridgeHandler.commands))
            self.assertTrue(any(path.endswith("/stop") for path, _ in BridgeHandler.commands))
        finally:
            server.shutdown()
            server.server_close()

    def test_node_red_flow_contains_required_endpoints(self) -> None:
        path = Path(__file__).parents[1] / "deploy" / "node_red" / "recamera_control_bridge.json"
        flow = json.loads(path.read_text(encoding="utf-8"))
        urls = {node.get("url") for node in flow if node.get("type") == "http in"}
        self.assertTrue({
            "/recamera-control/v1/session/start",
            "/recamera-control/v1/session/heartbeat",
            "/recamera-control/v1/session/stop",
            "/recamera-control/v1/command",
            "/recamera-control/v1/stop",
            "/recamera-control/v1/status",
        }.issubset(urls))


if __name__ == "__main__":
    unittest.main()
