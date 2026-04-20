"""faster-whisper ASR 引擎实现 — 基于 CTranslate2 的高性能语音识别。

单一职责：将音频数据转换为文本，不负责 VAD、翻译或任何后处理。
通过 ASREngine 抽象基类实现 OCP（新增引擎无需修改已有代码）。

核心特性：
- 模型懒加载：首次 transcribe() 调用时才加载，避免启动阻塞
- GPU/CPU 自动降级：根据 config.ASRSettings.resolve_with_gpu() 结果配置
- 支持中英混合识别（language=None 时自动检测）
"""

from __future__ import annotations

import logging
import threading
import time
from typing import List, Optional

import numpy as np

from backend.asr.base import (
    ASREngine,
    TranscriptionResult,
    TranscriptionSegment,
    WordSegment,
)
from backend.config import ASRSettings

logger = logging.getLogger(__name__)

_WHISPER_SAMPLE_RATE = 16000

# faster-whisper 支持的语言列表 (Whisper large-v3)
_SUPPORTED_LANGUAGES = [
    "af", "am", "ar", "as", "az", "ba", "be", "bg", "bn", "bo", "br", "bs",
    "ca", "cs", "cy", "da", "de", "el", "en", "es", "et", "eu", "fa", "fi",
    "fo", "fr", "gl", "gu", "ha", "haw", "he", "hi", "hr", "ht", "hu", "hy",
    "id", "is", "it", "ja", "jw", "ka", "kk", "km", "kn", "ko", "la", "lb",
    "ln", "lo", "lt", "lv", "mg", "mi", "mk", "ml", "mn", "mr", "ms", "mt",
    "my", "ne", "nl", "nn", "no", "oc", "pa", "pl", "ps", "pt", "ro", "ru",
    "sa", "sd", "si", "sk", "sl", "sn", "so", "sq", "sr", "su", "sv", "sw",
    "ta", "te", "tg", "th", "tk", "tl", "tr", "tt", "uk", "ur", "uz", "vi",
    "yi", "yo", "zh", "yue",
]


