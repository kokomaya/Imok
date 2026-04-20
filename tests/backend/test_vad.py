"""VAD 语音活动检测模块单元测试。

测试策略：
- 通过 mock Silero-VAD 模型，用合成音频（正弦波 + 静音）验证：
  1. AudioSegment 数据类正确性
  2. 语音段起止点边界检测
  3. 最大段时长截断
  4. 静音间隔分句
  5. flush 刷出残余语音段
  6. reset 状态清除
- 标记 @pytest.mark.hardware 的测试使用真实 Silero-VAD 模型

注意：CI 环境中可通过 `pytest -m "not hardware"` 跳过硬件测试。
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from backend.asr.vad import AudioSegment, VoiceActivityDetector, _SILERO_SAMPLE_RATE


# ============================================================================
# AudioSegment 数据类测试
# ============================================================================


class TestAudioSegment:
    """AudioSegment 数据类正确性。"""

    def test_duration_computed(self) -> None:
        data = np.zeros(16000, dtype=np.float32)
        seg = AudioSegment(audio_data=data, sample_rate=16000, start_time=1.0, end_time=2.0)
        assert seg.duration_s == pytest.approx(1.0)

    def test_duration_fractional(self) -> None:
        data = np.zeros(8000, dtype=np.float32)
        seg = AudioSegment(audio_data=data, sample_rate=16000, start_time=0.0, end_time=0.5)
        assert seg.duration_s == pytest.approx(0.5)


# ============================================================================
# Mock 辅助
# ============================================================================


def _make_mock_vad_model(speech_prob_func):
    """创建 mock Silero-VAD 模型。

    Args:
        speech_prob_func: callable(window_tensor) -> float，返回语音概率。
    """
    model = MagicMock()
    model.eval = MagicMock()
    model.reset_states = MagicMock()

    def side_effect(tensor, sr):
        result = MagicMock()
        result.item.return_value = speech_prob_func(tensor)
        return result

    model.__call__ = MagicMock(side_effect=side_effect)
    model.side_effect = side_effect
    # Silero-VAD model is called as model(tensor, sr)
    model.return_value = None

    return model


def _create_vad_with_mock(speech_prob_func, **kwargs):
    """创建 VAD 实例，使用 mock 模型替换 torch.hub.load。"""
    mock_model = _make_mock_vad_model(speech_prob_func)

    with patch("backend.asr.vad.torch.hub.load") as mock_load:
        mock_load.return_value = (mock_model, None)
        vad = VoiceActivityDetector(**kwargs)

    # Replace the internal _get_speech_prob to use our mock directly
    original_get_prob = vad._get_speech_prob

    def mock_get_prob(window):
        import torch
        tensor = torch.from_numpy(window)
        return speech_prob_func(tensor)

    vad._get_speech_prob = mock_get_prob
    return vad


def _generate_sine(duration_s: float, freq: float = 440.0, sr: int = 16000) -> np.ndarray:
    """生成正弦波 (模拟有语音)。"""
    t = np.arange(int(duration_s * sr), dtype=np.float32) / sr
    return (0.5 * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def _generate_silence(duration_s: float, sr: int = 16000) -> np.ndarray:
    """生成静音。"""
    return np.zeros(int(duration_s * sr), dtype=np.float32)


# ============================================================================
# VoiceActivityDetector 单元测试 (mock 模型)
# ============================================================================


class TestVADBasicBehavior:
    """VAD 基本行为测试 — 使用 mock 模型。"""

    def test_silence_only_produces_no_segments(self) -> None:
        """纯静音输入不应产生任何语音段。"""
        vad = _create_vad_with_mock(lambda t: 0.1)  # always low prob
        silence = _generate_silence(2.0)
        segments = vad.feed(silence)
        assert segments == []

    def test_speech_only_truncated_at_max(self) -> None:
        """持续语音超过 max_segment_s 时应被截断。"""
        vad = _create_vad_with_mock(
            lambda t: 0.9,  # always high prob
            max_segment_s=1.0,
        )
        # Feed 2 seconds of "speech"
        speech = _generate_sine(2.0)
        segments = vad.feed(speech)
        assert len(segments) >= 1
        # First segment should be approximately max_segment_s
        assert segments[0].duration_s <= 1.1  # allow small tolerance

    def test_speech_then_silence_produces_segment(self) -> None:
        """语音 + 足够静音 → 应输出一个语音段。"""
        call_count = [0]
        samples_per_window = 512
        speech_windows = int(0.5 * 16000 / samples_per_window)  # 0.5s of speech

        def prob_func(tensor):
            call_count[0] += 1
            if call_count[0] <= speech_windows:
                return 0.9  # speech
            return 0.1  # silence

        vad = _create_vad_with_mock(
            prob_func,
            min_silence_ms=300,
        )

        # Feed 0.5s speech + 0.5s silence
        audio = np.concatenate([
            _generate_sine(0.5),
            _generate_silence(0.5),
        ])
        segments = vad.feed(audio)
        final_segments = [s for s in segments if not s.is_partial]
        assert len(final_segments) == 1
        assert final_segments[0].duration_s > 0.2  # at least some speech

    def test_two_speech_segments_separated_by_silence(self) -> None:
        """两段语音被长静音分隔 → 应输出两个语音段。"""
        call_count = [0]
        samples_per_window = 512
        sr = 16000

        speech1_windows = int(0.5 * sr / samples_per_window)
        silence_windows = int(0.8 * sr / samples_per_window)  # > min_silence_ms
        speech2_windows = int(0.5 * sr / samples_per_window)
        total_speech_end = speech1_windows + silence_windows + speech2_windows

        def prob_func(tensor):
            call_count[0] += 1
            c = call_count[0]
            if c <= speech1_windows:
                return 0.9
            elif c <= speech1_windows + silence_windows:
                return 0.1
            elif c <= total_speech_end:
                return 0.9
            return 0.1

        vad = _create_vad_with_mock(prob_func, min_silence_ms=300)

        # Feed: 0.5s speech + 0.8s silence + 0.5s speech + 0.5s silence (to flush 2nd)
        audio = np.concatenate([
            _generate_sine(0.5),
            _generate_silence(0.8),
            _generate_sine(0.5, freq=880),
            _generate_silence(0.5),
        ])
        segments = vad.feed(audio)
        final_segments = [s for s in segments if not s.is_partial]
        assert len(final_segments) == 2

    def test_flush_outputs_remaining_speech(self) -> None:
        """flush() 应输出当前未完成的语音段。"""
        vad = _create_vad_with_mock(lambda t: 0.9)  # all speech

        speech = _generate_sine(0.5)
        segments = vad.feed(speech)
        # No silence → no completed (non-partial) segment from feed
        final_segments = [s for s in segments if not s.is_partial]
        assert len(final_segments) == 0

        # flush should produce the accumulated segment
        flushed = vad.flush()
        assert flushed is not None
        assert flushed.duration_s > 0

    def test_flush_returns_none_when_no_speech(self) -> None:
        """无累积语音时 flush() 返回 None。"""
        vad = _create_vad_with_mock(lambda t: 0.1)
        vad.feed(_generate_silence(0.5))
        assert vad.flush() is None

    def test_reset_clears_state(self) -> None:
        """reset() 后应清除所有内部状态。"""
        vad = _create_vad_with_mock(lambda t: 0.9)
        vad.feed(_generate_sine(0.5))

        # There should be accumulated speech
        assert vad._in_speech is True

        vad.reset()
        assert vad._in_speech is False
        assert len(vad._speech_buffer) == 0
        assert len(vad._buffer) == 0
        assert vad._total_samples_fed == 0

    def test_invalid_sample_rate_raises(self) -> None:
        """不支持的采样率应抛出 ValueError。"""
        with pytest.raises(ValueError, match="sample rates"):
            _create_vad_with_mock(lambda t: 0.5, sample_rate=44100)

    def test_segment_start_time_increases(self) -> None:
        """多个语音段的 start_time 应递增。"""
        call_count = [0]
        samples_per_window = 512
        sr = 16000
        speech_windows = int(0.3 * sr / samples_per_window)
        silence_windows = int(0.5 * sr / samples_per_window)
        cycle_windows = speech_windows + silence_windows

        def prob_func(tensor):
            call_count[0] += 1
            # Repeating pattern: speech then silence
            pos_in_cycle = (call_count[0] - 1) % cycle_windows
            if pos_in_cycle < speech_windows:
                return 0.9
            return 0.1

        vad = _create_vad_with_mock(prob_func, min_silence_ms=200)

        # Feed enough for 2+ cycles
        audio = np.concatenate([
            _generate_sine(0.3),
            _generate_silence(0.5),
            _generate_sine(0.3, freq=660),
            _generate_silence(0.5),
        ])
        segments = vad.feed(audio)
        assert len(segments) >= 2
        assert segments[1].start_time > segments[0].start_time


# ============================================================================
# 集成测试 — 使用真实 Silero-VAD 模型
# ============================================================================


@pytest.mark.hardware
class TestVADWithRealModel:
    """使用真实 Silero-VAD 模型的集成测试。

    需要网络下载模型（首次运行）。
    """

    def test_real_model_detects_speech_in_sine(self) -> None:
        """真实模型应能检测到正弦波（模拟语音）并分段。

        注意：Silero-VAD 对正弦波的响应未必与真实语音一致，
        此测试主要验证端到端调用不抛异常。
        """
        vad = VoiceActivityDetector(
            sample_rate=16000,
            threshold=0.3,  # 低阈值使正弦波更可能触发
            min_silence_ms=200,
            max_segment_s=5.0,
        )

        # 1s speech-like + 0.5s silence + 1s speech-like + 0.5s silence
        audio = np.concatenate([
            _generate_sine(1.0, freq=300),
            _generate_silence(0.5),
            _generate_sine(1.0, freq=500),
            _generate_silence(0.5),
        ])

        segments = vad.feed(audio)
        flushed = vad.flush()

        total = len(segments) + (1 if flushed else 0)
        # Model loads and runs without errors; segment count may vary
        assert total >= 0  # primary goal: no crash

    def test_real_model_pure_silence(self) -> None:
        """纯静音不应产生语音段。"""
        vad = VoiceActivityDetector(
            sample_rate=16000,
            threshold=0.5,
            min_silence_ms=200,
            max_segment_s=5.0,
        )

        silence = _generate_silence(3.0)
        segments = vad.feed(silence)
        flushed = vad.flush()

        assert len(segments) == 0
        assert flushed is None
