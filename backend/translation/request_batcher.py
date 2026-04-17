"""请求合并器 — 在时间窗口内合并短句、去重相同文本。

单一职责：只负责收集输入文本、在合并窗口到期后输出合并结果。
不负责翻译调用或上下文管理。

设计决策：
- 使用 asyncio.Event + asyncio.wait_for 实现非阻塞的窗口等待
- 相同文本（与上一次输出完全相同）直接丢弃，避免重复翻译
- 窗口内多条短句用换行合并
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

logger = logging.getLogger(__name__)

_DEFAULT_MERGE_WINDOW_MS = 500


class RequestBatcher:
    """翻译请求合并器。

    在 merge_window_ms 内收集的短句会被合并为一条翻译请求。
    完全相同的文本（与上次输出对比）会被去重。

    使用方式：
        batcher = RequestBatcher(merge_window_ms=500)
        batcher.submit("Hello")
        batcher.submit("World")
        merged = await batcher.wait_and_flush()  # "Hello\nWorld"
    """

    def __init__(self, merge_window_ms: int = _DEFAULT_MERGE_WINDOW_MS) -> None:
        self._merge_window_s = merge_window_ms / 1000.0
        self._pending: list[str] = []
        self._event = asyncio.Event()
        self._last_output: Optional[str] = None

    def submit(self, text: str) -> None:
        """提交一条待翻译文本。

        空文本会被忽略。
        """
        stripped = text.strip()
        if not stripped:
            return

        self._pending.append(stripped)
        self._event.set()

    async def wait_and_flush(self) -> Optional[str]:
        """等待合并窗口到期，返回合并后的文本。

        流程：
        1. 等待首条文本到达（阻塞）
        2. 等待 merge_window_ms，期间累积更多文本
        3. 合并并去重后返回

        Returns:
            合并后的文本，如果去重后为空则返回 None。
        """
        # 1. 等待至少一条文本到达
        await self._event.wait()

        # 2. 等待合并窗口，期间可能有更多文本
        await asyncio.sleep(self._merge_window_s)

        # 3. 取出所有待处理文本
        self._event.clear()
        texts = self._pending[:]
        self._pending.clear()

        if not texts:
            return None

        merged = "\n".join(texts)

        # 4. 去重：与上次输出完全相同则跳过
        if merged == self._last_output:
            logger.debug("Batcher: dedup — same as last output, skipping")
            return None

        self._last_output = merged
        return merged

    def flush_immediate(self) -> Optional[str]:
        """立即取出所有待处理文本（不等待窗口）。

        用于停止翻译时清空缓冲区。

        Returns:
            合并后的文本，无待处理则返回 None。
        """
        if not self._pending:
            return None

        texts = self._pending[:]
        self._pending.clear()
        self._event.clear()

        merged = "\n".join(texts)
        if merged == self._last_output:
            return None

        self._last_output = merged
        return merged

    def reset(self) -> None:
        """重置合并器状态。"""
        self._pending.clear()
        self._event.clear()
        self._last_output = None

    @property
    def pending_count(self) -> int:
        """当前待合并的文本数。"""
        return len(self._pending)

    @property
    def has_pending(self) -> bool:
        return len(self._pending) > 0
