"""Task 3.6 — 本地存储层（JSON/JSONL 文件）测试。

覆盖：
- 数据模型序列化/反序列化往返测试
- MeetingStore CRUD 操作（创建、追加、读取、列表、结束）
- JSONL 追加写入线程安全
- Pipeline 集成回调验证
- 边界情况（空会议、大量转写、缺失文件等）
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest

from backend.storage.models import (
    ActionItemRecord,
    MeetingData,
    MeetingMeta,
    MeetingStatus,
    MeetingSummaries,
    SummaryRecord,
    TranscriptionEntry,
)
from backend.storage.meeting_store import MeetingStore


# ═══════════════════════════════════════════════════════════════════
# 数据模型序列化/反序列化往返测试
# ═══════════════════════════════════════════════════════════════════


class TestMeetingMeta:
    """MeetingMeta 模型测试。"""

    def test_roundtrip(self):
        meta = MeetingMeta(
            meeting_id="2026-04-17_1430_standup",
            title="standup",
            started_at=1713350400.0,
            status=MeetingStatus.RUNNING,
            audio_source="mic",
            ended_at=None,
        )
        d = meta.to_dict()
        restored = MeetingMeta.from_dict(d)
        assert restored.meeting_id == meta.meeting_id
        assert restored.title == meta.title
        assert restored.started_at == meta.started_at
        assert restored.status == MeetingStatus.RUNNING
        assert restored.audio_source == "mic"
        assert restored.ended_at is None

    def test_roundtrip_finished(self):
        meta = MeetingMeta(
            meeting_id="test",
            title="Test",
            started_at=1000.0,
            status=MeetingStatus.FINISHED,
            ended_at=2000.0,
        )
        restored = MeetingMeta.from_dict(meta.to_dict())
        assert restored.status == MeetingStatus.FINISHED
        assert restored.ended_at == 2000.0

    def test_from_dict_ignores_unknown_keys(self):
        data = {
            "meeting_id": "x",
            "title": "t",
            "started_at": 1.0,
            "future_field": "ignored",
        }
        meta = MeetingMeta.from_dict(data)
        assert meta.meeting_id == "x"

    def test_defaults(self):
        meta = MeetingMeta(meeting_id="a", title="b", started_at=0.0)
        assert meta.status == MeetingStatus.RUNNING
        assert meta.audio_source == ""
        assert meta.ended_at is None


class TestTranscriptionEntry:
    """TranscriptionEntry 模型测试。"""

    def test_roundtrip(self):
        entry = TranscriptionEntry(
            text="Hello world",
            timestamp=1000.0,
            language="en",
            confidence=0.95,
            speaker="user1",
            segment_start=0.0,
            segment_end=3.5,
        )
        restored = TranscriptionEntry.from_dict(entry.to_dict())
        assert restored.text == "Hello world"
        assert restored.language == "en"
        assert restored.confidence == 0.95
        assert restored.speaker == "user1"
        assert restored.segment_start == 0.0
        assert restored.segment_end == 3.5

    def test_defaults(self):
        entry = TranscriptionEntry(text="test")
        assert entry.language == ""
        assert entry.confidence == 0.0
        assert entry.speaker == ""

    def test_json_roundtrip(self):
        """JSON string 往返。"""
        entry = TranscriptionEntry(text="你好", language="zh", confidence=0.9)
        line = json.dumps(entry.to_dict(), ensure_ascii=False)
        restored = TranscriptionEntry.from_dict(json.loads(line))
        assert restored.text == "你好"
        assert restored.language == "zh"


class TestSummaryRecord:
    """SummaryRecord 模型测试。"""

    def test_segment_roundtrip(self):
        record = SummaryRecord(
            summary_type="segment",
            raw_text="Discussion about architecture.",
            time_range="00:00 - 01:00",
            topics=["architecture", "design"],
            conclusions=["Use microservices"],
            action_items=["Review proposal"],
        )
        restored = SummaryRecord.from_dict(record.to_dict())
        assert restored.summary_type == "segment"
        assert restored.time_range == "00:00 - 01:00"
        assert restored.topics == ["architecture", "design"]
        assert restored.conclusions == ["Use microservices"]
        assert restored.action_items == ["Review proposal"]

    def test_global_roundtrip(self):
        record = SummaryRecord(
            summary_type="global",
            raw_text="Overall meeting summary.",
            segments_merged=5,
            merge_count=1,
        )
        restored = SummaryRecord.from_dict(record.to_dict())
        assert restored.summary_type == "global"
        assert restored.segments_merged == 5
        assert restored.merge_count == 1

    def test_defaults(self):
        record = SummaryRecord(summary_type="segment", raw_text="x")
        assert record.topics == []
        assert record.conclusions == []
        assert record.segments_merged == 0


class TestActionItemRecord:
    """ActionItemRecord 模型测试。"""

    def test_roundtrip(self):
        item = ActionItemRecord(
            description="Write design doc",
            assignee="Alice",
            deadline="next Friday",
            status="open",
            source="00:05 - 01:00",
        )
        restored = ActionItemRecord.from_dict(item.to_dict())
        assert restored.description == "Write design doc"
        assert restored.assignee == "Alice"
        assert restored.deadline == "next Friday"
        assert restored.source == "00:05 - 01:00"

    def test_defaults(self):
        item = ActionItemRecord(description="Do something")
        assert item.assignee == ""
        assert item.deadline == ""
        assert item.status == "open"
        assert item.source == ""


class TestMeetingSummaries:
    """MeetingSummaries 模型测试。"""

    def test_empty_roundtrip(self):
        ms = MeetingSummaries()
        d = ms.to_dict()
        assert d["segments"] == []
        assert d["global_summary"] is None
        assert d["action_items"] == []
        restored = MeetingSummaries.from_dict(d)
        assert len(restored.segments) == 0
        assert restored.global_summary is None

    def test_full_roundtrip(self):
        ms = MeetingSummaries(
            segments=[
                SummaryRecord(summary_type="segment", raw_text="Seg 1"),
                SummaryRecord(summary_type="segment", raw_text="Seg 2"),
            ],
            global_summary=SummaryRecord(
                summary_type="global",
                raw_text="Global",
                segments_merged=2,
                merge_count=1,
            ),
            action_items=[
                ActionItemRecord(description="Task A"),
                ActionItemRecord(description="Task B", assignee="Bob"),
            ],
        )
        restored = MeetingSummaries.from_dict(ms.to_dict())
        assert len(restored.segments) == 2
        assert restored.segments[0].raw_text == "Seg 1"
        assert restored.global_summary is not None
        assert restored.global_summary.segments_merged == 2
        assert len(restored.action_items) == 2
        assert restored.action_items[1].assignee == "Bob"


class TestMeetingData:
    """MeetingData 模型测试。"""

    def test_defaults(self):
        md = MeetingData(
            meta=MeetingMeta(meeting_id="x", title="t", started_at=0.0)
        )
        assert md.transcriptions == []
        assert md.summaries.segments == []


# ═══════════════════════════════════════════════════════════════════
# MeetingStore CRUD 操作测试
# ═══════════════════════════════════════════════════════════════════


class TestMeetingStoreCreate:
    """MeetingStore 会议创建测试。"""

    def test_create_meeting_returns_id(self, tmp_path):
        store = MeetingStore(tmp_path)
        mid = store.create_meeting(title="standup", audio_source="mic")
        assert "standup" in mid
        assert (tmp_path / "meetings" / mid / "meta.json").exists()

    def test_create_meeting_without_title(self, tmp_path):
        store = MeetingStore(tmp_path)
        mid = store.create_meeting()
        # ID 格式：YYYY-MM-DD_HHMM
        assert len(mid) >= 15  # 至少有日期+时间

    def test_create_meeting_meta_content(self, tmp_path):
        store = MeetingStore(tmp_path)
        mid = store.create_meeting(title="test", audio_source="wasapi")
        data = store.load_meeting(mid)
        assert data.meta.meeting_id == mid
        assert data.meta.title == "test"
        assert data.meta.audio_source == "wasapi"
        assert data.meta.status == MeetingStatus.RUNNING
        assert data.meta.ended_at is None

    def test_create_meeting_sanitizes_title(self, tmp_path):
        store = MeetingStore(tmp_path)
        mid = store.create_meeting(title="My Meeting! @#$%")
        # 特殊字符应被移除
        assert "@" not in mid
        assert "#" not in mid

    def test_meetings_dir_created(self, tmp_path):
        store = MeetingStore(tmp_path)
        assert (tmp_path / "meetings").exists()


class TestMeetingStoreTranscription:
    """MeetingStore 转写追加测试。"""

    def test_append_and_load(self, tmp_path):
        store = MeetingStore(tmp_path)
        mid = store.create_meeting()
        entry = TranscriptionEntry(
            text="Hello", language="en", confidence=0.9
        )
        store.append_transcription(mid, entry)
        data = store.load_meeting(mid)
        assert len(data.transcriptions) == 1
        assert data.transcriptions[0].text == "Hello"
        assert data.transcriptions[0].language == "en"

    def test_append_multiple(self, tmp_path):
        store = MeetingStore(tmp_path)
        mid = store.create_meeting()
        for i in range(5):
            entry = TranscriptionEntry(text=f"Line {i}")
            store.append_transcription(mid, entry)
        data = store.load_meeting(mid)
        assert len(data.transcriptions) == 5
        assert data.transcriptions[2].text == "Line 2"

    def test_append_preserves_chinese(self, tmp_path):
        store = MeetingStore(tmp_path)
        mid = store.create_meeting()
        entry = TranscriptionEntry(text="你好世界", language="zh")
        store.append_transcription(mid, entry)
        data = store.load_meeting(mid)
        assert data.transcriptions[0].text == "你好世界"

    def test_jsonl_format(self, tmp_path):
        """验证 JSONL 文件的每行都是有效 JSON。"""
        store = MeetingStore(tmp_path)
        mid = store.create_meeting()
        for i in range(3):
            store.append_transcription(mid, TranscriptionEntry(text=f"L{i}"))
        path = store.get_meeting_dir(mid) / "transcriptions.jsonl"
        lines = path.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 3
        for line in lines:
            obj = json.loads(line)
            assert "text" in obj

    def test_empty_meeting_has_no_transcriptions(self, tmp_path):
        store = MeetingStore(tmp_path)
        mid = store.create_meeting()
        data = store.load_meeting(mid)
        assert data.transcriptions == []


class TestMeetingStoreSummaries:
    """MeetingStore 摘要保存测试。"""

    def test_add_segment_summary(self, tmp_path):
        store = MeetingStore(tmp_path)
        mid = store.create_meeting()
        record = SummaryRecord(
            summary_type="segment",
            raw_text="Discussion topic A.",
            time_range="00:00 - 01:00",
            topics=["A"],
        )
        store.add_segment_summary(mid, record)
        data = store.load_meeting(mid)
        assert len(data.summaries.segments) == 1
        assert data.summaries.segments[0].topics == ["A"]

    def test_add_multiple_segments(self, tmp_path):
        store = MeetingStore(tmp_path)
        mid = store.create_meeting()
        for i in range(3):
            record = SummaryRecord(
                summary_type="segment",
                raw_text=f"Segment {i}",
                time_range=f"{i}:00 - {i+1}:00",
            )
            store.add_segment_summary(mid, record)
        data = store.load_meeting(mid)
        assert len(data.summaries.segments) == 3

    def test_save_global_summary(self, tmp_path):
        store = MeetingStore(tmp_path)
        mid = store.create_meeting()
        global_record = SummaryRecord(
            summary_type="global",
            raw_text="Overall summary.",
            segments_merged=5,
            merge_count=1,
        )
        items = [
            ActionItemRecord(description="Write doc", assignee="Alice"),
            ActionItemRecord(description="Review PR"),
        ]
        store.save_global_summary(mid, global_record, items)
        data = store.load_meeting(mid)
        assert data.summaries.global_summary is not None
        assert data.summaries.global_summary.raw_text == "Overall summary."
        assert data.summaries.global_summary.segments_merged == 5
        assert len(data.summaries.action_items) == 2
        assert data.summaries.action_items[0].assignee == "Alice"

    def test_global_summary_update_preserves_segments(self, tmp_path):
        """更新全局摘要不影响已有段落摘要。"""
        store = MeetingStore(tmp_path)
        mid = store.create_meeting()
        # 先添加段落
        store.add_segment_summary(
            mid,
            SummaryRecord(summary_type="segment", raw_text="Seg 1"),
        )
        # 再更新全局
        store.save_global_summary(
            mid,
            SummaryRecord(summary_type="global", raw_text="Global"),
            [ActionItemRecord(description="Task 1")],
        )
        data = store.load_meeting(mid)
        assert len(data.summaries.segments) == 1
        assert data.summaries.global_summary.raw_text == "Global"
        assert len(data.summaries.action_items) == 1

    def test_empty_summaries(self, tmp_path):
        store = MeetingStore(tmp_path)
        mid = store.create_meeting()
        data = store.load_meeting(mid)
        assert data.summaries.segments == []
        assert data.summaries.global_summary is None
        assert data.summaries.action_items == []


class TestMeetingStoreFinish:
    """MeetingStore 会议结束测试。"""

    def test_finish_updates_status(self, tmp_path):
        store = MeetingStore(tmp_path)
        mid = store.create_meeting()
        store.finish_meeting(mid)
        data = store.load_meeting(mid)
        assert data.meta.status == MeetingStatus.FINISHED
        assert data.meta.ended_at is not None
        assert data.meta.ended_at >= data.meta.started_at

    def test_finish_nonexistent_raises(self, tmp_path):
        store = MeetingStore(tmp_path)
        with pytest.raises(FileNotFoundError):
            store.finish_meeting("nonexistent_meeting")


class TestMeetingStoreList:
    """MeetingStore 列表查询测试。"""

    def test_list_empty(self, tmp_path):
        store = MeetingStore(tmp_path)
        assert store.list_meetings() == []

    def test_list_multiple(self, tmp_path):
        store = MeetingStore(tmp_path)
        ids = []
        for title in ["aaa", "bbb", "ccc"]:
            mid = store.create_meeting(title=title)
            ids.append(mid)
            time.sleep(0.01)  # 确保时间戳不同
        meetings = store.list_meetings()
        assert len(meetings) == 3
        # 按开始时间倒序
        assert meetings[0].started_at >= meetings[1].started_at

    def test_list_skips_invalid_dirs(self, tmp_path):
        store = MeetingStore(tmp_path)
        store.create_meeting(title="valid")
        # 创建无效目录（无 meta.json）
        (tmp_path / "meetings" / "bad_dir").mkdir()
        meetings = store.list_meetings()
        assert len(meetings) == 1


class TestMeetingStoreLoadMeeting:
    """MeetingStore 完整会议加载测试。"""

    def test_load_full_meeting(self, tmp_path):
        store = MeetingStore(tmp_path)
        mid = store.create_meeting(title="full test", audio_source="mic")

        # 添加转写
        store.append_transcription(mid, TranscriptionEntry(text="Line 1"))
        store.append_transcription(mid, TranscriptionEntry(text="Line 2"))

        # 添加摘要
        store.add_segment_summary(
            mid,
            SummaryRecord(
                summary_type="segment",
                raw_text="Summary 1",
                topics=["topic A"],
            ),
        )
        store.save_global_summary(
            mid,
            SummaryRecord(summary_type="global", raw_text="Global"),
            [ActionItemRecord(description="Task 1")],
        )

        # 结束会议
        store.finish_meeting(mid)

        # 加载并验证
        data = store.load_meeting(mid)
        assert data.meta.status == MeetingStatus.FINISHED
        assert len(data.transcriptions) == 2
        assert len(data.summaries.segments) == 1
        assert data.summaries.global_summary is not None
        assert len(data.summaries.action_items) == 1

    def test_load_nonexistent_raises(self, tmp_path):
        store = MeetingStore(tmp_path)
        with pytest.raises(FileNotFoundError):
            store.load_meeting("does_not_exist")


class TestMeetingStoreGetDir:
    """MeetingStore.get_meeting_dir 测试。"""

    def test_get_existing_dir(self, tmp_path):
        store = MeetingStore(tmp_path)
        mid = store.create_meeting()
        d = store.get_meeting_dir(mid)
        assert d.is_dir()
        assert d.name == mid

    def test_get_nonexistent_raises(self, tmp_path):
        store = MeetingStore(tmp_path)
        with pytest.raises(FileNotFoundError):
            store.get_meeting_dir("no_such_meeting")


# ═══════════════════════════════════════════════════════════════════
# JSONL 追加写入线程安全测试
# ═══════════════════════════════════════════════════════════════════


class TestMeetingStoreThreadSafety:
    """多线程并发追加转写测试。"""

    def test_concurrent_append(self, tmp_path):
        store = MeetingStore(tmp_path)
        mid = store.create_meeting()
        n_threads = 8
        n_entries_per_thread = 50

        def writer_fn(thread_id: int) -> None:
            for i in range(n_entries_per_thread):
                entry = TranscriptionEntry(text=f"T{thread_id}-{i}")
                store.append_transcription(mid, entry)

        threads = [
            threading.Thread(target=writer_fn, args=(t,))
            for t in range(n_threads)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        data = store.load_meeting(mid)
        expected = n_threads * n_entries_per_thread
        assert len(data.transcriptions) == expected

        # 验证每行都是完整 JSON
        path = store.get_meeting_dir(mid) / "transcriptions.jsonl"
        lines = path.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == expected
        for line in lines:
            obj = json.loads(line)
            assert "text" in obj

    def test_concurrent_append_and_summary(self, tmp_path):
        """同时追加转写和保存摘要。"""
        store = MeetingStore(tmp_path)
        mid = store.create_meeting()
        errors: list = []

        def append_transcriptions() -> None:
            try:
                for i in range(30):
                    store.append_transcription(
                        mid, TranscriptionEntry(text=f"transcription-{i}")
                    )
            except Exception as e:
                errors.append(e)

        def add_summaries() -> None:
            try:
                for i in range(5):
                    store.add_segment_summary(
                        mid,
                        SummaryRecord(
                            summary_type="segment",
                            raw_text=f"segment-{i}",
                        ),
                    )
            except Exception as e:
                errors.append(e)

        t1 = threading.Thread(target=append_transcriptions)
        t2 = threading.Thread(target=add_summaries)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        assert errors == []
        data = store.load_meeting(mid)
        assert len(data.transcriptions) == 30
        assert len(data.summaries.segments) == 5


# ═══════════════════════════════════════════════════════════════════
# Pipeline 集成回调验证
# ═══════════════════════════════════════════════════════════════════


class TestPipelineIntegration:
    """模拟 Pipeline 回调链路验证存储集成。"""

    def test_transcription_callback_pattern(self, tmp_path):
        """模拟 main.py 中的 _on_transcription_store 回调。"""
        store = MeetingStore(tmp_path)
        mid = store.create_meeting(audio_source="mic")

        # 模拟 TranscriptionEvent 数据
        entry = TranscriptionEntry(
            text="Today we discussed the architecture.",
            timestamp=1713350400.0,
            language="en",
            confidence=0.92,
            segment_start=0.0,
            segment_end=3.5,
        )
        store.append_transcription(mid, entry)

        data = store.load_meeting(mid)
        assert data.transcriptions[0].text == "Today we discussed the architecture."

    def test_segment_summary_callback_pattern(self, tmp_path):
        """模拟 SummaryCoordinator.on_segment_summary 回调。"""
        store = MeetingStore(tmp_path)
        mid = store.create_meeting()

        record = SummaryRecord(
            summary_type="segment",
            raw_text="Discussed microservices architecture.",
            time_range="00:00 - 01:00",
            topics=["architecture", "microservices"],
            conclusions=["Use event-driven pattern"],
            action_items=["Create design document"],
        )
        store.add_segment_summary(mid, record)

        data = store.load_meeting(mid)
        seg = data.summaries.segments[0]
        assert seg.topics == ["architecture", "microservices"]

    def test_global_summary_callback_pattern(self, tmp_path):
        """模拟 SummaryCoordinator.on_global_summary 回调。"""
        store = MeetingStore(tmp_path)
        mid = store.create_meeting()

        # 模拟 ActionItem → ActionItemRecord 转换
        global_record = SummaryRecord(
            summary_type="global",
            raw_text="Overall: decided on microservices approach.",
            segments_merged=5,
            merge_count=1,
        )
        item_records = [
            ActionItemRecord(
                description="Write design doc",
                assignee="Alice",
                deadline="next Friday",
                status="open",
                source="00:05 - 01:00",
            ),
        ]
        store.save_global_summary(mid, global_record, item_records)

        data = store.load_meeting(mid)
        assert data.summaries.global_summary.segments_merged == 5
        assert data.summaries.action_items[0].assignee == "Alice"

    def test_full_callback_lifecycle(self, tmp_path):
        """模拟完整会议生命周期：创建→转写→摘要→结束→读取。"""
        store = MeetingStore(tmp_path)
        mid = store.create_meeting(title="standup", audio_source="mic")

        # 转写阶段
        for i in range(10):
            store.append_transcription(
                mid,
                TranscriptionEntry(
                    text=f"Transcription line {i}",
                    segment_start=i * 15.0,
                    segment_end=(i + 1) * 15.0,
                ),
            )

        # 段落摘要
        for i in range(2):
            store.add_segment_summary(
                mid,
                SummaryRecord(
                    summary_type="segment",
                    raw_text=f"Segment summary {i}",
                    time_range=f"{i*60}s - {(i+1)*60}s",
                    topics=[f"topic_{i}"],
                ),
            )

        # 全局摘要
        store.save_global_summary(
            mid,
            SummaryRecord(
                summary_type="global",
                raw_text="Global meeting summary.",
                segments_merged=2,
                merge_count=1,
            ),
            [ActionItemRecord(description="Follow up on design")],
        )

        # 结束
        store.finish_meeting(mid)

        # 验证完整数据
        data = store.load_meeting(mid)
        assert data.meta.status == MeetingStatus.FINISHED
        assert data.meta.ended_at is not None
        assert len(data.transcriptions) == 10
        assert len(data.summaries.segments) == 2
        assert data.summaries.global_summary is not None
        assert len(data.summaries.action_items) == 1


# ═══════════════════════════════════════════════════════════════════
# 边界情况
# ═══════════════════════════════════════════════════════════════════


class TestEdgeCases:
    """边界情况测试。"""

    def test_large_transcription_count(self, tmp_path):
        """大量转写条目。"""
        store = MeetingStore(tmp_path)
        mid = store.create_meeting()
        n = 500
        for i in range(n):
            store.append_transcription(
                mid, TranscriptionEntry(text=f"Line {i}")
            )
        data = store.load_meeting(mid)
        assert len(data.transcriptions) == n

    def test_empty_text_transcription(self, tmp_path):
        store = MeetingStore(tmp_path)
        mid = store.create_meeting()
        store.append_transcription(mid, TranscriptionEntry(text=""))
        data = store.load_meeting(mid)
        assert data.transcriptions[0].text == ""

    def test_unicode_in_all_fields(self, tmp_path):
        store = MeetingStore(tmp_path)
        mid = store.create_meeting(title="日本語テスト")
        store.append_transcription(
            mid, TranscriptionEntry(text="こんにちは世界", language="ja")
        )
        store.add_segment_summary(
            mid,
            SummaryRecord(
                summary_type="segment",
                raw_text="議論：アーキテクチャ",
                topics=["アーキテクチャ"],
            ),
        )
        store.save_global_summary(
            mid,
            SummaryRecord(summary_type="global", raw_text="全体まとめ"),
            [ActionItemRecord(description="設計ドキュメント作成")],
        )
        data = store.load_meeting(mid)
        assert data.transcriptions[0].text == "こんにちは世界"
        assert data.summaries.segments[0].topics == ["アーキテクチャ"]
        assert data.summaries.action_items[0].description == "設計ドキュメント作成"

    def test_summaries_json_atomic_write(self, tmp_path):
        """验证 summaries.json 没有残留 .tmp 文件。"""
        store = MeetingStore(tmp_path)
        mid = store.create_meeting()
        store.add_segment_summary(
            mid,
            SummaryRecord(summary_type="segment", raw_text="test"),
        )
        meeting_dir = store.get_meeting_dir(mid)
        assert not (meeting_dir / "summaries.tmp").exists()
        assert (meeting_dir / "summaries.json").exists()

    def test_meta_json_valid_after_finish(self, tmp_path):
        """验证 finish 后 meta.json 仍然是有效 JSON。"""
        store = MeetingStore(tmp_path)
        mid = store.create_meeting()
        store.finish_meeting(mid)
        path = store.get_meeting_dir(mid) / "meta.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["status"] == "finished"
        assert data["ended_at"] is not None

    def test_meeting_id_uniqueness_with_same_title(self, tmp_path):
        """同一秒创建相同标题的会议 — ID 基于时间所以可能相同。
        这是已知的限制，测试验证不会崩溃。
        """
        store = MeetingStore(tmp_path)
        mid1 = store.create_meeting(title="test")
        # 由于生成的 ID 可能相同，mkdir(exist_ok=True) 应该不会崩溃
        mid2 = store.create_meeting(title="test")
        # 至少不会抛异常
        assert mid1 is not None
        assert mid2 is not None
