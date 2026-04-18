"""SummaryCoordinator 集成测试 + IPC 摘要消息测试。

覆盖范围：
- SummaryCoordinator 生命周期（start/stop）
- feed_transcription → TimeWindow → SegmentSummarizer → GlobalMerger → ActionItemExtractor
- 回调触发与数据正确性
- LLM 客户端降级/异常场景
- IPC 消息序列化（segment_summary / global_summary）
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.ipc.messages import IPCMessage, MessageType
from backend.llm.base import LLMClient, LLMClientState, LLMResponse
from backend.summary.action_item_extractor import ActionItem
from backend.summary.global_merger import GlobalSummary
from backend.summary.segment_summarizer import SegmentSummary
from backend.summary.summary_coordinator import SummaryCoordinator


# ── Fixtures ──


class MockLLMClient(LLMClient):
    """可配置的 LLM 客户端 mock。"""

    def __init__(self, *, responses=None, state=LLMClientState.READY):
        self._responses = list(responses or [])
        self._call_count = 0
        self._state = state

    async def complete(self, prompt, *, system_prompt=None, messages=None,
                       temperature=0.3, max_tokens=2048):
        idx = min(self._call_count, len(self._responses) - 1)
        self._call_count += 1
        if idx < 0 or not self._responses:
            return LLMResponse(content="")
        resp = self._responses[idx]
        if isinstance(resp, Exception):
            raise resp
        return LLMResponse(content=resp)

    async def stream(self, prompt, **kwargs):
        yield ""  # pragma: no cover

    async def close(self):
        pass

    @property
    def state(self):
        return self._state


@dataclass
class FakeTranscriptionResult:
    text: str
    language: str = "zh"
    language_probability: float = 0.9
    is_empty: bool = False


@dataclass
class FakeTranscriptionEvent:
    result: FakeTranscriptionResult
    segment_start_time: float
    segment_end_time: float
    speaker: str = ""


def make_event(text, start, end, lang="zh"):
    return FakeTranscriptionEvent(
        result=FakeTranscriptionResult(text=text, language=lang),
        segment_start_time=start,
        segment_end_time=end,
    )


SEGMENT_LLM_RESPONSE = """\
## 主题
- 新版本发布计划

## 结论
- 下周三发布 v2.0

## Action Items
- 张三：完成回归测试，周五前
"""

MERGE_LLM_RESPONSE = """\
## 主题
- 新版本发布计划

## 关键结论
- 下周三发布 v2.0

## Action Items
- 张三：完成回归测试，周五前
- 李四：更新文档

