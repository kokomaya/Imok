"""会议流水线 — 编排 AudioSource → VAD → ASR 核心链路。

单一职责：组合各功能模块，管理异步音频读取循环和 VAD→ASR 调度。
不负责翻译、总结或 WebSocket 推送（后续 Phase 中扩展）。

设计决策：
- 音频采集在后台线程运行（AudioSource.read_chunk 是阻塞的）
- VAD 在同一后台线程中处理（避免跨线程传递大量音频数据）
- ASR 在线程池中执行（CPU 密集型，避免阻塞事件循环）
- 通过回调机制通知下游消费者（解耦 Pipeline 与 WebSocket/UI）
"""

from __future__ import annotations

import asyncio
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, List, Optional

from backend.asr.base import ASREngine, TranscriptionResult
from backend.asr.vad import AudioSegment, VoiceActivityDetector
from backend.audio.base import AudioChunk, AudioSource
from backend.speaker.base import SpeakerEmbedderBase, SpeakerTrackerBase

logger = logging.getLogger(__name__)


class PipelineState(str, Enum):
    """流水线状态。"""

    IDLE = "idle"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    ERROR = "error"


@dataclass
class TranscriptionEvent:
    """转写事件 — 包含转写结果及其来源信息。

    用于回调通知，下游可根据 segment 信息获取时间戳等元数据。
    """

    result: TranscriptionResult
    segment_start_time: float  # VAD 检测到的语音段起始时间 (秒)
    segment_end_time: float
    speaker: str = ""  # 说话人 ID（如 "Speaker_1"），空字符串表示未识别
    timestamp: float = field(default_factory=time.time)  # 事件产生的墙钟时间


# 回调类型
TranscriptionCallback = Callable[[TranscriptionEvent], None]


