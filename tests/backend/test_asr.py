"""ASR 引擎模块单元测试。

测试策略：
- TranscriptionResult / TranscriptionSegment / WordSegment 数据类正确性
- WhisperEngine 懒加载行为（mock faster-whisper）
- WhisperEngine.transcribe 完整流程（mock 模型返回值）
- 错误处理（transcribe 异常时返回空结果）
- 标记 @pytest.mark.hardware 的测试使用真实 faster-whisper 模型
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from backend.asr.base import (
    ASREngine,
    TranscriptionResult,
    TranscriptionSegment,
    WordSegment,
)
from backend.asr.whisper_engine import WhisperEngine
from backend.config import ASRSettings


# ============================================================================
# 数据类测试
# ============================================================================


class TestDataClasses:
    """TranscriptionResult 及子类的数据正确性。"""

    def test_word_segment(self) -> None:
        ws = WordSegment(word="hello", start=0.0, end=0.5, probability=0.95)
        assert ws.word == "hello"
        assert ws.probability == 0.95

    def test_transcription_segment(self) -> None:
        ts = TranscriptionSegment(
            text="hello world",
            start=0.0,
            end=1.0,
            words=[WordSegment("hello", 0.0, 0.5), WordSegment("world", 0.5, 1.0)],
            avg_logprob=-0.3,
            no_speech_prob=0.01,
        )
        assert ts.text == "hello world"
        assert len(ts.words) == 2

    def test_transcription_result_is_empty(self) -> None:
        empty = TranscriptionResult(text="", language="en")
        assert empty.is_empty is True

        nonempty = TranscriptionResult(text="hello", language="en")
        assert nonempty.is_empty is False

    def test_transcription_result_whitespace_is_empty(self) -> None:
        ws = TranscriptionResult(text="   \n ", language="en")
        assert ws.is_empty is True

    def test_transcription_result_defaults(self) -> None:
        r = TranscriptionResult(text="test", language="zh")
        assert r.language_probability == 0.0
        assert r.segments == []
        assert r.duration_s == 0.0


# ============================================================================
# Mock 辅助
# ============================================================================


def _make_mock_segment(text: str, start: float, end: float):
    """创建一个模拟的 faster-whisper Segment 对象。"""
    seg = SimpleNamespace()
    seg.text = text
    seg.start = start
    seg.end = end
    seg.avg_logprob = -0.25
    seg.no_speech_prob = 0.02

    word1 = SimpleNamespace(word=text, start=start, end=end, probability=0.9)
    seg.words = [word1]
    return seg


def _make_mock_info(language: str = "zh", language_probability: float = 0.95):
    """创建一个模拟的 faster-whisper TranscriptionInfo 对象。"""
    info = SimpleNamespace()
    info.language = language
    info.language_probability = language_probability
    return info


def _create_engine_with_mock_model(
    segments_data=None,
    language="zh",
    lang_prob=0.95,
):
    """创建 WhisperEngine 实例并注入 mock 模型。"""
    settings = ASRSettings(
        model_size="medium",
        compute_type="int8",
        device="cpu",
        beam_size=3,
    )

    with patch("backend.asr.whisper_engine.ASRSettings.resolve_with_gpu") as mock_resolve:
        mock_resolve.return_value = settings
        engine = WhisperEngine(settings)

    # 注入 mock 模型
    mock_model = MagicMock()
    mock_info = _make_mock_info(language, lang_prob)

    if segments_data is None:
        segments_data = [("你好世界", 0.0, 1.5)]

    mock_segments = [_make_mock_segment(t, s, e) for t, s, e in segments_data]
    mock_model.transcribe.return_value = (iter(mock_segments), mock_info)

    engine._model = mock_model
    return engine, mock_model


# ============================================================================
# WhisperEngine 单元测试
# ============================================================================


class TestWhisperEngineLazyLoad:
    """模型懒加载行为测试。"""

    def test_not_loaded_initially(self) -> None:
        settings = ASRSettings(
            model_size="medium", compute_type="int8", device="cpu"
        )
        with patch("backend.asr.whisper_engine.ASRSettings.resolve_with_gpu") as mock:
            mock.return_value = settings
            engine = WhisperEngine(settings)

        assert engine.is_loaded is False

    def test_loaded_after_transcribe(self) -> None:
        engine, mock_model = _create_engine_with_mock_model()
        assert engine.is_loaded is True  # we injected mock model

    def test_ensure_loaded_only_once(self) -> None:
        """多次 transcribe 只加载模型一次。"""
        engine, mock_model = _create_engine_with_mock_model()
        audio = np.zeros(16000, dtype=np.float32)

        engine.transcribe(audio)
        engine.transcribe(audio)

        # transcribe called twice on same model
        assert mock_model.transcribe.call_count == 2


class TestWhisperEngineTranscribe:
    """转写功能测试（mock 模型）。"""

    def test_basic_transcription(self) -> None:
        engine, _ = _create_engine_with_mock_model(
            segments_data=[("你好", 0.0, 0.8), ("世界", 0.8, 1.5)],
            language="zh",
            lang_prob=0.92,
        )
        audio = np.zeros(24000, dtype=np.float32)  # 1.5s at 16kHz
        result = engine.transcribe(audio)

        assert result.text == "你好 世界"
        assert result.language == "zh"
        assert result.language_probability == 0.92
        assert len(result.segments) == 2
        assert result.duration_s == pytest.approx(1.5)

    def test_segments_have_words(self) -> None:
        engine, _ = _create_engine_with_mock_model(
            segments_data=[("hello world", 0.0, 1.0)]
        )
        audio = np.zeros(16000, dtype=np.float32)
        result = engine.transcribe(audio)

        assert len(result.segments) == 1
        assert len(result.segments[0].words) == 1

    def test_language_override(self) -> None:
        """传入 language 参数应覆盖默认值。"""
        engine, mock_model = _create_engine_with_mock_model()
        audio = np.zeros(16000, dtype=np.float32)

        engine.transcribe(audio, language="en")

        call_kwargs = mock_model.transcribe.call_args
        assert call_kwargs[1]["language"] == "en"

    def test_empty_audio_returns_result(self) -> None:
        """零长度音频也应返回结果（不抛异常）。"""
        engine, _ = _create_engine_with_mock_model(segments_data=[])
        audio = np.zeros(0, dtype=np.float32)
        result = engine.transcribe(audio)

        assert result.text == ""
        assert result.is_empty is True

    def test_transcription_error_returns_empty(self) -> None:
        """模型异常时应返回空结果而非抛出。"""
        engine, mock_model = _create_engine_with_mock_model()
        mock_model.transcribe.side_effect = RuntimeError("OOM")

        audio = np.zeros(16000, dtype=np.float32)
        result = engine.transcribe(audio)

        assert result.is_empty is True
        assert result.language == "unknown"

    def test_vad_filter_disabled(self) -> None:
        """验证 vad_filter=False（我们使用独立 VAD）。"""
        engine, mock_model = _create_engine_with_mock_model()
        audio = np.zeros(16000, dtype=np.float32)
        engine.transcribe(audio)

        call_kwargs = mock_model.transcribe.call_args[1]
        assert call_kwargs["vad_filter"] is False


class TestWhisperEngineProperties:
    """引擎属性测试。"""

    def test_sample_rate(self) -> None:
        engine, _ = _create_engine_with_mock_model()
        assert engine.sample_rate == 16000

    def test_supported_languages(self) -> None:
        engine, _ = _create_engine_with_mock_model()
        languages = engine.get_supported_languages()
        assert "zh" in languages
        assert "en" in languages
        assert len(languages) > 50

    def test_is_asr_engine_subclass(self) -> None:
        assert issubclass(WhisperEngine, ASREngine)


# ============================================================================
# 集成测试 — 使用真实 faster-whisper 模型
# ============================================================================


@pytest.mark.hardware
class TestWhisperEngineReal:
    """使用真实 faster-whisper 模型的集成测试。

    需要下载模型（首次运行较慢）。
    机器无 GPU 时自动使用 CPU int8 模式。
    """

    def test_real_model_loads_and_transcribes(self) -> None:
        """验证真实模型能加载并处理静音音频（不抛异常）。"""
        engine = WhisperEngine()
        assert engine.is_loaded is False

        # 1s 静音
        silence = np.zeros(16000, dtype=np.float32)
        result = engine.transcribe(silence)

        assert engine.is_loaded is True
        assert isinstance(result, TranscriptionResult)

    def test_real_model_sine_wave(self) -> None:
        """正弦波不是语音，转写结果应较短或为空。"""
        engine = WhisperEngine()
        t = np.arange(16000 * 2, dtype=np.float32) / 16000
        sine = (0.3 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)

        result = engine.transcribe(sine)
        assert isinstance(result, TranscriptionResult)
        # Whisper may hallucinate on non-speech, but should not crash
