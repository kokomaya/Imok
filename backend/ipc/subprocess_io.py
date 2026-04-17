"""子进程 I/O — stdin/stdout JSON Lines 读写。

单一职责：管理子进程与 Electron 主进程之间的 JSON Lines 传输。
不负责消息内容语义（由 messages.py 负责）。

设计要点：
- SubprocessWriter: 线程安全写 stdout（ASR 回调可能在 ThreadPoolExecutor 线程中）
- SubprocessReader: 异步从 stdin 读取控制命令
- 所有日志输出到 stderr，避免污染 stdout JSON Lines 协议
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
import threading
from typing import Callable, Optional

from backend.ipc.messages import IPCMessage

logger = logging.getLogger(__name__)


class SubprocessWriter:
    """向 stdout 写入 JSON Lines 消息。

    线程安全：内部使用 Lock 保护写操作，支持从多线程调用。
    """

    def __init__(self, stream=None) -> None:
        self._stream = stream or sys.stdout
        self._lock = threading.Lock()
        self._closed = False

    def write(self, message: IPCMessage) -> None:
        """写入一条 IPC 消息到 stdout。

        Args:
            message: IPCMessage 实例。
        """
        if self._closed:
            return

        line = message.to_json_line()

        with self._lock:
            try:
                self._stream.write(line + "\n")
                self._stream.flush()
            except (BrokenPipeError, OSError):
                # Electron 端可能已关闭 stdin 管道
                logger.debug("Stdout pipe broken, stopping writes.")
                self._closed = True

    def close(self) -> None:
        """标记为关闭，不再写入。"""
        self._closed = True

    @property
    def is_closed(self) -> bool:
        return self._closed


class SubprocessReader:
    """从 stdin 异步读取 JSON Lines 控制命令。

    使用 asyncio 在后台线程中 readline，避免阻塞事件循环。
    """

    def __init__(self, stream=None) -> None:
        self._stream = stream or sys.stdin
        self._handlers: dict[str, Callable[[IPCMessage], None]] = {}
        self._running = False
        self._task: Optional[asyncio.Task] = None

    def on_message(self, msg_type: str, handler: Callable[[IPCMessage], None]) -> None:
        """注册消息类型处理器。

        Args:
            msg_type: MessageType 值（如 "control"）。
            handler: 收到该类型消息时调用的回调函数。
        """
        self._handlers[msg_type] = handler

    async def start(self) -> None:
        """启动异步读取循环。"""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._read_loop())
        logger.debug("SubprocessReader started.")

    async def stop(self) -> None:
        """停止读取循环。"""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.debug("SubprocessReader stopped.")

    async def _read_loop(self) -> None:
        """后台读取循环 — 在线程中执行阻塞 readline。"""
        loop = asyncio.get_running_loop()

        while self._running:
            try:
                line = await loop.run_in_executor(None, self._stream.readline)
            except (EOFError, OSError):
                logger.debug("Stdin closed or EOF.")
                break

            if not line:
                # EOF — Electron 端关闭了管道
                logger.debug("Stdin EOF, stopping reader.")
                break

            line = line.strip()
            if not line:
                continue

            try:
                message = IPCMessage.from_json_line(line)
            except ValueError as e:
                logger.warning("Invalid IPC message: %s", e)
                continue

            handler = self._handlers.get(message.type)
            if handler:
                try:
                    handler(message)
                except Exception:
                    logger.exception("Error in IPC handler for type '%s'", message.type)
            else:
                logger.debug("No handler for message type '%s'", message.type)

        self._running = False
