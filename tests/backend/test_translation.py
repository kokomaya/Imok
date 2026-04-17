"""实时翻译服务模块单元测试。

覆盖范围：
- ContextWindow: 添加/淘汰/格式化/清空
- RequestBatcher: 合并窗口、去重、立即刷出
- RealtimeTranslator: 翻译流程、超时降级、离线降级、回调机制
"""

from __future__ import annotations

import asyncio
import json
from typing import AsyncIterator, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.config import LLMSettings
from backend.llm.base import (
    ChatMessage,
    LLMClient,
    LLMClientState,
    LLMResponse,
)
from backend.llm.glossary import GlossaryManager
from backend.llm.prompt_manager import PromptManager
from backend.translation.context_window import ContextWindow, TranslationEntry
from backend.translation.request_batcher import RequestBatcher
from backend.translation.translator import (
    RealtimeTranslator,
    TranslationResult,
    TranslationStatus,
)


# =========================================================================
# ContextWindow 测试
# =========================================================================
class TestContextWindow:
    def test_empty_window(self):
        ctx = ContextWindow()
        assert ctx.size == 0
        assert ctx.format_for_prompt() == "(无上下文)"

    def test_add_entries(self):
        ctx = ContextWindow(max_entries=3)
        ctx.add("你好", "Hello")
        ctx.add("谢谢", "Thank you")
        assert ctx.size == 2

    def test_format_with_entries(self):
        ctx = ContextWindow()
        ctx.add("你好", "Hello")
        ctx.add("谢谢", "Thank you")
        result = ctx.format_for_prompt()
        assert "你好 → Hello" in result
        assert "谢谢 → Thank you" in result

    def test_max_entries_eviction(self):
        ctx = ContextWindow(max_entries=2)
        ctx.add("a", "1")
        ctx.add("b", "2")
        ctx.add("c", "3")
        assert ctx.size == 2
        entries = ctx.entries
        assert entries[0].source == "b"
        assert entries[1].source == "c"

    def test_clear(self):
        ctx = ContextWindow()
        ctx.add("a", "1")
        ctx.clear()
        assert ctx.size == 0
        assert ctx.format_for_prompt() == "(无上下文)"

    def test_entries_is_copy(self):
        ctx = ContextWindow()
        ctx.add("a", "1")
        entries = ctx.entries
        entries.clear()
        assert ctx.size == 1

    def test_max_entries_property(self):
        ctx = ContextWindow(max_entries=5)
        assert ctx.max_entries == 5


class TestTranslationEntry:
    def test_fields(self):
        entry = TranslationEntry(source="hello", translated="你好")
        assert entry.source == "hello"
        assert entry.translated == "你好"


# =========================================================================
# RequestBatcher 测试
# =========================================================================
class TestRequestBatcherSync:
    """RequestBatcher 同步方法测试。"""

    def test_initial_state(self):
        batcher = RequestBatcher()
        assert batcher.pending_count == 0
        assert not batcher.has_pending

    def test_submit_adds_to_pending(self):
        batcher = RequestBatcher()
        batcher.submit("hello")
        assert batcher.pending_count == 1
        assert batcher.has_pending

    def test_submit_ignores_empty(self):
        batcher = RequestBatcher()
        batcher.submit("")
        batcher.submit("   ")
        assert batcher.pending_count == 0

    def test_flush_immediate(self):
        batcher = RequestBatcher()
        batcher.submit("hello")
        batcher.submit("world")
        result = batcher.flush_immediate()
        assert result == "hello\nworld"
        assert batcher.pending_count == 0

    def test_flush_immediate_empty(self):
        batcher = RequestBatcher()
        assert batcher.flush_immediate() is None

    def test_flush_immediate_dedup(self):
        batcher = RequestBatcher()
        batcher.submit("same text")
        batcher.flush_immediate()  # first time → "same text"

        batcher.submit("same text")
        assert batcher.flush_immediate() is None  # dedup

    def test_reset(self):
        batcher = RequestBatcher()
        batcher.submit("hello")
        batcher.flush_immediate()
        batcher.reset()
        assert batcher.pending_count == 0
        # After reset, dedup should not trigger
        batcher.submit("hello")
        assert batcher.flush_immediate() == "hello"


