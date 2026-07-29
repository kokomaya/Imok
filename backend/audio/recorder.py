"""录音器 — 将一个或多个音频源混音为单声道 WAV，增量写入磁盘。

单一职责（SRP）：只负责把送入的 float32 音频帧混音、按静音策略处理后写入 WAV。
不感知会议 / 管线 / IPC；由上层（main 子进程）驱动其生命周期与开关状态。

混音策略：
- 各源实时按相同采样率、相近节奏送帧；按“各源已缓冲的最小长度”对齐后逐块求和。
- close() 时对残余缓冲以零填充对齐后写出。

静音（mute）策略（全局，作用于所有源）：
- 'skip'    — 不写入任何数据（录音文件在该时段留空 / 时间轴不推进）
- 'silence' — 写入等长静音占位（时间轴连续，但不含实际声音）
"""

from __future__ import annotations

import logging
import threading
import wave
from pathlib import Path
from typing import Dict, Optional

import numpy as np

logger = logging.getLogger(__name__)

# 静音模式
MUTE_SILENCE = "silence"
MUTE_SKIP = "skip"


class AudioRecorder:
    """多源混音 WAV 录音器（线程安全）。"""

    def __init__(self, path, sample_rate: int, *, block_frames: int = 1600) -> None:
        """
        Args:
            path: 输出 WAV 文件路径。
            sample_rate: 采样率（Hz），与音频源一致（通常 16000）。
            block_frames: 对齐混音的最小块大小（帧）。
        """
        self._path = Path(path)
        self._sample_rate = int(sample_rate)
        self._block = int(block_frames)

        self._lock = threading.Lock()
        self._wav: Optional[wave.Wave_write] = None
        self._buffers: Dict[str, np.ndarray] = {}

        self._muted = False
        self._mute_mode = MUTE_SILENCE
        self._frames_written = 0

    # ── 状态 ─────────────────────────────────────────────

    @property
    def is_open(self) -> bool:
        return self._wav is not None

    @property
    def frames_written(self) -> int:
        return self._frames_written

    def set_muted(self, muted: bool) -> None:
        self._muted = bool(muted)

    def set_mute_mode(self, mode: str) -> None:
        if mode in (MUTE_SILENCE, MUTE_SKIP):
            self._mute_mode = mode

    # ── 生命周期 ─────────────────────────────────────────

    def open(self) -> None:
        """打开（或复用）WAV 文件。幂等。"""
        with self._lock:
            if self._wav is not None:
                return
            self._path.parent.mkdir(parents=True, exist_ok=True)
            w = wave.open(str(self._path), "wb")
            w.setnchannels(1)
            w.setsampwidth(2)  # int16
            w.setframerate(self._sample_rate)
            self._wav = w
            self._buffers.clear()
            logger.info("Recording started: %s", self._path)

    def close(self) -> None:
        """刷出残余缓冲并关闭文件。幂等。"""
        with self._lock:
            if self._wav is None:
                return
            self._flush_remaining_locked()
            try:
                self._wav.close()
            finally:
                self._wav = None
            logger.info(
                "Recording stopped: %s (%.1fs)",
                self._path,
                self._frames_written / self._sample_rate if self._sample_rate else 0.0,
            )

    # ── 送帧 ─────────────────────────────────────────────

    def feed(self, source_name: str, samples: np.ndarray) -> None:
        """送入某个源的一块音频（mono float32, [-1,1]）。线程安全。"""
        if self._wav is None or samples is None or len(samples) == 0:
            return

        # 静音处理
        if self._muted:
            if self._mute_mode == MUTE_SKIP:
                return
            samples = np.zeros(len(samples), dtype=np.float32)

        pcm = self._to_int16(samples)

        with self._lock:
            if self._wav is None:
                return
            buf = self._buffers.get(source_name)
            self._buffers[source_name] = (
                np.concatenate([buf, pcm]) if buf is not None else pcm
            )
            self._mix_and_write_locked()

    # ── 内部 ─────────────────────────────────────────────

    @staticmethod
    def _to_int16(samples: np.ndarray) -> np.ndarray:
        clipped = np.clip(samples, -1.0, 1.0)
        return (clipped * 32767.0).astype(np.int16)

    def _mix_and_write_locked(self) -> None:
        """按各源缓冲的最小长度对齐求和并写出。"""
        if not self._buffers:
            return
        n = min(len(b) for b in self._buffers.values())
        if n < self._block:
            return

        acc = np.zeros(n, dtype=np.int32)
        for name, buf in self._buffers.items():
            acc += buf[:n].astype(np.int32)
            self._buffers[name] = buf[n:]

        self._write_int32_locked(acc)

    def _flush_remaining_locked(self) -> None:
        """关闭时以零填充对齐残余缓冲并写出。"""
        if not self._buffers:
            return
        max_len = max((len(b) for b in self._buffers.values()), default=0)
        if max_len == 0:
            return
        acc = np.zeros(max_len, dtype=np.int32)
        for buf in self._buffers.values():
            if len(buf):
                acc[: len(buf)] += buf.astype(np.int32)
        self._buffers.clear()
        self._write_int32_locked(acc)

    def _write_int32_locked(self, acc: np.ndarray) -> None:
        mixed = np.clip(acc, -32768, 32767).astype(np.int16)
        try:
            self._wav.writeframes(mixed.tobytes())
            self._frames_written += len(mixed)
        except Exception:
            logger.exception("Failed to write recording frames")
