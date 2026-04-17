"""IPC 消息协议与子进程 I/O 单元测试。

覆盖范围：
- MessageType / ProcessState / ControlAction 枚举
- IPCMessage 序列化/反序列化（JSON Lines）
- IPCMessage 工厂方法（transcription/status/error/control）
- TranscriptionData / StatusData / ErrorData / ControlData 数据类
- SubprocessWriter 线程安全写入
- SubprocessReader 异步读取与处理器分发
- 边界情况：空行、无效 JSON、未知类型、管道断开
"""

from __future__ import annotations

import asyncio
import io
import json
import threading
import time
from unittest.mock import MagicMock

import pytest

from backend.ipc.messages import (
    ControlAction,
    ControlData,
    ErrorData,
    IPCMessage,
    MessageType,
    ProcessState,
    StatusData,
    TranscriptionData,
)
from backend.ipc.subprocess_io import SubprocessReader, SubprocessWriter


# =========================================================================
# 1. 枚举测试
# =========================================================================
class TestEnums:
    def test_message_types(self):
        assert MessageType.TRANSCRIPTION == "transcription"
        assert MessageType.STATUS == "status"
        assert MessageType.ERROR == "error"
        assert MessageType.CONTROL == "control"

    def test_process_states(self):
        assert ProcessState.READY == "ready"
        assert ProcessState.LOADING == "loading"
        assert ProcessState.RUNNING == "running"
        assert ProcessState.STOPPED == "stopped"
        assert ProcessState.ERROR == "error"

    def test_control_actions(self):
        assert ControlAction.START == "start"
        assert ControlAction.STOP == "stop"
        assert ControlAction.SWITCH_SOURCE == "switch_source"


# =========================================================================
# 2. IPCMessage 序列化/反序列化
# =========================================================================
class TestIPCMessageSerialization:
    def test_to_json_line_basic(self):
        msg = IPCMessage(type="status", data={"state": "ready"}, ts=1234567890.0)
        line = msg.to_json_line()
        parsed = json.loads(line)
        assert parsed["type"] == "status"
        assert parsed["data"]["state"] == "ready"
        assert parsed["ts"] == 1234567890.0

    def test_to_json_line_no_trailing_newline(self):
        msg = IPCMessage(type="status", data={})
        line = msg.to_json_line()
        assert "\n" not in line

    def test_from_json_line_basic(self):
        line = '{"type": "control", "data": {"action": "start"}, "ts": 100.0}'
        msg = IPCMessage.from_json_line(line)
        assert msg.type == "control"
        assert msg.data["action"] == "start"
        assert msg.ts == 100.0

    def test_from_json_line_strips_whitespace(self):
        line = '  {"type": "status", "data": {}}  \n'
        msg = IPCMessage.from_json_line(line)
        assert msg.type == "status"

    def test_from_json_line_missing_data_defaults_empty(self):
        line = '{"type": "status"}'
        msg = IPCMessage.from_json_line(line)
        assert msg.data == {}

    def test_from_json_line_empty_raises(self):
        with pytest.raises(ValueError, match="Empty line"):
            IPCMessage.from_json_line("")

    def test_from_json_line_invalid_json_raises(self):
        with pytest.raises(ValueError, match="Invalid JSON"):
            IPCMessage.from_json_line("{not json}")

    def test_from_json_line_missing_type_raises(self):
        with pytest.raises(ValueError, match="type"):
            IPCMessage.from_json_line('{"data": {}}')

    def test_roundtrip(self):
        original = IPCMessage(type="transcription", data={"text": "你好", "language": "zh"})
        line = original.to_json_line()
        restored = IPCMessage.from_json_line(line)
        assert restored.type == original.type
        assert restored.data == original.data

    def test_unicode_preserved(self):
        msg = IPCMessage(type="transcription", data={"text": "你好世界"})
        line = msg.to_json_line()
        assert "你好世界" in line
        restored = IPCMessage.from_json_line(line)
        assert restored.data["text"] == "你好世界"


