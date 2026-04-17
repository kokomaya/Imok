"""LLM Provider 配置模型 — 从 YAML 文件加载多提供商配置。

单一职责：定义 provider 配置结构 + YAML 文件读取/校验。
不负责客户端创建（由 factory.py 负责）。

配置层级：
    llm_providers.yaml (provider 选择 + 连接参数)
    .env              (API_TOKEN 等敏感凭证)
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Optional

import yaml

logger = logging.getLogger(__name__)


@dataclass
class ProviderEntry:
    """单个 LLM 提供商的配置。"""

    type: str  # "openai_compatible"
    base_url: str
    model: str
    api_token_env: str = ""  # .env 中密钥变量名，如 "API_TOKEN"
    headers: Dict[str, str] = field(default_factory=dict)
    timeout: float = 60.0
    max_retries: int = 2
    stream: bool = False
    ssl_verify: bool = True

    def resolve_api_token(self) -> str:
        """从环境变量解析 API token。

        如果 api_token_env 为空或环境变量未设置，返回空字符串。
        """
        if not self.api_token_env:
            return ""
        return os.environ.get(self.api_token_env, "")


@dataclass
class ProvidersConfig:
    """多提供商配置聚合。"""

    default_provider: str
    providers: Dict[str, ProviderEntry] = field(default_factory=dict)

    def get_default(self) -> ProviderEntry:
        """获取默认 provider 配置。

        Raises:
            KeyError: 默认 provider 名称不在 providers 中。
        """
        if self.default_provider not in self.providers:
            raise KeyError(
                f"Default provider '{self.default_provider}' not found. "
                f"Available: {list(self.providers.keys())}"
            )
        return self.providers[self.default_provider]

    def get_provider(self, name: str) -> ProviderEntry:
        """按名称获取 provider 配置。

        Raises:
            KeyError: provider 名称不存在。
        """
        if name not in self.providers:
            raise KeyError(
                f"Provider '{name}' not found. "
                f"Available: {list(self.providers.keys())}"
            )
        return self.providers[name]


def load_providers_config(path: Path) -> ProvidersConfig:
    """从 YAML 文件加载 provider 配置。

    Args:
        path: YAML 文件路径。

    Returns:
        ProvidersConfig 实例。

    Raises:
        FileNotFoundError: 文件不存在。
        ValueError: YAML 格式错误或缺少必要字段。
    """
    if not path.exists():
        raise FileNotFoundError(f"Provider config not found: {path}")

    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    if not isinstance(raw, dict):
        raise ValueError(f"Invalid provider config: expected dict, got {type(raw).__name__}")

    default_provider = raw.get("default_provider")
    if not default_provider:
        raise ValueError("Missing 'default_provider' in provider config")

    raw_providers = raw.get("providers")
    if not isinstance(raw_providers, dict) or not raw_providers:
        raise ValueError("Missing or empty 'providers' in provider config")

    providers: Dict[str, ProviderEntry] = {}
    for name, entry_raw in raw_providers.items():
        if not isinstance(entry_raw, dict):
            raise ValueError(f"Provider '{name}': expected dict, got {type(entry_raw).__name__}")

        required = ("type", "base_url", "model")
        for key in required:
            if key not in entry_raw:
                raise ValueError(f"Provider '{name}': missing required field '{key}'")

        providers[name] = ProviderEntry(
            type=str(entry_raw["type"]),
            base_url=str(entry_raw["base_url"]),
            model=str(entry_raw["model"]),
            api_token_env=str(entry_raw.get("api_token_env", "")),
            headers={str(k): str(v) for k, v in entry_raw.get("headers", {}).items()},
            timeout=float(entry_raw.get("timeout", 60.0)),
            max_retries=int(entry_raw.get("max_retries", 2)),
            stream=bool(entry_raw.get("stream", False)),
            ssl_verify=bool(entry_raw.get("ssl_verify", True)),
        )

    logger.info(
        "Loaded %d provider(s) from %s, default='%s'",
        len(providers),
        path,
        default_provider,
    )

    return ProvidersConfig(default_provider=default_provider, providers=providers)
