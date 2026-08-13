"""Whisper 模型本地存在性检查与按需下载。

单一职责：判断当前配置解析出的语音模型是否已在本地缓存，以及在缺失时触发下载。
供打包发行版在「模型未下载」时于 UI 引导用户下载使用。
"""

from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)


def resolved_model_size() -> str:
    """返回当前配置（含 GPU 自动选择）解析出的模型尺寸。"""
    from backend.config import get_settings

    return get_settings().asr.resolve_with_gpu().model_size or "small"


def is_model_cached(model_size: Optional[str] = None) -> bool:
    """判断指定模型是否已下载到本地缓存（不联网）。"""
    size = model_size or resolved_model_size()
    try:
        from faster_whisper import download_model

        download_model(size, local_files_only=True)
        return True
    except Exception:
        return False


def download_model_files(model_size: Optional[str] = None) -> str:
    """下载（若缺失）指定模型到本地缓存，返回本地路径。

    联网从 HuggingFace 拉取；已缓存时直接返回本地路径。
    """
    from faster_whisper import download_model

    size = model_size or resolved_model_size()
    logger.info("Ensuring Whisper model is available: %s", size)
    path = download_model(size)
    logger.info("Whisper model ready at: %s", path)
    return path
