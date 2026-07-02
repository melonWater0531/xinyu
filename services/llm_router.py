"""LLM routing: DeepSeek -> Zhipu GLM-4-Flash.

Endpoint handlers keep their existing local fallback behavior. This router
only tries cloud providers and returns an empty string when none can answer.
"""

import os
import aiohttp
from utils.logger import get_logger

logger = get_logger(__name__)

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_API_URL = os.getenv("DEEPSEEK_API_URL", "https://api.deepseek.com/chat/completions")
DEEPSEEK_MODEL   = os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash")

ZHIPU_API_KEY = os.getenv("ZHIPU_API_KEY", "")
ZHIPU_API_URL = "https://open.bigmodel.cn/api/paas/v4/chat/completions"
ZHIPU_MODEL   = "glm-4-flash"

_TIMEOUT = aiohttp.ClientTimeout(total=15)


async def _call_openai_compat(url, api_key, model, messages, max_tokens) -> str:
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {"model": model, "messages": messages, "max_tokens": max_tokens,
               "temperature": 0.8, "top_p": 0.9}
    try:
        async with aiohttp.ClientSession(timeout=_TIMEOUT) as s:
            async with s.post(url, json=payload, headers=headers) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data["choices"][0]["message"]["content"].strip()
                logger.warning("LLM %s => %d: %s", url, resp.status, (await resp.text())[:80])
    except Exception as exc:
        logger.warning("LLM %s error: %s", url, str(exc)[:80])
    return ""


class LLMRouter:
    async def complete(self, messages: list, max_tokens: int = 600) -> str:
        """Try DeepSeek first, then Zhipu. Return "" when cloud providers fail."""
        result = await self.complete_with_provider(messages, max_tokens)
        return result["text"]

    async def complete_with_provider(self, messages: list, max_tokens: int = 600) -> dict:
        """Try cloud providers and report which one produced the text."""
        if DEEPSEEK_API_KEY:
            reply = await _call_openai_compat(
                DEEPSEEK_API_URL, DEEPSEEK_API_KEY, DEEPSEEK_MODEL, messages, max_tokens)
            if reply:
                logger.info("LLM routed to deepseek")
                return {"text": reply, "provider": "deepseek"}

        if ZHIPU_API_KEY:
            reply = await _call_openai_compat(
                ZHIPU_API_URL, ZHIPU_API_KEY, ZHIPU_MODEL, messages, max_tokens)
            if reply:
                logger.info("LLM routed to zhipu")
                return {"text": reply, "provider": "zhipu"}

        logger.info("LLM cloud providers unavailable; endpoint fallback will handle response")
        return {"text": "", "provider": "none"}


router = LLMRouter()
