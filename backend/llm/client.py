"""公司 LLM API 客户端 — OpenAI 兼容的 Streaming SSE 客户端。

单一职责：管理与 LLM API 的 HTTP 通信，包括连接池、认证、重试和断网检测。
不负责 Prompt 构建、术语表注入或翻译逻辑。

核心特性：
- httpx.AsyncClient 连接池复用
- SSE Streaming 逐 token 输出
- 指数退避重试（可配置最大重试次数）
- 断网检测与状态降级标记
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import AsyncIterator, List, Optional

import httpx

from backend.config import LLMSettings
from backend.llm.base import (
    ChatMessage,
    LLMClient,
    LLMClientState,
    LLMResponse,
)

logger = logging.getLogger(__name__)


def _build_messages(
    prompt: str,
    system_prompt: Optional[str],
    messages: Optional[List[ChatMessage]],
) -> List[dict]:
    """构建 OpenAI 格式的 messages 列表。"""
    if messages:
        return [{"role": m.role, "content": m.content} for m in messages]

    msgs: List[dict] = []
    if system_prompt:
        msgs.append({"role": "system", "content": system_prompt})
    msgs.append({"role": "user", "content": prompt})
    return msgs


class CompanyLLMClient(LLMClient):
    """公司 LLM API 客户端 — 兼容 OpenAI Chat Completions API。

    使用 httpx.AsyncClient 进行 HTTP/2 连接复用。
    支持 SSE Streaming 和指数退避重试。
    """

    def __init__(self, settings: Optional[LLMSettings] = None) -> None:
        self._settings = settings or LLMSettings()
        self._state = LLMClientState.READY
        self._consecutive_failures = 0
        self._last_success_time = time.monotonic()

        # httpx 异步客户端（连接池复用）
        headers = {"Content-Type": "application/json"}
        if self._settings.api_key:
            headers["Authorization"] = f"Bearer {self._settings.api_key}"

        self._client = httpx.AsyncClient(
            base_url=self._settings.api_base_url,
            headers=headers,
            timeout=httpx.Timeout(
                connect=10.0,
                read=self._settings.timeout_s,
                write=10.0,
                pool=5.0,
            ),
            limits=httpx.Limits(
                max_connections=10,
                max_keepalive_connections=5,
            ),
        )

        logger.info(
            "LLM client configured: url=%s, model=%s, timeout=%.1fs, retries=%d",
            self._settings.api_base_url,
            self._settings.model_name,
            self._settings.timeout_s,
            self._settings.max_retries,
        )

    async def complete(
        self,
        prompt: str,
        *,
        system_prompt: Optional[str] = None,
        messages: Optional[List[ChatMessage]] = None,
        temperature: float = 0.3,
        max_tokens: int = 2048,
    ) -> LLMResponse:
        """一次性获取完整响应（带重试）。"""
        msgs = _build_messages(prompt, system_prompt, messages)
        payload = {
            "model": self._settings.model_name,
            "messages": msgs,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }

        last_exc: Optional[Exception] = None

        for attempt in range(self._settings.max_retries + 1):
            try:
                response = await self._client.post(
                    "/chat/completions",
                    json=payload,
                )
                response.raise_for_status()
                data = response.json()

                self._on_success()

                choice = data["choices"][0]
                usage = data.get("usage", {})

                return LLMResponse(
                    content=choice["message"]["content"],
                    model=data.get("model", ""),
                    usage_prompt_tokens=usage.get("prompt_tokens", 0),
                    usage_completion_tokens=usage.get("completion_tokens", 0),
                    finish_reason=choice.get("finish_reason", ""),
                )

            except (httpx.HTTPStatusError, httpx.RequestError, KeyError) as exc:
                last_exc = exc
                self._on_failure(exc)

                if attempt < self._settings.max_retries:
                    delay = self._backoff_delay(attempt)
                    logger.warning(
                        "LLM complete attempt %d/%d failed (%s), retrying in %.1fs",
                        attempt + 1,
                        self._settings.max_retries + 1,
                        type(exc).__name__,
                        delay,
                    )
                    await asyncio.sleep(delay)

        raise ConnectionError(
            f"LLM complete failed after {self._settings.max_retries + 1} attempts"
        ) from last_exc

    async def stream(
        self,
        prompt: str,
        *,
        system_prompt: Optional[str] = None,
        messages: Optional[List[ChatMessage]] = None,
        temperature: float = 0.3,
        max_tokens: int = 2048,
    ) -> AsyncIterator[str]:
        """SSE Streaming 逐 token 输出（带重试）。"""
        msgs = _build_messages(prompt, system_prompt, messages)
        payload = {
            "model": self._settings.model_name,
            "messages": msgs,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True,
        }

        last_exc: Optional[Exception] = None

        for attempt in range(self._settings.max_retries + 1):
            try:
                async with self._client.stream(
                    "POST",
                    "/chat/completions",
                    json=payload,
                ) as response:
                    response.raise_for_status()
                    self._on_success()

                    async for line in response.aiter_lines():
                        if not line.startswith("data: "):
                            continue

                        data_str = line[len("data: "):]
                        if data_str.strip() == "[DONE]":
                            return

                        try:
                            data = json.loads(data_str)
                        except json.JSONDecodeError:
                            continue

                        choices = data.get("choices", [])
                        if not choices:
                            continue

                        delta = choices[0].get("delta", {})
                        content = delta.get("content")
                        if content:
                            yield content

                    return  # Stream finished normally

            except (httpx.HTTPStatusError, httpx.RequestError) as exc:
                last_exc = exc
                self._on_failure(exc)

                if attempt < self._settings.max_retries:
                    delay = self._backoff_delay(attempt)
                    logger.warning(
                        "LLM stream attempt %d/%d failed (%s), retrying in %.1fs",
                        attempt + 1,
                        self._settings.max_retries + 1,
                        type(exc).__name__,
                        delay,
                    )
                    await asyncio.sleep(delay)

        raise ConnectionError(
            f"LLM stream failed after {self._settings.max_retries + 1} attempts"
        ) from last_exc

    async def close(self) -> None:
        """关闭 httpx 客户端，释放连接池。"""
        await self._client.aclose()
        logger.debug("LLM client closed.")

    @property
    def state(self) -> LLMClientState:
        return self._state

    def _on_success(self) -> None:
        """请求成功时更新状态。"""
        self._consecutive_failures = 0
        self._last_success_time = time.monotonic()
        if self._state != LLMClientState.READY:
            logger.info("LLM client recovered, state → READY")
            self._state = LLMClientState.READY

    def _on_failure(self, exc: Exception) -> None:
        """请求失败时更新状态。"""
        self._consecutive_failures += 1

        if self._consecutive_failures >= 3:
            if self._state != LLMClientState.OFFLINE:
                logger.error("LLM client state → OFFLINE after %d consecutive failures", self._consecutive_failures)
            self._state = LLMClientState.OFFLINE
        elif self._consecutive_failures >= 1:
            if self._state == LLMClientState.READY:
                logger.warning("LLM client state → DEGRADED")
            self._state = LLMClientState.DEGRADED

    def _backoff_delay(self, attempt: int) -> float:
        """指数退避延迟（1s, 2s, 4s...），加随机抖动。"""
        import random
        base = min(2 ** attempt, 8)  # cap at 8s
        jitter = random.uniform(0, 0.5)
        return base + jitter
