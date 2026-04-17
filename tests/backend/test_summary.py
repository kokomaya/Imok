"""段落级摘要模块单元测试。

覆盖范围：
- SegmentSummarizer: 正常摘要、空文本、LLM 离线、LLM 异常、结构化解析
- TimeWindowManager: 窗口切分、重叠缓冲、flush、reset、回调
- _extract_section: 各种 LLM 输出格式的解析
"""

from __future__ import annotations

import asyncio
from typing import AsyncIterator, List, Optional
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.llm.base import ChatMessage, LLMClient, LLMClientState, LLMResponse
from backend.llm.prompt_manager import PromptManager
from backend.summary.segment_summarizer import (
    SegmentSummarizer,
    SegmentSummary,
    _extract_section,
)
from backend.summary.time_window import (
    TimeWindowManager,
    TranscriptEntry,
    WindowContent,
    _fmt_time,
)


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
# Fixtures
# =========================================================================

SAMPLE_LLM_OUTPUT = """\
## 讨论主题
- AUTOSAR 架构迁移方案
- CAN 总线通信问题

## 关键结论
- 决定采用 Classic AUTOSAR 4.4
- CAN 总线延迟问题已定位到硬件驱动层

## Action Items
- 张三：下周完成 BSW 模块适配
- 李四：周五前提交 CAN 驱动修复 patch

## 风险项
- GPU 显存不足可能影响模型推理性能
"""


@pytest.fixture
def mock_client() -> MockLLMClient:
    return MockLLMClient(response_content=SAMPLE_LLM_OUTPUT)


@pytest.fixture
def prompt_manager() -> PromptManager:
    return PromptManager()


@pytest.fixture
def summarizer(mock_client: MockLLMClient, prompt_manager: PromptManager) -> SegmentSummarizer:
    return SegmentSummarizer(mock_client, prompt_manager, glossary="- ECU → ECU")


# =========================================================================
# SegmentSummarizer Tests
# =========================================================================


class TestSegmentSummarizer:
    """SegmentSummarizer 测试集。"""

    @pytest.mark.asyncio
    async def test_summarize_basic(self, summarizer: SegmentSummarizer, mock_client: MockLLMClient):
        """正常摘要调用。"""
        result = await summarizer.summarize(
            "讨论了 AUTOSAR 架构迁移方案",
            time_range="00:00 - 01:00",
        )
        assert result is not None
        assert result.time_range == "00:00 - 01:00"
        assert result.source_text == "讨论了 AUTOSAR 架构迁移方案"
        assert len(result.raw_text) > 0
        assert len(mock_client.complete_calls) == 1

    @pytest.mark.asyncio
    async def test_summarize_extracts_topics(self, summarizer: SegmentSummarizer):
        """提取讨论主题。"""
        result = await summarizer.summarize("一些讨论内容", time_range="00:00 - 01:00")
        assert result is not None
        assert "AUTOSAR 架构迁移方案" in result.topics
        assert "CAN 总线通信问题" in result.topics

    @pytest.mark.asyncio
    async def test_summarize_extracts_conclusions(self, summarizer: SegmentSummarizer):
        """提取关键结论。"""
        result = await summarizer.summarize("一些讨论内容", time_range="00:00 - 01:00")
        assert result is not None
        assert len(result.conclusions) == 2
        assert any("Classic AUTOSAR" in c for c in result.conclusions)

    @pytest.mark.asyncio
    async def test_summarize_extracts_action_items(self, summarizer: SegmentSummarizer):
        """提取 Action Items。"""
        result = await summarizer.summarize("一些讨论内容", time_range="00:00 - 01:00")
        assert result is not None
        assert len(result.action_items) == 2
        assert any("张三" in a for a in result.action_items)

    @pytest.mark.asyncio
    async def test_summarize_empty_text(self, summarizer: SegmentSummarizer):
        """空文本返回 None。"""
        result = await summarizer.summarize("")
        assert result is None

    @pytest.mark.asyncio
    async def test_summarize_whitespace_text(self, summarizer: SegmentSummarizer):
        """纯空白文本返回 None。"""
        result = await summarizer.summarize("   \n  \n  ")
        assert result is None

    @pytest.mark.asyncio
    async def test_summarize_llm_offline(self, prompt_manager: PromptManager):
        """LLM 离线时返回 None。"""
        client = MockLLMClient(state=LLMClientState.OFFLINE)
        summarizer = SegmentSummarizer(client, prompt_manager)
        result = await summarizer.summarize("一些内容")
        assert result is None
        assert len(client.complete_calls) == 0

    @pytest.mark.asyncio
    async def test_summarize_llm_degraded_still_works(self, prompt_manager: PromptManager):
        """LLM DEGRADED 状态仍尝试摘要。"""
        client = MockLLMClient(
            response_content="## 主题\n- 测试主题",
            state=LLMClientState.DEGRADED,
        )
        summarizer = SegmentSummarizer(client, prompt_manager)
        result = await summarizer.summarize("内容")
        assert result is not None

    @pytest.mark.asyncio
    async def test_summarize_llm_exception(self, prompt_manager: PromptManager):
        """LLM 调用异常时返回 None。"""
        client = MockLLMClient(raise_on_complete=RuntimeError("timeout"))
        summarizer = SegmentSummarizer(client, prompt_manager)
        result = await summarizer.summarize("一些内容")
        assert result is None

    @pytest.mark.asyncio
    async def test_summarize_llm_empty_response(self, prompt_manager: PromptManager):
        """LLM 返回空内容时返回 None。"""
        client = MockLLMClient(response_content="")
        summarizer = SegmentSummarizer(client, prompt_manager)
        result = await summarizer.summarize("一些内容")
        assert result is None

    @pytest.mark.asyncio
    async def test_summarize_uses_glossary(self, mock_client: MockLLMClient, prompt_manager: PromptManager):
        """摘要请求包含术语表。"""
        summarizer = SegmentSummarizer(mock_client, prompt_manager, glossary="- ECU → ECU")
        await summarizer.summarize("内容", time_range="00:00 - 01:00")
        call = mock_client.complete_calls[0]
        assert "ECU" in call["system_prompt"]

    @pytest.mark.asyncio
    async def test_summarize_custom_temperature(self, prompt_manager: PromptManager):
        """自定义温度参数传递到 LLM。"""
        client = MockLLMClient(response_content="## 主题\n- X")
        summarizer = SegmentSummarizer(client, prompt_manager, temperature=0.7)
        await summarizer.summarize("内容")
        assert client.complete_calls[0]["temperature"] == 0.7

    def test_glossary_property(self, summarizer: SegmentSummarizer):
        """术语表可读写。"""
        assert summarizer.glossary == "- ECU → ECU"
        summarizer.glossary = "- CAN → CAN Bus"
        assert summarizer.glossary == "- CAN → CAN Bus"


