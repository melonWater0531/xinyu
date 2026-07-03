#!/usr/bin/env python3
"""Verify Zhipu API configuration without exposing secrets.

Default behavior:
  - no ZHIPU_API_KEY: print JSON with configured=false and exit 1
  - with key: run a tiny GLM chat smoke test
  - with --asr-sample: also run a GLM-ASR smoke test for that wav file
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _base() -> dict:
    key = os.environ.get("ZHIPU_API_KEY", "")
    return {
        "configured": bool(key),
        "key_len": len(key) if key else 0,
        "llm": {"tested": False, "status": "offline", "error_type": ""},
        "asr": {"tested": False, "status": "skipped", "error_type": ""},
    }


async def _run(asr_sample: str = "") -> tuple[int, dict]:
    result = _base()
    if not result["configured"]:
        result["reason"] = "ZHIPU_API_KEY is not set"
        return 1, result

    import services.llm_router as llm_router

    text, err = await llm_router._call_openai_compat(
        llm_router.ZHIPU_API_URL,
        os.environ["ZHIPU_API_KEY"],
        llm_router.ZHIPU_MODEL,
        [{"role": "user", "content": "回复 OK"}],
        max_tokens=8,
        temperature=0.1,
    )
    result["llm"] = {
        "tested": True,
        "status": "ready" if text else "degraded",
        "provider": "zhipu",
        "error_type": err or "",
        "last_success_at": 0.0,
    }

    if asr_sample:
        import services.cloud_asr as cloud_asr_module

        path = Path(asr_sample)
        if not path.exists():
            result["asr"] = {"tested": True, "status": "offline", "error_type": "sample_missing"}
        else:
            text = await cloud_asr_module.cloud_asr._zhipu(str(path))
            result["asr"] = {
                "tested": True,
                "status": "ready" if text else "degraded",
                "error_type": cloud_asr_module.cloud_asr.last_error,
                "last_success_at": cloud_asr_module.cloud_asr.last_success_at,
            }

    ok = result["llm"]["status"] == "ready" and result["asr"]["status"] in {"ready", "skipped"}
    return (0 if ok else 2), result


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify Zhipu LLM/ASR availability")
    parser.add_argument("--asr-sample", default="", help="Optional short wav file for GLM-ASR smoke test")
    args = parser.parse_args()
    code, result = asyncio.run(_run(args.asr_sample))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
