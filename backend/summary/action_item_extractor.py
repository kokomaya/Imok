"""Action Items 提取器 — 从摘要文本中提取结构化待办事项。

单一职责：解析 LLM 生成的摘要文本，提取 Action Items 并解析为
结构化数据（描述、责任人、截止时间）。不负责 LLM 调用或摘要生成。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional

logger = logging.getLogger(__name__)


# =========================================================================
# 数据类
# =========================================================================


class ActionItemStatus(str, Enum):
    """Action Item 状态。"""

    OPEN = "open"
    IN_PROGRESS = "in_progress"
    DONE = "done"


@dataclass
class ActionItem:
    """结构化 Action Item。"""

    description: str
    assignee: str = ""  # 责任人（如可识别）
    deadline: str = ""  # 截止时间（如提及），保留原始文本
    source: str = ""  # 来源段落的时间范围
    status: ActionItemStatus = ActionItemStatus.OPEN

    @property
    def has_assignee(self) -> bool:
        return bool(self.assignee.strip())

    @property
    def has_deadline(self) -> bool:
        return bool(self.deadline.strip())


# =========================================================================
# 解析模式
# =========================================================================

# 责任人模式：
#   "张三：完成 XXX"  /  "张三 - 完成 XXX"  /  "@张三 完成 XXX"
#   "Alice: do XXX"  /  "Alice — do XXX"
_ASSIGNEE_PATTERNS = [
    # "Name：description" 或 "Name: description"（中英文冒号）
    # 名字不能以数字开头（避免匹配日期如 "2026-04-20"）
    re.compile(r"^[@]?(?P<assignee>(?![0-9])[^\s:：\-—]{1,20})\s*[：:]\s*(?P<desc>.+)$"),
    # "Name - description" 或 "Name — description"
    re.compile(r"^[@]?(?P<assignee>(?![0-9])[^\s:：\-—]{1,20})\s*[—\-]\s*(?P<desc>.+)$"),
]

# 截止时间模式：匹配常见时间表述
_DEADLINE_PATTERNS = [
    # 中文：下周一、周五前、本周五、明天、后天、X月X日
    re.compile(r"(下周[一二三四五六日天]|本?周[一二三四五六日天]前?|明天|后天|\d{1,2}月\d{1,2}[日号]前?)"),
    # 中文：X天内、X个工作日内
    re.compile(r"(\d+\s*(?:天|个工作日|个?星期|周)内)"),
    # 英文：by Friday, next Monday, by EOD, by end of week
    re.compile(r"(by\s+\w+day|next\s+\w+day|by\s+EOD|by\s+end\s+of\s+\w+)", re.IGNORECASE),
    # 日期格式：2026-04-20、4/20、04/20
    re.compile(r"(\d{4}[-/]\d{1,2}[-/]\d{1,2}|\d{1,2}/\d{1,2})"),
]


# =========================================================================
# ActionItemExtractor
# =========================================================================


class ActionItemExtractor:
    """从摘要文本中提取结构化 Action Items。

    支持两种输入：
    1. 原始摘要文本（自动定位 Action Items 段落）
    2. 预提取的条目列表（如 SegmentSummary.action_items）

    使用方式：
        extractor = ActionItemExtractor()
        items = extractor.extract_from_text(global_summary.raw_text)
        items = extractor.extract_from_lines(segment.action_items, source="00:00 - 01:00")
    """

    def extract_from_text(
        self,
        text: str,
        *,
        source: str = "",
    ) -> List[ActionItem]:
        """从完整摘要文本中提取 Action Items。

        自动定位 Action Items / 行动项 / 待办 等标题下的内容。

        Args:
            text: LLM 生成的摘要文本。
            source: 来源标识（如时间范围）。

        Returns:
            结构化 Action Items 列表。
        """
        if not text or not text.strip():
            return []

        raw_items = _extract_action_lines(text)
        return [self._parse_item(line, source=source) for line in raw_items]

    def extract_from_lines(
        self,
        lines: List[str],
        *,
        source: str = "",
    ) -> List[ActionItem]:
        """从预提取的条目列表中解析结构化 Action Items。

        Args:
            lines: 原始条目文本列表。
            source: 来源标识。

        Returns:
            结构化 Action Items 列表。
        """
        return [self._parse_item(line, source=source) for line in lines if line.strip()]

    def _parse_item(self, raw: str, *, source: str = "") -> ActionItem:
        """解析单条 Action Item 文本为结构化数据。"""
        raw = raw.strip()
        assignee = ""
        description = raw

        # 尝试提取责任人
        for pattern in _ASSIGNEE_PATTERNS:
            m = pattern.match(raw)
            if m:
                candidate = m.group("assignee").strip()
                # 过滤掉明显不是人名的内容（太长或含特殊字符）
                if len(candidate) <= 15 and not re.search(r"[#\[\]{}()]", candidate):
                    assignee = candidate
                    description = m.group("desc").strip()
                break

        # 尝试提取截止时间
        deadline = ""
        for pattern in _DEADLINE_PATTERNS:
            m = pattern.search(description)
            if m:
                deadline = m.group(1).strip()
                break

        return ActionItem(
            description=description,
            assignee=assignee,
            deadline=deadline,
            source=source,
        )


# =========================================================================
# 内部辅助
# =========================================================================


def _extract_action_lines(text: str) -> List[str]:
    """从摘要文本中提取 Action Items 段落下的列表项。"""
    keywords = ("action", "行动", "待办", "todo")
    lines = text.split("\n")
    items: List[str] = []
    capturing = False

    for line in lines:
        stripped = line.strip()
        lower = stripped.lower()

        is_heading = stripped.startswith("#") or (
            stripped.endswith(":") or stripped.endswith("：")
        )

        if is_heading:
            capturing = any(kw in lower for kw in keywords)
            continue

        if capturing:
            if not stripped:
                continue
            if stripped.startswith(("-", "*", "•")) or (
                len(stripped) > 1 and stripped[0].isdigit() and stripped[1] in ".）)"
            ):
                item = stripped.lstrip("-*•0123456789.）) ").strip()
                if item:
                    items.append(item)
            elif items:
                capturing = False

    return items