class WhisperEngine(ASREngine):
    """基于 faster-whisper 的 ASR 引擎。

    模型懒加载：__init__ 仅记录配置，首次 transcribe() 时才加载模型。
    线程安全：模型加载使用 threading.Lock 保护，避免并发初始化。

    语言缓存：当 language=None（自动检测）时，首次检测到高概率语言后缓存，
    后续推理使用缓存语言以节省 ~1s/段。调用 reset_language_cache() 可重置。
    """

    # 语言缓存需要连续 N 次一致检测才锁定
    _LANG_CACHE_THRESHOLD = 3
    # 语言检测概率需达到此值才计入一致性判断
    _LANG_PROB_MIN = 0.8

    def __init__(self, settings: Optional[ASRSettings] = None) -> None:
        resolved = (settings or ASRSettings()).resolve_with_gpu()
        self._model_size: str = resolved.model_size or "medium"
        self._compute_type: str = resolved.compute_type or "int8"
        self._device: str = resolved.device or "cpu"
        self._beam_size: int = resolved.beam_size
        self._language: Optional[str] = resolved.language

        self._model = None  # WhisperModel, lazily loaded
        self._load_lock = threading.Lock()

        # 语言缓存状态
        self._cached_language: Optional[str] = None
        self._lang_detect_streak: int = 0
        self._last_detected_lang: Optional[str] = None

        logger.info(
            "WhisperEngine configured: model=%s, device=%s, compute=%s, beam=%d, lang=%s",
            self._model_size,
            self._device,
            self._compute_type,
            self._beam_size,
            self._language or "auto",
        )

    def transcribe(
        self,
        audio: np.ndarray,
        language: Optional[str] = None,
    ) -> TranscriptionResult:
        """对音频数据执行语音识别。

        Args:
            audio: mono float32, 16kHz 采样率。
            language: 覆盖默认语言设置。None 使用构造时的配置。

        Returns:
            TranscriptionResult 包含完整转写文本和分段信息。
        """
        self._ensure_loaded()

        # 语言优先级：参数 > 配置 > 缓存 > None（自动检测）
        lang = language or self._language or self._cached_language
        duration_s = len(audio) / _WHISPER_SAMPLE_RATE

        t0 = time.monotonic()
        try:
            segments_iter, info = self._model.transcribe(
                audio,
                beam_size=self._beam_size,
                language=lang,
                word_timestamps=True,
                vad_filter=False,  # 我们使用独立的 Silero-VAD，不需要内置过滤
                condition_on_previous_text=False,  # 段间独立，避免错误传播
            )

            segments: List[TranscriptionSegment] = []
            full_text_parts: List[str] = []

            for seg in segments_iter:
                words = [
                    WordSegment(
                        word=w.word,
                        start=w.start,
                        end=w.end,
                        probability=w.probability,
                    )
                    for w in (seg.words or [])
                ]

                ts = TranscriptionSegment(
                    text=seg.text.strip(),
                    start=seg.start,
                    end=seg.end,
                    words=words,
                    avg_logprob=seg.avg_logprob,
                    no_speech_prob=seg.no_speech_prob,
                )
                segments.append(ts)
                full_text_parts.append(seg.text.strip())

            full_text = " ".join(full_text_parts)

            result = TranscriptionResult(
                text=full_text,
                language=info.language,
                language_probability=info.language_probability,
                segments=segments,
                duration_s=duration_s,
            )

            elapsed = time.monotonic() - t0
            # 更新语言缓存（仅当未显式指定语言时）
            if not (language or self._language):
                self._update_language_cache(info.language, info.language_probability)

            logger.debug(
                "Transcribed %.1fs audio in %.2fs (RTF=%.2f) → '%s' [%s prob=%.2f, lang_hint=%s]",
                duration_s,
                elapsed,
                elapsed / max(duration_s, 0.01),
                full_text[:80],
                info.language,
                info.language_probability,
                lang or "auto",
            )
            return result

        except Exception:
            logger.exception("Transcription failed for %.1fs audio", duration_s)
            return TranscriptionResult(
                text="",
                language=lang or "unknown",
                language_probability=0.0,
                duration_s=duration_s,
            )

    def transcribe_fast(
        self,
        audio: np.ndarray,
        language: Optional[str] = None,
    ) -> TranscriptionResult:
        """快速转写 — 用于流式 partial 展示。

        使用贪心解码（beam_size=1）、无 word_timestamps，速度比完整推理快 2-3 倍。
        牺牲少量准确性换取低延迟，最终结果仍由 transcribe() 给出。
        """
        self._ensure_loaded()

        lang = language or self._language or self._cached_language
        duration_s = len(audio) / _WHISPER_SAMPLE_RATE

        t0 = time.monotonic()
        try:
            segments_iter, info = self._model.transcribe(
                audio,
                beam_size=1,
                language=lang,
                word_timestamps=False,
                vad_filter=False,
                condition_on_previous_text=False,
                without_timestamps=True,
            )

            full_text_parts: List[str] = []
            for seg in segments_iter:
                text = seg.text.strip()
                if text:
                    full_text_parts.append(text)

            full_text = " ".join(full_text_parts)

            elapsed = time.monotonic() - t0
            if not (language or self._language):
                self._update_language_cache(info.language, info.language_probability)

            logger.debug(
                "Fast transcribed %.1fs in %.2fs (RTF=%.2f) → '%s'",
                duration_s, elapsed, elapsed / max(duration_s, 0.01),
                full_text[:80],
            )

            return TranscriptionResult(
                text=full_text,
                language=info.language,
                language_probability=info.language_probability,
                duration_s=duration_s,
            )

        except Exception:
            logger.exception("Fast transcription failed for %.1fs audio", duration_s)
            return TranscriptionResult(
                text="",
                language=lang or "unknown",
                language_probability=0.0,
                duration_s=duration_s,
            )

    def get_supported_languages(self) -> List[str]:
        return list(_SUPPORTED_LANGUAGES)

    @property
    def sample_rate(self) -> int:
        return _WHISPER_SAMPLE_RATE

    @property
    def is_loaded(self) -> bool:
        return self._model is not None

    def load(self) -> None:
        """显式预加载 Whisper 模型，避免首次 transcribe() 的延迟。"""
        self._ensure_loaded()

    def reset_language_cache(self) -> None:
        """重置语言缓存，恢复自动检测。每次新会议开始时调用。"""
        self._cached_language = None
        self._lang_detect_streak = 0
        self._last_detected_lang = None
        logger.debug("Language cache reset.")

    def _update_language_cache(self, detected_lang: str, probability: float) -> None:
        """更新语言缓存：连续 N 次高概率一致检测后锁定语言。"""
        if probability < self._LANG_PROB_MIN:
            # 低概率检测不参与缓存判断
            return

        if detected_lang == self._last_detected_lang:
            self._lang_detect_streak += 1
        else:
            self._last_detected_lang = detected_lang
            self._lang_detect_streak = 1

        if self._lang_detect_streak >= self._LANG_CACHE_THRESHOLD and self._cached_language != detected_lang:
            self._cached_language = detected_lang
            logger.info(
                "Language locked to '%s' after %d consistent detections (saves ~1s/segment).",
                detected_lang,
                self._lang_detect_streak,
            )

    def _ensure_loaded(self) -> None:
        """懒加载模型 — 首次调用时加载，线程安全。"""
        if self._model is not None:
            return

        with self._load_lock:
            # 双重检查锁
            if self._model is not None:
                return

            logger.info(
                "Loading Whisper model: %s (device=%s, compute=%s)...",
                self._model_size,
                self._device,
                self._compute_type,
            )

            from faster_whisper import WhisperModel

            try:
                self._model = WhisperModel(
                    self._model_size,
                    device=self._device,
                    compute_type=self._compute_type,
                )
            except RuntimeError as exc:
                if self._device == "cuda" and "out of memory" in str(exc).lower():
                    # CPU 上大模型太慢，降级到 small 以保证实时性
                    cpu_model = "small" if self._model_size in ("large-v3", "medium") else self._model_size
                    logger.warning(
                        "CUDA OOM loading %s model, falling back to %s on CPU int8.",
                        self._model_size,
                        cpu_model,
                    )
                    self._model_size = cpu_model
                    self._device = "cpu"
                    self._compute_type = "int8"
                    self._model = WhisperModel(
                        cpu_model,
                        device="cpu",
                        compute_type="int8",
                    )
                else:
                    raise

            logger.info("Whisper model loaded successfully.")
