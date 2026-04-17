"""总结协调器 — 编排 TimeWindowManager → SegmentSummarizer → GlobalMerger → ActionItemExtractor。

单一职责：协调总结流水线各模块的执行顺序和异步调度。
不负责音频采集、ASR 或 IPC 传输。

设计要点：
- feed_transcription() 是同步方法，由 MeetingPipeline 回调调用（事件循环线程）
- TimeWindowManager 在窗口完成时将内容放入 asyncio.Queue
- 后台 worker 从队列取出内容，依次执行段落摘要 → 全局合并 → Action Items 提取
- 通过回调机制通知下游（IPC 推送等），保持与 Pipeline 一致的解耦模式
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import asdict, dataclass
from typing import Callable, List, Optional

from backend.llm.base import LLMClient
from backend.llm.prompt_manager import PromptManager
from backend.summary.action_item_extractor import ActionItem, ActionItemExtractor
from backend.summary.global_merger import GlobalMerger, GlobalSummary
from backend.summary.segment_summarizer import SegmentSummarizer, SegmentSummary
from backend.summary.time_window import TimeWindowManager, WindowContent

logger = logging.getLogger(__name__)

# 回调类型
SegmentSummaryCallback = Callable[[SegmentSummary], None]
GlobalSummaryCallback = Callable[[GlobalSummary, List[ActionItem]], None]


class SummaryCoordinator:
    """协调总结模块的异步执行。

    从 MeetingPipeline 的转写事件中收集文本，按时间窗口分段，
    通过 LLM 生成段落摘要，增量合并为全局摘要，提取 Action Items。

    使用方式：
        coordinator = SummaryCoordinator(llm_client)
        coordinator.on_segment_summary(lambda seg: ...)
        coordinator.on_global_summary(lambda gs, items: ...)
        await coordinator.start()
        pipeline.on_transcription(coordinator.feed_transcription)
        # ... 会议进行中 ...
        await coordinator.stop()
    """

    def __init__(
        self,
        llm_client: LLMClient,
        prompt_manager: Optional[PromptManager] = None,
        *,
        window_duration_s: float = 60.0,
        overlap_s: float = 10.0,
        merge_threshold: int = 5,
        glossary: str = "",
    ) -> None:
        self._pm = prompt_manager or PromptManager()

        self._time_window = TimeWindowManager(
            window_duration_s=window_duration_s,
            overlap_s=overlap_s,
        )
        self._summarizer = SegmentSummarizer(
            llm_client, self._pm, glossary=glossary,
        )
        self._merger = GlobalMerger(
            llm_client, self._pm, merge_threshold=merge_threshold,
        )
        self._extractor = ActionItemExtractor()

        self._window_queue: asyncio.Queue[WindowContent] = asyncio.Queue()
        self._worker_task: Optional[asyncio.Task] = None

        self._segment_callbacks: List[SegmentSummaryCallback] = []
        self._global_callbacks: List[GlobalSummaryCallback] = []

        # Wire time window manager callback
        self._time_window.on_window_complete(self._on_window_complete)

    def on_segment_summary(self, callback: SegmentSummaryCallback) -> None:
        """注册段落摘要完成回调。"""
        self._segment_callbacks.append(callback)

    def on_global_summary(self, callback: GlobalSummaryCallback) -> None:
        """注册全局摘要更新回调。"""
        self._global_callbacks.append(callback)

    async def start(self) -> None:
        """启动后台 worker — 处理时间窗口队列。"""
        self._worker_task = asyncio.create_task(self._worker_loop())
        logger.info("SummaryCoordinator started.")

    async def stop(self) -> None:
        """停止协调器 — 刷出残余窗口，强制合并，等待队列处理完毕。"""
        # 刷出 TimeWindowManager 中未完成的窗口
        self._time_window.flush()

        # 等待队列中所有窗口处理完毕
        if not self._window_queue.empty():
            try:
                await asyncio.wait_for(self._window_queue.join(), timeout=30.0)
            except asyncio.TimeoutError:
                logger.warning("Summary queue drain timed out")

        # 强制合并剩余的段落摘要
        summary = await self._merger.force_merge()
        if summary and not summary.is_empty:
            items = self._extractor.extract_from_text(summary.raw_text)
            self._notify_global(summary, items)

        # 停止 worker
        if self._worker_task:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
            self._worker_task = None

        logger.info("SummaryCoordinator stopped.")

    def feed_transcription(self, event) -> None:
        """接收转写事件（同步），送入时间窗口。

        由 MeetingPipeline 的回调调用（在事件循环线程中）。
        event 应具有 result.text, result.language, segment_start_time, segment_end_time 属性。
        """
        self._time_window.add(
            event.result.text,
            language=event.result.language,
            start_time=event.segment_start_time,
            end_time=event.segment_end_time,
        )

    def _on_window_complete(self, content: WindowContent) -> None:
        """TimeWindowManager 回调 — 将窗口内容放入异步队列。"""
        try:
            self._window_queue.put_nowait(content)
        except asyncio.QueueFull:
            logger.warning("Summary queue full, dropping window %d", content.window_index)

    async def _worker_loop(self) -> None:
        """后台 worker — 从队列取窗口内容，执行摘要 → 合并。"""
        while True:
            try:
                content = await self._window_queue.get()
            except asyncio.CancelledError:
                break

            try:
                await self._process_window(content)
            except Exception:
                logger.exception("Error processing summary window %d", content.window_index)
            finally:
                self._window_queue.task_done()

    async def _process_window(self, content: WindowContent) -> None:
        """处理单个时间窗口：段落摘要 → 全局合并 → Action Items 提取。"""
        text = content.merged_text
        if not text.strip():
            return

        # 1. 段落摘要
        segment = await self._summarizer.summarize(
            text, time_range=content.time_range,
        )
        if segment is None:
            return

        self._notify_segment(segment)

        # 2. 全局合并（GlobalMerger 内部控制合并频率）
        global_result = await self._merger.add_segment(segment)
        if global_result and not global_result.is_empty:
            items = self._extractor.extract_from_text(global_result.raw_text)
            self._notify_global(global_result, items)

    def _notify_segment(self, segment: SegmentSummary) -> None:
        for cb in self._segment_callbacks:
            try:
                cb(segment)
            except Exception:
                logger.exception("Segment summary callback error")

    def _notify_global(self, summary: GlobalSummary, items: List[ActionItem]) -> None:
        for cb in self._global_callbacks:
            try:
                cb(summary, items)
            except Exception:
                logger.exception("Global summary callback error")

    def reset(self) -> None:
        """重置所有状态。"""
        self._time_window.reset()
        self._merger.reset()

    def set_summary_interval(self, interval_s: float) -> None:
        """设置自动摘要的时间窗口间隔（秒）。

        Args:
            interval_s: 间隔秒数，范围 [60, 600]。
        """
        clamped = max(60.0, min(600.0, interval_s))
        self._time_window.set_window_duration(clamped)
        logger.info("Summary interval set to %.0fs", clamped)

    async def trigger_segment_summary(self) -> None:
        """手动触发段落摘要 — 刷出当前时间窗口，立即生成段落摘要。"""
        self._time_window.flush()
        # flush 会通过回调将窗口内容放入队列，worker 会自动处理
        # 等待队列中所有待处理窗口完成
        if not self._window_queue.empty():
            try:
                await asyncio.wait_for(self._window_queue.join(), timeout=30.0)
            except asyncio.TimeoutError:
                logger.warning("Manual segment summary timed out")

    async def trigger_global_summary(self) -> None:
        """手动触发全局摘要 — 先刷出段落，再强制合并所有已有段落为全局摘要。"""
        # 先刷出当前时间窗口的段落摘要
        await self.trigger_segment_summary()

        # 强制合并所有段落摘要为全局摘要
        summary = await self._merger.force_merge()
        if summary and not summary.is_empty:
            items = self._extractor.extract_from_text(summary.raw_text)
            self._notify_global(summary, items)

    @property
    def global_summary(self) -> Optional[GlobalSummary]:
        return self._merger.global_summary