# =========================================================================
# 3. IPCMessage 工厂方法
# =========================================================================
class TestIPCMessageFactories:
    def test_transcription_factory(self):
        msg = IPCMessage.transcription(
            "hello world",
            language="en",
            confidence=0.95,
            segment_start=1.0,
            segment_end=3.5,
        )
        assert msg.type == MessageType.TRANSCRIPTION
        assert msg.data["text"] == "hello world"
        assert msg.data["language"] == "en"
        assert msg.data["confidence"] == 0.95
        assert msg.data["segment_start"] == 1.0
        assert msg.data["segment_end"] == 3.5
        assert isinstance(msg.data["timestamp"], float)
        assert msg.data["segments"] == []

    def test_transcription_with_segments(self):
        segments = [{"start": 0.0, "end": 1.0, "text": "hi"}]
        msg = IPCMessage.transcription("hi", segments=segments)
        assert msg.data["segments"] == segments

    def test_status_factory(self):
        msg = IPCMessage.status(
            ProcessState.RUNNING,
            source="wasapi",
            asr_model="medium",
            message="All systems go",
        )
        assert msg.type == MessageType.STATUS
        assert msg.data["state"] == "running"
        assert msg.data["source"] == "wasapi"
        assert msg.data["asr_model"] == "medium"
        assert msg.data["message"] == "All systems go"

    def test_status_ready_minimal(self):
        msg = IPCMessage.status(ProcessState.READY)
        assert msg.data["state"] == "ready"
        assert msg.data["source"] == ""

    def test_error_factory(self):
        msg = IPCMessage.error("audio_device_lost", "No audio device found")
        assert msg.type == MessageType.ERROR
        assert msg.data["code"] == "audio_device_lost"
        assert msg.data["message"] == "No audio device found"

    def test_control_start(self):
        msg = IPCMessage.control(ControlAction.START)
        assert msg.type == MessageType.CONTROL
        assert msg.data["action"] == "start"
        assert msg.data["source"] == ""

    def test_control_switch_source(self):
        msg = IPCMessage.control(ControlAction.SWITCH_SOURCE, source="mic")
        assert msg.data["action"] == "switch_source"
        assert msg.data["source"] == "mic"

    def test_factory_roundtrip(self):
        """工厂方法创建的消息可以序列化/反序列化。"""
        for msg in [
            IPCMessage.transcription("test", language="zh"),
            IPCMessage.status(ProcessState.RUNNING, source="wasapi"),
            IPCMessage.error("test_err", "test message"),
            IPCMessage.control(ControlAction.STOP),
        ]:
            line = msg.to_json_line()
            restored = IPCMessage.from_json_line(line)
            assert restored.type == msg.type
            assert restored.data == msg.data


# =========================================================================
# 4. SubprocessWriter 测试
# =========================================================================
class TestSubprocessWriter:
    def test_write_to_stream(self):
        stream = io.StringIO()
        writer = SubprocessWriter(stream=stream)
        msg = IPCMessage.status(ProcessState.READY)
        writer.write(msg)

        output = stream.getvalue()
        assert output.endswith("\n")
        parsed = json.loads(output.strip())
        assert parsed["type"] == "status"
        assert parsed["data"]["state"] == "ready"

    def test_write_multiple_messages(self):
        stream = io.StringIO()
        writer = SubprocessWriter(stream=stream)

        writer.write(IPCMessage.status(ProcessState.READY))
        writer.write(IPCMessage.transcription("hello"))
        writer.write(IPCMessage.error("test", "err"))

        lines = stream.getvalue().strip().split("\n")
        assert len(lines) == 3
        assert json.loads(lines[0])["type"] == "status"
        assert json.loads(lines[1])["type"] == "transcription"
        assert json.loads(lines[2])["type"] == "error"

    def test_thread_safety(self):
        """多线程同时写入不会交错。"""
        stream = io.StringIO()
        writer = SubprocessWriter(stream=stream)
        errors = []

        def write_n(n: int) -> None:
            try:
                for i in range(n):
                    writer.write(IPCMessage.transcription(f"thread-{threading.current_thread().name}-{i}"))
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=write_n, args=(20,)) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        lines = stream.getvalue().strip().split("\n")
        assert len(lines) == 100  # 5 threads * 20 messages

        # 每行都是合法 JSON
        for line in lines:
            parsed = json.loads(line)
            assert parsed["type"] == "transcription"

    def test_close_stops_writing(self):
        stream = io.StringIO()
        writer = SubprocessWriter(stream=stream)
        writer.write(IPCMessage.status(ProcessState.READY))
        writer.close()
        writer.write(IPCMessage.transcription("should not appear"))

        lines = stream.getvalue().strip().split("\n")
        assert len(lines) == 1

    def test_is_closed_property(self):
        writer = SubprocessWriter(stream=io.StringIO())
        assert not writer.is_closed
        writer.close()
        assert writer.is_closed

    def test_broken_pipe_marks_closed(self):
        """stdout 管道断开时自动标记为关闭。"""
        stream = MagicMock()
        stream.write.side_effect = BrokenPipeError()
        writer = SubprocessWriter(stream=stream)

        writer.write(IPCMessage.status(ProcessState.READY))  # should not raise
        assert writer.is_closed


