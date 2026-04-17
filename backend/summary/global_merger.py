"""全局合并摘要器 — 增量合并段落摘要为结构化全局会议总结。

单一职责：收集段落摘要，按阈值触发 LLM 合并，维护全局摘要状态。
不负责段落摘要生成（由 SegmentSummarizer 负责）、不负责 Action Items 提取。

依赖倒置：依赖 LLMClient 抽象接口和 PromptManager，不依赖具体实现。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Callable, List, Optional

from backend.llm.base import LLMClient, LLMClientState
from backend.llm.prompt_manager import PromptManager
from backend.summary.segment_summarizer import SegmentSummary

logger = logging.getLogger(__name__)


# =========================================================================
# 数据类
# =========================================================================


@dataclass
class GlobalSummary:
    """全局会议总结。"""

    raw_text: str  # LLM 返回的合并摘要原文
    segments_merged: int  # 已合并的段落摘要数量
    merge_count: int  # 触发合并的次数

    @property
    def is_empty(self) -> bool:
        return not self.raw_text.strip()


MergeCallback = Callable[[GlobalSummary], None]


# =========================================================================
# GlobalMerger
# =========================================================================


class GlobalMerger:
    """全局合并摘要器。

    收集段落摘要，每累积 merge_threshold 个后触发一次增量合并。
    合并方式：将已有全局摘要 + 新段落摘要文本一起发送给 LLM，
    输出更新后的结构化总结。

    使用方式：
        merger = GlobalMerger(llm_client, prompt_manager, merge_threshold=5)
        merger.on_merge(callback)
        await merger.add_segment(segment_summary)  # 累积到阈值时自动合并
        await merger.force_merge()                  # 会议结束时强制合并剩余
    """

    def __init__(
        self,
        llm_client: LLMClient,
        prompt_manager: Optional[PromptManager] = None,
        *,
        merge_threshold: int = 5,
        temperature: float = 0.3,
        max_tokens: int = 2048,
    ) -> None:
        if merge_threshold < 1:
            raise ValueError("merge_threshold must be >= 1")

        self._llm = llm_client
        self._pm = prompt_manager or PromptManager()
        self._merge_threshold = merge_threshold
        self._temperature = temperature
        self._max_tokens = max_tokens

        # 状态
        self._pending: List[SegmentSummary] = []
        self._global_summary: Optional[GlobalSummary] = None
        self._total_segments: int = 0
        self._merge_count: int = 0
        self._callbacks: List[MergeCallback] = []

    # ------------------------------------------------------------------
    # 公开接口
    # ------------------------------------------------------------------

    def on_merge(self, callback: MergeCallback) -> None:
        """注册合并完成回调。"""
        self._callbacks.append(callback)

    async def add_segment(self, segment: SegmentSummary) -> Optional[GlobalSummary]:
        """添加一个段落摘要。达到阈值时自动触发合并。

        Args:
            segment: 段落级摘要结果。

        Returns:
            如果触发了合并，返回更新后的 GlobalSummary；否则返回 None。
        """
        self._pending.append(segment)
        self._total_segments += 1

        if len(self._pending) >= self._merge_threshold:
            return await self._do_merge()
        return None

    async def force_merge(self) -> Optional[GlobalSummary]:
        """强制合并当前所有待处理的段落摘要（会议结束时调用）。

        Returns:
            合并结果，若无待处理内容则返回 None。
        """
        if not self._pending:
            return self._global_summary
        return await self._do_merge()

    @property
    def global_summary(self) -> Optional[GlobalSummary]:
        """当前全局摘要。"""
        return self._global_summary

    @property
    def pending_count(self) -> int:
        """待合并的段落摘要数。"""
        return len(self._pending)

    @property
    def total_segments(self) -> int:
        """已添加的段落摘要总数。"""
        return self._total_segments

    @property
    def merge_count(self) -> int:
        """已执行的合并次数。"""
        return self._merge_count

    def reset(self) -> None:
        """重置所有状态（新会议开始时调用）。"""
        self._pending.clear()
        self._global_summary = None
        self._total_segments = 0
        self._merge_count = 0

    # ------------------------------------------------------------------
    # 内部实现
    # ------------------------------------------------------------------

    async def _do_merge(self) -> Optional[GlobalSummary]:
        """执行一次增量合并。"""
        if self._llm.state == LLMClientState.OFFLINE:
            logger.warning("LLM offline, skipping global merge")
            return None

        new_text = self._format_pending()
        existing_text = self._global_summary.raw_text if self._global_summary else "（尚无全局摘要）"

        system_prompt, user_prompt = self._pm.render_merge_summary(
            existing_summary=existing_text,
            new_segment_summary=new_text,
        )

        try:
            response = await self._llm.complete(
                user_prompt,
                system_prompt=system_prompt,
                temperature=self._temperature,
                max_tokens=self._max_tokens,
            )
        except Exception:
            logger.exception("Global merge LLM call failed")
            return None

        raw = response.content.strip()
        if not raw:
            return None

        self._merge_count += 1
        self._global_summary = GlobalSummary(
            raw_text=raw,
            segments_merged=self._total_segments,
            merge_count=self._merge_count,
        )
        self._pending.clear()

        self._notify(self._global_summary)
        return self._global_summary

    def _format_pending(self) -> str:
        """将待合并的段落摘要格式化为文本。"""
        parts: List[str] = []
        for seg in self._pending:
            header = f"[{seg.time_range}]" if seg.time_range else ""
            parts.append(f"{header}\n{seg.raw_text}".strip())
        return "\n\n---\n\n".join(parts)

    def _notify(self, summary: GlobalSummary) -> None:
        """触发回调通知。"""
        for cb in self._callbacks:
            try:
                cb(summary)
            except Exception:
                logger.exception("Merge callback error")
