"""ASR 引擎抽象基类 — 定义语音识别引擎的统一接口（OCP: 开放封闭）。

所有具体 ASR 引擎（faster-whisper、Azure Speech 等）都必须实现此接口，
上层模块（Pipeline）仅依赖此抽象，不依赖具体实现（DIP: 依赖倒置）。

设计决策：
- transcribe() 接受 np.ndarray 而非 AudioSegment，避免与 VAD 模块耦合。
- TranscriptionResult 携带完整元信息（语言、置信度、时间片段），
  供下游翻译模块和存储模块使用。
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import List, Optional

import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class WordSegment:
    """单个词/短语的时间对齐信息。"""

    word: str
    start: float  # 相对于音频段起始的偏移 (秒)
    end: float
    probability: float = 0.0


@dataclass
class TranscriptionSegment:
    """一个转写片段（通常对应一句话或子句）。"""

    text: str
    start: float  # 相对于音频段起始的偏移 (秒)
    end: float
    words: List[WordSegment] = field(default_factory=list)
    avg_logprob: float = 0.0
    no_speech_prob: float = 0.0


@dataclass
class TranscriptionResult:
    """ASR 转写结果 — 包含完整文本和分段详情。

    Attributes:
        text: 完整转写文本（所有 segments 拼接）。
        language: 检测到的语言代码 (如 "zh", "en")。
        language_probability: 语言检测置信度 [0, 1]。
        segments: 分段转写结果列表。
        duration_s: 输入音频时长 (秒)。
    """

    text: str
    language: str
    language_probability: float = 0.0
    segments: List[TranscriptionSegment] = field(default_factory=list)
    duration_s: float = 0.0

    @property
    def is_empty(self) -> bool:
        return not self.text.strip()


class ASREngine(ABC):
    """ASR 引擎抽象基类。

    使用方式（里氏替换 — 可替换任何具体实现）：
        engine: ASREngine = WhisperEngine(settings)
        result = engine.transcribe(audio_data)
        print(result.text, result.language)
    """

    @abstractmethod
    def transcribe(
        self,
        audio: np.ndarray,
        language: Optional[str] = None,
    ) -> TranscriptionResult:
        """对音频数据进行语音识别。

        Args:
            audio: mono float32 音频数据，采样率应为引擎期望值（通常 16kHz）。
            language: 指定语言代码。None 表示自动检测。

        Returns:
            TranscriptionResult 包含转写文本及元信息。
        """

    def transcribe_fast(
        self,
        audio: np.ndarray,
        language: Optional[str] = None,
    ) -> TranscriptionResult:
        """快速转写（用于流式 partial）。默认回退到 transcribe()。"""
        return self.transcribe(audio, language)

    @abstractmethod
    def get_supported_languages(self) -> List[str]:
        """返回引擎支持的语言代码列表。"""

    @property
    @abstractmethod
    def sample_rate(self) -> int:
        """引擎期望的输入音频采样率 (Hz)。"""

    @property
    @abstractmethod
    def is_loaded(self) -> bool:
        """模型是否已加载到内存。"""

    def load(self) -> None:
        """显式加载模型到内存。

        默认实现为空操作（对于无需预加载的引擎）。
        支持懒加载的引擎应重写此方法以提前加载，避免首次 transcribe() 延迟。
        """
