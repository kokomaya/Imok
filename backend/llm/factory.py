"""LLM 客户端工厂 — 根据 Provider 配置创建对应的 LLMClient 实例。

单一职责：将 ProviderEntry → LLMClient 的映射逻辑集中管理。
上层模块无需了解具体客户端类的构造细节（DIP）。

支持的 provider type：
- "openai_compatible": 通用 OpenAI Chat Completions 兼容端点
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from backend.config import LLMSettings
from backend.llm.base import LLMClient
from backend.llm.client import CompanyLLMClient
from backend.llm.provider_config import (
    ProviderEntry,
    ProvidersConfig,
    load_providers_config,
)

logger = logging.getLogger(__name__)

# 支持的 provider 类型
_SUPPORTED_TYPES = {"openai_compatible"}


def create_client_from_provider(entry: ProviderEntry) -> LLMClient:
    """根据单个 ProviderEntry 创建 LLMClient。

    Args:
        entry: 提供商配置。

    Returns:
        配置好的 LLMClient 实例。

    Raises:
        ValueError: 不支持的 provider type。
    """
    if entry.type not in _SUPPORTED_TYPES:
        raise ValueError(
            f"Unsupported provider type '{entry.type}'. "
            f"Supported: {_SUPPORTED_TYPES}"
        )

    api_token = entry.resolve_api_token()

    settings = LLMSettings(
        api_base_url=entry.base_url,
        api_key=api_token,
        model_name=entry.model,
        timeout_s=entry.timeout,
        max_retries=entry.max_retries,
    )

    client = CompanyLLMClient(
        settings=settings,
        extra_headers=entry.headers or None,
        verify_ssl=entry.ssl_verify,
    )

    logger.info(
        "Created LLM client: type=%s, url=%s, model=%s, ssl_verify=%s",
        entry.type,
        entry.base_url,
        entry.model,
        entry.ssl_verify,
    )

    return client


def create_client_from_config(
    config_path: Path,
    *,
    provider_name: Optional[str] = None,
) -> LLMClient:
    """从 YAML 配置文件创建 LLMClient。

    Args:
        config_path: llm_providers.yaml 路径。
        provider_name: 指定 provider 名称。None 则使用 default_provider。

    Returns:
        配置好的 LLMClient 实例。
    """
    config = load_providers_config(config_path)

    if provider_name:
        entry = config.get_provider(provider_name)
    else:
        entry = config.get_default()

    return create_client_from_provider(entry)
