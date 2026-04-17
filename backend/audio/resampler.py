"""音频重采样工具 — 单一职责：将任意采样率的音频转换为目标采样率。

从 AudioSource 实现中抽离出来，避免重采样逻辑在多个 Source 中重复。
"""

from __future__ import annotations

import numpy as np
from scipy.signal import resample_poly
from math import gcd


def resample_audio(
    audio: np.ndarray,
    orig_sr: int,
    target_sr: int,
) -> np.ndarray:
    """将音频从 orig_sr 重采样到 target_sr。

    Args:
        audio: 输入音频，shape (frames,) 或 (frames, channels), float32。
        orig_sr: 原始采样率。
        target_sr: 目标采样率。

    Returns:
        重采样后的音频，同类型同维度。
    """
    if orig_sr == target_sr:
        return audio

    divisor = gcd(orig_sr, target_sr)
    up = target_sr // divisor
    down = orig_sr // divisor

    return resample_poly(audio, up, down).astype(np.float32)


def to_mono_float32(audio: np.ndarray) -> np.ndarray:
    """将音频统一转换为 mono float32 [-1, 1]。

    Args:
        audio: 输入音频，可能为 int16/int32/float32，mono 或 stereo。

    Returns:
        mono float32 ndarray, shape (frames,)。
    """
    # 转为 float32
    if audio.dtype == np.int16:
        audio = audio.astype(np.float32) / 32768.0
    elif audio.dtype == np.int32:
        audio = audio.astype(np.float32) / 2147483648.0
    elif audio.dtype != np.float32:
        audio = audio.astype(np.float32)

    # 转为 mono
    if audio.ndim == 2:
        audio = audio.mean(axis=1)

    return audio
