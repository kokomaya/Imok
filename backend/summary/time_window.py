"""时间窗口管理器 — 按固定时长分段收集转写文本，触发摘要。

单一职责：管理时间窗口的切分和重叠缓冲，收集转写事件，
窗口满时回调通知外部进行摘要。不负责 LLM 调用或摘要生成。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Callable, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class TranscriptEntry:
    """时间窗口内的一条转写记录。"""

    text: str
    language: str
    start_time: float  # 相对会议开始的秒数
    end_time: float


@dataclass
class WindowContent:
    """一个完整时间窗口的内容。"""

    window_index: int
    start_time: float
    end_time: float
    entries: List[TranscriptEntry]
    time_range: str = ""  # 可读的时间范围

    @property
    def merged_text(self) -> str:
        """合并所有转写文本为单一字符串。"""
        return "\n".join(e.text for e in self.entries if e.text.strip())

    def __post_init__(self) -> None:
        if not self.time_range:
            self.time_range = (
                f"{_fmt_time(self.start_time)} - {_fmt_time(self.end_time)}"
            )


WindowCallback = Callable[[WindowContent], None]


class TimeWindowManager:
    """按固定时长分段收集转写文本。

    功能：
    - 以 window_duration_s 为周期切分窗口
    - 支持 overlap_s 重叠缓冲（前一个窗口末尾的内容会包含在下一个窗口开头）
    - 窗口结束时触发回调，传递 WindowContent

    使用方式：
        manager = TimeWindowManager(window_duration_s=60, overlap_s=10)
        manager.on_window_complete(callback)
        # 每次收到转写结果时调用：
        manager.add(text="...", language="zh", start_time=12.5, end_time=15.0)
        # 会议结束时刷新最后一个未满的窗口：
        manager.flush()
    """

    def __init__(
        self,
        *,
        window_duration_s: float = 60.0,
        overlap_s: float = 10.0,
    ) -> None:
        if window_duration_s <= 0:
            raise ValueError("window_duration_s must be positive")
        if overlap_s < 0 or overlap_s >= window_duration_s:
            raise ValueError("overlap_s must be in [0, window_duration_s)")

        self._window_duration = window_duration_s
        self._overlap = overlap_s
        self._callbacks: List[WindowCallback] = []

        # 状态
        self._entries: List[TranscriptEntry] = []
        self._window_index: int = 0
        self._window_start: float = 0.0
        self._window_end: float = window_duration_s

    def on_window_complete(self, callback: WindowCallback) -> None:
        """注册窗口完成回调。"""
        self._callbacks.append(callback)

    def add(
        self,
        text: str,
        *,
        language: str = "",
        start_time: float = 0.0,
        end_time: float = 0.0,
    ) -> None:
        """添加一条转写记录。如果超出当前窗口范围，自动触发窗口完成。

        Args:
            text: 转写文本。
            language: 语言标识。
            start_time: 段落开始时间（秒）。
            end_time: 段落结束时间（秒）。
        """
        entry = TranscriptEntry(
            text=text.strip(),
            language=language,
            start_time=start_time,
            end_time=end_time,
        )

        if not entry.text:
            return

        # 如果这条记录的开始时间超出当前窗口，先完成当前窗口
        while end_time > self._window_end and self._entries:
            self._emit_window()

        self._entries.append(entry)

    def flush(self) -> None:
        """刷新当前未满窗口（会议结束时调用）。"""
        if self._entries:
            self._emit_window()

    def reset(self) -> None:
        """重置所有状态（新会议开始时调用）。"""
        self._entries.clear()
        self._window_index = 0
        self._window_start = 0.0
        self._window_end = self._window_duration

    @property
    def window_index(self) -> int:
        return self._window_index

    @property
    def pending_count(self) -> int:
        """当前窗口中待处理的条目数。"""
        return len(self._entries)

    def _emit_window(self) -> None:
        """完成当前窗口，触发回调，并准备下一个窗口（含重叠）。"""
        content = WindowContent(
            window_index=self._window_index,
            start_time=self._window_start,
            end_time=self._window_end,
            entries=list(self._entries),
        )

        for cb in self._callbacks:
            try:
                cb(content)
            except Exception:
                logger.exception("Window callback error (window %d)", self._window_index)

        # 计算重叠：保留末尾 overlap_s 时间内的条目
        overlap_cutoff = self._window_end - self._overlap
        overlap_entries = [
            e for e in self._entries if e.end_time > overlap_cutoff
        ] if self._overlap > 0 else []

        # 推进窗口
        self._window_index += 1
        self._window_start = self._window_end - self._overlap
        self._window_end = self._window_start + self._window_duration
        self._entries = overlap_entries


def _fmt_time(seconds: float) -> str:
    """格式化秒数为 MM:SS。"""
    m, s = divmod(int(seconds), 60)
    if m >= 60:
        h, m = divmod(m, 60)
        return f"{h:d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"
