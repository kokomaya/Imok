"""MeetingPipeline 核心流水线单元测试。

测试策略：
- 使用 mock AudioSource / VoiceActivityDetector / ASREngine
- 验证流水线编排逻辑：启动/停止、回调通知、错误处理
- 不依赖真实硬件或模型

标记 @pytest.mark.hardware 的测试使用真实设备。
"""

from __future__ import annotations

import asyncio
from typing import List, Optional
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from backend.asr.base import ASREngine, TranscriptionResult, TranscriptionSegment
from backend.asr.vad import AudioSegment
from backend.audio.base import AudioChunk, AudioSource, AudioSourceType
from backend.pipeline.meeting_pipeline import (
    MeetingPipeline,
    PipelineState,
    TranscriptionEvent,
)


# ============================================================================
# Mock 实现
# ============================================================================


class FakeAudioSource(AudioSource):
    """假音频源 — 返回预设的 AudioChunk 序列，用完后返回 None。"""

    def __init__(self, chunks: List[np.ndarray], sample_rate: int = 16000) -> None:
        self._chunks = list(chunks)
        self._sample_rate = sample_rate
        self._active = False
        self._index = 0
        self._timestamp = 0.0

    def start(self) -> None:
        self._active = True
        self._index = 0
        self._timestamp = 0.0

    def stop(self) -> None:
        self._active = False

    def read_chunk(self) -> Optional[AudioChunk]:
        if self._index < len(self._chunks):
            data = self._chunks[self._index]
            chunk = AudioChunk(
                data=data,
                sample_rate=self._sample_rate,
                timestamp_s=self._timestamp,
                source_type=AudioSourceType.MICROPHONE,
            )
            self._timestamp += len(data) / self._sample_rate
            self._index += 1
            return chunk
        return None

    def get_sample_rate(self) -> int:
        return self._sample_rate

    def get_source_type(self) -> AudioSourceType:
        return AudioSourceType.MICROPHONE

    @property
    def is_active(self) -> bool:
        return self._active


class FakeVAD:
    """假 VAD — 每次 feed 直接将音频打包成一个 AudioSegment 返回。"""

    def __init__(self, return_segments: bool = True) -> None:
        self._return_segments = return_segments
        self._feed_count = 0
        self._time_offset = 0.0
        self.sample_rate = 16000

    def feed(self, audio_chunk: np.ndarray) -> List[AudioSegment]:
        self._feed_count += 1
        if not self._return_segments:
            return []
        duration = len(audio_chunk) / self.sample_rate
        seg = AudioSegment(
            audio_data=audio_chunk,
            sample_rate=self.sample_rate,
            start_time=self._time_offset,
            end_time=self._time_offset + duration,
        )
        self._time_offset += duration
        return [seg]

    def flush(self) -> Optional[AudioSegment]:
        return None

    def reset(self) -> None:
        self._feed_count = 0
        self._time_offset = 0.0


class FakeASR(ASREngine):
    """假 ASR 引擎 — 返回预设文本。"""

    def __init__(self, text: str = "hello world", language: str = "en") -> None:
        self._text = text
        self._language = language
        self.transcribe_count = 0

    def transcribe(
        self, audio: np.ndarray, language: Optional[str] = None
    ) -> TranscriptionResult:
        self.transcribe_count += 1
        duration = len(audio) / 16000
        return TranscriptionResult(
            text=self._text,
            language=language or self._language,
            language_probability=0.95,
            segments=[
                TranscriptionSegment(text=self._text, start=0.0, end=duration)
            ],
            duration_s=duration,
        )

    def get_supported_languages(self) -> List[str]:
        return ["en", "zh"]

    @property
    def sample_rate(self) -> int:
        return 16000

    @property
    def is_loaded(self) -> bool:
        return True


# ============================================================================
# Pipeline 测试
# ============================================================================


