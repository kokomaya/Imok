"""段落级摘要器 — 对固定时间窗口的转写文本生成结构化摘要。

单一职责：接收一段转写文本，调用 LLM 生成摘要并解析为结构化数据。
不负责时间窗口管理（由 TimeWindowManager 负责）、不负责全局合并。

依赖倒置：依赖 LLMClient 抽象接口和 PromptManager，不依赖具体实现。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import List, Optional

from backend.llm.base import LLMClient, LLMClientState
from backend.llm.prompt_manager import PromptManager

logger = logging.getLogger(__name__)


# =========================================================================
# 数据类
# =========================================================================


@dataclass
class SegmentSummary:
    """段落级摘要结果。"""

    time_range: str  # e.g. "00:00 - 01:00"
    raw_text: str  # LLM 返回的原始摘要文本
    source_text: str  # 输入的转写文本
    topics: List[str] = field(default_factory=list)
    conclusions: List[str] = field(default_factory=list)
    action_items: List[str] = field(default_factory=list)


# =========================================================================
# SegmentSummarizer
# =========================================================================


class SegmentSummarizer:
    """段落级摘要器。

    使用方式：
        summarizer = SegmentSummarizer(llm_client, prompt_manager)
        result = await summarizer.summarize(text, time_range="00:00 - 01:00")
    """

    def __init__(
        self,
        llm_client: LLMClient,
        prompt_manager: Optional[PromptManager] = None,
        *,
        glossary: str = "",
        temperature: float = 0.3,
        max_tokens: int = 1024,
    ) -> None:
        self._llm = llm_client
        self._pm = prompt_manager or PromptManager()
        self._glossary = glossary
        self._temperature = temperature
        self._max_tokens = max_tokens

    @property
    def glossary(self) -> str:
        return self._glossary

    @glossary.setter
    def glossary(self, value: str) -> None:
        self._glossary = value

    async def summarize(
        self,
        text: str,
        *,
        time_range: str = "",
    ) -> Optional[SegmentSummary]:
        """对一段转写文本生成结构化摘要。

        Args:
            text: 转写文本（一个时间窗口内的全部内容）。
            time_range: 可读的时间范围标识，如 "00:00 - 01:00"。

        Returns:
            SegmentSummary 或 None（文本为空或 LLM 不可用时）。
        """
        text = text.strip()
        if not text:
            return None

        if self._llm.state == LLMClientState.OFFLINE:
            logger.warning("LLM offline, skipping segment summary")
            return None

        system_prompt, user_prompt = self._pm.render_summary(
            text=text,
            glossary=self._glossary,
            time_range=time_range,
        )

        try:
            response = await self._llm.complete(
                user_prompt,
                system_prompt=system_prompt,
                temperature=self._temperature,
                max_tokens=self._max_tokens,
            )
        except Exception:
            logger.exception("Segment summary LLM call failed")
            return None

        raw = response.content.strip()
        if not raw:
            return None

        return SegmentSummary(
            time_range=time_range,
            raw_text=raw,
            source_text=text,
            topics=_extract_section(raw, "主题", "topic"),
            conclusions=_extract_section(raw, "结论", "conclusion", "决策", "decision"),
            action_items=_extract_section(raw, "action", "行动", "待办", "todo"),
        )


# =========================================================================
# 解析辅助
# =========================================================================


def _extract_section(text: str, *keywords: str) -> List[str]:
    """从 LLM 摘要文本中按关键词提取段落下的条目列表。

    支持两种格式：
    1. 列表项（- / * / • / 数字开头）
    2. Markdown 表格行（| col1 | col2 | ...）

    本函数找到包含任一 keyword 的标题行，收集其下方的列表项。
    """
    lines = text.split("\n")
    items: List[str] = []
    capturing = False
    in_table_header = False

    for line in lines:
        stripped = line.strip()
        lower = stripped.lower()

        # 检测标题行（## 开头或以 : 结尾的标题）
        is_heading = stripped.startswith("#") or (
            stripped.endswith(":") or stripped.endswith("：")
        )

        if is_heading:
            # 如果标题包含任一关键词，开始捕获
            capturing = any(kw.lower() in lower for kw in keywords)
            in_table_header = False
            continue

        if capturing:
            # 空行跳过
            if not stripped:
                continue
            # 表格分隔行（|---|---| 或类似）
            if stripped.startswith("|") and set(stripped.replace("|", "").strip()) <= {"-", ":", " "}:
                in_table_header = True
                continue
            # 表格表头行（第一个 | 行，跳过）
            if stripped.startswith("|") and not in_table_header and items == [] or False:
                # 只有当紧跟分隔行时才是表头，先标记后续为数据行
                in_table_header = False
                continue
            # 表格数据行
            if stripped.startswith("|") and stripped.endswith("|"):
                in_table_header = False
                cells = [c.strip() for c in stripped.split("|")[1:-1]]
                # 合并非空单元格为一条
                merged = "；".join(c for c in cells if c and not c.startswith("---") and c != "#")
                if merged:
                    items.append(merged)
                continue
            # 收集列表项（- 或 * 或数字开头）
            if stripped.startswith(("-", "*", "•")) or (
                len(stripped) > 1 and stripped[0].isdigit() and stripped[1] in ".）)"
            ):
                item = stripped.lstrip("-*•0123456789.）) ").strip()
                if item:
                    items.append(item)
            elif items:
                # 非列表项且已有内容 → 本段结束
                capturing = False

    return items
