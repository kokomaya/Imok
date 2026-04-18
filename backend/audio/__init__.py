"""音频采集模块 - 提供系统音频和麦克风音频的采集能力。"""

from backend.audio.base import AudioChunk, AudioDeviceInfo, AudioSource, AudioSourceType
from backend.audio.mic_source import MicrophoneSource
from backend.audio.mixer import AudioMixer
from backend.audio.wasapi_source import WASAPILoopbackSource

__all__ = [
    "AudioChunk",
    "AudioDeviceInfo",
    "AudioMixer",
    "AudioSource",
    "AudioSourceType",
    "MicrophoneSource",
    "WASAPILoopbackSource",
]
