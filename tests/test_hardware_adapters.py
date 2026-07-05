from __future__ import annotations

import json
import os
import threading
import tempfile
import time
import unittest
from unittest import mock
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from audio.respeaker_doa import LED_EFFECT_CMDID, ReSpeakerDOA
from core.event import ControlCommand
from hardware.recamera_client import RecameraClient
from hardware.recamera_audio_client import RecameraAudioClient
from services.voice_policy import VoicePolicy
from services.zhipu_voice import VOICE_PRESETS


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


class AudioBridgeHandler(BaseHTTPRequestHandler):
    commands = []
    failures_before_ok = 0
    post_count = 0

    def do_GET(self):
        if self.path.endswith("/audio/status"):
            self._json(200, {"ok": True, "state": "idle"})
        else:
            self._json(404, {})

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        payload = json.loads(self.rfile.read(length) or b"{}")
        self.commands.append((self.path, payload))
        if self.path.endswith("/audio/play"):
            self.__class__.post_count += 1
            if self.__class__.post_count <= self.__class__.failures_before_ok:
                self._json(503, {"ok": False, "error": "temporary_bridge_error"})
                return
            self._json(202, {"ok": True, "accepted": True, "state": "saving"})
            return
        if self.path.endswith("/audio/stop"):
            self._json(200, {"ok": True, "state": "stopped"})
            return
        self._json(404, {})

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
    def setUp(self) -> None:
        AudioBridgeHandler.commands = []
        AudioBridgeHandler.failures_before_ok = 0
        AudioBridgeHandler.post_count = 0

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
            self.assertTrue(client.start_session("hardware-test-2"))
            calibrate = ControlCommand.make("test", action="calibrate",
                                            session_id="hardware-test-2", sequence=1)
            self.assertTrue(client.apply_command(calibrate))
            self.assertTrue(client.stop_session("hardware-test"))
            self.assertTrue(any(path.endswith("/command") for path, _ in BridgeHandler.commands))
            self.assertTrue(any(path.endswith("/stop") for path, _ in BridgeHandler.commands))
            self.assertTrue(any(path.endswith("/calibrate") for path, _ in BridgeHandler.commands))
        finally:
            server.shutdown()
            server.server_close()

    def test_node_red_client_ignores_broken_environment_proxy(self) -> None:
        server = ThreadingHTTPServer(("0.0.0.0", 0), BridgeHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        proxy_env = {
            "http_proxy": "http://127.0.0.1:9",
            "HTTP_PROXY": "http://127.0.0.1:9",
            "no_proxy": "",
            "NO_PROXY": "",
        }
        try:
            with mock.patch.dict(os.environ, proxy_env, clear=False):
                client = RecameraClient(base_url=f"http://127.0.0.2:{server.server_port}")
                self.assertTrue(client.connect())
                self.assertAlmostEqual(client.get_status()["yaw"], 181.2)
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
            "/recamera-control/v1/calibrate",
            "/recamera-control/v1/stop",
            "/recamera-control/v1/status",
        }.issubset(urls))
        status_tick = next(node for node in flow if node.get("id") == "rc-status-tick")
        self.assertEqual(status_tick["repeat"], "1")
        self.assertEqual(status_tick["wires"], [["rc-get-yaw"]])
        yaw_cache = next(node for node in flow if node.get("id") == "rc-cache-yaw")
        self.assertEqual(yaw_cache["wires"], [["rc-get-pitch"]])
        self.assertTrue(any(node.get("type") == "catch" and node.get("id") == "rc-motor-catch" for node in flow))

    def test_voice_policy_build_returns_first_utterance(self) -> None:
        policy = VoicePolicy()
        first = policy.build("小屿语音测试。")
        self.assertIsNotNone(first)
        self.assertEqual(first.text, "小屿语音测试。")
        self.assertIsNotNone(policy.build("测试语句 1"))

    def test_zhipu_tts_presets_use_supported_system_voices(self) -> None:
        supported = {"tongtong", "chuichui", "xiaochen", "jam", "kazi", "douji", "luodo"}
        for preset in VOICE_PRESETS.values():
            self.assertIn(preset["voice"], supported)

    def test_recamera_audio_client_posts_configured_aplay_device(self) -> None:
        server = ThreadingHTTPServer(("127.0.0.1", 0), AudioBridgeHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        wav = Path(tempfile.gettempdir()) / "recamera_audio_client_test.wav"
        wav.write_bytes(b"RIFF....WAVEfmt ")
        try:
            with mock.patch.dict(os.environ, {"RECAMERA_APLAY_DEVICE": "auto"}, clear=False):
                client = RecameraAudioClient(base_url=f"http://127.0.0.1:{server.server_port}", timeout_ms=500)
                result = client.play(str(wav), "unit_audio")
            self.assertTrue(result["ok"])
            path, payload = AudioBridgeHandler.commands[-1]
            self.assertTrue(path.endswith("/audio/play"))
            self.assertEqual(payload["audio_id"], "unit_audio")
            self.assertEqual(payload["aplay_device"], "auto")
        finally:
            server.shutdown()
            server.server_close()
            wav.unlink(missing_ok=True)

    def test_recamera_audio_client_retries_transient_bridge_failure(self) -> None:
        server = ThreadingHTTPServer(("127.0.0.1", 0), AudioBridgeHandler)
        AudioBridgeHandler.failures_before_ok = 1
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        wav = Path(tempfile.gettempdir()) / "recamera_audio_client_retry.wav"
        wav.write_bytes(b"RIFF....WAVEfmt ")
        try:
            with mock.patch.dict(os.environ, {"RECAMERA_AUDIO_BRIDGE_RETRY_BASE_SEC": "0.05"}, clear=False):
                client = RecameraAudioClient(base_url=f"http://127.0.0.1:{server.server_port}", timeout_ms=500)
                result = client.play(str(wav), "retry_audio")
            self.assertTrue(result["ok"])
            self.assertGreaterEqual(AudioBridgeHandler.post_count, 2)
        finally:
            server.shutdown()
            server.server_close()
            wav.unlink(missing_ok=True)

    def test_audio_node_red_flow_uses_dynamic_aplay_device(self) -> None:
        path = Path(__file__).parents[1] / "deploy" / "node_red" / "recamera_audio_bridge_supplement.json"
        flow = json.loads(path.read_text(encoding="utf-8"))
        exec_node = next(node for node in flow if node.get("id") == "rc-audio-exec-aplay")
        prep_node = next(node for node in flow if node.get("id") == "rc-audio-prep-aplay")
        self.assertEqual(exec_node["command"], "sh -lc")
        self.assertIn("aplay -D", prep_node["func"])
        self.assertIn("aplay -l", prep_node["func"])
        self.assertNotIn("sudo aplay -D hw:1,0", json.dumps(flow))


if __name__ == "__main__":
    unittest.main()
