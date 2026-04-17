"""LLM 客户端模块单元测试。

覆盖范围：
- LLMClient ABC 接口定义
- CompanyLLMClient complete() / stream() 正常流程
- SSE Streaming 逐 token 解析
- 指数退避重试机制
- 断网检测与状态降级
- 超时处理
- 认证头注入
- ChatMessage / LLMResponse 数据类
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import AsyncIterator, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from backend.config import LLMSettings
from backend.llm.base import (
    ChatMessage,
    LLMClient,
    LLMClientState,
    LLMResponse,
)
from backend.llm.client import CompanyLLMClient, _build_messages


# =========================================================================
# Fixtures
# =========================================================================
@pytest.fixture
def default_settings() -> LLMSettings:
    return LLMSettings(
        api_base_url="http://test-llm:8080/v1",
        api_key="test-key-123",
        model_name="test-model",
        timeout_s=10.0,
        max_retries=3,
    )


@pytest.fixture
def no_retry_settings() -> LLMSettings:
    return LLMSettings(
        api_base_url="http://test-llm:8080/v1",
        api_key="test-key-123",
        model_name="test-model",
        timeout_s=5.0,
        max_retries=0,
    )


def _make_complete_response(content: str = "Hello") -> dict:
    """构造 OpenAI Chat Completions 标准响应。"""
    return {
        "id": "chatcmpl-123",
        "object": "chat.completion",
        "model": "test-model",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": 10,
            "completion_tokens": 5,
            "total_tokens": 15,
        },
    }


def _make_sse_lines(tokens: List[str], include_done: bool = True) -> str:
    """构造 SSE 响应行。"""
    lines = []
    for token in tokens:
        chunk = {
            "id": "chatcmpl-123",
            "object": "chat.completion.chunk",
            "model": "test-model",
            "choices": [
                {
                    "index": 0,
                    "delta": {"content": token},
                    "finish_reason": None,
                }
            ],
        }
        lines.append(f"data: {json.dumps(chunk)}")
        lines.append("")
    if include_done:
        lines.append("data: [DONE]")
        lines.append("")
    return "\n".join(lines)


# =========================================================================
# 1. ABC 接口测试
# =========================================================================
class TestLLMClientABC:
    """测试 LLMClient 抽象基类约束。"""

    def test_cannot_instantiate_abc(self):
        """ABC 不能直接实例化。"""
        with pytest.raises(TypeError):
            LLMClient()  # type: ignore

    def test_must_implement_all_methods(self):
        """缺少抽象方法不能实例化。"""

        class PartialClient(LLMClient):
            async def complete(self, prompt, **kw):
                return LLMResponse(content="")

            # Missing stream, close, state

        with pytest.raises(TypeError):
            PartialClient()  # type: ignore

    def test_concrete_implementation_works(self):
        """实现全部抽象方法即可实例化。"""

        class StubClient(LLMClient):
            async def complete(self, prompt, **kw):
                return LLMResponse(content="stub")

            async def stream(self, prompt, **kw) -> AsyncIterator[str]:
                yield "stub"

            async def close(self):
                pass

            @property
            def state(self):
                return LLMClientState.READY

        client = StubClient()
        assert client.state == LLMClientState.READY


# =========================================================================
# 2. 数据类测试
# =========================================================================
class TestDataClasses:
    def test_chat_message(self):
        msg = ChatMessage(role="user", content="hello")
        assert msg.role == "user"
        assert msg.content == "hello"

    def test_llm_response_defaults(self):
        resp = LLMResponse(content="test")
        assert resp.content == "test"
        assert resp.model == ""
        assert resp.usage_prompt_tokens == 0
        assert resp.finish_reason == ""

    def test_llm_client_state_values(self):
        assert LLMClientState.READY == "ready"
        assert LLMClientState.DEGRADED == "degraded"
        assert LLMClientState.OFFLINE == "offline"


# =========================================================================
# 3. _build_messages 工具函数测试
# =========================================================================
class TestBuildMessages:
    def test_simple_prompt(self):
        msgs = _build_messages("hello", None, None)
        assert len(msgs) == 1
        assert msgs[0] == {"role": "user", "content": "hello"}

    def test_with_system_prompt(self):
        msgs = _build_messages("hello", "You are a translator", None)
        assert len(msgs) == 2
        assert msgs[0]["role"] == "system"
        assert msgs[1]["role"] == "user"

    def test_messages_override(self):
        """提供 messages 时忽略 prompt 和 system_prompt。"""
        history = [
            ChatMessage(role="system", content="sys"),
            ChatMessage(role="user", content="q1"),
            ChatMessage(role="assistant", content="a1"),
        ]
        msgs = _build_messages("ignored", "ignored", history)
        assert len(msgs) == 3
        assert msgs[0]["content"] == "sys"


# =========================================================================
# 4. CompanyLLMClient - 初始化测试
# =========================================================================
class TestClientInit:
    def test_default_state_ready(self, default_settings):
        client = CompanyLLMClient(default_settings)
        assert client.state == LLMClientState.READY

    def test_api_key_in_headers(self, default_settings):
        client = CompanyLLMClient(default_settings)
        assert "Authorization" in client._client.headers
        assert client._client.headers["Authorization"] == "Bearer test-key-123"

    def test_no_api_key_no_auth_header(self):
        settings = LLMSettings(api_key="")
        client = CompanyLLMClient(settings)
        assert "Authorization" not in client._client.headers

    def test_timeout_configuration(self, default_settings):
        client = CompanyLLMClient(default_settings)
        assert client._client.timeout.read == 10.0

    @pytest.mark.asyncio
    async def test_context_manager(self, default_settings):
        async with CompanyLLMClient(default_settings) as client:
            assert client.state == LLMClientState.READY
        # close() was called — client is closed
        assert client._client.is_closed


# =========================================================================
# 5. complete() 测试
# =========================================================================
class TestComplete:
    @pytest.mark.asyncio
    async def test_complete_success(self, default_settings):
        """正常 complete 请求。"""
        mock_response = httpx.Response(
            status_code=200,
            json=_make_complete_response("translated text"),
            request=httpx.Request("POST", "http://test/v1/chat/completions"),
        )

        client = CompanyLLMClient(default_settings)
        client._client = AsyncMock(spec=httpx.AsyncClient)
        client._client.post = AsyncMock(return_value=mock_response)

        result = await client.complete("translate this")

        assert isinstance(result, LLMResponse)
        assert result.content == "translated text"
        assert result.model == "test-model"
        assert result.usage_prompt_tokens == 10
        assert result.usage_completion_tokens == 5
        assert result.finish_reason == "stop"

    @pytest.mark.asyncio
    async def test_complete_with_system_prompt(self, default_settings):
        """验证 system_prompt 被正确传递。"""
        mock_response = httpx.Response(
            status_code=200,
            json=_make_complete_response("ok"),
            request=httpx.Request("POST", "http://test/v1/chat/completions"),
        )

        client = CompanyLLMClient(default_settings)
        client._client = AsyncMock(spec=httpx.AsyncClient)
        client._client.post = AsyncMock(return_value=mock_response)

        await client.complete("hello", system_prompt="You are a translator")

        call_args = client._client.post.call_args
        payload = call_args.kwargs["json"]
        assert payload["messages"][0]["role"] == "system"
        assert payload["messages"][0]["content"] == "You are a translator"

    @pytest.mark.asyncio
    async def test_complete_with_custom_params(self, default_settings):
        """验证 temperature 和 max_tokens 传递。"""
        mock_response = httpx.Response(
            status_code=200,
            json=_make_complete_response("ok"),
            request=httpx.Request("POST", "http://test/v1/chat/completions"),
        )

        client = CompanyLLMClient(default_settings)
        client._client = AsyncMock(spec=httpx.AsyncClient)
        client._client.post = AsyncMock(return_value=mock_response)

        await client.complete("hello", temperature=0.8, max_tokens=512)

        payload = client._client.post.call_args.kwargs["json"]
        assert payload["temperature"] == 0.8
        assert payload["max_tokens"] == 512
        assert payload["stream"] is False


# =========================================================================
# 6. stream() 测试
# =========================================================================
class TestStream:
    @pytest.mark.asyncio
    async def test_stream_collects_tokens(self, default_settings):
        """SSE Streaming 正确收集 tokens。"""
        tokens = ["Hello", " ", "World", "!"]
        sse_body = _make_sse_lines(tokens)

        client = CompanyLLMClient(default_settings)

        # Create a mock stream context manager
        mock_response = AsyncMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.aiter_lines = self._make_aiter_lines(sse_body)

        mock_stream_ctx = AsyncMock()
        mock_stream_ctx.__aenter__ = AsyncMock(return_value=mock_response)
        mock_stream_ctx.__aexit__ = AsyncMock(return_value=False)

        client._client = AsyncMock(spec=httpx.AsyncClient)
        client._client.stream = MagicMock(return_value=mock_stream_ctx)

        collected = []
        async for token in client.stream("test"):
            collected.append(token)

        assert collected == tokens
        assert "".join(collected) == "Hello World!"

    @pytest.mark.asyncio
    async def test_stream_ignores_non_data_lines(self, default_settings):
        """SSE 忽略非 data: 开头的行。"""
        sse_body = ": keepalive\n\ndata: " + json.dumps({
            "choices": [{"delta": {"content": "ok"}, "index": 0}]
        }) + "\n\ndata: [DONE]\n"

        client = CompanyLLMClient(default_settings)

        mock_response = AsyncMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.aiter_lines = self._make_aiter_lines(sse_body)

        mock_stream_ctx = AsyncMock()
        mock_stream_ctx.__aenter__ = AsyncMock(return_value=mock_response)
        mock_stream_ctx.__aexit__ = AsyncMock(return_value=False)

        client._client = AsyncMock(spec=httpx.AsyncClient)
        client._client.stream = MagicMock(return_value=mock_stream_ctx)

        collected = []
        async for token in client.stream("test"):
            collected.append(token)

        assert collected == ["ok"]

    @pytest.mark.asyncio
    async def test_stream_handles_empty_delta(self, default_settings):
        """SSE 中 delta 没有 content 字段时跳过。"""
        chunk_no_content = {
            "choices": [{"delta": {"role": "assistant"}, "index": 0}]
        }
        chunk_with_content = {
            "choices": [{"delta": {"content": "hello"}, "index": 0}]
        }
        sse_body = (
            f"data: {json.dumps(chunk_no_content)}\n\n"
            f"data: {json.dumps(chunk_with_content)}\n\n"
            "data: [DONE]\n"
        )

        client = CompanyLLMClient(default_settings)

        mock_response = AsyncMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.aiter_lines = self._make_aiter_lines(sse_body)

        mock_stream_ctx = AsyncMock()
        mock_stream_ctx.__aenter__ = AsyncMock(return_value=mock_response)
        mock_stream_ctx.__aexit__ = AsyncMock(return_value=False)

        client._client = AsyncMock(spec=httpx.AsyncClient)
        client._client.stream = MagicMock(return_value=mock_stream_ctx)

        collected = []
        async for token in client.stream("test"):
            collected.append(token)

        assert collected == ["hello"]

    @staticmethod
    def _make_aiter_lines(sse_body: str):
        """创建 async line iterator mock。"""
        lines = sse_body.split("\n")

        async def aiter_lines():
            for line in lines:
                yield line

        return aiter_lines


# =========================================================================
# 7. 重试机制测试
# =========================================================================
class TestRetry:
    @pytest.mark.asyncio
    async def test_complete_retry_on_server_error(self, default_settings):
        """服务端 500 错误触发重试，最终成功。"""
        error_response = httpx.Response(
            status_code=500,
            text="Internal Server Error",
            request=httpx.Request("POST", "http://test/v1/chat/completions"),
        )
        success_response = httpx.Response(
            status_code=200,
            json=_make_complete_response("recovered"),
            request=httpx.Request("POST", "http://test/v1/chat/completions"),
        )

        client = CompanyLLMClient(default_settings)
        client._client = AsyncMock(spec=httpx.AsyncClient)
        client._client.post = AsyncMock(
            side_effect=[error_response, error_response, success_response]
        )

        # Patch sleep to avoid real delays
        with patch("backend.llm.client.asyncio.sleep", new_callable=AsyncMock):
            result = await client.complete("test")

        assert result.content == "recovered"
        assert client._client.post.call_count == 3

    @pytest.mark.asyncio
    async def test_complete_exhausts_retries(self, default_settings):
        """所有重试耗尽后抛出 ConnectionError。"""
        error_response = httpx.Response(
            status_code=503,
            text="Service Unavailable",
            request=httpx.Request("POST", "http://test/v1/chat/completions"),
        )

        client = CompanyLLMClient(default_settings)
        client._client = AsyncMock(spec=httpx.AsyncClient)
        client._client.post = AsyncMock(return_value=error_response)

        with patch("backend.llm.client.asyncio.sleep", new_callable=AsyncMock):
            with pytest.raises(ConnectionError, match="failed after 4 attempts"):
                await client.complete("test")

        # 1 initial + 3 retries = 4 attempts
        assert client._client.post.call_count == 4

    @pytest.mark.asyncio
    async def test_no_retry_when_max_retries_zero(self, no_retry_settings):
        """max_retries=0 时不重试。"""
        error_response = httpx.Response(
            status_code=500,
            text="Error",
            request=httpx.Request("POST", "http://test/v1/chat/completions"),
        )

        client = CompanyLLMClient(no_retry_settings)
        client._client = AsyncMock(spec=httpx.AsyncClient)
        client._client.post = AsyncMock(return_value=error_response)

        with pytest.raises(ConnectionError, match="failed after 1 attempts"):
            await client.complete("test")

        assert client._client.post.call_count == 1

    @pytest.mark.asyncio
    async def test_retry_on_network_error(self, default_settings):
        """网络连接错误触发重试。"""
        success_response = httpx.Response(
            status_code=200,
            json=_make_complete_response("ok"),
            request=httpx.Request("POST", "http://test/v1/chat/completions"),
        )

        client = CompanyLLMClient(default_settings)
        client._client = AsyncMock(spec=httpx.AsyncClient)
        client._client.post = AsyncMock(
            side_effect=[
                httpx.ConnectError("Connection refused"),
                success_response,
            ]
        )

        with patch("backend.llm.client.asyncio.sleep", new_callable=AsyncMock):
            result = await client.complete("test")

        assert result.content == "ok"

    def test_backoff_delay_exponential(self, default_settings):
        """指数退避延迟递增。"""
        client = CompanyLLMClient(default_settings)
        d0 = client._backoff_delay(0)
        d1 = client._backoff_delay(1)
        d2 = client._backoff_delay(2)
        d3 = client._backoff_delay(3)

        # Base: 1, 2, 4, 8 + jitter [0, 0.5]
        assert 1.0 <= d0 <= 1.5
        assert 2.0 <= d1 <= 2.5
        assert 4.0 <= d2 <= 4.5
        assert 8.0 <= d3 <= 8.5  # capped at 8

    def test_backoff_delay_capped(self, default_settings):
        """高 attempt 值不超过 cap。"""
        client = CompanyLLMClient(default_settings)
        d10 = client._backoff_delay(10)
        assert d10 <= 8.5  # cap=8 + max_jitter=0.5


# =========================================================================
# 8. 断网检测与降级状态测试
# =========================================================================
class TestConnectivityState:
    @pytest.mark.asyncio
    async def test_state_degrades_on_failure(self, default_settings):
        """单次失败 → DEGRADED。"""
        client = CompanyLLMClient(default_settings)
        assert client.state == LLMClientState.READY

        client._on_failure(Exception("network"))
        assert client.state == LLMClientState.DEGRADED

    @pytest.mark.asyncio
    async def test_state_offline_after_three_failures(self, default_settings):
        """连续 3 次失败 → OFFLINE。"""
        client = CompanyLLMClient(default_settings)

        client._on_failure(Exception("1"))
        client._on_failure(Exception("2"))
        assert client.state == LLMClientState.DEGRADED

        client._on_failure(Exception("3"))
        assert client.state == LLMClientState.OFFLINE

    @pytest.mark.asyncio
    async def test_state_recovers_on_success(self, default_settings):
        """成功后状态恢复 → READY。"""
        client = CompanyLLMClient(default_settings)

        # Go offline
        for _ in range(3):
            client._on_failure(Exception("fail"))
        assert client.state == LLMClientState.OFFLINE

        # Recover
        client._on_success()
        assert client.state == LLMClientState.READY
        assert client._consecutive_failures == 0

    @pytest.mark.asyncio
    async def test_failure_counter_resets_on_success(self, default_settings):
        """成功后连续失败计数器重置。"""
        client = CompanyLLMClient(default_settings)

        client._on_failure(Exception("1"))
        client._on_failure(Exception("2"))
        assert client._consecutive_failures == 2

        client._on_success()
        assert client._consecutive_failures == 0

    @pytest.mark.asyncio
    async def test_state_tracks_through_complete(self, no_retry_settings):
        """complete 失败后状态正确更新。"""
        error_response = httpx.Response(
            status_code=500,
            text="Error",
            request=httpx.Request("POST", "http://test/v1/chat/completions"),
        )

        client = CompanyLLMClient(no_retry_settings)
        client._client = AsyncMock(spec=httpx.AsyncClient)
        client._client.post = AsyncMock(return_value=error_response)

        with pytest.raises(ConnectionError):
            await client.complete("test")

        assert client.state == LLMClientState.DEGRADED


# =========================================================================
# 9. 超时处理测试
# =========================================================================
class TestTimeout:
    @pytest.mark.asyncio
    async def test_complete_timeout_raises(self, no_retry_settings):
        """超时异常触发 ConnectionError。"""
        client = CompanyLLMClient(no_retry_settings)
        client._client = AsyncMock(spec=httpx.AsyncClient)
        client._client.post = AsyncMock(
            side_effect=httpx.ReadTimeout("Read timed out")
        )

        with pytest.raises(ConnectionError):
            await client.complete("test")

    @pytest.mark.asyncio
    async def test_stream_timeout_raises(self, no_retry_settings):
        """stream 超时异常触发 ConnectionError。"""
        client = CompanyLLMClient(no_retry_settings)

        mock_stream_ctx = AsyncMock()
        mock_stream_ctx.__aenter__ = AsyncMock(
            side_effect=httpx.ReadTimeout("Read timed out")
        )
        mock_stream_ctx.__aexit__ = AsyncMock(return_value=False)

        client._client = AsyncMock(spec=httpx.AsyncClient)
        client._client.stream = MagicMock(return_value=mock_stream_ctx)

        with pytest.raises(ConnectionError):
            async for _ in client.stream("test"):
                pass


# =========================================================================
# 10. 首 token 延迟测试
# =========================================================================
class TestFirstTokenLatency:
    @pytest.mark.asyncio
    async def test_first_token_arrives_quickly(self, default_settings):
        """验证 stream 首 token 可快速到达（模拟场景）。"""
        tokens = ["First", " token", " here"]
        sse_body = _make_sse_lines(tokens)

        client = CompanyLLMClient(default_settings)

        mock_response = AsyncMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.aiter_lines = TestStream._make_aiter_lines(sse_body)

        mock_stream_ctx = AsyncMock()
        mock_stream_ctx.__aenter__ = AsyncMock(return_value=mock_response)
        mock_stream_ctx.__aexit__ = AsyncMock(return_value=False)

        client._client = AsyncMock(spec=httpx.AsyncClient)
        client._client.stream = MagicMock(return_value=mock_stream_ctx)

        start = time.monotonic()
        first_token = None
        async for token in client.stream("translate"):
            if first_token is None:
                first_token = token
                latency = time.monotonic() - start
            break

        assert first_token == "First"
        # Mock 场景下首 token 延迟应极低（< 100ms）
        assert latency < 0.1


# =========================================================================
# 11. stream() 重试测试
# =========================================================================
class TestStreamRetry:
    @pytest.mark.asyncio
    async def test_stream_retries_on_connection_error(self, default_settings):
        """stream 连接错误触发重试，最终成功。"""
        tokens = ["OK"]
        sse_body = _make_sse_lines(tokens)

        mock_response_ok = AsyncMock()
        mock_response_ok.raise_for_status = MagicMock()
        mock_response_ok.aiter_lines = TestStream._make_aiter_lines(sse_body)

        mock_stream_fail = AsyncMock()
        mock_stream_fail.__aenter__ = AsyncMock(
            side_effect=httpx.ConnectError("refused")
        )
        mock_stream_fail.__aexit__ = AsyncMock(return_value=False)

        mock_stream_ok = AsyncMock()
        mock_stream_ok.__aenter__ = AsyncMock(return_value=mock_response_ok)
        mock_stream_ok.__aexit__ = AsyncMock(return_value=False)

        client = CompanyLLMClient(default_settings)
        client._client = AsyncMock(spec=httpx.AsyncClient)
        client._client.stream = MagicMock(
            side_effect=[mock_stream_fail, mock_stream_ok]
        )

        with patch("backend.llm.client.asyncio.sleep", new_callable=AsyncMock):
            collected = []
            async for token in client.stream("test"):
                collected.append(token)

        assert collected == ["OK"]