# =========================================================================
# 5. SubprocessReader 测试
# =========================================================================
class TestSubprocessReader:
    @pytest.mark.asyncio
    async def test_reads_and_dispatches(self):
        """读取 JSON Lines 并分发到处理器。"""
        lines = [
            IPCMessage.control(ControlAction.START).to_json_line() + "\n",
            IPCMessage.control(ControlAction.STOP).to_json_line() + "\n",
            "",  # EOF
        ]
        stream = io.StringIO("".join(lines))

        received = []
        reader = SubprocessReader(stream=stream)
        reader.on_message("control", lambda msg: received.append(msg))

        await reader.start()
        # 给读取循环时间处理
        await asyncio.sleep(0.2)
        await reader.stop()

        assert len(received) == 2
        assert received[0].data["action"] == "start"
        assert received[1].data["action"] == "stop"

    @pytest.mark.asyncio
    async def test_ignores_invalid_lines(self):
        """无效 JSON 行被跳过，不影响后续消息。"""
        lines = [
            "not json\n",
            IPCMessage.control(ControlAction.START).to_json_line() + "\n",
            "{bad}\n",
            "",
        ]
        stream = io.StringIO("".join(lines))

        received = []
        reader = SubprocessReader(stream=stream)
        reader.on_message("control", lambda msg: received.append(msg))

        await reader.start()
        await asyncio.sleep(0.2)
        await reader.stop()

        assert len(received) == 1

    @pytest.mark.asyncio
    async def test_no_handler_for_type(self):
        """没有注册处理器的消息类型被跳过。"""
        lines = [
            IPCMessage.status(ProcessState.READY).to_json_line() + "\n",
            "",
        ]
        stream = io.StringIO("".join(lines))

        received = []
        reader = SubprocessReader(stream=stream)
        reader.on_message("control", lambda msg: received.append(msg))

        await reader.start()
        await asyncio.sleep(0.2)
        await reader.stop()

        assert len(received) == 0  # status has no handler, only control does

    @pytest.mark.asyncio
    async def test_eof_stops_reader(self):
        """stdin EOF 后读取循环自动停止。"""
        stream = io.StringIO("")  # immediate EOF
        reader = SubprocessReader(stream=stream)

        await reader.start()
        await asyncio.sleep(0.2)

        assert not reader._running

    @pytest.mark.asyncio
    async def test_handler_exception_does_not_crash(self):
        """处理器抛异常不影响后续消息处理。"""
        lines = [
            IPCMessage.control(ControlAction.START).to_json_line() + "\n",
            IPCMessage.control(ControlAction.STOP).to_json_line() + "\n",
            "",
        ]
        stream = io.StringIO("".join(lines))

        received = []
        call_count = 0

        def flaky_handler(msg):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("handler crashed")
            received.append(msg)

        reader = SubprocessReader(stream=stream)
        reader.on_message("control", flaky_handler)

        await reader.start()
        await asyncio.sleep(0.2)
        await reader.stop()

        # First message handler crashes, second succeeds
        assert len(received) == 1
        assert received[0].data["action"] == "stop"

    @pytest.mark.asyncio
    async def test_stop_is_idempotent(self):
        """多次调用 stop 不会报错。"""
        stream = io.StringIO("")
        reader = SubprocessReader(stream=stream)

        await reader.start()
        await asyncio.sleep(0.1)
        await reader.stop()
        await reader.stop()  # second stop should not raise
