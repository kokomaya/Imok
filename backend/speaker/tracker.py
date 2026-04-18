"""Online speaker tracker — 基于嵌入向量的在线说话人跟踪。

单一职责：接收说话人嵌入向量，分配或匹配说话人 ID。
不负责嵌入提取、音频处理或任何 I/O。

算法：增量式余弦相似度匹配。
- 维护已知说话人的平均嵌入向量
- 新嵌入与所有已知说话人比较余弦相似度
- 超过阈值则分配到最匹配的说话人并更新其平均向量
- 低于阈值则创建新说话人
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np

from backend.speaker.base import SpeakerTrackerBase

logger = logging.getLogger(__name__)

# 默认余弦相似度阈值 — 高于此值认为是同一个人
# 使用 L2 归一化嵌入向量时，同一说话人典型相似度 0.5–0.9，不同说话人典型 0.0–0.3
_DEFAULT_THRESHOLD = 0.40

# 最大说话人数量 — 防止无限增长
_MAX_SPEAKERS = 20


@dataclass
class SpeakerProfile:
    """已知说话人的累积信息。"""

    speaker_id: str
    label: str  # 显示名称（可由用户修改）
    embedding_sum: np.ndarray  # 嵌入向量的累积和
    count: int = 0  # 被匹配到的次数

    @property
    def centroid(self) -> np.ndarray:
        """当前平均嵌入向量（质心）。"""
        return self.embedding_sum / max(self.count, 1)


class SpeakerTracker(SpeakerTrackerBase):
    """在线说话人跟踪器 — 增量式余弦相似度聚类。

    Args:
        threshold: 余弦相似度阈值。高于此值匹配为同一说话人。
        max_speakers: 最大说话人数量。
    """

    def __init__(
        self,
        threshold: float = _DEFAULT_THRESHOLD,
        max_speakers: int = _MAX_SPEAKERS,
    ) -> None:
        self._threshold = threshold
        self._max_speakers = max_speakers
        self._profiles: Dict[str, SpeakerProfile] = {}
        self._next_id = 1

    @property
    def speaker_count(self) -> int:
        return len(self._profiles)

    @property
    def profiles(self) -> List[SpeakerProfile]:
        """返回所有已知说话人的 profile（按 ID 排序）。"""
        return sorted(self._profiles.values(), key=lambda p: p.speaker_id)

    def identify(self, embedding: np.ndarray) -> str:
        """为一个嵌入向量分配说话人 ID。

        Args:
            embedding: 说话人嵌入向量 (float32)。

        Returns:
            说话人 ID 字符串（如 "Speaker_1"）。
        """
        if len(self._profiles) == 0:
            return self._create_speaker(embedding)

        # 计算与所有已知说话人的余弦相似度
        best_id = None
        best_sim = -1.0

        for sid, profile in self._profiles.items():
            sim = _cosine_similarity(embedding, profile.centroid)
            if sim > best_sim:
                best_sim = sim
                best_id = sid

        logger.debug(
            "Speaker match: best=%s sim=%.3f threshold=%.2f",
            best_id, best_sim, self._threshold,
        )

        if best_sim >= self._threshold and best_id is not None:
            # 匹配到已知说话人 — 更新其质心
            self._update_profile(best_id, embedding)
            return best_id

        # 未匹配 — 创建新说话人（如果未超过限制）
        if len(self._profiles) >= self._max_speakers:
            # 超过限制，强制分配到最近的
            logger.warning(
                "Max speakers (%d) reached, forcing assignment to closest.",
                self._max_speakers,
            )
            if best_id is not None:
                self._update_profile(best_id, embedding)
                return best_id

        return self._create_speaker(embedding)

    def rename_speaker(self, speaker_id: str, new_label: str) -> bool:
        """重命名说话人的显示名称。"""
        profile = self._profiles.get(speaker_id)
        if profile is None:
            return False
        profile.label = new_label
        return True

    def get_label(self, speaker_id: str) -> str:
        """获取说话人的显示名称。"""
        profile = self._profiles.get(speaker_id)
        return profile.label if profile else speaker_id

    def reset(self) -> None:
        """重置所有说话人信息（新会议时调用）。"""
        self._profiles.clear()
        self._next_id = 1

    def to_dict(self) -> dict:
        """序列化为可持久化的字典。"""
        return {
            "threshold": self._threshold,
            "speakers": [
                {
                    "speaker_id": p.speaker_id,
                    "label": p.label,
                    "count": p.count,
                    "centroid": p.centroid.tolist(),
                }
                for p in self.profiles
            ],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "SpeakerTracker":
        """从持久化的字典恢复。"""
        tracker = cls(threshold=data.get("threshold", _DEFAULT_THRESHOLD))
        for sp in data.get("speakers", []):
            centroid = np.array(sp["centroid"], dtype=np.float32)
            count = sp.get("count", 1)
            tracker._profiles[sp["speaker_id"]] = SpeakerProfile(
                speaker_id=sp["speaker_id"],
                label=sp.get("label", sp["speaker_id"]),
                embedding_sum=centroid * count,
                count=count,
            )
            # 更新 _next_id
            num = sp["speaker_id"].replace("Speaker_", "")
            if num.isdigit():
                tracker._next_id = max(tracker._next_id, int(num) + 1)
        return tracker

    def _create_speaker(self, embedding: np.ndarray) -> str:
        """创建新说话人。"""
        speaker_id = f"Speaker_{self._next_id}"
        self._next_id += 1
        self._profiles[speaker_id] = SpeakerProfile(
            speaker_id=speaker_id,
            label=speaker_id,
            embedding_sum=embedding.copy(),
            count=1,
        )
        logger.info("New speaker detected: %s (total: %d)", speaker_id, len(self._profiles))
        return speaker_id

    def _update_profile(self, speaker_id: str, embedding: np.ndarray) -> None:
        """用新嵌入更新说话人的累积向量。"""
        profile = self._profiles[speaker_id]
        profile.embedding_sum += embedding
        profile.count += 1


def _cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    """计算两个向量的余弦相似度。"""
    norm_a = np.linalg.norm(a)
    norm_b = np.linalg.norm(b)
    if norm_a < 1e-9 or norm_b < 1e-9:
        return 0.0
    return float(np.dot(a, b) / (norm_a * norm_b))