# =========================================================================
# _extract_section Tests
# =========================================================================


class TestExtractSection:
    """LLM 输出解析测试。"""

    def test_markdown_heading(self):
        """标准 Markdown ## 标题。"""
        text = "## 讨论主题\n- 主题A\n- 主题B\n## 结论\n- 结论1"
        topics = _extract_section(text, "主题", "topic")
        assert topics == ["主题A", "主题B"]

    def test_colon_heading(self):
        """冒号结尾的标题。"""
        text = "Action Items:\n- Do X\n- Do Y"
        items = _extract_section(text, "action")
        assert items == ["Do X", "Do Y"]

    def test_chinese_colon_heading(self):
        """中文冒号标题。"""
        text = "行动项：\n- 完成A\n- 完成B"
        items = _extract_section(text, "行动")
        assert items == ["完成A", "完成B"]

    def test_asterisk_list(self):
        """* 列表符。"""
        text = "## Topics\n* Topic1\n* Topic2"
        items = _extract_section(text, "topic")
        assert items == ["Topic1", "Topic2"]

    def test_numbered_list(self):
        """数字编号列表。"""
        text = "## 结论\n1. 第一条\n2. 第二条"
        items = _extract_section(text, "结论")
        assert items == ["第一条", "第二条"]

    def test_no_match(self):
        """没有匹配的关键词返回空列表。"""
        text = "## 其他内容\n- 不相关"
        items = _extract_section(text, "主题")
        assert items == []

    def test_multiple_keywords(self):
        """多关键词匹配。"""
        text = "## 关键决策\n- 采用方案A"
        items = _extract_section(text, "结论", "决策", "conclusion")
        assert items == ["采用方案A"]

    def test_empty_text(self):
        """空文本。"""
        assert _extract_section("", "主题") == []

    def test_stops_at_next_heading(self):
        """下一个标题处停止捕获。"""
        text = "## 主题\n- A\n- B\n## 结论\n- C"
        topics = _extract_section(text, "主题")
        assert topics == ["A", "B"]
        conclusions = _extract_section(text, "结论")
        assert conclusions == ["C"]


# =========================================================================
# TimeWindowManager Tests
# =========================================================================


