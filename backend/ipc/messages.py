"""IPC 消息协议 — Python 子进程与 Electron 之间的 JSON Lines 消息定义。

单一职责：定义消息类型枚举、消息数据类和序列化/反序列化。
不负责 I/O 传输（由 subprocess_io.py 负责）。

协议格式：每行一个 JSON 对象（JSON Lines），以 \\n 结尾。
方向：
    stdout (Python → Electron)：TRANSCRIPTION, STATUS, ERROR, SEGMENT_SUMMARY, GLOBAL_SUMMARY
    stdin  (Electron → Python)：CONTROL
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class MessageType(str, Enum):
    """IPC 消息类型。"""

    # Python → Electron (stdout)
    TRANSCRIPTION = "transcription"  # ASR 转写结果
    STATUS = "status"  # 子进程状态
    ERROR = "error"  # 错误通知
    SEGMENT_SUMMARY = "segment_summary"  # 段落摘要
    GLOBAL_SUMMARY = "global_summary"  # 全局会议总结
    AUDIO_LEVEL = "audio_level"  # 实时音频电平
    RETRANSCRIBE_PROGRESS = "retranscribe_progress"  # 录音重解析进度
    RETRANSCRIBE_DONE = "retranscribe_done"  # 录音重解析完成
    MODEL_STATUS = "model_status"  # 语音模型本地存在性
    MODEL_DOWNLOAD_PROGRESS = "model_download_progress"  # 模型下载中
    MODEL_DOWNLOAD_DONE = "model_download_done"  # 模型下载完成

    # Electron → Python (stdin)
    CONTROL = "control"  # 控制命令


class ProcessState(str, Enum):
    """子进程运行状态。"""

    READY = "ready"  # 初始化完成，等待启动命令
    LOADING = "loading"  # 加载模型中
    RUNNING = "running"  # 正在采集和识别
    STOPPING = "stopping"  # 正在停止（等待清理完成）
    STOPPED = "stopped"  # 已停止
    ERROR = "error"  # 发生错误


class ControlAction(str, Enum):
    """控制命令动作。"""

    START = "start"  # 开始音频采集和识别
    STOP = "stop"  # 停止
    SWITCH_SOURCE = "switch_source"  # 切换音频源
    SET_DEVICES = "set_devices"  # 设置音频设备索引
    TRIGGER_SEGMENT_SUMMARY = "trigger_segment_summary"  # 手动触发段落摘要
    TRIGGER_GLOBAL_SUMMARY = "trigger_global_summary"  # 手动触发全局摘要
    SET_SUMMARY_INTERVAL = "set_summary_interval"  # 设置自动摘要间隔（秒）
    SET_AUTO_SUMMARY = "set_auto_summary"  # 开关自动定时摘要
    SET_SUMMARY_TEMPLATE = "set_summary_template"  # 设置会议总结模板（system prompt）
    SET_RECORDING = "set_recording"  # 设置录音开关与静音模式
    SET_ASR_CONFIG = "set_asr_config"  # 设置 ASR/VAD 参数（下次开始识别生效）
    RETRANSCRIBE = "retranscribe"  # 对已结束会议的录音重新解析生成独立字幕
    CHECK_MODEL = "check_model"  # 检查语音模型是否已下载
    DOWNLOAD_MODEL = "download_model"  # 下载语音模型


@dataclass
class TranscriptionData:
    """转写结果数据。"""

    text: str
    language: str = ""
    confidence: float = 0.0
    segment_start: float = 0.0
    segment_end: float = 0.0
    speaker: str = ""  # 说话人 ID
    source: str = ""  # 音频源名称（wasapi / mic）
    timestamp: float = field(default_factory=time.time)
    segments: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class StatusData:
    """状态数据。"""

    state: str  # ProcessState value
    source: str = ""  # 当前音频源
    asr_model: str = ""  # 当前 ASR 模型
    message: str = ""  # 附加信息
    meeting_id: str = ""  # 当前会议 ID（RUNNING/STOPPED 时携带）


@dataclass
class ErrorData:
    """错误数据。"""

    code: str  # 错误代码
    message: str  # 错误描述


@dataclass
class ControlData:
    """控制命令数据。"""

    action: str  # ControlAction value
    source: str = ""  # switch_source 时的目标音频源


@dataclass
class SegmentSummaryData:
    """段落摘要数据。"""

    time_range: str
    topics: List[str] = field(default_factory=list)
    conclusions: List[str] = field(default_factory=list)
    action_items: List[str] = field(default_factory=list)
    raw_text: str = ""


@dataclass
class GlobalSummaryData:
    """全局会议总结数据。"""

    raw_text: str
    segments_merged: int = 0
    merge_count: int = 0
    action_items: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class IPCMessage:
    """IPC 消息封装。

    序列化为 JSON 后通过 stdout/stdin 传输，每条消息占一行。
    """

    type: str  # MessageType value
    data: Dict[str, Any] = field(default_factory=dict)
    ts: float = field(default_factory=time.time)

    def to_json_line(self) -> str:
        """序列化为 JSON 行（不含尾部换行符）。"""
        return json.dumps(
            {"type": self.type, "data": self.data, "ts": self.ts},
            ensure_ascii=False,
        )

    @classmethod
    def from_json_line(cls, line: str) -> "IPCMessage":
        """从 JSON 行反序列化。

        Args:
            line: JSON 字符串（可含尾部换行/空白）。

        Raises:
            ValueError: JSON 解析失败或缺少必要字段。
        """
        line = line.strip()
        if not line:
            raise ValueError("Empty line")

        try:
            raw = json.loads(line)
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON: {e}") from e

        if not isinstance(raw, dict) or "type" not in raw:
            raise ValueError("Missing 'type' field")

        return cls(
            type=raw["type"],
            data=raw.get("data", {}),
            ts=raw.get("ts", time.time()),
        )

    # ── 便捷工厂方法 ──

    @classmethod
    def transcription(
        cls,
        text: str,
        *,
        language: str = "",
        confidence: float = 0.0,
        segment_start: float = 0.0,
        segment_end: float = 0.0,
        speaker: str = "",
        source: str = "",
        segments: Optional[List[Dict[str, Any]]] = None,
    ) -> "IPCMessage":
        """创建转写结果消息。"""
        data = TranscriptionData(
            text=text,
            language=language,
            confidence=confidence,
            segment_start=segment_start,
            segment_end=segment_end,
            speaker=speaker,
            source=source,
            segments=segments or [],
        )
        return cls(type=MessageType.TRANSCRIPTION, data=asdict(data))

    @classmethod
    def status(
        cls,
        state: ProcessState,
        *,
        source: str = "",
        asr_model: str = "",
        message: str = "",
        meeting_id: str = "",
    ) -> "IPCMessage":
        """创建状态消息。"""
        data = StatusData(
            state=state.value,
            source=source,
            asr_model=asr_model,
            message=message,
            meeting_id=meeting_id,
        )
        return cls(type=MessageType.STATUS, data=asdict(data))

    @classmethod
    def error(cls, code: str, message: str) -> "IPCMessage":
        """创建错误消息。"""
        data = ErrorData(code=code, message=message)
        return cls(type=MessageType.ERROR, data=asdict(data))

    @classmethod
    def retranscribe_progress(
        cls, meeting_id: str, processed: float, total: float
    ) -> "IPCMessage":
        """创建录音重解析进度消息。"""
        ratio = (processed / total) if total > 0 else 0.0
        return cls(
            type=MessageType.RETRANSCRIBE_PROGRESS,
            data={
                "meeting_id": meeting_id,
                "processed": round(float(processed), 2),
                "total": round(float(total), 2),
                "ratio": round(min(max(ratio, 0.0), 1.0), 4),
            },
        )

    @classmethod
    def retranscribe_done(
        cls,
        meeting_id: str,
        *,
        ok: bool,
        count: int = 0,
        error: str = "",
    ) -> "IPCMessage":
        """创建录音重解析完成消息。"""
        return cls(
            type=MessageType.RETRANSCRIBE_DONE,
            data={
                "meeting_id": meeting_id,
                "ok": bool(ok),
                "count": int(count),
                "error": error,
            },
        )

    @classmethod
    def model_status(
        cls, model_size: str, present: bool
    ) -> "IPCMessage":
        """创建语音模型本地存在性消息。"""
        return cls(
            type=MessageType.MODEL_STATUS,
            data={"model_size": model_size, "present": bool(present)},
        )

    @classmethod
    def model_download_progress(
        cls, model_size: str, message: str = ""
    ) -> "IPCMessage":
        """创建模型下载进度（不确定进度）消息。"""
        return cls(
            type=MessageType.MODEL_DOWNLOAD_PROGRESS,
            data={"model_size": model_size, "message": message},
        )

    @classmethod
    def model_download_done(
        cls, model_size: str, *, ok: bool, error: str = ""
    ) -> "IPCMessage":
        """创建模型下载完成消息。"""
        return cls(
            type=MessageType.MODEL_DOWNLOAD_DONE,
            data={"model_size": model_size, "ok": bool(ok), "error": error},
        )

    @classmethod
    def control(cls, action: ControlAction, *, source: str = "") -> "IPCMessage":
        """创建控制命令消息。"""
        data = ControlData(action=action.value, source=source)
        return cls(type=MessageType.CONTROL, data=asdict(data))

    @classmethod
    def segment_summary(
        cls,
        *,
        time_range: str = "",
        topics: Optional[List[str]] = None,
        conclusions: Optional[List[str]] = None,
        action_items: Optional[List[str]] = None,
        raw_text: str = "",
    ) -> "IPCMessage":
        """创建段落摘要消息。"""
        data = SegmentSummaryData(
            time_range=time_range,
            topics=topics or [],
            conclusions=conclusions or [],
            action_items=action_items or [],
            raw_text=raw_text,
        )
        return cls(type=MessageType.SEGMENT_SUMMARY, data=asdict(data))

    @classmethod
    def global_summary(
        cls,
        *,
        raw_text: str,
        segments_merged: int = 0,
        merge_count: int = 0,
        action_items: Optional[List[Dict[str, Any]]] = None,
    ) -> "IPCMessage":
        """创建全局会议总结消息。"""
        data = GlobalSummaryData(
            raw_text=raw_text,
            segments_merged=segments_merged,
            merge_count=merge_count,
            action_items=action_items or [],
        )
        return cls(type=MessageType.GLOBAL_SUMMARY, data=asdict(data))

    @classmethod
    def audio_level(cls, levels: Dict[str, float]) -> "IPCMessage":
        """创建音频电平消息。levels: {source_name: rms_value}"""
        return cls(type=MessageType.AUDIO_LEVEL, data={"levels": levels})
