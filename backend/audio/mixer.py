"""音频混合器 — 将多个 AudioSource 合并为一个统一的音频流。

单一职责：从多个音频源读取数据块，混合后输出单个 AudioChunk。
遵循 LSP：AudioMixer 本身实现 AudioSource 接口，Pipeline 无需修改。
遵循 OCP：通过 add_source/remove_source 动态增减音频源。
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from typing import Dict, List, Optional

import numpy as np

from backend.audio.base import AudioChunk, AudioSource, AudioSourceType

logger = logging.getLogger(__name__)


class AudioMixer(AudioSource):
    """多音频源混合器 — 将多个 AudioSource 混合为一个流。

    将 N 个音频源的数据对齐并叠加，输出 mono float32。
    每个源在独立线程中读取，混合在 read_chunk() 调用时完成。

    使用方式：
        mixer = AudioMixer(target_sample_rate=16000, chunk_duration_s=0.032)
        mixer.add_source('system', wasapi_source)
        mixer.add_source('mic', mic_source)
        mixer.start()
        chunk = mixer.read_chunk()  # 混合后的数据
    """

    def __init__(
        self,
        target_sample_rate: int = 16000,
        chunk_duration_s: float = 0.032,
        buffer_max_seconds: float = 10.0,
    ) -> None:
        self._target_sr = target_sample_rate
        self._chunk_frames = int(target_sample_rate * chunk_duration_s)
        self._buffer_max = int(buffer_max_seconds / chunk_duration_s)

        self._sources: Dict[str, AudioSource] = {}
        self._queues: Dict[str, queue.Queue] = {}
        self._threads: Dict[str, threading.Thread] = {}
        self._active = False
        self._stop_event = threading.Event()
        self._frames_read = 0
        self._start_time = 0.0
        self._lock = threading.Lock()

        # 每个源的最新 RMS 电平 (0.0~1.0)
        self._levels: Dict[str, float] = {}

    def add_source(self, name: str, source: AudioSource) -> None:
        """添加一个音频源。必须在 start() 之前调用。"""
        if self._active:
            raise RuntimeError("Cannot add source while mixer is active")
        self._sources[name] = source
        self._queues[name] = queue.Queue(maxsize=self._buffer_max)

    def remove_source(self, name: str) -> Optional[AudioSource]:
        """移除一个音频源。必须在 start() 之前调用。"""
        if self._active:
            raise RuntimeError("Cannot remove source while mixer is active")
        source = self._sources.pop(name, None)
        self._queues.pop(name, None)
        return source

    @property
    def source_names(self) -> List[str]:
        return list(self._sources.keys())

    def start(self) -> None:
        if self._active:
            return
        if not self._sources:
            raise RuntimeError("No audio sources added to mixer")

        self._stop_event.clear()
        self._start_time = time.monotonic()
        self._frames_read = 0

        # 启动所有源
        for name, source in self._sources.items():
            source.start()
            t = threading.Thread(
                target=self._reader_loop,
                args=(name, source),
                name=f"mixer-{name}",
                daemon=True,
            )
            self._threads[name] = t
            t.start()

        self._active = True
        logger.info(
            "AudioMixer started with %d sources: %s",
            len(self._sources),
            list(self._sources.keys()),
        )

    def stop(self) -> None:
        if not self._active:
            return
        self._stop_event.set()
        self._active = False

        # 停止所有源
        for name, source in self._sources.items():
            try:
                source.stop()
            except Exception:
                logger.exception("Error stopping source %s", name)

        # 等待读取线程结束
        for name, t in self._threads.items():
            t.join(timeout=2.0)
            if t.is_alive():
                logger.warning("Reader thread %s did not stop in time", name)

        self._threads.clear()
        # 清空队列
        for q in self._queues.values():
            while not q.empty():
                try:
                    q.get_nowait()
                except queue.Empty:
                    break

        logger.info("AudioMixer stopped.")

    def read_chunk(self) -> Optional[AudioChunk]:
        """读取一个混合后的音频块。

        从所有活跃源的队列中取数据，叠加混合。
        如果某个源暂时无数据，用静音填充其部分。
        """
        if not self._active:
            return None

        mixed = np.zeros(self._chunk_frames, dtype=np.float32)
        has_data = False

        for name, q in self._queues.items():
            try:
                chunk_data = q.get(timeout=0.05)
                # 对齐长度
                if len(chunk_data) >= self._chunk_frames:
                    mixed += chunk_data[: self._chunk_frames]
                else:
                    mixed[: len(chunk_data)] += chunk_data
                has_data = True
            except queue.Empty:
                continue

        if not has_data:
            return None

        # 软限幅防止叠加后削波
        peak = np.abs(mixed).max()
        if peak > 1.0:
            mixed /= peak

        timestamp = self._frames_read / self._target_sr
        self._frames_read += self._chunk_frames

        return AudioChunk(
            data=mixed,
            sample_rate=self._target_sr,
            timestamp_s=timestamp,
            source_type=AudioSourceType.WASAPI_LOOPBACK,  # 混合源标记为主源类型
        )

    def get_sample_rate(self) -> int:
        return self._target_sr

    def get_source_type(self) -> AudioSourceType:
        return AudioSourceType.WASAPI_LOOPBACK

    @property
    def is_active(self) -> bool:
        return self._active

    def _reader_loop(self, name: str, source: AudioSource) -> None:
        """在独立线程中持续从单个源读取数据。"""
        logger.debug("Mixer reader started for source: %s", name)
        while not self._stop_event.is_set():
            try:
                chunk = source.read_chunk()
                if chunk is None:
                    continue
                # 计算该源的 RMS 电平
                rms = float(np.sqrt(np.mean(chunk.data ** 2)))
                self._levels[name] = rms
                try:
                    self._queues[name].put_nowait(chunk.data)
                except queue.Full:
                    # 丢弃最老的数据
                    try:
                        self._queues[name].get_nowait()
                    except queue.Empty:
                        pass
                    self._queues[name].put_nowait(chunk.data)
            except Exception:
                if not self._stop_event.is_set():
                    logger.exception("Error reading from source %s", name)
                break
        logger.debug("Mixer reader stopped for source: %s", name)

    def get_levels(self) -> Dict[str, float]:
        """返回每个源的最新 RMS 电平 (0.0~1.0)。"""
        return dict(self._levels)
