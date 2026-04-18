"""Speaker module abstract base classes — 定义说话人识别的统一接口。

遵循 DIP（依赖倒置）：Pipeline 依赖这些抽象，不依赖具体实现。
遵循 OCP（开放封闭）：新的嵌入模型或跟踪算法只需实现接口，无需修改上层。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Dict, List, Optional

import numpy as np


class SpeakerEmbedderBase(ABC):
    """说话人嵌入提取器抽象基类。

    单一职责：将音频数据转换为固定维度的说话人嵌入向量。
    """

    @property
    @abstractmethod
    def embedding_dim(self) -> int:
        """嵌入向量维度。"""

    @abstractmethod
    def embed(
        self, audio: np.ndarray, sample_rate: int = 16000
    ) -> Optional[np.ndarray]:
        """从音频段提取说话人嵌入向量。

        Args:
            audio: 单声道 float32 音频数据。
            sample_rate: 采样率。

        Returns:
            固定维度 float32 numpy 向量，或 None（音频太短/无效时）。
        """


class SpeakerTrackerBase(ABC):
    """在线说话人跟踪器抽象基类。

    单一职责：接收说话人嵌入向量，分配或匹配说话人 ID。
    """

    @abstractmethod
    def identify(self, embedding: np.ndarray) -> str:
        """为一个嵌入向量分配说话人 ID。

        Args:
            embedding: 说话人嵌入向量。

        Returns:
            说话人 ID 字符串（如 "Speaker_1"）。
        """

    @abstractmethod
    def reset(self) -> None:
        """重置所有说话人信息。"""

    @abstractmethod
    def to_dict(self) -> dict:
        """序列化为可持久化的字典。"""

    @classmethod
    @abstractmethod
    def from_dict(cls, data: dict) -> "SpeakerTrackerBase":
        """从持久化的字典恢复。"""

    @abstractmethod
    def get_label(self, speaker_id: str) -> str:
        """获取说话人的显示名称。"""

    @abstractmethod
    def rename_speaker(self, speaker_id: str, new_label: str) -> bool:
        """重命名说话人的显示名称。"""
