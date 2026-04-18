"""Speaker embedding extraction — 从音频段中提取说话人向量。

单一职责：将音频数据转换为固定维度的说话人嵌入向量。
不负责聚类、ID 分配或任何下游逻辑。

使用 SpeechBrain 预训练的 ECAPA-TDNN 模型（192 维嵌入）。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import numpy as np
import torch

from backend.speaker.base import SpeakerEmbedderBase

logger = logging.getLogger(__name__)

# 模型要求的采样率
_EMBEDDING_SAMPLE_RATE = 16000

# 最短有效音频（0.5 秒），太短的段不足以提取可靠的说话人特征
_MIN_DURATION_S = 0.5


class SpeakerEmbedder(SpeakerEmbedderBase):
    """说话人嵌入提取器 — 封装 SpeechBrain ECAPA-TDNN。

    延迟加载：模型在第一次调用 embed() 时才加载。

    Args:
        model_dir: 模型缓存目录。None 表示使用默认缓存。
        device: 推理设备 ('cuda' / 'cpu')。None 表示自动检测。
    """

    def __init__(
        self,
        model_dir: Optional[Path] = None,
        device: Optional[str] = None,
    ) -> None:
        self._model_dir = model_dir
        self._device = device or ("cuda" if torch.cuda.is_available() else "cpu")
        self._model = None
        self._embedding_dim: int = 192  # ECAPA-TDNN 输出维度

    @property
    def embedding_dim(self) -> int:
        return self._embedding_dim

    def load(self) -> None:
        """显式加载模型。可选 — embed() 会自动触发。"""
        if self._model is not None:
            return

        from speechbrain.inference.speaker import EncoderClassifier

        logger.info("Loading speaker embedding model (ECAPA-TDNN)...")
        source = "speechbrain/spkrec-ecapa-voxceleb"
        save_dir = str(self._model_dir) if self._model_dir else None

        self._model = EncoderClassifier.from_hparams(
            source=source,
            savedir=save_dir or "pretrained_models/spkrec-ecapa-voxceleb",
            run_opts={"device": self._device},
        )
        logger.info("Speaker embedding model loaded on %s.", self._device)

    def embed(self, audio: np.ndarray, sample_rate: int = _EMBEDDING_SAMPLE_RATE) -> Optional[np.ndarray]:
        """从音频段提取说话人嵌入向量。

        Args:
            audio: 单声道 float32 音频数据。
            sample_rate: 采样率（必须为 16000）。

        Returns:
            192 维 float32 numpy 向量，或 None（音频太短时）。
        """
        if sample_rate != _EMBEDDING_SAMPLE_RATE:
            raise ValueError(f"Expected {_EMBEDDING_SAMPLE_RATE} Hz, got {sample_rate}")

        duration = len(audio) / sample_rate
        if duration < _MIN_DURATION_S:
            logger.debug("Audio too short (%.2fs) for embedding, skipping.", duration)
            return None

        if self._model is None:
            self.load()

        # SpeechBrain expects a torch tensor [batch, time]
        waveform = torch.from_numpy(audio).unsqueeze(0).to(self._device)
        with torch.no_grad():
            embedding = self._model.encode_batch(waveform)

        # embedding shape: [1, 1, 192] → squeeze to [192]
        vec = embedding.squeeze().cpu().numpy().astype(np.float32)
        self._embedding_dim = vec.shape[0]
        return vec