class TestRequestBatcherAsync:
    """RequestBatcher 异步方法测试。"""

    @pytest.mark.asyncio
    async def test_wait_and_flush_single(self):
        batcher = RequestBatcher(merge_window_ms=50)  # short window for test
        batcher.submit("hello")
        result = await batcher.wait_and_flush()
        assert result == "hello"

    @pytest.mark.asyncio
    async def test_wait_and_flush_merges(self):
        batcher = RequestBatcher(merge_window_ms=100)
        batcher.submit("hello")
        batcher.submit("world")
        result = await batcher.wait_and_flush()
        assert result == "hello\nworld"

    @pytest.mark.asyncio
    async def test_wait_and_flush_dedup(self):
        batcher = RequestBatcher(merge_window_ms=50)
        batcher.submit("same")
        result = await batcher.wait_and_flush()
        assert result == "same"

        batcher.submit("same")
        result = await batcher.wait_and_flush()
        assert result is None  # dedup

    @pytest.mark.asyncio
    async def test_wait_and_flush_different_after_same(self):
        batcher = RequestBatcher(merge_window_ms=50)
        batcher.submit("first")
        await batcher.wait_and_flush()

        batcher.submit("second")
        result = await batcher.wait_and_flush()
        assert result == "second"

    @pytest.mark.asyncio
    async def test_merge_window_collects_late_arrivals(self):
        """在合并窗口内提交的文本会被合并。"""
        batcher = RequestBatcher(merge_window_ms=200)
        batcher.submit("first")

        # Submit more during the merge window
        async def late_submit():
            await asyncio.sleep(0.05)
            batcher.submit("second")

        asyncio.create_task(late_submit())
        result = await batcher.wait_and_flush()
        assert result == "first\nsecond"


# =========================================================================
# Stub LLMClient for translator tests
# =========================================================================
class StubLLMClient(LLMClient):
    """测试用 LLM 客户端桩。"""

    def __init__(
        self,
        *,
        response: str = "translated text",
        delay: float = 0.0,
        state: LLMClientState = LLMClientState.READY,
        raise_error: bool = False,
    ):
        self._response = response
        self._delay = delay
        self._state = state
        self._raise_error = raise_error
        self.call_count = 0

    async def complete(self, prompt, **kw) -> LLMResponse:
        return LLMResponse(content=self._response)

    async def stream(self, prompt, **kw) -> AsyncIterator[str]:
        self.call_count += 1
        if self._raise_error:
            raise ConnectionError("LLM connection failed")
        if self._delay:
            await asyncio.sleep(self._delay)
        for char in self._response:
            yield char

    async def close(self):
        pass

    @property
    def state(self) -> LLMClientState:
        return self._state


# =========================================================================
# RealtimeTranslator 测试
# =========================================================================
class TestTranslationResult:
    def test_ok_not_degraded(self):
        r = TranslationResult(
            source_text="x", translated_text="y", status=TranslationStatus.OK
        )
        assert not r.is_degraded

    def test_timeout_is_degraded(self):
        r = TranslationResult(
            source_text="x", translated_text="x", status=TranslationStatus.TIMEOUT
        )
        assert r.is_degraded

    def test_offline_is_degraded(self):
        r = TranslationResult(
            source_text="x", translated_text="x", status=TranslationStatus.OFFLINE
        )
        assert r.is_degraded

    def test_error_is_degraded(self):
        r = TranslationResult(
            source_text="x", translated_text="x", status=TranslationStatus.ERROR
        )
        assert r.is_degraded


