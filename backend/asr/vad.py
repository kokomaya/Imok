"""VAD 语音活动检测 — 封装 Silero-VAD，检测语音段边界。

单一职责：接收音频流，输出完整的语音段（AudioSegment）。
不负责 ASR 推理、翻译或任何下游处理。

核心逻辑：
1. 持续接收音频 chunk（来自 AudioSource.read_chunk）
2. Silero-VAD 逐帧判断是否有语音
3. 检测到语音起始 → 开始累积音频数据
4. 检测到静音超过 min_silence_ms → 输出一个完整 AudioSegment
5. 累积时长超过 max_segment_s → 强制截断输出
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import List, Optional

import numpy as np
import torch

logger = logging.getLogger(__name__)

# Silero-VAD 要求的固定采样率和帧尺寸
_SILERO_SAMPLE_RATE = 16000
_SILERO_WINDOW_SIZES = {16000: 512, 8000: 256}  # 采样率 → 每帧样本数


@dataclass
class AudioSegment:
    """一段完整的语音数据 — VAD 检测到的一句话。

    Attributes:
        audio_data: mono float32 音频数据，采样率为 sample_rate。
        sample_rate: 采样率 (Hz)。
        start_time: 相对于 VAD 启动的起始时间 (秒)。
        end_time: 相对于 VAD 启动的结束时间 (秒)。
        duration_s: 时长 (秒)，自动计算。
        source_name: 产生此段落的音频源名称（如 "wasapi"、"mic"）。
        is_partial: 是否为中间结果（语音仍在进行中，尚未结束）。
    """

    audio_data: np.ndarray
    sample_rate: int
    start_time: float
    end_time: float
    source_name: str = ""
    is_partial: bool = False
    duration_s: float = field(init=False)

    def __post_init__(self) -> None:
        self.duration_s = self.end_time - self.start_time


class VoiceActivityDetector:
    """Silero-VAD 封装 — 流式语音活动检测。

    使用方式：
        vad = VoiceActivityDetector(threshold=0.5, min_silence_ms=300, max_segment_s=15)
        for chunk in audio_source:
            segments = vad.feed(chunk.data)
            for seg in segments:
                # seg 是一个完整的语音段，可以送入 ASR
                asr.transcribe(seg.audio_data)

    参数由 config.ASRSettings 提供，也可直接传入。
    """

    def __init__(
        self,
        sample_rate: int = 16000,
        threshold: float = 0.5,
        min_silence_ms: int = 300,
        max_segment_s: float = 15.0,
        partial_min_s: float = 0.3,
        partial_interval_s: float = 0.3,
    ) -> None:
        if sample_rate not in _SILERO_WINDOW_SIZES:
            raise ValueError(
                f"Silero-VAD only supports sample rates {list(_SILERO_WINDOW_SIZES)}, "
                f"got {sample_rate}"
            )

        self._sample_rate = sample_rate
        self._threshold = threshold
        self._min_silence_samples = int(min_silence_ms * sample_rate / 1000)
        self._max_segment_samples = int(max_segment_s * sample_rate)
        self._window_size = _SILERO_WINDOW_SIZES[sample_rate]
        self._partial_min_samples = int(partial_min_s * sample_rate)
        self._partial_interval_samples = int(partial_interval_s * sample_rate)

        # 加载 Silero-VAD 模型
        self._model, _utils = torch.hub.load(
            repo_or_dir="snakers4/silero-vad",
            model="silero_vad",
            trust_repo=True,
        )
        self._model.eval()

        # 内部状态
        self._reset_state()

        logger.info(
            "VAD initialized: sr=%d, threshold=%.2f, min_silence=%dms, max_segment=%.1fs, "
            "partial_min=%.1fs, partial_interval=%.1fs",
            sample_rate,
            threshold,
            min_silence_ms,
            max_segment_s,
            partial_min_s,
            partial_interval_s,
        )

    def feed(self, audio_chunk: np.ndarray) -> List[AudioSegment]:
        """输入一段音频，返回检测到的完整语音段列表。

        Args:
            audio_chunk: mono float32 音频数据，采样率必须匹配构造时的 sample_rate。

        Returns:
            0 或多个 AudioSegment。包含：
            - 完整段（is_partial=False）：语音段结束（静音或超长截断）
            - 中间段（is_partial=True）：语音进行中但已积累足够长度，用于流式展示
        """
        self._buffer = np.concatenate([self._buffer, audio_chunk])
        completed_segments: List[AudioSegment] = []

        # 逐窗口送入 VAD 模型
        while len(self._buffer) >= self._window_size:
            window = self._buffer[: self._window_size]
            self._buffer = self._buffer[self._window_size :]

            # Silero-VAD 推理
            speech_prob = self._get_speech_prob(window)
            is_speech = speech_prob >= self._threshold

            self._total_samples_fed += self._window_size

            if is_speech:
                if not self._in_speech:
                    # 语音开始
                    self._in_speech = True
                    self._speech_start_sample = self._total_samples_fed - self._window_size
                    self._speech_buffer = []
                    self._silence_counter = 0
                    self._last_partial_samples = 0

                self._speech_buffer.append(window)
                self._silence_counter = 0

                # 检查最大段时长
                total_speech_samples = sum(len(b) for b in self._speech_buffer)
                if total_speech_samples >= self._max_segment_samples:
                    segment = self._finalize_segment()
                    completed_segments.append(segment)
                elif total_speech_samples >= self._partial_min_samples:
                    # 检查是否该发送中间结果
                    samples_since_last = total_speech_samples - self._last_partial_samples
                    if (self._last_partial_samples == 0 or
                            samples_since_last >= self._partial_interval_samples):
                        partial = self._make_partial_segment()
                        if partial is not None:
                            completed_segments.append(partial)
                            self._last_partial_samples = total_speech_samples

            else:
                if self._in_speech:
                    # 在语音段内遇到静音，仍然保留（因为可能是短暂停顿）
                    self._speech_buffer.append(window)
                    self._silence_counter += self._window_size

                    # 静音持续超过阈值 → 语音段结束
                    if self._silence_counter >= self._min_silence_samples:
                        segment = self._finalize_segment()
                        completed_segments.append(segment)

        return completed_segments

    def flush(self) -> Optional[AudioSegment]:
        """强制输出当前累积的语音段（如果有）。

        用于会议结束时，确保最后一段语音不丢失。
        """
        if self._in_speech and self._speech_buffer:
            return self._finalize_segment()
        return None

    def reset(self) -> None:
        """重置所有内部状态，准备处理新的音频流。"""
        self._reset_state()
        self._model.reset_states()
        logger.debug("VAD state reset.")

    @property
    def sample_rate(self) -> int:
        return self._sample_rate

    def set_partial_timing(self, partial_min_s: float, partial_interval_s: float) -> None:
        """运行时更新 partial 参数（不重置状态）。"""
        self._partial_min_samples = int(partial_min_s * self._sample_rate)
        self._partial_interval_samples = int(partial_interval_s * self._sample_rate)
        logger.info(
            "VAD partial timing updated: min=%.1fs, interval=%.1fs",
            partial_min_s, partial_interval_s,
        )

    def _reset_state(self) -> None:
        """重置内部跟踪变量。"""
        self._buffer = np.array([], dtype=np.float32)
        self._in_speech = False
        self._speech_buffer: List[np.ndarray] = []
        self._speech_start_sample = 0
        self._silence_counter = 0
        self._total_samples_fed = 0
        self._last_partial_samples = 0

    def _get_speech_prob(self, window: np.ndarray) -> float:
        """对单个窗口执行 VAD 推理，返回语音概率。"""
        tensor = torch.from_numpy(window)
        with torch.no_grad():
            prob = self._model(tensor, self._sample_rate).item()
        return prob

    def _make_partial_segment(self) -> Optional[AudioSegment]:
        """创建当前累积语音的中间快照（不重置状态）。"""
        if not self._speech_buffer:
            return None

        audio_data = np.concatenate(self._speech_buffer)
        start_time = self._speech_start_sample / self._sample_rate
        end_time = start_time + len(audio_data) / self._sample_rate

        logger.debug(
            "VAD partial: %.2f-%.2f s (%.2f s, %d samples)",
            start_time, end_time, end_time - start_time, len(audio_data),
        )

        return AudioSegment(
            audio_data=audio_data,
            sample_rate=self._sample_rate,
            start_time=start_time,
            end_time=end_time,
            is_partial=True,
        )

    def _finalize_segment(self) -> AudioSegment:
        """将当前累积的语音数据打包为 AudioSegment 并重置状态。"""
        audio_data = np.concatenate(self._speech_buffer)

        # 如果末尾有过多静音，裁掉尾部静音（保留少量以避免截断感）
        keep_tail_samples = min(
            self._min_silence_samples // 2,
            len(audio_data),
        )
        if self._silence_counter > 0:
            trim_samples = max(0, self._silence_counter - keep_tail_samples)
            if trim_samples > 0 and trim_samples < len(audio_data):
                audio_data = audio_data[: len(audio_data) - trim_samples]

        start_time = self._speech_start_sample / self._sample_rate
        end_time = start_time + len(audio_data) / self._sample_rate

        segment = AudioSegment(
            audio_data=audio_data,
            sample_rate=self._sample_rate,
            start_time=start_time,
            end_time=end_time,
        )

        logger.debug(
            "VAD segment: %.2f-%.2f s (%.2f s, %d samples)",
            start_time,
            end_time,
            segment.duration_s,
            len(audio_data),
        )

        # 重置语音段状态
        self._in_speech = False
        self._speech_buffer = []
        self._silence_counter = 0

        return segment
