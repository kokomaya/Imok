"""音频源抽象基类 — 定义所有音频采集源的统一接口（ISP: 接口隔离）。

所有具体音频源（WASAPI Loopback、麦克风、虚拟声卡）都必须实现此接口，
上层模块（Pipeline）仅依赖此抽象，不依赖具体实现（DIP: 依赖倒置）。
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)


class AudioSourceType(str, Enum):
    """音频源类型枚举。"""

    WASAPI_LOOPBACK = "wasapi_loopback"
    MICROPHONE = "microphone"
    VBCABLE = "vbcable"


@dataclass(frozen=True)
class AudioDeviceInfo:
    """音频设备信息。"""

    index: int
    name: str
    host_api: str
    max_input_channels: int
    max_output_channels: int
    default_sample_rate: float
    is_loopback: bool = False


@dataclass
class AudioChunk:
    """音频数据块 — 携带元数据的音频片段。"""

    data: np.ndarray  # shape: (frames,) mono float32, normalized to [-1, 1]
    sample_rate: int
    timestamp_s: float  # 相对于采集开始的时间偏移 (秒)
    source_type: AudioSourceType = AudioSourceType.MICROPHONE
    duration_s: float = field(init=False)

    def __post_init__(self) -> None:
        self.duration_s = len(self.data) / self.sample_rate


class AudioSource(ABC):
    """音频源抽象基类。

    生命周期: __init__ → start() → read_chunk() ... → stop()

    所有实现必须保证：
    - read_chunk() 返回的 ndarray 为 mono float32, 采样率为 target_sample_rate
    - start() 幂等（重复调用不报错）
    - stop() 幂等且释放所有系统资源
    """

    @abstractmethod
    def start(self) -> None:
        """开始音频采集。可重复调用（幂等）。"""

    @abstractmethod
    def stop(self) -> None:
        """停止音频采集并释放资源。可重复调用（幂等）。"""

    @abstractmethod
    def read_chunk(self) -> Optional[AudioChunk]:
        """读取一个音频块。

        Returns:
            AudioChunk 或 None（无数据时）。
            数据格式：mono float32, 采样率为 get_sample_rate()。
        """

    @abstractmethod
    def get_sample_rate(self) -> int:
        """返回输出音频的目标采样率 (Hz)。"""

    @abstractmethod
    def get_source_type(self) -> AudioSourceType:
        """返回音频源类型。"""

    @property
    @abstractmethod
    def is_active(self) -> bool:
        """当前是否正在采集。"""

    def __enter__(self) -> "AudioSource":
        self.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.stop()
