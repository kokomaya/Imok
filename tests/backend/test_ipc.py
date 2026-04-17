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
import logging
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


# =========================================================================
# 6. main.py subprocess 模式集成测试
# =========================================================================
class TestMainSubprocessMode:
    """验证 main.py 的 subprocess 模式参数解析和日志配置。"""

    def test_parse_args_subprocess_mode(self):
        """--mode=subprocess 可被正确解析。"""
        from backend.main import _parse_args

        # 模拟命令行参数
        import sys
        original_argv = sys.argv
        sys.argv = ["main.py", "--mode=subprocess", "--source=wasapi"]
        try:
            args = _parse_args()
            assert args.mode == "subprocess"
            assert args.source == "wasapi"
        finally:
            sys.argv = original_argv

    def test_parse_args_subprocess_mic(self):
        from backend.main import _parse_args

        import sys
        original_argv = sys.argv
        sys.argv = ["main.py", "--mode=subprocess", "--source=mic"]
        try:
            args = _parse_args()
            assert args.mode == "subprocess"
            assert args.source == "mic"
        finally:
            sys.argv = original_argv

    def test_setup_logging_stderr_only(self):
        """stderr_only=True 模式下日志输出到 stderr。"""
        import sys
        from backend.main import _setup_logging

        root = logging.getLogger()
        # 清除之前的 handlers
        original_handlers = root.handlers[:]
        root.handlers.clear()

        try:
            _setup_logging("DEBUG", stderr_only=True)
            assert len(root.handlers) >= 1
            handler = root.handlers[-1]
            assert handler.stream is sys.stderr
        finally:
            root.handlers = original_handlers

    def test_ipc_writer_writes_status_ready(self):
        """SubprocessWriter 可以写 READY 状态消息。"""
        stream = io.StringIO()
        writer = SubprocessWriter(stream=stream)
        writer.write(IPCMessage.status(ProcessState.READY))

        output = stream.getvalue().strip()
        parsed = json.loads(output)
        assert parsed["type"] == "status"
        assert parsed["data"]["state"] == "ready"

    def test_transcription_event_to_ipc(self):
        """验证转写事件可以被正确转换为 IPC 消息。"""
        msg = IPCMessage.transcription(
            "这是一段测试文本",
            language="zh",
            confidence=0.92,
            segment_start=1.0,
            segment_end=3.5,
        )
        line = msg.to_json_line()
        restored = IPCMessage.from_json_line(line)
        assert restored.data["text"] == "这是一段测试文本"
        assert restored.data["language"] == "zh"
        assert restored.data["confidence"] == 0.92
        assert restored.data["segment_start"] == 1.0
        assert restored.data["segment_end"] == 3.5

    @pytest.mark.asyncio
    async def test_control_start_stop_roundtrip(self):
        """验证 stdin 控制命令 start/stop 可被 SubprocessReader 正确分发。"""
        start_msg = IPCMessage.control(ControlAction.START)
        stop_msg = IPCMessage.control(ControlAction.STOP)
        lines = start_msg.to_json_line() + "\n" + stop_msg.to_json_line() + "\n"
        stream = io.StringIO(lines)

        actions: list[str] = []
        reader = SubprocessReader(stream=stream)
        reader.on_message("control", lambda m: actions.append(m.data["action"]))

        await reader.start()
        await asyncio.sleep(0.2)
        await reader.stop()

        assert actions == ["start", "stop"]

    @pytest.mark.asyncio
    async def test_control_switch_source(self):
        """验证 switch_source 控制命令携带 source 参数。"""
        msg = IPCMessage.control(ControlAction.SWITCH_SOURCE, source="mic")
        stream = io.StringIO(msg.to_json_line() + "\n")

        received: list[IPCMessage] = []
        reader = SubprocessReader(stream=stream)
        reader.on_message("control", lambda m: received.append(m))

        await reader.start()
        await asyncio.sleep(0.2)
        await reader.stop()

        assert len(received) == 1
        assert received[0].data["action"] == "switch_source"
        assert received[0].data["source"] == "mic"


