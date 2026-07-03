#!/usr/bin/env python3
"""Read-only verification for a deployed reCamera Node-RED bridge."""
from __future__ import annotations

import argparse
import json
import statistics
import time
from urllib import error, request


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("device_ip")
    parser.add_argument("--samples", type=int, default=10)
    parser.add_argument("--timeout", type=float, default=1.0)
    args = parser.parse_args()
    url = f"http://{args.device_ip}:1880/recamera-control/v1/status"
    latencies, failures, last = [], [], {}
    for _ in range(max(1, args.samples)):
        started = time.monotonic()
        try:
            with request.urlopen(url, timeout=args.timeout) as response:
                last = json.loads(response.read().decode("utf-8"))
            latencies.append((time.monotonic() - started) * 1000)
        except (OSError, error.URLError, error.HTTPError, ValueError) as exc:
            failures.append(str(exc))
        time.sleep(0.1)
    result = {
        "url": url,
        "samples": args.samples,
        "successes": len(latencies),
        "failures": len(failures),
        "latency_ms": {
            "min": round(min(latencies), 1) if latencies else None,
            "mean": round(statistics.mean(latencies), 1) if latencies else None,
            "max": round(max(latencies), 1) if latencies else None,
        },
        "last_status": last,
        "last_error": failures[-1] if failures else "",
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if latencies and max(latencies) < 100 else 1


if __name__ == "__main__":
    raise SystemExit(main())
