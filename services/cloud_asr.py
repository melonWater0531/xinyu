"""Cloud ASR: Zhipu GLM-ASR with local faster-whisper fallback."""

import os
import aiohttp
from pathlib import Path
from utils.logger import get_logger

logger = get_logger(__name__)

ZHIPU_API_KEY = os.getenv("ZHIPU_API_KEY", "")
ZHIPU_ASR_URL = "https://open.bigmodel.cn/api/paas/v4/audio/transcriptions"
ASR_PROVIDER = os.getenv("ASR_PROVIDER", "zhipu")  # zhipu | local


class CloudASR:
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
            async with aiohttp.ClientSession() as s:
                form = aiohttp.FormData()
                form.add_field("model", "glm-asr")
                with open(path, "rb") as f:
                    form.add_field("file", f, filename=path.name,
                                   content_type="audio/wav")
                headers = {"Authorization": f"Bearer {ZHIPU_API_KEY}"}
                async with s.post(ZHIPU_ASR_URL, data=form, headers=headers,
                                  timeout=aiohttp.ClientTimeout(total=60)) as resp:
                    if resp.status == 200:
                        return (await resp.json()).get("text", "").strip()
                    logger.warning("Zhipu ASR => %d: %s",
                                   resp.status, (await resp.text())[:80])
        except Exception as exc:
            logger.warning("Zhipu ASR error: %s", str(exc)[:80])
        return ""

    async def _local(self, audio_path: str) -> str:
        try:
            from audio.transcriber import transcribe_wav
            return await transcribe_wav(audio_path)
        except Exception:
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