class MeetingPipeline:
    """会议核心流水线 — 音频采集 → VAD → ASR。

    使用方式：
        pipeline = MeetingPipeline(audio_source, vad, asr)
        pipeline.on_transcription(lambda event: print(event.result.text))
        await pipeline.start()
        # ... 会议进行中 ...
        await pipeline.stop()

    依赖倒置（DIP）：仅依赖 AudioSource / VoiceActivityDetector / ASREngine 抽象，
    不依赖具体的 WASAPI/Mic 或 Whisper 实现。
    """

    def __init__(
        self,
        audio_source: AudioSource,
        vad: VoiceActivityDetector,
        asr: ASREngine,
        max_asr_workers: int = 1,
        speaker_embedder: Optional[SpeakerEmbedderBase] = None,
        speaker_tracker: Optional[SpeakerTrackerBase] = None,
    ) -> None:
        self._audio_source = audio_source
        self._vad = vad
        self._asr = asr
        self._speaker_embedder = speaker_embedder
        self._speaker_tracker = speaker_tracker

        self._callbacks: List[TranscriptionCallback] = []
        self._state = PipelineState.IDLE
        self._stop_event = asyncio.Event()
        self._audio_task: Optional[asyncio.Task] = None
        self._asr_executor = ThreadPoolExecutor(
            max_workers=max_asr_workers,
            thread_name_prefix="asr-worker",
        )

        # 统计信息
        self._segments_processed = 0
        self._total_audio_duration = 0.0

    def on_transcription(self, callback: TranscriptionCallback) -> None:
        """注册转写结果回调。可注册多个，按添加顺序调用。"""
        self._callbacks.append(callback)

    async def start(self) -> None:
        """启动流水线 — 开始音频采集和识别循环。

        幂等：已运行时不重复启动。
        """
        if self._state == PipelineState.RUNNING:
            logger.warning("Pipeline already running, ignoring start().")
            return

        self._state = PipelineState.STARTING
        self._stop_event.clear()
        self._segments_processed = 0
        self._total_audio_duration = 0.0

        try:
            self._audio_source.start()
        except Exception:
            self._state = PipelineState.ERROR
            logger.exception("Failed to start audio source.")
            raise

        self._state = PipelineState.RUNNING
        self._audio_task = asyncio.create_task(self._audio_loop())
        logger.info("MeetingPipeline started.")

    async def stop(self) -> None:
        """停止流水线 — 停止采集，刷出残余语音段，等待 ASR 完成。

        幂等：已停止时不重复操作。
        """
        if self._state not in (PipelineState.RUNNING, PipelineState.ERROR):
            return

        self._state = PipelineState.STOPPING
        self._stop_event.set()

        # 等待音频循环退出
        if self._audio_task and not self._audio_task.done():
            try:
                await asyncio.wait_for(self._audio_task, timeout=5.0)
            except asyncio.TimeoutError:
                logger.warning("Audio loop did not stop in time, cancelling.")
                self._audio_task.cancel()

        # 停止音频源
        try:
            self._audio_source.stop()
        except Exception:
            logger.exception("Error stopping audio source.")

        # 刷出 VAD 残余语音段
        await self._flush_vad()

        self._state = PipelineState.IDLE
        logger.info(
            "MeetingPipeline stopped. Processed %d segments, %.1fs total audio.",
            self._segments_processed,
            self._total_audio_duration,
        )

    @property
    def state(self) -> PipelineState:
        return self._state

    @property
    def segments_processed(self) -> int:
        return self._segments_processed

    async def _audio_loop(self) -> None:
        """后台音频读取循环 — 在线程中读取音频，在事件循环中处理 VAD/ASR。"""
        loop = asyncio.get_running_loop()

        while not self._stop_event.is_set():
            try:
                # 在线程中执行阻塞的 read_chunk
                chunk = await loop.run_in_executor(
                    None, self._audio_source.read_chunk
                )

                if chunk is None:
                    # 无数据，短暂等待后重试
                    await asyncio.sleep(0.01)
                    continue

                # VAD 处理（在线程中执行以避免阻塞事件循环）
                segments = await loop.run_in_executor(
                    None, self._vad.feed, chunk.data
                )

                # 对每个检测到的语音段执行 ASR
                for segment in segments:
                    await self._process_segment(segment)

            except asyncio.CancelledError:
                break
            except Exception:
                if self._stop_event.is_set():
                    break
                logger.exception("Error in audio loop, retrying...")
                await asyncio.sleep(0.5)

    async def _flush_vad(self) -> None:
        """停止时刷出 VAD 中残余的语音段。"""
        loop = asyncio.get_running_loop()
        try:
            flushed = await loop.run_in_executor(None, self._vad.flush)
            if flushed is not None:
                await self._process_segment(flushed)
        except Exception:
            logger.exception("Error flushing VAD.")

    async def _process_segment(self, segment: AudioSegment) -> None:
        """对单个语音段执行 ASR 并通知回调。"""
        loop = asyncio.get_running_loop()

        try:
            # ASR 在线程池中执行（CPU 密集型）
            result = await loop.run_in_executor(
                self._asr_executor,
                self._asr.transcribe,
                segment.audio_data,
            )

            self._segments_processed += 1
            self._total_audio_duration += segment.duration_s

            if result.is_empty:
                logger.debug(
                    "ASR returned empty for segment %.2f-%.2f s",
                    segment.start_time,
                    segment.end_time,
                )
                return

            # 说话人识别（可选）
            speaker = ""
            if self._speaker_embedder is not None and self._speaker_tracker is not None:
                try:
                    embedding = await loop.run_in_executor(
                        None,
                        self._speaker_embedder.embed,
                        segment.audio_data,
                        segment.sample_rate,
                    )
                    if embedding is not None:
                        speaker = self._speaker_tracker.identify(embedding)
                    else:
                        logger.debug(
                            "Speaker embedding returned None for segment %.2f-%.2f s (too short?)",
                            segment.start_time, segment.end_time,
                        )
                except Exception:
                    logger.warning("Speaker identification failed, continuing without.", exc_info=True)

            event = TranscriptionEvent(
                result=result,
                segment_start_time=segment.start_time,
                segment_end_time=segment.end_time,
                speaker=speaker,
            )

            # 通知所有回调
            for callback in self._callbacks:
                try:
                    callback(event)
                except Exception:
                    logger.exception("Error in transcription callback.")

        except Exception:
            logger.exception(
                "ASR failed for segment %.2f-%.2f s",
                segment.start_time,
                segment.end_time,
            )
