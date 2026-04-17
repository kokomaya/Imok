"""麦克风音频源 — 捕获本地麦克风输入（用于闭麦辅助表达场景）。

使用 sounddevice 采集麦克风音频，支持指定设备 ID，
自动处理重采样和声道转换，输出 mono float32。
"""

from __future__ import annotations

import logging
import queue
import time
import threading
from typing import Optional

import numpy as np
import sounddevice as sd

from backend.audio.base import AudioChunk, AudioSource, AudioSourceType
from backend.audio.resampler import resample_audio, to_mono_float32

logger = logging.getLogger(__name__)


class MicrophoneSource(AudioSource):
    """麦克风音频采集源。

    通过 sounddevice 捕获本地麦克风输入，
    自动重采样为目标采样率 (16kHz)，输出 mono float32。
    """

    def __init__(
        self,
        target_sample_rate: int = 16000,
        chunk_frames: int = 512,
        device_index: int | None = None,
        buffer_max_seconds: float = 30.0,
    ) -> None:
        self._target_sr = target_sample_rate
        self._chunk_frames = chunk_frames
        self._device_index = device_index
        self._buffer_max = int(buffer_max_seconds * target_sample_rate / chunk_frames)

        self._stream: Optional[sd.InputStream] = None
        self._queue: queue.Queue[np.ndarray] = queue.Queue(maxsize=self._buffer_max)
        self._active = False
        self._frames_read = 0

        self._device_sr = 0
        self._device_channels = 0
        self._lock = threading.Lock()

    def start(self) -> None:
        if self._active:
            return

        # 查询设备信息
        if self._device_index is not None:
            dev_info = sd.query_devices(self._device_index, kind="input")
        else:
            dev_info = sd.query_devices(kind="input")

        self._device_sr = int(dev_info["default_samplerate"])
        self._device_channels = min(dev_info["max_input_channels"], 1) or 1

        # 按设备原生采样率计算 blocksize，使 chunk 时长与目标一致
        device_blocksize = int(
            self._chunk_frames * self._device_sr / self._target_sr
        )

        self._stream = sd.InputStream(
            samplerate=self._device_sr,
            blocksize=device_blocksize,
            device=self._device_index,
            channels=self._device_channels,
            dtype="float32",
            callback=self._audio_callback,
        )
        self._stream.start()

        self._frames_read = 0
        self._active = True

        logger.info(
            "Microphone started: device=%s sr=%d ch=%d",
            dev_info["name"],
            self._device_sr,
            self._device_channels,
        )

    def stop(self) -> None:
        with self._lock:
            if not self._active:
                return
            self._active = False

        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None

        # 清空缓冲区
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break

        logger.info("Microphone stopped.")

    def read_chunk(self) -> Optional[AudioChunk]:
        try:
            data = self._queue.get(timeout=0.1)
        except queue.Empty:
            return None

        timestamp = self._frames_read / self._target_sr
        self._frames_read += len(data)

        return AudioChunk(
            data=data,
            sample_rate=self._target_sr,
            timestamp_s=timestamp,
            source_type=AudioSourceType.MICROPHONE,
        )

    def get_sample_rate(self) -> int:
        return self._target_sr

    def get_source_type(self) -> AudioSourceType:
        return AudioSourceType.MICROPHONE

    @property
    def is_active(self) -> bool:
        return self._active

    def _audio_callback(
        self,
        indata: np.ndarray,
        frames: int,
        time_info: object,
        status: sd.CallbackFlags,
    ) -> None:
        """sounddevice 回调 — 在音频线程中运行。"""
        if status:
            logger.warning("Microphone callback status: %s", status)

        audio = to_mono_float32(indata.copy())

        # 重采样到目标采样率
        if self._device_sr != self._target_sr:
            audio = resample_audio(audio, self._device_sr, self._target_sr)

        try:
            self._queue.put_nowait(audio)
        except queue.Full:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                pass
            try:
                self._queue.put_nowait(audio)
            except queue.Full:
                pass
