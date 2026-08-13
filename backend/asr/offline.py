"""离线录音重解析 — 对整段 recording.wav 重新转写，生成独立于会议实时字幕的第二份字幕。

单一职责：读取 WAV → 归一化为 16k mono float32 → 用 WhisperEngine 流式转写 → 组装
TranscriptionEntry 列表。参数偏向准确性（非实时）：更大 beam、启用内置 VAD 与上下文条件；
GPU 上使用 large-v3 模型。

不负责存储/IPC，由调用方（main.py）驱动进度上报与落盘。
"""

from __future__ import annotations

import logging
import wave
from pathlib import Path
from typing import Callable, List, Optional

import numpy as np

from backend.asr.whisper_engine import _WHISPER_SAMPLE_RATE, WhisperEngine
from backend.audio.resampler import resample_audio, to_mono_float32
from backend.config import get_settings
from backend.storage.models import TranscriptionEntry

logger = logging.getLogger(__name__)

# 进度回调签名：(processed_seconds, total_seconds)
ProgressCallback = Callable[[float, float], None]


def build_offline_engine() -> WhisperEngine:
    """构建偏向准确性的离线转写引擎（非实时）。

    复用实时转写已下载的同一 Whisper 模型（不额外下载新模型），仅通过更慢更准的
    解码参数提升准确率：加大 beam 宽度、开启词级时间戳；上下文条件与内置 VAD 由
    transcribe_stream 默认开启。
    """
    base = get_settings().asr.resolve_with_gpu()
    offline_settings = base.model_copy(
        update={
            "beam_size": max(base.beam_size, 5),
            "word_timestamps": True,
        }
    )
    logger.info(
        "Offline re-transcribe engine (reuse live model): model=%s device=%s beam=%d",
        offline_settings.model_size,
        offline_settings.device,
        offline_settings.beam_size,
    )
    return WhisperEngine(offline_settings)


def _load_wav_16k_mono(path: Path) -> np.ndarray:
    """读取 WAV 文件并归一化为 16kHz mono float32。"""
    with wave.open(str(path), "rb") as wf:
        sample_rate = wf.getframerate()
        n_channels = wf.getnchannels()
        sample_width = wf.getsampwidth()
        n_frames = wf.getnframes()
        raw = wf.readframes(n_frames)

    if sample_width == 2:
        audio = np.frombuffer(raw, dtype=np.int16)
    elif sample_width == 4:
        audio = np.frombuffer(raw, dtype=np.int32)
    elif sample_width == 1:
        # 8-bit WAV 为无符号，转为有符号中心化
        audio = (np.frombuffer(raw, dtype=np.uint8).astype(np.int16) - 128) * 256
    else:
        raise ValueError(f"Unsupported sample width: {sample_width}")

    if n_channels > 1:
        audio = audio.reshape(-1, n_channels)

    mono = to_mono_float32(audio)
    if sample_rate != _WHISPER_SAMPLE_RATE:
        mono = resample_audio(mono, sample_rate, _WHISPER_SAMPLE_RATE)
    return mono


def retranscribe_recording(
    wav_path: Path,
    engine: WhisperEngine,
    *,
    started_at: float = 0.0,
    progress_cb: Optional[ProgressCallback] = None,
) -> List[TranscriptionEntry]:
    """对整段录音重新转写，返回 TranscriptionEntry 列表。

    Args:
        wav_path: recording.wav 路径。
        engine: 离线转写引擎（build_offline_engine 创建）。
        started_at: 会议开始的 Unix 时间戳，用于计算各段的绝对 timestamp。
        progress_cb: 进度回调 (已处理秒, 总秒)。

    Returns:
        转写条目列表；音频为空或无语音时返回空列表。
    """
    audio = _load_wav_16k_mono(Path(wav_path))
    total_s = len(audio) / _WHISPER_SAMPLE_RATE
    if total_s <= 0:
        return []

    entries: List[TranscriptionEntry] = []
    for seg in engine.transcribe_stream(audio):
        text = (seg.text or "").strip()
        if text:
            entries.append(
                TranscriptionEntry(
                    text=text,
                    timestamp=started_at + seg.start,
                    language="",
                    confidence=float(np.exp(seg.avg_logprob)) if seg.avg_logprob else 0.0,
                    segment_start=float(seg.start),
                    segment_end=float(seg.end),
                )
            )
        if progress_cb is not None:
            progress_cb(min(seg.end, total_s), total_s)

    if progress_cb is not None:
        progress_cb(total_s, total_s)
    return entries
