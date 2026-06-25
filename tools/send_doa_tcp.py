#!/usr/bin/env python3
"""Forward ReSpeaker/xvf_host DOA readings to recamera_multimodal over TCP."""
from __future__ import annotations

import argparse
import json
import socket
import subprocess
import sys
import time


def connect(host: str, port: int, retry_sec: float) -> socket.socket:
    while True:
        try:
            sock = socket.create_connection((host, port), timeout=3.0)
            sock.settimeout(3.0)
            print(f"Connected to DOA receiver at {host}:{port}", file=sys.stderr)
            return sock
        except OSError as exc:
            print(f"DOA receiver unavailable ({exc}); retrying...", file=sys.stderr)
            time.sleep(retry_sec)


def send_line(sock: socket.socket, line: str) -> None:
    sock.sendall((line.rstrip("\r\n") + "\n").encode("utf-8"))


def read_command(command: str, interval: float):
    while True:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=max(2.0, interval + 1.0),
        )
        output = result.stdout.strip()
        if output:
            yield output
        time.sleep(interval)


def read_stdin():
    for line in sys.stdin:
        if line.strip():
            yield line.strip()


def read_mock(angle: float, interval: float):
    while True:
        yield json.dumps({
            "azimuth_deg": angle,
            "speech": True,
            "source": "send_doa_tcp_mock",
        })
        time.sleep(interval)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Forward xvf_host/plain/JSON DOA lines to the FastAPI TCP receiver"
    )
    parser.add_argument("--host", default="127.0.0.1", help="FastAPI/WSL host")
    parser.add_argument("--port", type=int, default=9999, help="DOA TCP port")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--command", help='Polling command, e.g. "xvf_host.exe AUDIO_MGR_SELECTED_AZIMUTHS"')
    source.add_argument("--stdin", action="store_true", help="Forward newline-delimited stdin")
    source.add_argument("--mock-angle", type=float, help="Continuously send a fixed test angle")
    parser.add_argument("--interval", type=float, default=0.1, help="Polling/send interval")
    parser.add_argument("--retry", type=float, default=1.0, help="Reconnect delay")
    args = parser.parse_args()

    if args.command:
        lines = read_command(args.command, args.interval)
    elif args.stdin:
        lines = read_stdin()
    else:
        lines = read_mock(args.mock_angle, args.interval)

    sock = connect(args.host, args.port, args.retry)
    try:
        for line in lines:
            while True:
                try:
                    send_line(sock, line)
                    break
                except OSError:
                    try:
                        sock.close()
                    except OSError:
                        pass
                    sock = connect(args.host, args.port, args.retry)
    except KeyboardInterrupt:
        return 0
    finally:
        sock.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
