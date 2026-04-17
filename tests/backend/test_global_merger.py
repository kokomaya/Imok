"""全局合并摘要模块单元测试。

覆盖范围：
- GlobalMerger: 阈值触发、增量合并、强制合并、LLM 离线/异常、回调、reset
- GlobalSummary: 数据类属性
"""

from __future__ import annotations

from typing import AsyncIterator, List, Optional
from unittest.mock import AsyncMock

import pytest

from backend.llm.base import ChatMessage, LLMClient, LLMClientState, LLMResponse
from backend.llm.prompt_manager import PromptManager
from backend.summary.global_merger import GlobalMerger, GlobalSummary
from backend.summary.segment_summarizer import SegmentSummary


# =========================================================================
# Mock LLM Client
# =========================================================================


class MockLLMClient(LLMClient):
    """可控的 Mock LLM 客户端。"""

    def __init__(
        self,
        response_content: str = "",
        state: LLMClientState = LLMClientState.READY,
        raise_on_complete: Optional[Exception] = None,
    ) -> None:
        self._response_content = response_content
        self._state = state
        self._raise = raise_on_complete
        self.complete_calls: list = []

    async def complete(
        self,
        prompt: str,
        *,
        system_prompt: Optional[str] = None,
        messages: Optional[List[ChatMessage]] = None,
        temperature: float = 0.3,
        max_tokens: int = 2048,
    ) -> LLMResponse:
        self.complete_calls.append(
            {"prompt": prompt, "system_prompt": system_prompt, "temperature": temperature}
        )
        if self._raise:
            raise self._raise
        return LLMResponse(content=self._response_content, model="test")

    async def stream(
        self,
        prompt: str,
        *,
        system_prompt: Optional[str] = None,
        messages: Optional[List[ChatMessage]] = None,
        temperature: float = 0.3,
        max_tokens: int = 2048,
    ) -> AsyncIterator[str]:
        yield self._response_content

    async def close(self) -> None:
        pass

    @property
    def state(self) -> LLMClientState:
        return self._state


# =========================================================================
# Helpers
# =========================================================================

MERGED_OUTPUT = """\
## 讨论主题
- AUTOSAR 架构迁移
- CAN 总线通信

## 关键结论
- 采用 Classic AUTOSAR 4.4
- CAN 延迟定位到驱动层

## Action Items
- 张三：完成 BSW 适配
- 李四：提交 CAN 修复

## 风险项
- 显存不足可能影响推理
"""


def _make_segment(index: int) -> SegmentSummary:
    """创建测试用段落摘要。"""
    return SegmentSummary(
        time_range=f"{index:02d}:00 - {index + 1:02d}:00",
        raw_text=f"## 主题\n- 话题{index}\n## 结论\n- 结论{index}",
        source_text=f"第{index}段转写文本",
        topics=[f"话题{index}"],
        conclusions=[f"结论{index}"],
        action_items=[],
    )


# =========================================================================
# Fixtures
# =========================================================================


@pytest.fixture
def mock_client() -> MockLLMClient:
    return MockLLMClient(response_content=MERGED_OUTPUT)


@pytest.fixture
def prompt_manager() -> PromptManager:
    return PromptManager()


@pytest.fixture
def merger(mock_client: MockLLMClient, prompt_manager: PromptManager) -> GlobalMerger:
    return GlobalMerger(mock_client, prompt_manager, merge_threshold=3)


# =========================================================================
# GlobalMerger Tests
# =========================================================================