class TestPipelineLifecycle:
    """流水线生命周期测试。"""

    @pytest.mark.asyncio
    async def test_start_and_stop(self) -> None:
        source = FakeAudioSource([np.zeros(512, dtype=np.float32)])
        vad = FakeVAD(return_segments=False)
        asr = FakeASR()

        pipeline = MeetingPipeline(source, vad, asr)
        assert pipeline.state == PipelineState.IDLE

        await pipeline.start()
        assert pipeline.state == PipelineState.RUNNING

        # Let the audio loop run briefly
        await asyncio.sleep(0.1)

        await pipeline.stop()
        assert pipeline.state == PipelineState.IDLE

    @pytest.mark.asyncio
    async def test_start_idempotent(self) -> None:
        source = FakeAudioSource([])
        vad = FakeVAD()
        asr = FakeASR()

        pipeline = MeetingPipeline(source, vad, asr)
        await pipeline.start()
        await pipeline.start()  # should not raise
        assert pipeline.state == PipelineState.RUNNING
        await pipeline.stop()

    @pytest.mark.asyncio
    async def test_stop_idempotent(self) -> None:
        source = FakeAudioSource([])
        vad = FakeVAD()
        asr = FakeASR()

        pipeline = MeetingPipeline(source, vad, asr)
        await pipeline.stop()  # not started, should not raise
        assert pipeline.state == PipelineState.IDLE

    @pytest.mark.asyncio
    async def test_audio_source_start_failure(self) -> None:
        source = FakeAudioSource([])
        source.start = MagicMock(side_effect=RuntimeError("No audio device"))
        vad = FakeVAD()
        asr = FakeASR()

        pipeline = MeetingPipeline(source, vad, asr)
        with pytest.raises(RuntimeError, match="No audio device"):
            await pipeline.start()

        assert pipeline.state == PipelineState.ERROR


class TestPipelineTranscription:
    """流水线转写功能测试。"""

    @pytest.mark.asyncio
    async def test_callback_receives_transcription(self) -> None:
        """音频 → VAD → ASR → 回调应收到结果。"""
        chunks = [np.random.randn(1024).astype(np.float32) for _ in range(3)]
        source = FakeAudioSource(chunks)
        vad = FakeVAD(return_segments=True)
        asr = FakeASR(text="你好世界", language="zh")

        events: List[TranscriptionEvent] = []
        pipeline = MeetingPipeline(source, vad, asr)
        pipeline.on_transcription(events.append)

        await pipeline.start()
        # Wait for all chunks to be processed
        await asyncio.sleep(0.5)
        await pipeline.stop()

        assert len(events) == 3
        for e in events:
            assert e.result.text == "你好世界"
            assert e.result.language == "zh"

    @pytest.mark.asyncio
    async def test_empty_transcription_not_reported(self) -> None:
        """ASR 返回空文本时不应触发回调。"""
        chunks = [np.zeros(512, dtype=np.float32)]
        source = FakeAudioSource(chunks)
        vad = FakeVAD(return_segments=True)
        asr = FakeASR(text="")  # empty

        events: List[TranscriptionEvent] = []
        pipeline = MeetingPipeline(source, vad, asr)
        pipeline.on_transcription(events.append)

        await pipeline.start()
        await asyncio.sleep(0.3)
        await pipeline.stop()

        assert len(events) == 0

    @pytest.mark.asyncio
    async def test_no_segments_no_asr(self) -> None:
        """VAD 不输出语音段时，ASR 不应被调用。"""
        chunks = [np.zeros(512, dtype=np.float32) for _ in range(3)]
        source = FakeAudioSource(chunks)
        vad = FakeVAD(return_segments=False)
        asr = FakeASR()

        pipeline = MeetingPipeline(source, vad, asr)
        await pipeline.start()
        await asyncio.sleep(0.3)
        await pipeline.stop()

        assert asr.transcribe_count == 0

    @pytest.mark.asyncio
    async def test_multiple_callbacks(self) -> None:
        """多个回调都应被调用。"""
        chunks = [np.random.randn(512).astype(np.float32)]
        source = FakeAudioSource(chunks)
        vad = FakeVAD(return_segments=True)
        asr = FakeASR(text="test")

        events_a: List[TranscriptionEvent] = []
        events_b: List[TranscriptionEvent] = []

        pipeline = MeetingPipeline(source, vad, asr)
        pipeline.on_transcription(events_a.append)
        pipeline.on_transcription(events_b.append)

        await pipeline.start()
        await asyncio.sleep(0.3)
        await pipeline.stop()

        assert len(events_a) == 1
        assert len(events_b) == 1

    @pytest.mark.asyncio
    async def test_segments_processed_counter(self) -> None:
        """segments_processed 计数器应正确递增。"""
        chunks = [np.random.randn(512).astype(np.float32) for _ in range(5)]
        source = FakeAudioSource(chunks)
        vad = FakeVAD(return_segments=True)
        asr = FakeASR(text="test")

        pipeline = MeetingPipeline(source, vad, asr)
        await pipeline.start()
        await asyncio.sleep(0.5)
        await pipeline.stop()

        assert pipeline.segments_processed == 5


