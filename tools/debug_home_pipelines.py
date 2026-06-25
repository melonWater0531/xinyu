#!/usr/bin/env python3
"""
Debug /home single-person and multi-person control paths.

This script calls the same HTTP APIs used by dashboard/home.html and prints the
state that proves the two paths are mutually exclusive.
"""
from __future__ import annotations

import argparse
import json
import socket
import sys
import threading
import time
import urllib.error
import urllib.request


def request_json(method: str, url: str, payload: dict | None = None, timeout: float = 5.0) -> dict:
    body = None
    headers = {}
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        raise RuntimeError(f"{method} {url} -> HTTP {e.code}: {raw}") from e


def data_of(state: dict) -> dict:
    return state.get("data", state)


def ok(label: str, condition: bool, detail: str = "") -> bool:
    mark = "OK" if condition else "FAIL"
    print(f"[{mark}] {label}{': ' + detail if detail else ''}")
    return condition


def start_doa_sender(host: str, port: int, angle: float) -> tuple[threading.Event, threading.Thread]:
    """Continuously inject network DOA packets during the multi-person check."""
    stop = threading.Event()

    def run() -> None:
        deadline = time.monotonic() + 8.0
        sock = None
        while not stop.is_set() and time.monotonic() < deadline:
            try:
                sock = socket.create_connection((host, port), timeout=1.0)
                break
            except OSError:
                time.sleep(0.2)
        if sock is None:
            return
        with sock:
            while not stop.wait(0.1):
                packet = json.dumps({
                    "azimuth_deg": angle,
                    "speech": True,
                    "source": "debug_home_pipelines",
                }) + "\n"
                try:
                    sock.sendall(packet.encode("utf-8"))
                except OSError:
                    return

    thread = threading.Thread(target=run, daemon=True, name="doa-test-sender")
    thread.start()
    return stop, thread


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:8001", help="FastAPI base URL")
    ap.add_argument("--leave", choices=["single", "multi", "stopped"], default="stopped")
    ap.add_argument("--doa-host", default="127.0.0.1", help="TCP DOA listener host")
    ap.add_argument("--doa-port", type=int, default=9999, help="TCP DOA listener port")
    ap.add_argument("--doa-angle", type=float, default=35.0, help="Injected test angle")
    args = ap.parse_args()
    base = args.base.rstrip("/")

    failures = 0

    print(f"== Checking server: {base} ==")
    state = data_of(request_json("GET", f"{base}/api/state"))
    print("initial:", json.dumps({
        "tracking_mode": state.get("tracking_mode"),
        "gimbal": state.get("gimbal", {}),
        "sound_follow": state.get("sound_follow", {}),
        "conversation": state.get("conversation", {}),
    }, ensure_ascii=False, indent=2))

    print("\n== Single path ==")
    single = request_json("POST", f"{base}/api/single_track/start", {"speed": 360})
    time.sleep(0.5)
    state = data_of(request_json("GET", f"{base}/api/state"))
    failures += not ok("single API success", bool(single.get("success")))
    failures += not ok("tracking_mode is single", state.get("tracking_mode") == "single", str(state.get("tracking_mode")))
    failures += not ok("sound_tracking disabled", not bool(state.get("gimbal", {}).get("sound_tracking")))
    failures += not ok("face_tracking enabled", bool(state.get("face_tracking")))
    failures += not ok("conversation inactive", not bool(state.get("conversation", {}).get("active")))

    print("\n== Multi path ==")
    doa_stop, doa_thread = start_doa_sender(args.doa_host, args.doa_port, args.doa_angle)
    multi = request_json("POST", f"{base}/api/multi_track/start", {"save_audio": False})
    time.sleep(1.0)
    state = data_of(request_json("GET", f"{base}/api/state"))
    sound_follow = state.get("sound_follow", {})
    conversation = state.get("conversation", {})
    doa = state.get("doa", {})
    failures += not ok("multi API success", bool(multi.get("success")), conversation.get("error", ""))
    failures += not ok("tracking_mode is multi", state.get("tracking_mode") == "multi", str(state.get("tracking_mode")))
    failures += not ok("sound_tracking enabled", bool(state.get("gimbal", {}).get("sound_tracking")))
    failures += not ok("face_tracking disabled", not bool(state.get("face_tracking")))
    failures += not ok("audio recording optional", not bool(conversation.get("active")), str(conversation.get("mode")))
    failures += not ok("DOA available", bool(doa.get("available")))
    failures += not ok("DOA source is TCP", doa.get("source") == "tcp", str(doa.get("source")))
    failures += not ok("DOA packet received", int(doa.get("packet_count", 0)) > 0, str(doa.get("packet_count")))
    failures += not ok(
        "DOA angle propagated",
        abs(float(doa.get("doa_deg", -999)) - args.doa_angle) < 0.6,
        str(doa.get("doa_deg")),
    )
    failures += not ok("DOA speech active", bool(doa.get("has_speech")))
    print("sound_follow:", json.dumps(sound_follow, ensure_ascii=False, indent=2))
    print("doa:", json.dumps(doa, ensure_ascii=False, indent=2))
    print("conversation.current:", json.dumps(conversation.get("current", {}), ensure_ascii=False, indent=2))

    print("\n== Stop/leave state ==")
    if args.leave == "single":
        request_json("POST", f"{base}/api/single_track/start", {"speed": 360})
        print("left running: single")
    elif args.leave == "multi":
        request_json("POST", f"{base}/api/multi_track/start", {"save_audio": False})
        print("left running: multi")
    else:
        request_json("POST", f"{base}/api/multi_track/stop", {"finalize": True})
        request_json("POST", f"{base}/api/single_track/stop", {})
        print("left running: stopped")
    doa_stop.set()
    doa_thread.join(timeout=1.0)

    if failures:
        print(f"\nResult: {failures} check(s) failed")
        return 1
    print("\nResult: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
