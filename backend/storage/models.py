"""数据模型定义 — 会议存储相关的数据结构。

单一职责：只定义数据结构和序列化/反序列化方法。
不包含任何 I/O 或业务逻辑。

注：GlossaryEntry 和 SceneConfig 已由 llm/glossary.py 和
expression/scene_manager.py 管理，不重复定义（ISP）。
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class MeetingStatus(str, Enum):
    """会议状态。"""

    RUNNING = "running"
    FINISHED = "finished"


# ---------------------------------------------------------------------------
# MeetingMeta — meta.json
# ---------------------------------------------------------------------------
@dataclass
class MeetingMeta:
    """会议元信息。"""

    meeting_id: str
    title: str
    started_at: float  # Unix timestamp
    status: str = MeetingStatus.RUNNING
    audio_source: str = ""
    ended_at: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> MeetingMeta:
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


# ---------------------------------------------------------------------------
# TranscriptionEntry — transcriptions.jsonl 的一行
# ---------------------------------------------------------------------------
@dataclass
class TranscriptionEntry:
    """转写记录条目。"""

    text: str
    timestamp: float = field(default_factory=time.time)
    language: str = ""
    confidence: float = 0.0
    speaker: str = ""
    segment_start: float = 0.0
    segment_end: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> TranscriptionEntry:
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


# ---------------------------------------------------------------------------
# SummaryRecord — summaries.json 内的摘要条目
# ---------------------------------------------------------------------------
@dataclass
class SummaryRecord:
    """摘要记录 — 段落摘要或全局摘要。"""

    summary_type: str  # "segment" or "global"
    raw_text: str
    time_range: str = ""
    topics: List[str] = field(default_factory=list)
    conclusions: List[str] = field(default_factory=list)
    action_items: List[str] = field(default_factory=list)  # segment-level
    segments_merged: int = 0  # global only
    merge_count: int = 0  # global only
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> SummaryRecord:
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


# ---------------------------------------------------------------------------
# ActionItemRecord — summaries.json 内的 Action Item
# ---------------------------------------------------------------------------
@dataclass
class ActionItemRecord:
    """Action Item 记录。"""

    description: str
    assignee: str = ""
    deadline: str = ""
    status: str = "open"
    source: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ActionItemRecord:
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


# ---------------------------------------------------------------------------
# MeetingSummaries — summaries.json 整体结构
# ---------------------------------------------------------------------------
@dataclass
class MeetingSummaries:
    """会议摘要集合。"""

    segments: List[SummaryRecord] = field(default_factory=list)
    global_summary: Optional[SummaryRecord] = None
    action_items: List[ActionItemRecord] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "segments": [s.to_dict() for s in self.segments],
            "global_summary": self.global_summary.to_dict()
            if self.global_summary
            else None,
            "action_items": [a.to_dict() for a in self.action_items],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> MeetingSummaries:
        segments = [SummaryRecord.from_dict(s) for s in data.get("segments", [])]
        gs = data.get("global_summary")
        global_summary = SummaryRecord.from_dict(gs) if gs else None
        items = [ActionItemRecord.from_dict(a) for a in data.get("action_items", [])]
        return cls(
            segments=segments, global_summary=global_summary, action_items=items
        )


# ---------------------------------------------------------------------------
# MeetingData — load_meeting() 的返回类型
# ---------------------------------------------------------------------------
@dataclass
class MeetingData:
    """完整会议数据。"""

    meta: MeetingMeta
    transcriptions: List[TranscriptionEntry] = field(default_factory=list)
    summaries: MeetingSummaries = field(default_factory=MeetingSummaries)