class TestRealtimeTranslator:
    @pytest.mark.asyncio
    async def test_basic_translation(self):
        """基本翻译流程：feed → batcher → LLM → callback。"""
        llm = StubLLMClient(response="Hello World")
        glossary = GlossaryManager()
        pm = PromptManager()
        settings = LLMSettings(request_merge_ms=50, translation_timeout_s=5.0)

        results: list[TranslationResult] = []
        translator = RealtimeTranslator(
            llm_client=llm,
            glossary=glossary,
            prompt_manager=pm,
            settings=settings,
        )
        translator.on_translation(results.append)

        await translator.start()
        translator.feed("你好世界")
        await asyncio.sleep(0.3)  # wait for batcher + translation
        await translator.stop()

        assert len(results) >= 1
        assert results[0].status == TranslationStatus.OK
        assert results[0].translated_text == "Hello World"
        assert results[0].source_text == "你好世界"
        assert results[0].latency_ms >= 0

    @pytest.mark.asyncio
    async def test_timeout_degradation(self):
        """超时降级：LLM 响应慢于 translation_timeout_s 时输出原文。"""
        llm = StubLLMClient(response="slow response", delay=2.0)
        glossary = GlossaryManager()
        pm = PromptManager()
        settings = LLMSettings(
            request_merge_ms=50,
            translation_timeout_s=0.1,  # very short timeout
        )

        results: list[TranslationResult] = []
        translator = RealtimeTranslator(
            llm_client=llm,
            glossary=glossary,
            prompt_manager=pm,
            settings=settings,
        )
        translator.on_translation(results.append)

        await translator.start()
        translator.feed("测试超时")
        await asyncio.sleep(0.5)
        await translator.stop()

        assert len(results) >= 1
        assert results[0].status == TranslationStatus.TIMEOUT
        assert results[0].translated_text == "测试超时"  # fallback to source
        assert results[0].is_degraded

    @pytest.mark.asyncio
    async def test_offline_degradation(self):
        """LLM 离线时直接输出原文。"""
        llm = StubLLMClient(state=LLMClientState.OFFLINE)
        glossary = GlossaryManager()
        pm = PromptManager()
        settings = LLMSettings(request_merge_ms=50)

        results: list[TranslationResult] = []
        translator = RealtimeTranslator(
            llm_client=llm,
            glossary=glossary,
            prompt_manager=pm,
            settings=settings,
        )
        translator.on_translation(results.append)

        await translator.start()
        translator.feed("离线测试")
        await asyncio.sleep(0.3)
        await translator.stop()

        assert len(results) >= 1
        assert results[0].status == TranslationStatus.OFFLINE
        assert results[0].translated_text == "离线测试"

    @pytest.mark.asyncio
    async def test_error_degradation(self):
        """LLM 抛异常时降级。"""
        llm = StubLLMClient(raise_error=True)
        glossary = GlossaryManager()
        pm = PromptManager()
        settings = LLMSettings(request_merge_ms=50, translation_timeout_s=5.0)

        results: list[TranslationResult] = []
        translator = RealtimeTranslator(
            llm_client=llm,
            glossary=glossary,
            prompt_manager=pm,
            settings=settings,
        )
        translator.on_translation(results.append)

        await translator.start()
        translator.feed("错误测试")
        await asyncio.sleep(0.3)
        await translator.stop()

        assert len(results) >= 1
        assert results[0].status == TranslationStatus.ERROR
        assert results[0].translated_text == "错误测试"

    @pytest.mark.asyncio
    async def test_context_window_updated(self):
        """翻译成功后上下文窗口被更新。"""
        llm = StubLLMClient(response="translated")
        glossary = GlossaryManager()
        pm = PromptManager()
        settings = LLMSettings(request_merge_ms=50, translation_timeout_s=5.0)

        translator = RealtimeTranslator(
            llm_client=llm,
            glossary=glossary,
            prompt_manager=pm,
            settings=settings,
        )

        await translator.start()
        translator.feed("原文")
        await asyncio.sleep(0.3)
        await translator.stop()

        assert translator.context.size == 1
        assert translator.context.entries[0].source == "原文"
        assert translator.context.entries[0].translated == "translated"

    @pytest.mark.asyncio
    async def test_context_not_updated_on_timeout(self):
        """超时降级时上下文窗口不更新。"""
        llm = StubLLMClient(response="slow", delay=2.0)
        glossary = GlossaryManager()
        pm = PromptManager()
        settings = LLMSettings(request_merge_ms=50, translation_timeout_s=0.1)

        translator = RealtimeTranslator(
            llm_client=llm,
            glossary=glossary,
            prompt_manager=pm,
            settings=settings,
        )

        await translator.start()
        translator.feed("超时")
        await asyncio.sleep(0.5)
        await translator.stop()

        assert translator.context.size == 0

    @pytest.mark.asyncio
    async def test_multiple_callbacks(self):
        """多个回调都被通知。"""
        llm = StubLLMClient(response="ok")
        glossary = GlossaryManager()
        pm = PromptManager()
        settings = LLMSettings(request_merge_ms=50, translation_timeout_s=5.0)

        results1: list[TranslationResult] = []
        results2: list[TranslationResult] = []

        translator = RealtimeTranslator(
            llm_client=llm,
            glossary=glossary,
            prompt_manager=pm,
            settings=settings,
        )
        translator.on_translation(results1.append)
        translator.on_translation(results2.append)

        await translator.start()
        translator.feed("test")
        await asyncio.sleep(0.3)
        await translator.stop()

        assert len(results1) >= 1
        assert len(results2) >= 1

    @pytest.mark.asyncio
    async def test_glossary_injected(self):
        """验证术语表注入到 LLM 调用。"""
        call_args: list[dict] = []

        class SpyLLMClient(StubLLMClient):
            async def stream(self, prompt, *, system_prompt=None, **kw):
                call_args.append({"prompt": prompt, "system_prompt": system_prompt})
                async for t in super().stream(prompt, system_prompt=system_prompt, **kw):
                    yield t

        llm = SpyLLMClient(response="ok")
        glossary = GlossaryManager()
        glossary.add("IPC", "IPC")
        pm = PromptManager()
        settings = LLMSettings(request_merge_ms=50, translation_timeout_s=5.0)

        translator = RealtimeTranslator(
            llm_client=llm,
            glossary=glossary,
            prompt_manager=pm,
            settings=settings,
        )

        await translator.start()
        translator.feed("IPC 模块")
        await asyncio.sleep(0.3)
        await translator.stop()

        assert len(call_args) >= 1
        assert "IPC → IPC" in call_args[0]["system_prompt"]

    @pytest.mark.asyncio
    async def test_stop_flushes_remaining(self):
        """stop() 处理 batcher 中剩余的文本。"""
        llm = StubLLMClient(response="flushed")
        glossary = GlossaryManager()
        pm = PromptManager()
        settings = LLMSettings(request_merge_ms=5000, translation_timeout_s=5.0)

        results: list[TranslationResult] = []
        batcher = RequestBatcher(merge_window_ms=5000)  # very long window

        translator = RealtimeTranslator(
            llm_client=llm,
            glossary=glossary,
            prompt_manager=pm,
            settings=settings,
            batcher=batcher,
        )
        translator.on_translation(results.append)

        await translator.start()
        translator.feed("pending text")
        # Don't wait for batcher window — stop immediately
        await asyncio.sleep(0.05)
        await translator.stop()

        # The remaining text should have been flushed and translated
        assert len(results) >= 1
        assert results[0].translated_text == "flushed"

    @pytest.mark.asyncio
    async def test_idempotent_start_stop(self):
        """start/stop 可重复调用不出错。"""
        llm = StubLLMClient(response="ok")
        glossary = GlossaryManager()
        pm = PromptManager()

        translator = RealtimeTranslator(
            llm_client=llm, glossary=glossary, prompt_manager=pm
        )

        await translator.start()
        await translator.start()  # second start is noop
        assert translator.is_running

        await translator.stop()
        await translator.stop()  # second stop is noop
        assert not translator.is_running
