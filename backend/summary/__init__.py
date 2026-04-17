"""会议总结模块 - 提供段落摘要、全局合并和 Action Items 提取。"""

from backend.summary.action_item_extractor import ActionItem, ActionItemExtractor, ActionItemStatus
from backend.summary.global_merger import GlobalMerger, GlobalSummary
from backend.summary.segment_summarizer import SegmentSummarizer, SegmentSummary
from backend.summary.time_window import TimeWindowManager, WindowContent, TranscriptEntry

__all__ = [
    "ActionItem",
    "ActionItemExtractor",
    "ActionItemStatus",
    "GlobalMerger",
    "GlobalSummary",
    "SegmentSummarizer",
    "SegmentSummary",
    "TimeWindowManager",
    "WindowContent",
    "TranscriptEntry",
]
