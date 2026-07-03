#!/usr/bin/env python3
"""Read-only verification for a deployed reCamera Node-RED bridge."""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path
from urllib import error, request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.device_config import normalize_device_ip


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("device_ip")
    parser.add_argument("--samples", type=int, default=10)
    parser.add_argument("--timeout", type=float, default=1.0)
    args = parser.parse_args()
    device_ip = normalize_device_ip(args.device_ip, required=True)
    url = f"http://{device_ip}:1880/recamera-control/v1/status"
    opener = request.build_opener(request.ProxyHandler({}))
    latencies, failures, last = [], [], {}
    http_status = None
    error_type = ""
    for _ in range(max(1, args.samples)):
        started = time.monotonic()
        try:
            with opener.open(url, timeout=args.timeout) as response:
                http_status = response.status
                last = json.loads(response.read().decode("utf-8"))
            latencies.append((time.monotonic() - started) * 1000)
        except error.HTTPError as exc:
            http_status = exc.code
            error_type = f"http_{exc.code}"
            try:
                last = json.loads(exc.read().decode("utf-8"))
            except (ValueError, OSError):
                pass
            failures.append(str(exc))
        except (OSError, error.URLError, ValueError) as exc:
            error_type = "timeout" if "timed out" in str(exc).lower() else type(exc).__name__
            failures.append(str(exc))
        time.sleep(0.1)
    result = {
        "url": url,
        "device_ip": device_ip,
        "proxy_bypassed": True,
        "http_status": http_status,
        "samples": args.samples,
        "successes": len(latencies),
        "failures": len(failures),
        "latency_ms": {
            "min": round(min(latencies), 1) if latencies else None,
            "mean": round(statistics.mean(latencies), 1) if latencies else None,
            "max": round(max(latencies), 1) if latencies else None,
        },
        "latency_target_ms": 100,
        "latency_target_met": bool(latencies and max(latencies) < 100),
        "last_status": last,
        "last_error": failures[-1] if failures else "",
        "error_type": error_type,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if latencies and max(latencies) < 100 else 1


if __name__ == "__main__":
    raise SystemExit(main())