# =========================================================================
# 7. 子进程模式集成测试（模拟 stdin/stdout 完整交互）
# =========================================================================
class TestSubprocessIntegration:
    """模拟 Electron ↔ Python 子进程 IPC 的完整交互流程。

    使用 SubprocessWriter + SubprocessReader 搭建模拟环境，
    验证 JSON Lines 协议的端到端正确性。
    """

    def test_ready_status_on_startup(self):
        """子进程启动时应立即输出 READY 状态。"""
        stdout = io.StringIO()
        writer = SubprocessWriter(stream=stdout)
        writer.write(IPCMessage.status(ProcessState.READY))

        output = stdout.getvalue()
        lines = [l for l in output.strip().split("\n") if l.strip()]
        assert len(lines) == 1
        msg = json.loads(lines[0])
        assert msg["type"] == "status"
        assert msg["data"]["state"] == "ready"

    def test_full_lifecycle_status_sequence(self):
        """验证完整生命周期状态序列：READY → LOADING → RUNNING → STOPPED。"""
        stdout = io.StringIO()
        writer = SubprocessWriter(stream=stdout)

        writer.write(IPCMessage.status(ProcessState.READY))
        writer.write(IPCMessage.status(ProcessState.LOADING, message="Loading models..."))
        writer.write(IPCMessage.status(ProcessState.RUNNING, source="wasapi", asr_model="medium"))
        writer.write(IPCMessage.status(ProcessState.STOPPED))

        lines = [l for l in stdout.getvalue().strip().split("\n") if l.strip()]
        assert len(lines) == 4

        states = [json.loads(l)["data"]["state"] for l in lines]
        assert states == ["ready", "loading", "running", "stopped"]

        running_msg = json.loads(lines[2])
        assert running_msg["data"]["source"] == "wasapi"
        assert running_msg["data"]["asr_model"] == "medium"

    def test_transcription_output_format(self):
        """验证 transcription 消息格式完整性。"""
        stdout = io.StringIO()
        writer = SubprocessWriter(stream=stdout)

        writer.write(IPCMessage.transcription(
            "今天讨论一下架构优化方案",
            language="zh",
            confidence=0.95,
            segment_start=5.2,
            segment_end=8.7,
        ))

        line = stdout.getvalue().strip()
        msg = json.loads(line)
        assert msg["type"] == "transcription"
        assert msg["data"]["text"] == "今天讨论一下架构优化方案"
        assert msg["data"]["language"] == "zh"
        assert msg["data"]["confidence"] == 0.95
        assert msg["data"]["segment_start"] == 5.2
        assert msg["data"]["segment_end"] == 8.7
        assert "ts" in msg

    def test_error_output_format(self):
        """验证 error 消息格式。"""
        stdout = io.StringIO()
        writer = SubprocessWriter(stream=stdout)

        writer.write(IPCMessage.error("audio_device_lost", "WASAPI device disconnected"))

        msg = json.loads(stdout.getvalue().strip())
        assert msg["type"] == "error"
        assert msg["data"]["code"] == "audio_device_lost"
        assert msg["data"]["message"] == "WASAPI device disconnected"

    @pytest.mark.asyncio
    async def test_stdin_control_to_stdout_response(self):
        """模拟完整 stdin→处理→stdout 流程。

        模拟 Electron 发送 start/stop 控制命令，
        验证处理器能正确接收并可触发 stdout 响应。
        """
        # 模拟 stdin：发送 start 然后 stop
        start_cmd = IPCMessage.control(ControlAction.START)
        stop_cmd = IPCMessage.control(ControlAction.STOP)
        stdin_data = start_cmd.to_json_line() + "\n" + stop_cmd.to_json_line() + "\n"
        stdin_stream = io.StringIO(stdin_data)

        # 模拟 stdout
        stdout_stream = io.StringIO()
        writer = SubprocessWriter(stream=stdout_stream)

        # 处理器：收到 start → 写 RUNNING，收到 stop → 写 STOPPED
        def handle_control(msg: IPCMessage) -> None:
            action = msg.data.get("action", "")
            if action == "start":
                writer.write(IPCMessage.status(ProcessState.RUNNING, source="wasapi"))
            elif action == "stop":
                writer.write(IPCMessage.status(ProcessState.STOPPED))

        reader = SubprocessReader(stream=stdin_stream)
        reader.on_message("control", handle_control)

        await reader.start()
        await asyncio.sleep(0.3)
        await reader.stop()

        # 验证 stdout 输出
        lines = [l for l in stdout_stream.getvalue().strip().split("\n") if l.strip()]
        assert len(lines) == 2
        assert json.loads(lines[0])["data"]["state"] == "running"
        assert json.loads(lines[1])["data"]["state"] == "stopped"

    @pytest.mark.asyncio
    async def test_switch_source_control(self):
        """模拟 switch_source 命令并验证响应。"""
        switch_cmd = IPCMessage.control(ControlAction.SWITCH_SOURCE, source="mic")
        stdin_stream = io.StringIO(switch_cmd.to_json_line() + "\n")

        stdout_stream = io.StringIO()
        writer = SubprocessWriter(stream=stdout_stream)

        def handle_control(msg: IPCMessage) -> None:
            action = msg.data.get("action", "")
            if action == "switch_source":
                new_source = msg.data.get("source", "")
                writer.write(IPCMessage.status(ProcessState.RUNNING, source=new_source))

        reader = SubprocessReader(stream=stdin_stream)
        reader.on_message("control", handle_control)

        await reader.start()
        await asyncio.sleep(0.2)
        await reader.stop()

        lines = [l for l in stdout_stream.getvalue().strip().split("\n") if l.strip()]
        assert len(lines) == 1
        msg = json.loads(lines[0])
        assert msg["data"]["state"] == "running"
        assert msg["data"]["source"] == "mic"

    def test_multiple_transcriptions_interleaved(self):
        """验证多条 transcription 消息连续输出。"""
        stdout = io.StringIO()
        writer = SubprocessWriter(stream=stdout)

        texts = ["第一句话", "Second sentence", "第三句混合 mixed"]
        for i, text in enumerate(texts):
            writer.write(IPCMessage.transcription(
                text,
                language="zh" if i != 1 else "en",
                segment_start=float(i * 3),
                segment_end=float(i * 3 + 2.5),
            ))

        lines = [l for l in stdout.getvalue().strip().split("\n") if l.strip()]
        assert len(lines) == 3

        for i, line in enumerate(lines):
            msg = json.loads(line)
            assert msg["type"] == "transcription"
            assert msg["data"]["text"] == texts[i]

    @pytest.mark.asyncio
    async def test_stdin_eof_detected(self):
        """stdin EOF 时 reader 自动停止。"""
        stdin_stream = io.StringIO("")  # 立即 EOF
        reader = SubprocessReader(stream=stdin_stream)

        await reader.start()
        await asyncio.sleep(0.2)

        assert not reader._running

    def test_invalid_source_error_response(self):
        """模拟 switch_source 无效源时返回 error 消息。"""
        stdout = io.StringIO()
        writer = SubprocessWriter(stream=stdout)

        # 模拟 main.py 中对无效 source 的处理
        new_source = "invalid"
        if new_source not in ("wasapi", "mic"):
            writer.write(IPCMessage.error("invalid_source", f"Unknown source: {new_source}"))

        msg = json.loads(stdout.getvalue().strip())
        assert msg["type"] == "error"
        assert msg["data"]["code"] == "invalid_source"

    def test_all_messages_have_timestamp(self):
        """验证所有消息都包含时间戳。"""
        stdout = io.StringIO()
        writer = SubprocessWriter(stream=stdout)

        writer.write(IPCMessage.status(ProcessState.READY))
        writer.write(IPCMessage.transcription("test"))
        writer.write(IPCMessage.error("test", "test"))

        for line in stdout.getvalue().strip().split("\n"):
            msg = json.loads(line)
            assert "ts" in msg
            assert isinstance(msg["ts"], float)

    def test_cli_mode_preserved(self):
        """验证 --mode=cli 仍然可以被解析。"""
        from backend.main import _parse_args

        import sys
        original_argv = sys.argv
        sys.argv = ["main.py", "--mode=cli", "--source=mic"]
        try:
            args = _parse_args()
            assert args.mode == "cli"
            assert args.source == "mic"
        finally:
            sys.argv = original_argv