## 风险项
- 测试覆盖不足
"""


# ===========================================================================
# SummaryCoordinator 测试
# ===========================================================================

class TestSummaryCoordinator:

    @pytest.fixture
    def llm(self):
        return MockLLMClient(responses=[SEGMENT_LLM_RESPONSE, MERGE_LLM_RESPONSE] * 10)

    @pytest.fixture
    def coordinator(self, llm):
        return SummaryCoordinator(
            llm,
            window_duration_s=10.0,
            overlap_s=2.0,
            merge_threshold=2,
        )

    @pytest.mark.asyncio
    async def test_start_stop(self, coordinator):
        """协调器可以正常启动和停止。"""
        await coordinator.start()
        assert coordinator._worker_task is not None
        await coordinator.stop()
        assert coordinator._worker_task is None

    @pytest.mark.asyncio
    async def test_feed_triggers_window(self, coordinator):
        """喂入足够多的转写事件后，时间窗口应触发回调。"""
        segment_results = []
        coordinator.on_segment_summary(lambda seg: segment_results.append(seg))

        await coordinator.start()

        # 喂入超过一个窗口时长的事件
        for i in range(6):
            t = i * 3.0
            coordinator.feed_transcription(make_event(f"第{i}句话", t, t + 2.0))

        # 第6个事件 end_time=17s 超过窗口 end=10s → 触发窗口
        # 等待 worker 处理
        await asyncio.sleep(0.3)
        await coordinator.stop()

        assert len(segment_results) >= 1
        assert segment_results[0].topics

    @pytest.mark.asyncio
    async def test_segment_callback_data(self, coordinator):
        """段落摘要回调应包含正确的结构化数据。"""
        segment_results = []
        coordinator.on_segment_summary(lambda seg: segment_results.append(seg))

        await coordinator.start()

        for i in range(6):
            t = i * 3.0
            coordinator.feed_transcription(make_event(f"讨论第{i}个议题", t, t + 2.0))

        await asyncio.sleep(0.3)
        await coordinator.stop()

        assert len(segment_results) >= 1
        seg = segment_results[0]
        assert isinstance(seg, SegmentSummary)
        assert seg.time_range
        assert "新版本发布计划" in seg.topics

    @pytest.mark.asyncio
    async def test_global_callback_with_action_items(self):
        """累积足够段落后应触发全局合并，并提取 Action Items。"""
        llm = MockLLMClient(
            responses=[SEGMENT_LLM_RESPONSE, SEGMENT_LLM_RESPONSE, MERGE_LLM_RESPONSE] * 5
        )
        coord = SummaryCoordinator(
            llm,
            window_duration_s=5.0,
            overlap_s=1.0,
            merge_threshold=2,
        )

        global_results = []
        coord.on_global_summary(lambda gs, items: global_results.append((gs, items)))

        await coord.start()

        # 制造足够事件触发 2 个窗口 → 2 段落 → merge_threshold=2 → 全局合并
        for i in range(15):
            t = i * 1.5
            coord.feed_transcription(make_event(f"话题{i}", t, t + 1.0))

        await asyncio.sleep(0.5)
        await coord.stop()

        assert len(global_results) >= 1
        gs, items = global_results[0]
        assert isinstance(gs, GlobalSummary)
        assert not gs.is_empty
        # Action items should be extracted from merge response
        assert isinstance(items, list)

    @pytest.mark.asyncio
    async def test_stop_flushes_and_force_merges(self):
        """stop() 应刷出残余窗口并强制合并。"""
        llm = MockLLMClient(responses=[SEGMENT_LLM_RESPONSE, MERGE_LLM_RESPONSE] * 5)
        coord = SummaryCoordinator(
            llm,
            window_duration_s=60.0,
            overlap_s=5.0,
            merge_threshold=1,
        )

        global_results = []
        coord.on_global_summary(lambda gs, items: global_results.append((gs, items)))

        await coord.start()

        # 只喂入少量事件（不足以触发窗口完成）
        coord.feed_transcription(make_event("一些讨论内容", 0, 5))
        coord.feed_transcription(make_event("更多内容", 5, 10))

        # stop 应该 flush + force_merge
        await coord.stop()

        assert len(global_results) >= 1

    @pytest.mark.asyncio
    async def test_llm_offline_skips_summary(self):
        """LLM 离线时应跳过摘要，不崩溃。"""
        llm = MockLLMClient(state=LLMClientState.OFFLINE)
        coord = SummaryCoordinator(llm, window_duration_s=5.0, overlap_s=1.0)

        segment_results = []
        coord.on_segment_summary(lambda seg: segment_results.append(seg))

        await coord.start()

        for i in range(8):
            t = i * 1.5
            coord.feed_transcription(make_event(f"话题{i}", t, t + 1.0))

        await asyncio.sleep(0.2)
        await coord.stop()

        assert len(segment_results) == 0

    @pytest.mark.asyncio
    async def test_llm_exception_doesnt_crash(self):
        """LLM 异常不应导致 worker 崩溃。"""
        llm = MockLLMClient(responses=[ConnectionError("timeout")])
        coord = SummaryCoordinator(llm, window_duration_s=5.0, overlap_s=1.0)

        await coord.start()

        for i in range(8):
            t = i * 1.5
            coord.feed_transcription(make_event(f"话题{i}", t, t + 1.0))

        await asyncio.sleep(0.2)
        # Should not raise
        await coord.stop()

    @pytest.mark.asyncio
    async def test_empty_text_skipped(self, coordinator):
        """空文本的转写事件应被忽略。"""
        segment_results = []
        coordinator.on_segment_summary(lambda seg: segment_results.append(seg))

        await coordinator.start()

        coordinator.feed_transcription(make_event("", 0, 5))
        coordinator.feed_transcription(make_event("   ", 5, 10))

        await asyncio.sleep(0.1)
        await coordinator.stop()

        assert len(segment_results) == 0

    @pytest.mark.asyncio
    async def test_reset(self, coordinator):
        """reset 应清除所有状态。"""
        coordinator.feed_transcription(make_event("test", 0, 5))
        assert coordinator._time_window.pending_count > 0

        coordinator.reset()
        assert coordinator._time_window.pending_count == 0
        assert coordinator.global_summary is None

    @pytest.mark.asyncio
    async def test_callback_exception_doesnt_crash(self, coordinator):
        """回调异常不应导致 worker 崩溃。"""
        def bad_callback(seg):
            raise ValueError("callback error")

        coordinator.on_segment_summary(bad_callback)

        await coordinator.start()

        for i in range(6):
            t = i * 3.0
            coordinator.feed_transcription(make_event(f"话题{i}", t, t + 2.0))

        await asyncio.sleep(0.3)
        # Should not raise
        await coordinator.stop()


# ===========================================================================
# IPC 摘要消息测试
# ===========================================================================

class TestIPCSegmentSummary:

    def test_create_segment_summary_message(self):
        msg = IPCMessage.segment_summary(
            time_range="00:00 - 01:00",
            topics=["新版本发布"],
            conclusions=["下周三发布"],
            action_items=["完成测试"],
            raw_text="原始摘要",
        )
        assert msg.type == MessageType.SEGMENT_SUMMARY
        assert msg.data["time_range"] == "00:00 - 01:00"
        assert msg.data["topics"] == ["新版本发布"]
        assert msg.data["conclusions"] == ["下周三发布"]
        assert msg.data["action_items"] == ["完成测试"]
        assert msg.data["raw_text"] == "原始摘要"

    def test_segment_summary_roundtrip(self):
        msg = IPCMessage.segment_summary(
            time_range="01:00 - 02:00",
            topics=["topic1"],
            conclusions=["conclusion1"],
        )
        line = msg.to_json_line()
        restored = IPCMessage.from_json_line(line)
        assert restored.type == "segment_summary"
        assert restored.data["time_range"] == "01:00 - 02:00"
        assert restored.data["topics"] == ["topic1"]

    def test_segment_summary_defaults(self):
        msg = IPCMessage.segment_summary()
        assert msg.data["time_range"] == ""
        assert msg.data["topics"] == []
        assert msg.data["conclusions"] == []
        assert msg.data["action_items"] == []
        assert msg.data["raw_text"] == ""


class TestIPCGlobalSummary:

    def test_create_global_summary_message(self):
        msg = IPCMessage.global_summary(
            raw_text="全局摘要",
            segments_merged=5,
            merge_count=2,
            action_items=[
                {"description": "完成测试", "assignee": "张三",
                 "deadline": "周五", "status": "open"},
            ],
        )
        assert msg.type == MessageType.GLOBAL_SUMMARY
        assert msg.data["raw_text"] == "全局摘要"
        assert msg.data["segments_merged"] == 5
        assert msg.data["merge_count"] == 2
        assert len(msg.data["action_items"]) == 1
        assert msg.data["action_items"][0]["assignee"] == "张三"

    def test_global_summary_roundtrip(self):
        msg = IPCMessage.global_summary(
            raw_text="test summary",
            segments_merged=3,
            merge_count=1,
            action_items=[{"description": "task1", "assignee": "", "deadline": "", "status": "open"}],
        )
        line = msg.to_json_line()
        restored = IPCMessage.from_json_line(line)
        assert restored.type == "global_summary"
        assert restored.data["raw_text"] == "test summary"
        assert restored.data["segments_merged"] == 3
        assert len(restored.data["action_items"]) == 1

    def test_global_summary_defaults(self):
        msg = IPCMessage.global_summary(raw_text="text")
        assert msg.data["segments_merged"] == 0
        assert msg.data["merge_count"] == 0
        assert msg.data["action_items"] == []

    def test_message_type_values(self):
        """新的消息类型值应正确定义。"""
        assert MessageType.SEGMENT_SUMMARY == "segment_summary"
        assert MessageType.GLOBAL_SUMMARY == "global_summary"
