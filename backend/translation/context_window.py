"""上下文窗口 — 维护最近的翻译上下文，供 Prompt 注入使用。

单一职责：只负责存储和格式化最近的翻译对（原文+译文），
不负责翻译调用或请求合并。
"""

from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass
from typing import List

logger = logging.getLogger(__name__)

_DEFAULT_MAX_ENTRIES = 3


@dataclass
class TranslationEntry:
    """一条翻译记录（原文 + 译文）。"""

    source: str
    translated: str


class ContextWindow:
    """滑动上下文窗口 — 维护最近 N 条翻译记录。

    用于向翻译 Prompt 注入最近对话上下文，提升翻译连贯性。

    使用方式：
        ctx = ContextWindow(max_entries=3)
        ctx.add("这个 IPC 模块需要重构", "This IPC module needs refactoring")
        prompt_context = ctx.format_for_prompt()
    """

    def __init__(self, max_entries: int = _DEFAULT_MAX_ENTRIES) -> None:
        self._max_entries = max_entries
        self._entries: deque[TranslationEntry] = deque(maxlen=max_entries)

    def add(self, source: str, translated: str) -> None:
        """添加一条翻译记录。超出窗口大小时自动淘汰最旧的。"""
        self._entries.append(TranslationEntry(source=source, translated=translated))

    def format_for_prompt(self) -> str:
        """将上下文格式化为 Prompt 可注入的字符串。

        格式：
            [原文] → [译文]
            [原文] → [译文]

        空窗口返回 "(无上下文)"。
        """
        if not self._entries:
            return "(无上下文)"

        lines = []
        for entry in self._entries:
            lines.append(f"{entry.source} → {entry.translated}")
        return "\n".join(lines)

    def clear(self) -> None:
        """清空上下文窗口。"""
        self._entries.clear()

    @property
    def entries(self) -> List[TranslationEntry]:
        """当前窗口中的翻译记录（只读副本）。"""
        return list(self._entries)

    @property
    def size(self) -> int:
        """当前记录数。"""
        return len(self._entries)

    @property
    def max_entries(self) -> int:
        return self._max_entries
