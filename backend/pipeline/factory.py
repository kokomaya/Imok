"""Pipeline 组件工厂 — 从 main.py 提取。

提供统一的音频源、VAD、ASR 引擎创建函数，
在 CLI 模式和 Subprocess 模式间复用。
"""

from __future__ import annotations


def create_audio_source(source_type: str, *, loopback_device=None, mic_device=None):
    """创建音频源。

    支持的 source_type:
    - 'wasapi': 仅系统音频
    - 'mic': 仅麦克风
    - 'both': 系统音频 + 麦克风混合

    设备索引优先级: 参数 > 配置文件 > 自动检测(None)
    """
    from backend.config import get_settings

    settings = get_settings()
    lb_idx = loopback_device if loopback_device is not None else settings.audio.loopback_device
    mic_idx = mic_device if mic_device is not None else settings.audio.mic_device

    if source_type == "both":
        from backend.audio.mixer import AudioMixer
        from backend.audio.mic_source import MicrophoneSource
        from backend.audio.wasapi_source import WASAPILoopbackSource

        mixer = AudioMixer(
            target_sample_rate=settings.audio.sample_rate,
            chunk_duration_s=settings.audio.chunk_frames / settings.audio.sample_rate,
        )
        mixer.add_source(
            "wasapi",
            WASAPILoopbackSource(
                target_sample_rate=settings.audio.sample_rate,
                chunk_frames=settings.audio.chunk_frames,
                device_index=lb_idx,
            ),
        )
        mixer.add_source(
            "mic",
            MicrophoneSource(
                target_sample_rate=settings.audio.sample_rate,
                chunk_frames=settings.audio.chunk_frames,
                device_index=mic_idx,
            ),
        )
        return mixer
    elif source_type == "wasapi":
        from backend.audio.wasapi_source import WASAPILoopbackSource

        return WASAPILoopbackSource(
            target_sample_rate=settings.audio.sample_rate,
            chunk_frames=settings.audio.chunk_frames,
            device_index=lb_idx,
        )
    else:
        from backend.audio.mic_source import MicrophoneSource

        return MicrophoneSource(
            target_sample_rate=settings.audio.sample_rate,
            chunk_frames=settings.audio.chunk_frames,
            device_index=mic_idx,
        )


def create_vad():
    """创建 VAD 实例。"""
    from backend.config import get_settings
    from backend.asr.vad import VoiceActivityDetector

    settings = get_settings()
    return VoiceActivityDetector(
        sample_rate=settings.audio.sample_rate,
        threshold=settings.asr.vad_threshold,
        min_silence_ms=settings.asr.vad_min_silence_ms,
        max_segment_s=settings.asr.vad_max_segment_s,
    )


def create_asr():
    """创建 ASR 引擎。"""
    from backend.config import get_settings
    from backend.asr.whisper_engine import WhisperEngine

    settings = get_settings()
    return WhisperEngine(settings.asr)
