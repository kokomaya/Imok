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
from backend.audio.base import AudioChunk, AudioSource, AudioSourceType
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
    source_name: str = ""  # 音频源名称（如 "wasapi"、"mic"）
    timestamp: float = field(default_factory=time.time)  # 事件产生的墙钟时间


# 回调类型
TranscriptionCallback = Callable[[TranscriptionEvent], None]


# ASR 段落队列最大深度 — 防止内存无限增长；超出时丢弃最旧段
_MAX_ASR_QUEUE = 20

# 队列丢弃时的日志间隔（秒），避免日志风暴
_DROP_LOG_INTERVAL = 5.0


class MeetingPipeline:
    """会议核心流水线 — 多音频源独立 VAD → 共享 ASR。

    架构：
        source_1 audio_loop → VAD_1 → ┐
        source_2 audio_loop → VAD_2 → ├→ shared ASR Queue → asr_consumer → callbacks
        source_N audio_loop → VAD_N → ┘

    每个音频源拥有独立的 VAD 实例和音频读取循环，
    所有检测到的语音段带上 source_name 标签后汇入共享 ASR 队列。
    ASR 引擎（GPU 资源）全局唯一，按序处理。

    优势：
    - 不同音量的音频源互不干扰（不再混合后被淹没）
    - 每个源的 VAD 灵敏度独立适配
    - source_name 传递到 TranscriptionEvent 供下游区分角色

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
        *,
        extra_sources: Optional[List[tuple]] = None,
    ) -> None:
        """
        Args:
            audio_source: 主音频源（向后兼容）。
            vad: 主音频源的 VAD 实例。
            asr: 共享 ASR 引擎。
            extra_sources: 额外音频源列表 [(name, AudioSource, VoiceActivityDetector), ...]
        """
        self._asr = asr
        self._speaker_embedder = speaker_embedder
        self._speaker_tracker = speaker_tracker

        # 多源管理：[(name, source, vad)]
        self._sources: List[tuple] = []
        # 主源（向后兼容 — 推断名称）
        main_name = self._infer_source_name(audio_source)
        self._sources.append((main_name, audio_source, vad))

        if extra_sources:
            for name, src, src_vad in extra_sources:
                self._sources.append((name, src, src_vad))

        self._callbacks: List[TranscriptionCallback] = []
        self._level_callbacks: List[Callable] = []
        self._state = PipelineState.IDLE
        self._stop_event = asyncio.Event()
        self._audio_tasks: List[asyncio.Task] = []
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

        # 每个源的实时 RMS 电平
        self._levels: dict = {}

    @staticmethod
    def _infer_source_name(source: AudioSource) -> str:
        """从 AudioSource 类型推断源名称。"""
        src_type = source.get_source_type()
        if src_type == AudioSourceType.WASAPI_LOOPBACK:
            return "wasapi"
        elif src_type == AudioSourceType.MICROPHONE:
            return "mic"
        return "audio"

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

        # 启动所有音频源
        for name, source, vad in self._sources:
            try:
                source.start()
                logger.info("Audio source '%s' started.", name)
            except Exception:
                self._state = PipelineState.ERROR
                logger.exception("Failed to start audio source '%s'.", name)
                # 停止已启动的源
                for n2, s2, _ in self._sources:
                    if n2 == name:
                        break
                    try:
                        s2.stop()
                    except Exception:
                        pass
                raise

        self._state = PipelineState.RUNNING

        # 为每个源创建独立的音频读取循环
        self._audio_tasks = []
        for name, source, vad in self._sources:
            task = asyncio.create_task(
                self._audio_loop(name, source, vad),
                name=f"audio-{name}",
            )
            self._audio_tasks.append(task)

        self._asr_task = asyncio.create_task(self._asr_consumer())
        logger.info(
            "MeetingPipeline started with %d sources: %s",
            len(self._sources),
            [n for n, _, _ in self._sources],
        )

    async def stop(self) -> None:
        """停止流水线 — 停止采集，刷出残余语音段，等待 ASR 完成。

        幂等：已停止时不重复操作。
        """
        if self._state not in (PipelineState.RUNNING, PipelineState.ERROR):
            return

        self._state = PipelineState.STOPPING
        self._stop_event.set()

        # 等待所有音频循环退出
        for task in self._audio_tasks:
            if task and not task.done():
                try:
                    await asyncio.wait_for(task, timeout=5.0)
                except asyncio.TimeoutError:
                    logger.warning("Audio task %s did not stop in time, cancelling.", task.get_name())
                    task.cancel()

        # 停止所有音频源
        for name, source, _ in self._sources:
            try:
                source.stop()
                logger.info("Audio source '%s' stopped.", name)
            except Exception:
                logger.exception("Error stopping audio source '%s'.", name)

        # 刷出所有 VAD 残余语音段
        await self._flush_vad()

        # 发送 sentinel 通知 ASR 消费者退出，然后等待
        await self._segment_queue.put(None)
        if self._asr_task and not self._asr_task.done():
            try:
                await asyncio.wait_for(self._asr_task, timeout=30.0)
            except asyncio.TimeoutError:
                logger.warning("ASR consumer did not stop in time, cancelling.")
                self._asr_task.cancel()

        self._audio_tasks.clear()
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

    async def _audio_loop(self, source_name: str, source: AudioSource, vad: VoiceActivityDetector) -> None:
        """后台音频读取循环 — 读取单个音频源 → VAD → 将段落送入共享队列。

        每个音频源各有一个此循环的实例，互不干扰。
        """
        loop = asyncio.get_running_loop()
        last_level_ts = 0.0

        while not self._stop_event.is_set():
            try:
                # 在线程中执行阻塞的 read_chunk
                chunk = await loop.run_in_executor(
                    None, source.read_chunk
                )

                if chunk is None:
                    await asyncio.sleep(0.01)
                    continue

                # 定期发送音频电平 (~5Hz)
                now = time.monotonic()
                if self._level_callbacks and now - last_level_ts > 0.2:
                    last_level_ts = now
                    rms = float(np.sqrt(np.mean(chunk.data ** 2)))
                    self._levels[source_name] = rms
                    # 只由第一个源触发 level callback 广播
                    if source_name == self._sources[0][0]:
                        # 包含 mixer 兼容的 get_levels（如果主源有的话）
                        if hasattr(source, 'get_levels'):
                            levels = source.get_levels()
                        else:
                            levels = dict(self._levels)
                        for cb in self._level_callbacks:
                            try:
                                cb(levels)
                            except Exception:
                                logger.debug("Error in level callback", exc_info=True)

                # VAD 处理
                segments = await loop.run_in_executor(
                    None, vad.feed, chunk.data
                )

                # 将语音段送入队列（标记 source_name），队列满则丢弃最旧段
                for segment in segments:
                    segment.source_name = source_name
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
                logger.exception("Error in audio loop '%s', retrying...", source_name)
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
        """停止时刷出所有 VAD 实例中残余的语音段。"""
        loop = asyncio.get_running_loop()
        for name, _, vad in self._sources:
            try:
                flushed = await loop.run_in_executor(None, vad.flush)
                if flushed is not None:
                    flushed.source_name = name
                    await self._process_segment(flushed)
            except Exception:
                logger.exception("Error flushing VAD for source '%s'.", name)

    async def _process_segment(self, segment: AudioSegment) -> None:
        """对单个语音段执行 ASR 并通知回调。"""
        loop = asyncio.get_running_loop()
        t_start = time.monotonic()
        queue_depth = self._segment_queue.qsize()
        source_name = getattr(segment, 'source_name', '') or ''

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
                    "ASR empty [%s, %.2fs audio, %.2fs proc, queue=%d] seg %.2f-%.2f",
                    source_name, segment.duration_s, t_asr, queue_depth,
                    segment.start_time, segment.end_time,
                )
                return

            # 过滤 Whisper 幻觉输出
            if _is_hallucination(result):
                logger.info(
                    "Filtered hallucination [%s, %.2fs proc, queue=%d]: '%s'",
                    source_name, t_asr, queue_depth, result.text.strip()[:60],
                )
                return

            logger.info(
                "ASR OK [%s, %.2fs audio → %.2fs proc, RTF=%.2f, queue=%d, lang=%s]: '%s'",
                source_name, segment.duration_s, t_asr,
                t_asr / max(segment.duration_s, 0.01),
                queue_depth, result.language,
                result.text.strip()[:80],
            )

            # 说话人识别 — 麦克风源自动标记为 "我(本人)"，系统音频用嵌入识别
            speaker = ""
            if source_name == "mic":
                speaker = "我(本人)"
            elif self._speaker_embedder is not None and self._speaker_tracker is not None:
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
                source_name=source_name,
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
