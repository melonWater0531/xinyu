"""Cloud ASR: Zhipu GLM-ASR with local faster-whisper fallback."""

import os
import asyncio
import time
import aiohttp
from pathlib import Path
from utils.logger import get_logger

logger = get_logger(__name__)

ZHIPU_API_KEY = os.getenv("ZHIPU_API_KEY", "")
ZHIPU_ASR_URL = "https://open.bigmodel.cn/api/paas/v4/audio/transcriptions"
ASR_PROVIDER = os.getenv("ASR_PROVIDER", "zhipu")  # zhipu | local

_TIMEOUT = aiohttp.ClientTimeout(total=60, connect=5)


class CloudASR:
    def __init__(self):
        self.last_error = ""          # {auth, quota, timeout, network, bad_response, local_missing}
        self.last_error_at = 0.0
        self.last_success_at = 0.0

    def _record_error(self, err: str):
        self.last_error = err
        self.last_error_at = time.time()

    async def transcribe(self, audio_path: str) -> str:
        """Transcribe one audio file; return "" if all providers fail."""
        if ASR_PROVIDER != "local" and ZHIPU_API_KEY:
            text = await self._zhipu(audio_path)
            if text:
                return text
        return await self._local(audio_path)

    async def _zhipu(self, audio_path: str) -> str:
        path = Path(audio_path)
        if not path.exists():
            return ""
        try:
            async with aiohttp.ClientSession(timeout=_TIMEOUT) as s:
                form = aiohttp.FormData()
                form.add_field("model", "glm-asr")
                with open(path, "rb") as f:
                    form.add_field("file", f, filename=path.name,
                                   content_type="audio/wav")
                    headers = {"Authorization": f"Bearer {ZHIPU_API_KEY}"}
                    async with s.post(ZHIPU_ASR_URL, data=form, headers=headers) as resp:
                        if resp.status == 200:
                            text = (await resp.json()).get("text", "").strip()
                            if text:
                                self.last_error = ""
                                self.last_success_at = time.time()
                            return text
                        err = ("auth" if resp.status in (401, 403)
                               else "quota" if resp.status == 429
                               else "bad_response")
                        self._record_error(err)
                        logger.warning("Zhipu ASR => %d (%s): %s",
                                       resp.status, err, (await resp.text())[:80])
        except asyncio.TimeoutError:
            self._record_error("timeout")
            logger.warning("Zhipu ASR timeout: %s", path.name)
        except aiohttp.ClientError as exc:
            self._record_error("network")
            logger.warning("Zhipu ASR network error: %s", str(exc)[:80])
        except Exception as exc:
            self._record_error("network")
            logger.warning("Zhipu ASR error: %s", str(exc)[:80])
        return ""

    async def _local(self, audio_path: str) -> str:
        try:
            from audio.transcriber import transcribe_wav
        except ImportError as exc:
            self._record_error("local_missing")
            logger.warning("Local ASR unavailable (faster-whisper not installed?): %s", str(exc)[:80])
            return ""
        try:
            text = await transcribe_wav(audio_path)
            if text:
                self.last_error = ""
                self.last_success_at = time.time()
            return text
        except Exception as exc:
            self._record_error("local_error")
            logger.warning("Local ASR error: %s", str(exc)[:80])
            return ""

    async def transcribe_segments(self, wav_paths: list[str]) -> str:
        """Transcribe paths in order and skip failed segments."""
        parts = []
        for p in wav_paths:
            text = await self.transcribe(p)
            if text:
                parts.append(text)
        return "\n".join(parts)


cloud_asr = CloudASR()