class TestGlobalMerger:
    """GlobalMerger 测试集。"""

    @pytest.mark.asyncio
    async def test_no_merge_below_threshold(self, merger: GlobalMerger):
        """未达阈值不触发合并。"""
        result = await merger.add_segment(_make_segment(0))
        assert result is None
        assert merger.pending_count == 1
        assert merger.merge_count == 0
        assert merger.global_summary is None

    @pytest.mark.asyncio
    async def test_merge_at_threshold(self, merger: GlobalMerger):
        """达到阈值时触发合并。"""
        await merger.add_segment(_make_segment(0))
        await merger.add_segment(_make_segment(1))
        result = await merger.add_segment(_make_segment(2))

        assert result is not None
        assert result.raw_text == MERGED_OUTPUT.strip()
        assert result.segments_merged == 3
        assert result.merge_count == 1
        assert merger.pending_count == 0
        assert merger.merge_count == 1

    @pytest.mark.asyncio
    async def test_incremental_merge(self, merger: GlobalMerger, mock_client: MockLLMClient):
        """第二次合并使用已有全局摘要作为输入（增量合并）。"""
        # 第一次合并
        for i in range(3):
            await merger.add_segment(_make_segment(i))

        # 第二次合并
        mock_client._response_content = "更新后的全局摘要"
        for i in range(3, 6):
            await merger.add_segment(_make_segment(i))

        assert merger.merge_count == 2
        assert merger.global_summary is not None
        assert merger.global_summary.raw_text == "更新后的全局摘要"
        assert merger.global_summary.segments_merged == 6

        # 验证第二次合并请求包含了第一次的全局摘要
        second_call = mock_client.complete_calls[1]
        assert MERGED_OUTPUT.strip() in second_call["prompt"]

    @pytest.mark.asyncio
    async def test_force_merge_with_pending(self, merger: GlobalMerger):
        """force_merge 合并剩余的段落摘要。"""
        await merger.add_segment(_make_segment(0))
        await merger.add_segment(_make_segment(1))
        assert merger.pending_count == 2

        result = await merger.force_merge()
        assert result is not None
        assert merger.pending_count == 0
        assert merger.merge_count == 1

    @pytest.mark.asyncio
    async def test_force_merge_no_pending(self, merger: GlobalMerger):
        """无待处理内容时 force_merge 返回当前全局摘要。"""
        result = await merger.force_merge()
        assert result is None  # 从未合并过

    @pytest.mark.asyncio
    async def test_force_merge_returns_existing_when_no_pending(self, merger: GlobalMerger):
        """已有全局摘要、无待处理时返回已有摘要。"""
        for i in range(3):
            await merger.add_segment(_make_segment(i))

        existing = merger.global_summary
        result = await merger.force_merge()
        assert result is existing

    @pytest.mark.asyncio
    async def test_llm_offline(self, prompt_manager: PromptManager):
        """LLM 离线时不触发合并。"""
        client = MockLLMClient(state=LLMClientState.OFFLINE)
        merger = GlobalMerger(client, prompt_manager, merge_threshold=2)

        await merger.add_segment(_make_segment(0))
        result = await merger.add_segment(_make_segment(1))

        assert result is None
        assert len(client.complete_calls) == 0
        # 待处理列表不清空（LLM 恢复后可重试）
        assert merger.pending_count == 2

    @pytest.mark.asyncio
    async def test_llm_exception(self, prompt_manager: PromptManager):
        """LLM 异常时返回 None。"""
        client = MockLLMClient(raise_on_complete=RuntimeError("timeout"))
        merger = GlobalMerger(client, prompt_manager, merge_threshold=2)

        await merger.add_segment(_make_segment(0))
        result = await merger.add_segment(_make_segment(1))

        assert result is None
        assert merger.pending_count == 2  # 保留待处理

    @pytest.mark.asyncio
    async def test_llm_empty_response(self, prompt_manager: PromptManager):
        """LLM 空响应时返回 None。"""
        client = MockLLMClient(response_content="")
        merger = GlobalMerger(client, prompt_manager, merge_threshold=2)

        await merger.add_segment(_make_segment(0))
        result = await merger.add_segment(_make_segment(1))

        assert result is None

    @pytest.mark.asyncio
    async def test_callback_on_merge(self, merger: GlobalMerger):
        """合并完成时触发回调。"""
        results: list[GlobalSummary] = []
        merger.on_merge(results.append)

        for i in range(3):
            await merger.add_segment(_make_segment(i))

        assert len(results) == 1
        assert results[0].merge_count == 1

    @pytest.mark.asyncio
    async def test_callback_exception_doesnt_crash(self, merger: GlobalMerger):
        """回调异常不影响合并结果。"""
        def bad_cb(s: GlobalSummary):
            raise RuntimeError("callback error")

        results: list[GlobalSummary] = []
        merger.on_merge(bad_cb)
        merger.on_merge(results.append)

        for i in range(3):
            await merger.add_segment(_make_segment(i))

        assert len(results) == 1  # 第二个回调仍执行

    @pytest.mark.asyncio
    async def test_reset(self, merger: GlobalMerger):
        """reset 清空所有状态。"""
        for i in range(3):
            await merger.add_segment(_make_segment(i))

        assert merger.global_summary is not None
        merger.reset()

        assert merger.global_summary is None
        assert merger.pending_count == 0
        assert merger.total_segments == 0
        assert merger.merge_count == 0

    def test_invalid_threshold(self, mock_client: MockLLMClient, prompt_manager: PromptManager):
        """无效阈值抛异常。"""
        with pytest.raises(ValueError, match="merge_threshold"):
            GlobalMerger(mock_client, prompt_manager, merge_threshold=0)

    @pytest.mark.asyncio
    async def test_total_segments_tracks_all(self, merger: GlobalMerger):
        """total_segments 包含所有添加过的摘要。"""
        for i in range(5):
            await merger.add_segment(_make_segment(i))

        assert merger.total_segments == 5

    @pytest.mark.asyncio
    async def test_format_pending_includes_time_range(self, merger: GlobalMerger, mock_client: MockLLMClient):
        """合并请求包含段落的时间范围。"""
        for i in range(3):
            await merger.add_segment(_make_segment(i))

        call = mock_client.complete_calls[0]
        assert "00:00 - 01:00" in call["prompt"]
        assert "01:00 - 02:00" in call["prompt"]
        assert "02:00 - 03:00" in call["prompt"]

    @pytest.mark.asyncio
    async def test_merge_uses_render_merge_summary(self, merger: GlobalMerger, mock_client: MockLLMClient):
        """合并请求使用 merge_summary 模板。"""
        for i in range(3):
            await merger.add_segment(_make_segment(i))

        call = mock_client.complete_calls[0]
        # system_prompt 应来自 merge_summary 模板
        assert "合并" in call["system_prompt"] or "段落摘要" in call["system_prompt"]

    @pytest.mark.asyncio
    async def test_first_merge_has_no_existing_summary(self, merger: GlobalMerger, mock_client: MockLLMClient):
        """首次合并时 existing_summary 为占位文本。"""
        for i in range(3):
            await merger.add_segment(_make_segment(i))

        call = mock_client.complete_calls[0]
        assert "尚无全局摘要" in call["prompt"]


# =========================================================================
# GlobalSummary Tests
# =========================================================================


class TestGlobalSummary:
    """GlobalSummary 数据类测试。"""

    def test_is_empty_when_blank(self):
        s = GlobalSummary(raw_text="", segments_merged=0, merge_count=0)
        assert s.is_empty

    def test_is_empty_whitespace(self):
        s = GlobalSummary(raw_text="   \n  ", segments_merged=0, merge_count=0)
        assert s.is_empty

    def test_not_empty(self):
        s = GlobalSummary(raw_text="内容", segments_merged=1, merge_count=1)
        assert not s.is_empty
