"""实时翻译服务 — 接收 ASR 转写文本，经合并后调用 LLM Streaming 翻译。

单一职责：编排翻译流程（合并 → Prompt 构建 → LLM 调用 → 回调分发）。
不负责请求合并逻辑（RequestBatcher）、上下文管理（ContextWindow）、
Prompt 模板（PromptManager）或 LLM 通信（LLMClient）。

设计原则：
- DIP：依赖 LLMClient 抽象接口，不依赖具体实现
- SRP：只负责翻译编排，各子职责委托给专门的类
- 超时降级：translation_timeout_s 内无 LLM 响应时输出原文 + 降级标记
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, List, Optional

from backend.config import LLMSettings
from backend.llm.base import LLMClient, LLMClientState
from backend.llm.glossary import GlossaryManager
from backend.llm.prompt_manager import PromptManager
from backend.translation.context_window import ContextWindow
from backend.translation.request_batcher import RequestBatcher

logger = logging.getLogger(__name__)


class TranslationStatus(str, Enum):
    """翻译结果状态。"""

    OK = "ok"  # 翻译成功
    TIMEOUT = "timeout"  # 超时降级，显示原文
    OFFLINE = "offline"  # LLM 离线，显示原文
    ERROR = "error"  # 翻译出错


@dataclass
class TranslationResult:
    """翻译结果。"""

    source_text: str
    translated_text: str
    status: TranslationStatus
    latency_ms: float = 0.0
    is_streaming: bool = False

    @property
    def is_degraded(self) -> bool:
        """是否为降级结果（非正常翻译）。"""
        return self.status != TranslationStatus.OK


# 回调类型：接收 TranslationResult
TranslationCallback = Callable[[TranslationResult], None]


class RealtimeTranslator:
    """实时翻译服务。

    编排流程：ASR 文本 → RequestBatcher 合并 → PromptManager 构建 →
    LLMClient Streaming 翻译 → ContextWindow 更新 → 回调分发。

    使用方式：
        translator = RealtimeTranslator(
            llm_client=client,
            glossary=glossary_manager,
            prompt_manager=prompt_manager,
        )
        translator.on_translation(callback_fn)
        await translator.start()
        translator.feed("这是一段需要翻译的文本")
        # ... callback_fn 会被异步调用
        await translator.stop()
    """

    def __init__(
        self,
        llm_client: LLMClient,
        glossary: GlossaryManager,
        prompt_manager: PromptManager,
        *,
        settings: Optional[LLMSettings] = None,
        context_window: Optional[ContextWindow] = None,
        batcher: Optional[RequestBatcher] = None,
        target_language: str = "英文",
    ) -> None:
        self._llm = llm_client
        self._glossary = glossary
        self._prompt_manager = prompt_manager
        self._settings = settings or LLMSettings()

        self._context = context_window or ContextWindow()
        self._batcher = batcher or RequestBatcher(
            merge_window_ms=self._settings.request_merge_ms
        )
        self._target_language = target_language

        self._callbacks: List[TranslationCallback] = []
        self._running = False
        self._translate_task: Optional[asyncio.Task] = None

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------
    async def start(self) -> None:
        """启动翻译循环。"""
        if self._running:
            return
        self._running = True
        self._translate_task = asyncio.create_task(self._translation_loop())
        logger.info("RealtimeTranslator started (target=%s)", self._target_language)

    async def stop(self) -> None:
        """停止翻译循环，处理剩余缓冲。"""
        if not self._running:
            return
        self._running = False

        # 处理剩余文本
        remaining = self._batcher.flush_immediate()
        if remaining:
            await self._translate_and_notify(remaining)

        if self._translate_task:
            self._translate_task.cancel()
            try:
                await self._translate_task
            except asyncio.CancelledError:
                pass
            self._translate_task = None

        logger.info("RealtimeTranslator stopped")

    # ------------------------------------------------------------------
    # 输入
    # ------------------------------------------------------------------
    def feed(self, text: str) -> None:
        """提交 ASR 转写文本到翻译管线。"""
        self._batcher.submit(text)

    # ------------------------------------------------------------------
    # 回调注册
    # ------------------------------------------------------------------
    def on_translation(self, callback: TranslationCallback) -> None:
        """注册翻译结果回调。"""
        self._callbacks.append(callback)

    # ------------------------------------------------------------------
    # 翻译循环
    # ------------------------------------------------------------------
    async def _translation_loop(self) -> None:
        """主翻译循环：等待 Batcher 输出 → 翻译 → 通知。"""
        try:
            while self._running:
                merged = await self._batcher.wait_and_flush()
                if merged and self._running:
                    await self._translate_and_notify(merged)
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("Translation loop error")

    async def _translate_and_notify(self, source_text: str) -> None:
        """执行翻译并通知回调。"""
        # 检查 LLM 是否离线
        if self._llm.state == LLMClientState.OFFLINE:
            result = TranslationResult(
                source_text=source_text,
                translated_text=source_text,
                status=TranslationStatus.OFFLINE,
            )
            self._notify(result)
            return

        # 构建 Prompt
        glossary_str = self._glossary.format_for_prompt()
        context_str = self._context.format_for_prompt()
        system_prompt, user_prompt = self._prompt_manager.render_translation(
            text=source_text,
            glossary=glossary_str,
            recent_context=context_str,
            target_language=self._target_language,
        )

        start_time = time.monotonic()

        try:
            translated = await asyncio.wait_for(
                self._stream_translation(source_text, system_prompt, user_prompt),
                timeout=self._settings.translation_timeout_s,
            )

            latency_ms = (time.monotonic() - start_time) * 1000

            # 更新上下文窗口
            self._context.add(source_text, translated)

            result = TranslationResult(
                source_text=source_text,
                translated_text=translated,
                status=TranslationStatus.OK,
                latency_ms=latency_ms,
                is_streaming=True,
            )

        except asyncio.TimeoutError:
            latency_ms = (time.monotonic() - start_time) * 1000
            logger.warning(
                "Translation timeout (%.0fms > %.0fms): %s",
                latency_ms,
                self._settings.translation_timeout_s * 1000,
                source_text[:50],
            )
            result = TranslationResult(
                source_text=source_text,
                translated_text=source_text,
                status=TranslationStatus.TIMEOUT,
                latency_ms=latency_ms,
            )

        except Exception as exc:
            latency_ms = (time.monotonic() - start_time) * 1000
            logger.error("Translation error: %s", exc)
            result = TranslationResult(
                source_text=source_text,
                translated_text=source_text,
                status=TranslationStatus.ERROR,
                latency_ms=latency_ms,
            )

        self._notify(result)

    async def _stream_translation(
        self, source_text: str, system_prompt: str, user_prompt: str
    ) -> str:
        """通过 LLM Streaming 获取翻译结果。"""
        chunks: list[str] = []
        async for token in self._llm.stream(
            user_prompt,
            system_prompt=system_prompt,
            temperature=0.1,
            max_tokens=1024,
        ):
            chunks.append(token)

        return "".join(chunks).strip()

    def _notify(self, result: TranslationResult) -> None:
        """通知所有回调。"""
        for callback in self._callbacks:
            try:
                callback(result)
            except Exception:
                logger.exception("Translation callback error")

    # ------------------------------------------------------------------
    # 状态查询
    # ------------------------------------------------------------------
    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def context(self) -> ContextWindow:
        return self._context
