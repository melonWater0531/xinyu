"""LLM routing: DeepSeek -> Zhipu GLM-4-Flash.

Endpoint handlers keep their existing local fallback behavior. This router
only tries cloud providers and returns an empty string when none can answer.

Failures are classified ({auth, quota, timeout, network, bad_response}) and
tracked per provider, with one retry on transient errors and a simple
circuit breaker (3 consecutive failures -> open 120s -> half-open probe).
"""

import asyncio
import os
import time
import aiohttp
from utils.logger import get_logger

logger = get_logger(__name__)

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_API_URL = os.getenv("DEEPSEEK_API_URL", "https://api.deepseek.com/chat/completions")
DEEPSEEK_MODEL   = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")

ZHIPU_API_KEY = os.getenv("ZHIPU_API_KEY", "")
ZHIPU_API_URL = os.getenv("ZHIPU_API_URL", "https://open.bigmodel.cn/api/paas/v4/chat/completions")
ZHIPU_MODEL   = os.getenv("ZHIPU_MODEL", "glm-4-flash")

_TIMEOUT = aiohttp.ClientTimeout(total=15, connect=5)

# Errors worth one retry (transient); auth/quota/bad_response are not.
_RETRYABLE = {"timeout", "network", "server_error"}

BREAKER_THRESHOLD = 3      # consecutive failures to open
BREAKER_OPEN_SEC = 120.0   # stay open this long, then allow one probe


def _classify_status(status: int) -> str:
    if status in (401, 403):
        return "auth"
    if status == 429:
        return "quota"
    if status >= 500:
        return "server_error"
    return "bad_response"


async def _call_openai_compat(url, api_key, model, messages, max_tokens, temperature=0.8):
    """Single attempt. Returns (text, error) where error is None on success,
    else one of {auth, quota, timeout, network, server_error, bad_response}."""
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {"model": model, "messages": messages, "max_tokens": max_tokens,
               "temperature": float(temperature), "top_p": 0.9}
    try:
        async with aiohttp.ClientSession(timeout=_TIMEOUT) as s:
            async with s.post(url, json=payload, headers=headers) as resp:
                if resp.status == 200:
                    try:
                        data = await resp.json()
                        text = data["choices"][0]["message"]["content"].strip()
                    except Exception as exc:
                        logger.warning("LLM %s malformed response: %s", url, str(exc)[:80])
                        return "", "bad_response"
                    return text, None
                body = (await resp.text())[:120]
                err = _classify_status(resp.status)
                logger.warning("LLM %s => %d (%s): %s", url, resp.status, err, body)
                return "", err
    except asyncio.TimeoutError:
        logger.warning("LLM %s timeout", url)
        return "", "timeout"
    except aiohttp.ClientError as exc:
        logger.warning("LLM %s network error: %s", url, str(exc)[:80])
        return "", "network"
    except Exception as exc:
        logger.warning("LLM %s error: %s", url, str(exc)[:80])
        return "", "network"


class _ProviderState:
    def __init__(self, name: str):
        self.name = name
        self.consecutive_failures = 0
        self.opened_at = 0.0        # breaker open timestamp (0 = closed)
        self.last_error = ""        # last classified error
        self.last_error_at = 0.0
        self.last_success_at = 0.0

    @property
    def breaker_state(self) -> str:
        if not self.opened_at:
            return "closed"
        if time.time() - self.opened_at >= BREAKER_OPEN_SEC:
            return "half_open"
        return "open"

    def allow_request(self) -> bool:
        return self.breaker_state != "open"

    def record_success(self):
        self.consecutive_failures = 0
        self.opened_at = 0.0
        self.last_success_at = time.time()

    def record_failure(self, error: str):
        self.last_error = error
        self.last_error_at = time.time()
        self.consecutive_failures += 1
        if self.consecutive_failures >= BREAKER_THRESHOLD:
            self.opened_at = time.time()

    def as_dict(self, configured: bool) -> dict:
        return {
            "configured": configured,
            "breaker_state": self.breaker_state if configured else "closed",
            "last_error": self.last_error,
            "last_error_at": self.last_error_at,
            "last_success_at": self.last_success_at,
        }


