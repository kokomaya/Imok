"""会议文件存储管理。

单一职责：只负责会议数据的文件读写操作。
不包含业务逻辑（如何/何时触发存储由 Pipeline 回调决定）。

存储结构（每场会议一个文件夹）：
    data/meetings/{meeting_id}/
        ├── meta.json              # 会议元信息
        ├── transcriptions.jsonl   # 转写记录（JSON Lines 追加写入）
        └── summaries.json         # 段落摘要 + 全局摘要 + Action Items

线程安全：
    append_transcription / add_segment_summary / save_global_summary
    使用 threading.Lock 保护，因为 ASR 回调可能在后台线程中触发。
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from pathlib import Path
from typing import List

from backend.storage.models import (
    ActionItemRecord,
    MeetingData,
    MeetingMeta,
    MeetingStatus,
    MeetingSummaries,
    SummaryRecord,
    TranscriptionEntry,
)

logger = logging.getLogger(__name__)

_META_FILE = "meta.json"
_TRANSCRIPTIONS_FILE = "transcriptions.jsonl"
_SUMMARIES_FILE = "summaries.json"
_SPEAKERS_FILE = "speakers.json"


class MeetingStore:
    """会议文件存储管理器。"""

    def __init__(self, base_dir: Path) -> None:
        self._meetings_dir = Path(base_dir) / "meetings"
        self._meetings_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    # ── 生命周期 ──────────────────────────────────────────────

    def create_meeting(
        self, title: str = "", audio_source: str = ""
    ) -> str:
        """创建新会议文件夹和 meta.json，返回 meeting_id。"""
        meeting_id = self._generate_id(title)
        meeting_dir = self._meetings_dir / meeting_id
        meeting_dir.mkdir(parents=True, exist_ok=True)

        meta = MeetingMeta(
            meeting_id=meeting_id,
            title=title,
            started_at=time.time(),
            status=MeetingStatus.RUNNING,
            audio_source=audio_source,
        )
        _write_json(meeting_dir / _META_FILE, meta.to_dict())
        logger.info("Created meeting: %s", meeting_id)
        return meeting_id

    def finish_meeting(self, meeting_id: str) -> None:
        """标记会议结束，更新 meta.json 的结束时间和状态。"""
        meeting_dir = self._meeting_dir(meeting_id)
        meta = self._read_meta(meeting_dir)
        meta.ended_at = time.time()
        meta.status = MeetingStatus.FINISHED
        _write_json(meeting_dir / _META_FILE, meta.to_dict())
        logger.info("Finished meeting: %s", meeting_id)

    def resume_meeting(self, meeting_id: str) -> str:
        """重新打开已结束的会议，继续录制。

        返回 meeting_id（与传入值相同）。
        """
        meeting_dir = self._meeting_dir(meeting_id)
        meta = self._read_meta(meeting_dir)
        meta.status = MeetingStatus.RUNNING
        meta.ended_at = None
        _write_json(meeting_dir / _META_FILE, meta.to_dict())
        logger.info("Resumed meeting: %s", meeting_id)
        return meeting_id

    # ── 写入 ──────────────────────────────────────────────────

    def append_transcription(
        self, meeting_id: str, entry: TranscriptionEntry
    ) -> None:
        """追加一条转写记录到 transcriptions.jsonl（线程安全）。"""
        path = self._meeting_dir(meeting_id) / _TRANSCRIPTIONS_FILE
        line = json.dumps(entry.to_dict(), ensure_ascii=False) + "\n"
        with self._lock:
            with open(path, "a", encoding="utf-8") as f:
                f.write(line)

    def add_segment_summary(
        self, meeting_id: str, record: SummaryRecord
    ) -> None:
        """追加一条段落摘要（线程安全）。"""
        with self._lock:
            summaries = self._read_summaries(
                self._meeting_dir(meeting_id)
            )
            summaries.segments.append(record)
            self._write_summaries(meeting_id, summaries)

    def save_global_summary(
        self,
        meeting_id: str,
        global_record: SummaryRecord,
        action_items: List[ActionItemRecord],
    ) -> None:
        """更新全局摘要和 Action Items（线程安全）。"""
        with self._lock:
            summaries = self._read_summaries(
                self._meeting_dir(meeting_id)
            )
            summaries.global_summary = global_record
            summaries.action_items = action_items
            self._write_summaries(meeting_id, summaries)

    # ── 查询 ──────────────────────────────────────────────────

    def save_speakers(self, meeting_id: str, speaker_data: dict) -> None:
        """保存说话人识别状态到 speakers.json（线程安全）。"""
        path = self._meeting_dir(meeting_id) / _SPEAKERS_FILE
        with self._lock:
            _write_json(path, speaker_data)

    def load_speakers(self, meeting_id: str) -> dict:
        """读取说话人识别状态（如果存在）。"""
        path = self._meeting_dir(meeting_id) / _SPEAKERS_FILE
        if not path.exists():
            return {}
        return _read_json(path)

    def load_meeting(self, meeting_id: str) -> MeetingData:
        """读取完整会议数据。"""
        meeting_dir = self._meeting_dir(meeting_id)
        meta = self._read_meta(meeting_dir)
        transcriptions = self._read_transcriptions(meeting_dir)
        summaries = self._read_summaries(meeting_dir)
        return MeetingData(
            meta=meta, transcriptions=transcriptions, summaries=summaries
        )

    def list_meetings(self) -> List[MeetingMeta]:
        """列出所有会议（按开始时间倒序）。"""
        meetings: List[MeetingMeta] = []
        if not self._meetings_dir.exists():
            return meetings
        for d in self._meetings_dir.iterdir():
            if d.is_dir() and (d / _META_FILE).exists():
                try:
                    meetings.append(self._read_meta(d))
                except Exception:
                    logger.warning("Skipped invalid meeting dir: %s", d.name)
        meetings.sort(key=lambda m: m.started_at, reverse=True)
        return meetings

    def get_meeting_dir(self, meeting_id: str) -> Path:
        """获取会议文件夹路径。"""
        return self._meeting_dir(meeting_id)

    # ── 内部方法 ──────────────────────────────────────────────

    def _meeting_dir(self, meeting_id: str) -> Path:
        d = self._meetings_dir / meeting_id
        if not d.exists():
            raise FileNotFoundError(f"Meeting not found: {meeting_id}")
        return d

    def _generate_id(self, title: str) -> str:
        """生成会议 ID：YYYY-MM-DD_HHMM[_slug]。"""
        ts = time.strftime("%Y-%m-%d_%H%M")
        if title:
            slug = re.sub(r"[^\w\s-]", "", title).strip()
            slug = re.sub(r"[\s]+", "_", slug)[:30]
            return f"{ts}_{slug}"
        return ts

    @staticmethod
    def _read_meta(meeting_dir: Path) -> MeetingMeta:
        data = _read_json(meeting_dir / _META_FILE)
        return MeetingMeta.from_dict(data)

    @staticmethod
    def _read_transcriptions(meeting_dir: Path) -> List[TranscriptionEntry]:
        path = meeting_dir / _TRANSCRIPTIONS_FILE
        if not path.exists():
            return []
        entries: List[TranscriptionEntry] = []
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    entries.append(TranscriptionEntry.from_dict(json.loads(line)))
        return entries

    @staticmethod
    def _read_summaries(meeting_dir: Path) -> MeetingSummaries:
        path = meeting_dir / _SUMMARIES_FILE
        if not path.exists():
            return MeetingSummaries()
        data = _read_json(path)
        return MeetingSummaries.from_dict(data)

    def _write_summaries(
        self, meeting_id: str, summaries: MeetingSummaries
    ) -> None:
        path = self._meeting_dir(meeting_id) / _SUMMARIES_FILE
        _write_json(path, summaries.to_dict())


# ── 文件 I/O 工具函数 ────────────────────────────────────────


def _read_json(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _write_json(path: Path, data: dict) -> None:
    """原子写入：先写临时文件，再 rename 替换。"""
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    tmp.replace(path)