class TestTimeWindowManager:
    """TimeWindowManager 测试集。"""

    def test_basic_window_emit(self):
        """超过窗口时长时触发回调。"""
        results: list[WindowContent] = []
        mgr = TimeWindowManager(window_duration_s=10, overlap_s=0)
        mgr.on_window_complete(results.append)

        mgr.add("Hello", start_time=0, end_time=3)
        mgr.add("World", start_time=5, end_time=8)
        assert len(results) == 0  # 未超出窗口

        mgr.add("Next window", start_time=10, end_time=12)
        assert len(results) == 1
        assert results[0].window_index == 0
        assert results[0].merged_text == "Hello\nWorld"
        assert results[0].time_range == "00:00 - 00:10"

    def test_flush_emits_remaining(self):
        """flush 刷新未满窗口。"""
        results: list[WindowContent] = []
        mgr = TimeWindowManager(window_duration_s=60, overlap_s=0)
        mgr.on_window_complete(results.append)

        mgr.add("Some text", start_time=5, end_time=10)
        assert len(results) == 0

        mgr.flush()
        assert len(results) == 1
        assert results[0].merged_text == "Some text"

    def test_overlap_carries_entries(self):
        """重叠缓冲包含前一窗口末尾的条目。"""
        results: list[WindowContent] = []
        mgr = TimeWindowManager(window_duration_s=10, overlap_s=3)
        mgr.on_window_complete(results.append)

        mgr.add("Early", start_time=0, end_time=2)
        mgr.add("Late", start_time=8, end_time=9.5)
        # 触发窗口完成
        mgr.add("Next", start_time=10, end_time=12)

        assert len(results) == 1
        assert results[0].merged_text == "Early\nLate"

        # "Late" 应在重叠区 [7, 10)，被保留到下一窗口
        mgr.flush()
        assert len(results) == 2
        assert "Late" in results[1].merged_text
        assert "Next" in results[1].merged_text

    def test_empty_text_ignored(self):
        """空文本不被收集。"""
        mgr = TimeWindowManager(window_duration_s=60, overlap_s=0)
        mgr.add("", start_time=0, end_time=5)
        mgr.add("   ", start_time=5, end_time=10)
        assert mgr.pending_count == 0

    def test_reset(self):
        """reset 清空所有状态。"""
        mgr = TimeWindowManager(window_duration_s=60, overlap_s=0)
        mgr.add("Text", start_time=0, end_time=5)
        assert mgr.pending_count == 1

        mgr.reset()
        assert mgr.pending_count == 0
        assert mgr.window_index == 0

    def test_multiple_windows(self):
        """多个窗口依次触发。"""
        results: list[WindowContent] = []
        mgr = TimeWindowManager(window_duration_s=10, overlap_s=0)
        mgr.on_window_complete(results.append)

        for i in range(30):
            mgr.add(f"Seg-{i}", start_time=i, end_time=i + 0.5)

        mgr.flush()
        assert len(results) == 3
        assert results[0].window_index == 0
        assert results[1].window_index == 1
        assert results[2].window_index == 2

    def test_window_index_increments(self):
        """窗口索引自增。"""
        results: list[WindowContent] = []
        mgr = TimeWindowManager(window_duration_s=5, overlap_s=0)
        mgr.on_window_complete(results.append)

        mgr.add("A", start_time=0, end_time=1)
        mgr.add("B", start_time=5, end_time=6)
        mgr.add("C", start_time=10, end_time=11)
        mgr.flush()

        indices = [r.window_index for r in results]
        assert indices == [0, 1, 2]

    def test_invalid_window_duration(self):
        """无效窗口时长抛异常。"""
        with pytest.raises(ValueError, match="positive"):
            TimeWindowManager(window_duration_s=0)

    def test_invalid_overlap(self):
        """重叠时间超过窗口时长抛异常。"""
        with pytest.raises(ValueError):
            TimeWindowManager(window_duration_s=10, overlap_s=10)

    def test_negative_overlap(self):
        """负数重叠抛异常。"""
        with pytest.raises(ValueError):
            TimeWindowManager(window_duration_s=10, overlap_s=-1)

    def test_callback_exception_doesnt_crash(self):
        """回调异常不影响后续处理。"""
        def bad_cb(content: WindowContent):
            raise RuntimeError("callback failed")

        results: list[WindowContent] = []
        mgr = TimeWindowManager(window_duration_s=10, overlap_s=0)
        mgr.on_window_complete(bad_cb)
        mgr.on_window_complete(results.append)  # 第二个回调应仍执行

        mgr.add("Text", start_time=0, end_time=5)
        mgr.flush()
        assert len(results) == 1

    def test_time_range_format(self):
        """时间范围格式化。"""
        results: list[WindowContent] = []
        mgr = TimeWindowManager(window_duration_s=60, overlap_s=0)
        mgr.on_window_complete(results.append)

        mgr.add("X", start_time=0, end_time=30)
        mgr.flush()
        assert results[0].time_range == "00:00 - 01:00"

    def test_window_content_merged_text(self):
        """WindowContent.merged_text 合并。"""
        content = WindowContent(
            window_index=0,
            start_time=0,
            end_time=60,
            entries=[
                TranscriptEntry(text="Hello", language="en", start_time=0, end_time=5),
                TranscriptEntry(text="世界", language="zh", start_time=5, end_time=10),
                TranscriptEntry(text="", language="", start_time=10, end_time=15),
            ],
        )
        assert content.merged_text == "Hello\n世界"


# =========================================================================
# _fmt_time Tests
# =========================================================================


class TestFmtTime:
    """时间格式化测试。"""

    def test_zero(self):
        assert _fmt_time(0) == "00:00"

    def test_seconds(self):
        assert _fmt_time(45) == "00:45"

    def test_minutes(self):
        assert _fmt_time(125) == "02:05"

    def test_hours(self):
        assert _fmt_time(3665) == "1:01:05"