class LLMRouter:
    def __init__(self):
        self._states = {"deepseek": _ProviderState("deepseek"),
                        "zhipu": _ProviderState("zhipu")}

    def _providers(self):
        return [
            ("deepseek", DEEPSEEK_API_URL, DEEPSEEK_API_KEY, DEEPSEEK_MODEL),
            ("zhipu", ZHIPU_API_URL, ZHIPU_API_KEY, ZHIPU_MODEL),
        ]

    def status(self) -> dict:
        """Per-provider health for the status endpoint."""
        keys = {"deepseek": bool(DEEPSEEK_API_KEY), "zhipu": bool(ZHIPU_API_KEY)}
        return {name: st.as_dict(keys[name]) for name, st in self._states.items()}

    async def _try_provider(self, name, url, key, model, messages, max_tokens, temperature) -> str:
        state = self._states[name]
        if not state.allow_request():
            logger.debug("LLM %s breaker open, skipping", name)
            return ""
        text, err = await _call_openai_compat(url, key, model, messages, max_tokens, temperature)
        if err in _RETRYABLE:
            text, err = await _call_openai_compat(url, key, model, messages, max_tokens, temperature)
        if err is None and text:
            state.record_success()
            return text
        state.record_failure(err or "bad_response")
        return ""

    async def complete(self, messages: list, max_tokens: int = 600, temperature: float = 0.8) -> str:
        """Try DeepSeek first, then Zhipu. Return "" when cloud providers fail."""
        result = await self.complete_with_provider(messages, max_tokens, temperature)
        return result["text"]

    async def stream(self, messages: list, max_tokens: int = 600, temperature: float = 0.8):
        """Async generator yielding (provider, delta_text) chunks.

        Provider failover happens only before the first token; a mid-stream
        failure raises so the caller can emit an error event.
        """
        for name, url, key, model in self._providers():
            if not key:
                continue
            state = self._states[name]
            if not state.allow_request():
                continue
            headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
            payload = {"model": model, "messages": messages, "max_tokens": max_tokens,
                       "temperature": float(temperature), "top_p": 0.9, "stream": True}
            first_token = False
            try:
                async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=60, connect=5)) as s:
                    async with s.post(url, json=payload, headers=headers) as resp:
                        if resp.status != 200:
                            state.record_failure(_classify_status(resp.status))
                            continue  # pre-token: fail over to the next provider
                        async for raw_line in resp.content:
                            line = raw_line.decode("utf-8", "replace").strip()
                            if not line.startswith("data:"):
                                continue
                            data = line[5:].strip()
                            if data == "[DONE]":
                                break
                            try:
                                import json as _j
                                delta = _j.loads(data)["choices"][0].get("delta", {}).get("content", "")
                            except Exception:
                                continue
                            if delta:
                                if not first_token:
                                    first_token = True
                                    logger.info("LLM stream via %s", name)
                                yield name, delta
                if first_token:
                    state.record_success()
                    return
                state.record_failure("bad_response")
            except (asyncio.TimeoutError, aiohttp.ClientError) as exc:
                err = "timeout" if isinstance(exc, asyncio.TimeoutError) else "network"
                state.record_failure(err)
                if first_token:
                    # Mid-stream failure: do not fail over, surface to caller
                    raise
                continue
        # No provider produced a token; caller falls back to template

    async def complete_with_provider(self, messages: list, max_tokens: int = 600, temperature: float = 0.8) -> dict:
        """Try cloud providers and report which one produced the text."""
        for name, url, key, model in self._providers():
            if not key:
                continue
            reply = await self._try_provider(name, url, key, model, messages, max_tokens, temperature)
            if reply:
                logger.info("LLM routed to %s", name)
                return {"text": reply, "provider": name}

        logger.info("LLM cloud providers unavailable; endpoint fallback will handle response")
        return {"text": "", "provider": "none", "errors": {
            name: st.last_error for name, st in self._states.items() if st.last_error}}


router = LLMRouter()
