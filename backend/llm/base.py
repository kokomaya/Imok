"""LLM 客户端抽象基类 — 定义大模型调用的统一接口（OCP: 开放封闭）。

所有具体 LLM 客户端（公司 API、OpenAI、本地模型等）都必须实现此接口，
上层模块（Translator、ExpressionAssistant）仅依赖此抽象（DIP: 依赖倒置）。

设计决策：
- complete() 用于一次性获取完整响应（总结等场景）
- stream() 用于流式获取响应（翻译、表达辅助等低延迟场景）
- 支持 system_prompt 参数以注入角色/术语表上下文
- 支持 messages 参数以传入完整对话历史
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import AsyncIterator, List, Optional

logger = logging.getLogger(__name__)


class LLMClientState(str, Enum):
    """客户端连接状态。"""

    READY = "ready"
    DEGRADED = "degraded"  # 网络不稳定，部分功能可用
    OFFLINE = "offline"  # 完全不可用


@dataclass
class ChatMessage:
    """对话消息。"""

    role: str  # "system" | "user" | "assistant"
    content: str


@dataclass
class LLMResponse:
    """LLM 完整响应。"""

    content: str
    model: str = ""
    usage_prompt_tokens: int = 0
    usage_completion_tokens: int = 0
    finish_reason: str = ""


class LLMClient(ABC):
    """LLM 客户端抽象基类。

    使用方式（里氏替换 — 可替换任何具体实现）：
        client: LLMClient = CompanyLLMClient(settings)
        result = await client.complete("翻译这段话：...")
        async for token in client.stream("翻译这段话：..."):
            print(token, end="", flush=True)
    """

    @abstractmethod
    async def complete(
        self,
        prompt: str,
        *,
        system_prompt: Optional[str] = None,
        messages: Optional[List[ChatMessage]] = None,
        temperature: float = 0.3,
        max_tokens: int = 2048,
    ) -> LLMResponse:
        """一次性获取完整 LLM 响应。

        Args:
            prompt: 用户 prompt。如果提供 messages 则忽略此参数。
            system_prompt: 系统 prompt（角色设定、术语表等）。
            messages: 完整对话历史。提供时忽略 prompt/system_prompt。
            temperature: 采样温度。翻译场景建议 0.1-0.3。
            max_tokens: 最大生成 token 数。

        Returns:
            LLMResponse 包含完整文本和 token 用量。
        """

    @abstractmethod
    async def stream(
        self,
        prompt: str,
        *,
        system_prompt: Optional[str] = None,
        messages: Optional[List[ChatMessage]] = None,
        temperature: float = 0.3,
        max_tokens: int = 2048,
    ) -> AsyncIterator[str]:
        """流式获取 LLM 响应（逐 token）。

        Args:
            与 complete() 相同。

        Yields:
            逐个 token 的文本片段。
        """
        # Trick to make this an async generator in ABC
        yield ""  # pragma: no cover

    @abstractmethod
    async def close(self) -> None:
        """关闭客户端，释放连接池等资源。"""

    @property
    @abstractmethod
    def state(self) -> LLMClientState:
        """当前连接状态。"""

    async def __aenter__(self) -> "LLMClient":
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()
