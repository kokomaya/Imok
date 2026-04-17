"""WASAPI Loopback 音频源 — 捕获 Windows 系统音频（如 Teams 会议声音）。

使用 pyaudiowpatch 的 WASAPI loopback 模式采集系统输出音频，
自动处理重采样（系统通常 48kHz → 目标 16kHz）和声道转换。
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from typing import Optional

import numpy as np
import pyaudiowpatch as pyaudio

from backend.audio.base import AudioChunk, AudioDeviceInfo, AudioSource, AudioSourceType
from backend.audio.resampler import resample_audio, to_mono_float32

logger = logging.getLogger(__name__)


def _find_wasapi_loopback_device(
    pa: pyaudio.PyAudio,
    device_index: int | None = None,
) -> dict:
    """查找 WASAPI loopback 设备。

    Args:
        pa: PyAudio 实例。
        device_index: 指定设备索引，None 则自动选择默认输出设备的 loopback。

    Returns:
        设备信息字典。

    Raises:
        RuntimeError: 找不到合适的 loopback 设备。
    """
    if device_index is not None:
        info = pa.get_device_info_by_index(device_index)
        if info.get("isLoopbackDevice"):
            return info
        raise RuntimeError(
            f"Device index {device_index} is not a loopback device: {info['name']}"
        )

    # 获取 WASAPI host API
    wasapi_info = None
    for i in range(pa.get_host_api_count()):
        api_info = pa.get_host_api_info_by_index(i)
        if api_info["name"] == "Windows WASAPI":
            wasapi_info = api_info
            break

    if wasapi_info is None:
        raise RuntimeError("Windows WASAPI host API not found")

    # 查找默认输出设备对应的 loopback
    default_output_idx = wasapi_info["defaultOutputDevice"]
    default_output = pa.get_device_info_by_index(default_output_idx)
    default_output_name = default_output["name"]

    for i in range(pa.get_device_count()):
        dev = pa.get_device_info_by_index(i)
        if dev.get("isLoopbackDevice") and default_output_name in dev["name"]:
            logger.info(
                "Found loopback device: [%d] %s (%.0f Hz)",
                i,
                dev["name"],
                dev["defaultSampleRate"],
            )
            return dev

    raise RuntimeError(
        f"No loopback device found for default output: {default_output_name}"
    )


class WASAPILoopbackSource(AudioSource):
    """WASAPI Loopback 音频采集源。

    通过 pyaudiowpatch 捕获系统音频输出（如 Teams 会议声音），
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

        self._pa: Optional[pyaudio.PyAudio] = None
        self._stream: Optional[pyaudio.Stream] = None
        self._queue: queue.Queue[np.ndarray] = queue.Queue(maxsize=self._buffer_max)
        self._active = False
        self._start_time = 0.0
        self._frames_read = 0

        self._device_sr = 0
        self._device_channels = 0
        self._lock = threading.Lock()

    def start(self) -> None:
        if self._active:
            return

        self._pa = pyaudio.PyAudio()
        try:
            dev_info = _find_wasapi_loopback_device(self._pa, self._device_index)
        except RuntimeError:
            self._pa.terminate()
            self._pa = None
            raise

        self._device_sr = int(dev_info["defaultSampleRate"])
        self._device_channels = max(dev_info["maxInputChannels"], 1)

        # 按设备原生采样率计算每次采集帧数，使 chunk 时长与目标 chunk 时长一致
        device_chunk = int(
            self._chunk_frames * self._device_sr / self._target_sr
        )

        self._stream = self._pa.open(
            format=pyaudio.paFloat32,
            channels=self._device_channels,
            rate=self._device_sr,
            input=True,
            input_device_index=dev_info["index"],
            frames_per_buffer=device_chunk,
            stream_callback=self._audio_callback,
        )

        self._start_time = time.monotonic()
        self._frames_read = 0
        self._active = True

        logger.info(
            "WASAPI loopback started: device=%s sr=%d ch=%d",
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
            self._stream.stop_stream()
            self._stream.close()
            self._stream = None

        if self._pa is not None:
            self._pa.terminate()
            self._pa = None

        # 清空缓冲区
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break

        logger.info("WASAPI loopback stopped.")

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
            source_type=AudioSourceType.WASAPI_LOOPBACK,
        )

    def get_sample_rate(self) -> int:
        return self._target_sr

    def get_source_type(self) -> AudioSourceType:
        return AudioSourceType.WASAPI_LOOPBACK

    @property
    def is_active(self) -> bool:
        return self._active

    def _audio_callback(
        self,
        in_data: bytes | None,
        frame_count: int,
        time_info: dict,
        status: int,
    ) -> tuple[None, int]:
        """PyAudio 回调 — 在音频线程中运行，将数据送入 queue。"""
        if in_data is None:
            return (None, pyaudio.paContinue)

        audio = np.frombuffer(in_data, dtype=np.float32)

        # 多声道 → reshape → mono
        if self._device_channels > 1:
            audio = audio.reshape(-1, self._device_channels)
        audio = to_mono_float32(audio)

        # 重采样到目标采样率
        if self._device_sr != self._target_sr:
            audio = resample_audio(audio, self._device_sr, self._target_sr)

        try:
            self._queue.put_nowait(audio)
        except queue.Full:
            # 缓冲区满时丢弃最旧的数据
            try:
                self._queue.get_nowait()
            except queue.Empty:
                pass
            try:
                self._queue.put_nowait(audio)
            except queue.Full:
                pass

        return (None, pyaudio.paContinue)
