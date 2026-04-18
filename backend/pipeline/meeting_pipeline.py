"""会议流水线 — 编排 AudioSource → VAD → ASR 核心链路。

单一职责：组合各功能模块，管理异步音频读取循环和 VAD→ASR 调度。
不负责翻译、总结或 WebSocket 推送（后续 Phase 中扩展）。

设计决策：
- 音频采集在后台线程运行（AudioSource.read_chunk 是阻塞的）
- VAD 在同一后台线程中处理（避免跨线程传递大量音频数据）
- ASR 在独立消费者任务中执行（通过 Queue 解耦，不阻塞音频采集）
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

import numpy as np

from backend.asr.base import ASREngine, TranscriptionResult
from backend.asr.vad import AudioSegment, VoiceActivityDetector
from backend.audio.base import AudioChunk, AudioSource
from backend.speaker.base import SpeakerEmbedderBase, SpeakerTrackerBase

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Whisper 幻觉短语过滤
# Whisper 在处理低信噪比或接近静音的音频段时，容易生成这些训练集中高频出现的短语。
# ---------------------------------------------------------------------------
_HALLUCINATION_PHRASES: frozenset[str] = frozenset(
    p.lower()
    for p in [
        "Thanks for watching!",
        "Thank you for watching!",
        "Thank you for watching.",
        "Thanks for watching.",
        "Subscribe to my channel",
        "Please subscribe",
        "Like and subscribe",
        "See you next time",
        "See you in the next video",
        "Bye bye",
        "Bye bye!",
        "Bye-bye",
        "Thank you.",
        "Thank you!",
        "Thanks.",
        "You",
        "you",
        "...",
        "MBC 뉴스 이덕영입니다.",
        "ご視聴ありがとうございました",
        "字幕by索兰娅",
        "字幕由Amara.org社区提供",
        "请不吝点赞 订阅 转发 打赏支持明镜与点点栏目",
    ]
)

# 高 no_speech_prob 表示 Whisper 自己也认为这段没有语音
# 注意：系统音频（loopback）经常有较高的 no_speech_prob，阈值不宜太低
_NO_SPEECH_PROB_THRESHOLD = 0.8

# 低 avg_logprob 表示模型对自己的输出不自信
_LOW_CONFIDENCE_LOGPROB = -1.5


def _is_hallucination(result: TranscriptionResult) -> bool:
    """检测 Whisper 幻觉输出。"""
    text = result.text.strip().lower()

    # 精确匹配已知幻觉短语
    if text in _HALLUCINATION_PHRASES:
        return True

    # 检查每个 segment 的 no_speech_prob 和 avg_logprob
    if result.segments:
        avg_no_speech = sum(s.no_speech_prob for s in result.segments) / len(result.segments)
        avg_logprob = sum(s.avg_logprob for s in result.segments) / len(result.segments)

        # 高 no_speech_prob + 已知幻觉短语模式
        if avg_no_speech > _NO_SPEECH_PROB_THRESHOLD:
            logger.debug(
                "Filtered likely hallucination (no_speech=%.2f): '%s'",
                avg_no_speech, text[:60],
            )
            return True

        # 极低置信度 + 非常短的文本 → 大概率是幻觉
        if avg_logprob < _LOW_CONFIDENCE_LOGPROB and len(text) < 15:
            logger.debug(
                "Filtered low-confidence short text (logprob=%.2f): '%s'",
                avg_logprob, text[:60],
            )
            return True

    return False


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


# ASR 段落队列最大深度 — 防止内存无限增长；超出时丢弃最旧段
_MAX_ASR_QUEUE = 20

# 队列丢弃时的日志间隔（秒），避免日志风暴
_DROP_LOG_INTERVAL = 5.0


class MeetingPipeline:
    """会议核心流水线 — 音频采集 → VAD → ASR。

    架构：
        audio_loop (Task 1)  → read_chunk → VAD → Queue
        asr_consumer (Task 2) ← Queue → ASR → callbacks

    音频采集和 ASR 完全解耦：即使 ASR 处理缓慢，音频也能持续采集，
    不会因 ASR 阻塞而丢失操作系统音频缓冲区的数据。

    依赖倒置（DIP）：仅依赖 AudioSource / VoiceActivityDetector / ASREngine 抽象。
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
        self._level_callbacks: List[Callable] = []
        self._state = PipelineState.IDLE
        self._stop_event = asyncio.Event()
        self._audio_task: Optional[asyncio.Task] = None
        self._asr_task: Optional[asyncio.Task] = None
        self._segment_queue: asyncio.Queue[Optional[AudioSegment]] = asyncio.Queue(
            maxsize=_MAX_ASR_QUEUE
        )
        self._asr_executor = ThreadPoolExecutor(
            max_workers=max_asr_workers,
            thread_name_prefix="asr-worker",
        )

        # 统计信息
        self._segments_processed = 0
        self._total_audio_duration = 0.0
        self._segments_dropped = 0
        self._last_level_ts = 0.0

    def on_transcription(self, callback: TranscriptionCallback) -> None:
        """注册转写结果回调。可注册多个，按添加顺序调用。"""
        self._callbacks.append(callback)

    def on_audio_level(self, callback: Callable) -> None:
        """注册音频电平回调。callback(levels: dict[str, float])"""
        self._level_callbacks.append(callback)

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
        self._segments_dropped = 0

        # 重建队列（防止残留 sentinel）
        self._segment_queue = asyncio.Queue(maxsize=_MAX_ASR_QUEUE)

        # 重置 ASR 语言缓存（新会议可能切换语言）
        if hasattr(self._asr, "reset_language_cache"):
            self._asr.reset_language_cache()

        try:
            self._audio_source.start()
        except Exception:
            self._state = PipelineState.ERROR
            logger.exception("Failed to start audio source.")
            raise

        self._state = PipelineState.RUNNING
        self._audio_task = asyncio.create_task(self._audio_loop())
        self._asr_task = asyncio.create_task(self._asr_consumer())
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

        # 发送 sentinel 通知 ASR 消费者退出，然后等待
        await self._segment_queue.put(None)
        if self._asr_task and not self._asr_task.done():
            try:
                await asyncio.wait_for(self._asr_task, timeout=30.0)
            except asyncio.TimeoutError:
                logger.warning("ASR consumer did not stop in time, cancelling.")
                self._asr_task.cancel()

        self._state = PipelineState.IDLE
        logger.info(
            "MeetingPipeline stopped. Processed %d segments (%.1fs audio), dropped %d.",
            self._segments_processed,
            self._total_audio_duration,
            self._segments_dropped,
        )

    @property
    def state(self) -> PipelineState:
        return self._state

    @property
    def segments_processed(self) -> int:
        return self._segments_processed

    async def _audio_loop(self) -> None:
        """后台音频读取循环 — 读取音频 → VAD → 将段落送入队列。

        此循环绝不等待 ASR，保证音频持续采集不中断。
        """
        loop = asyncio.get_running_loop()

        while not self._stop_event.is_set():
            try:
                # 在线程中执行阻塞的 read_chunk
                chunk = await loop.run_in_executor(
                    None, self._audio_source.read_chunk
                )

                if chunk is None:
                    await asyncio.sleep(0.01)
                    continue

                # 定期发送音频电平 (~5Hz)
                now = time.monotonic()
                if self._level_callbacks and now - self._last_level_ts > 0.2:
                    self._last_level_ts = now
                    if hasattr(self._audio_source, 'get_levels'):
                        levels = self._audio_source.get_levels()
                    else:
                        rms = float(np.sqrt(np.mean(chunk.data ** 2)))
                        levels = {'main': rms}
                    for cb in self._level_callbacks:
                        try:
                            cb(levels)
                        except Exception:
                            logger.debug("Error in level callback", exc_info=True)

                # VAD 处理
                segments = await loop.run_in_executor(
                    None, self._vad.feed, chunk.data
                )

                # 将语音段送入队列（非阻塞），队列满则丢弃最旧段
                for segment in segments:
                    if self._segment_queue.full():
                        try:
                            self._segment_queue.get_nowait()
                            self._segments_dropped += 1
                            logger.warning(
                                "ASR queue full, dropped oldest segment (total dropped: %d)",
                                self._segments_dropped,
                            )
                        except asyncio.QueueEmpty:
                            pass
                    await self._segment_queue.put(segment)

            except asyncio.CancelledError:
                break
            except Exception:
                if self._stop_event.is_set():
                    break
                logger.exception("Error in audio loop, retrying...")
                await asyncio.sleep(0.5)

    async def _asr_consumer(self) -> None:
        """ASR 消费者任务 — 从队列取段落，执行 ASR + 说话人识别。

        独立于音频采集循环运行，保证音频不因 ASR 延迟而丢失。
        """
        while True:
            try:
                segment = await self._segment_queue.get()

                # None 是 sentinel，表示停止
                if segment is None:
                    break

                await self._process_segment(segment)
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Error in ASR consumer.")

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
        t_start = time.monotonic()
        queue_depth = self._segment_queue.qsize()

        try:
            # ASR 在线程池中执行（CPU 密集型）
            result = await loop.run_in_executor(
                self._asr_executor,
                self._asr.transcribe,
                segment.audio_data,
            )

            t_asr = time.monotonic() - t_start
            self._segments_processed += 1
            self._total_audio_duration += segment.duration_s

            if result.is_empty:
                logger.debug(
                    "ASR empty [%.2fs audio, %.2fs proc, queue=%d] seg %.2f-%.2f",
                    segment.duration_s, t_asr, queue_depth,
                    segment.start_time, segment.end_time,
                )
                return

            # 过滤 Whisper 幻觉输出
            if _is_hallucination(result):
                logger.info(
                    "Filtered hallucination [%.2fs proc, queue=%d]: '%s'",
                    t_asr, queue_depth, result.text.strip()[:60],
                )
                return

            logger.info(
                "ASR OK [%.2fs audio → %.2fs proc, RTF=%.2f, queue=%d, lang=%s]: '%s'",
                segment.duration_s, t_asr,
                t_asr / max(segment.duration_s, 0.01),
                queue_depth, result.language,
                result.text.strip()[:80],
            )

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