class TestPipelineErrorHandling:
    """流水线错误处理测试。"""

    @pytest.mark.asyncio
    async def test_asr_error_does_not_crash_pipeline(self) -> None:
        """ASR 异常不应导致流水线崩溃。"""
        chunks = [np.random.randn(512).astype(np.float32) for _ in range(3)]
        source = FakeAudioSource(chunks)
        vad = FakeVAD(return_segments=True)
        asr = FakeASR(text="ok")
        # Make ASR raise on first call only
        original_transcribe = asr.transcribe
        call_count = [0]

        def flaky_transcribe(audio, language=None):
            call_count[0] += 1
            if call_count[0] == 1:
                raise RuntimeError("OOM")
            return original_transcribe(audio, language)

        asr.transcribe = flaky_transcribe

        events: List[TranscriptionEvent] = []
        pipeline = MeetingPipeline(source, vad, asr)
        pipeline.on_transcription(events.append)

        await pipeline.start()
        await asyncio.sleep(0.5)
        await pipeline.stop()

        # Should have processed 2 of 3 (first one failed)
        assert len(events) == 2
        assert pipeline.state == PipelineState.IDLE

    @pytest.mark.asyncio
    async def test_callback_error_does_not_crash_pipeline(self) -> None:
        """回调异常不应导致流水线崩溃。"""
        chunks = [np.random.randn(512).astype(np.float32) for _ in range(2)]
        source = FakeAudioSource(chunks)
        vad = FakeVAD(return_segments=True)
        asr = FakeASR(text="test")

        good_events: List[TranscriptionEvent] = []

        def bad_callback(event):
            raise ValueError("callback error")

        pipeline = MeetingPipeline(source, vad, asr)
        pipeline.on_transcription(bad_callback)  # bad one first
        pipeline.on_transcription(good_events.append)  # good one second

        await pipeline.start()
        await asyncio.sleep(0.5)
        await pipeline.stop()

        # Good callback should still receive events
        assert len(good_events) == 2


class TestTranscriptionEvent:
    """TranscriptionEvent 数据类测试。"""

    def test_event_fields(self) -> None:
        result = TranscriptionResult(text="hello", language="en")
        event = TranscriptionEvent(
            result=result,
            segment_start_time=1.0,
            segment_end_time=2.5,
        )
        assert event.result.text == "hello"
        assert event.segment_start_time == 1.0
        assert event.segment_end_time == 2.5
        assert event.timestamp > 0


class TestPipelineState:
    """PipelineState 枚举测试。"""

    def test_states(self) -> None:
        assert PipelineState.IDLE == "idle"
        assert PipelineState.RUNNING == "running"
        assert PipelineState.STOPPING == "stopping"
        assert PipelineState.ERROR == "error"
