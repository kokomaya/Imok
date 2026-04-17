"""闭麦表达助手 — 将中文输入转化为地道英文会议表达。

单一职责：编排表达辅助流程（输入 → Prompt 构建 → LLM Streaming → 回调分发）。
不负责场景管理（SceneManager）、Prompt 模板（PromptManager）、
术语表（GlossaryManager）或 LLM 通信（LLMClient）。

设计原则：
- DIP：依赖 LLMClient / ASREngine 抽象接口
- SRP：只负责表达辅助编排
- 支持键盘输入和麦克风语音输入两种模式
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from enum import Enum
from typing import AsyncIterator, Callable, List, Optional

import numpy as np

from backend.asr.base import ASREngine
from backend.llm.base import LLMClient, LLMClientState
from backend.llm.glossary import GlossaryManager
from backend.llm.prompt_manager import PromptManager
from backend.expression.scene_manager import SceneManager
from backend.translation.context_window import ContextWindow

logger = logging.getLogger(__name__)


class InputMode(str, Enum):
    """输入模式。"""

    KEYBOARD = "keyboard"
    VOICE = "voice"


class ExpressionStatus(str, Enum):
    """表达结果状态。"""

    OK = "ok"
    TIMEOUT = "timeout"
    OFFLINE = "offline"
    ASR_ERROR = "asr_error"
    ERROR = "error"


@dataclass
class ExpressionResult:
    """表达辅助结果。"""

    source_text: str
    english_text: str
    status: ExpressionStatus
    input_mode: InputMode
    latency_ms: float = 0.0
    is_streaming: bool = False

    @property
    def is_degraded(self) -> bool:
        return self.status != ExpressionStatus.OK


# 回调类型
ExpressionCallback = Callable[[ExpressionResult], None]
# 流式回调：每次收到一个 token 时调用
StreamingTokenCallback = Callable[[str], None]


class ExpressionAssistant:
    """闭麦辅助表达助手。

    支持两种输入模式：
    - 键盘模式：直接文本 → LLM Streaming → 英文表达
    - 语音模式：音频 → ASR → 文本 → LLM Streaming → 英文表达

    使用方式：
        assistant = ExpressionAssistant(
            llm_client=client,
            scene_manager=scene_manager,
            glossary=glossary_manager,
            prompt_manager=prompt_manager,
        )
        assistant.on_result(callback_fn)

        # 键盘模式
        await assistant.express_text("我觉得这个方案可以优化一下")

        # 语音模式（需注入 ASR 引擎）
        assistant.set_asr_engine(whisper_engine)
        await assistant.express_voice(audio_data)
    """

    def __init__(
        self,
        llm_client: LLMClient,
        scene_manager: SceneManager,
        glossary: GlossaryManager,
        prompt_manager: PromptManager,
        *,
        asr_engine: Optional[ASREngine] = None,
        context_window: Optional[ContextWindow] = None,
        timeout_s: float = 10.0,
    ) -> None:
        self._llm = llm_client
        self._scene_manager = scene_manager
        self._glossary = glossary
        self._prompt_manager = prompt_manager
        self._asr: Optional[ASREngine] = asr_engine
        self._context = context_window or ContextWindow()
        self._timeout_s = timeout_s

        self._callbacks: List[ExpressionCallback] = []
        self._streaming_callbacks: List[StreamingTokenCallback] = []

    # ------------------------------------------------------------------
    # 依赖注入
    # ------------------------------------------------------------------
    def set_asr_engine(self, engine: ASREngine) -> None:
        """设置语音输入所用的 ASR 引擎。"""
        self._asr = engine

    # ------------------------------------------------------------------
    # 回调注册
    # ------------------------------------------------------------------
    def on_result(self, callback: ExpressionCallback) -> None:
        """注册表达结果回调。"""
        self._callbacks.append(callback)

    def on_streaming_token(self, callback: StreamingTokenCallback) -> None:
        """注册流式 token 回调（用于逐字显示）。"""
        self._streaming_callbacks.append(callback)

    # ------------------------------------------------------------------
    # 键盘输入模式
    # ------------------------------------------------------------------
    async def express_text(self, text: str) -> ExpressionResult:
        """键盘输入模式：中文文本 → 英文表达。"""
        return await self._do_expression(text, InputMode.KEYBOARD)

    # ------------------------------------------------------------------
    # 语音输入模式
    # ------------------------------------------------------------------
    async def express_voice(self, audio: np.ndarray) -> ExpressionResult:
        """语音输入模式：音频 → ASR → 英文表达。

        Raises:
            RuntimeError: 未设置 ASR 引擎。
        """
        if self._asr is None:
            raise RuntimeError("ASR engine not set. Call set_asr_engine() first.")

        start_time = time.monotonic()

        try:
            result = self._asr.transcribe(audio)
        except Exception as exc:
            logger.error("ASR transcription failed: %s", exc)
            asr_result = ExpressionResult(
                source_text="",
                english_text="",
                status=ExpressionStatus.ASR_ERROR,
                input_mode=InputMode.VOICE,
                latency_ms=(time.monotonic() - start_time) * 1000,
            )
            self._notify(asr_result)
            return asr_result

        if result.is_empty:
            empty_result = ExpressionResult(
                source_text="",
                english_text="",
                status=ExpressionStatus.OK,
                input_mode=InputMode.VOICE,
                latency_ms=(time.monotonic() - start_time) * 1000,
            )
            self._notify(empty_result)
            return empty_result

        return await self._do_expression(result.text, InputMode.VOICE)

    # ------------------------------------------------------------------
    # 流式表达
    # ------------------------------------------------------------------
    async def stream_expression(self, text: str) -> AsyncIterator[str]:
        """流式获取英文表达（逐 token yield）。

        用于前端逐字显示场景。完成后自动更新上下文窗口。
        """
        system_prompt, user_prompt = self._build_prompts(text)

        chunks: list[str] = []
        async for token in self._llm.stream(
            user_prompt,
            system_prompt=system_prompt,
            temperature=0.3,
            max_tokens=1024,
        ):
            chunks.append(token)
            yield token

        full_text = "".join(chunks).strip()
        if full_text:
            self._context.add(text, full_text)

    # ------------------------------------------------------------------
    # 核心表达逻辑
    # ------------------------------------------------------------------
    async def _do_expression(self, text: str, mode: InputMode) -> ExpressionResult:
        """执行表达辅助并通知回调。"""
        # LLM 离线检查
        if self._llm.state == LLMClientState.OFFLINE:
            result = ExpressionResult(
                source_text=text,
                english_text="",
                status=ExpressionStatus.OFFLINE,
                input_mode=mode,
            )
            self._notify(result)
            return result

        system_prompt, user_prompt = self._build_prompts(text)
        start_time = time.monotonic()

        try:
            english = await asyncio.wait_for(
                self._stream_and_collect(text, system_prompt, user_prompt),
                timeout=self._timeout_s,
            )

            latency_ms = (time.monotonic() - start_time) * 1000
            self._context.add(text, english)

            result = ExpressionResult(
                source_text=text,
                english_text=english,
                status=ExpressionStatus.OK,
                input_mode=mode,
                latency_ms=latency_ms,
                is_streaming=True,
            )

        except asyncio.TimeoutError:
            latency_ms = (time.monotonic() - start_time) * 1000
            logger.warning(
                "Expression timeout (%.0fms): %s", latency_ms, text[:50]
            )
            result = ExpressionResult(
                source_text=text,
                english_text="",
                status=ExpressionStatus.TIMEOUT,
                input_mode=mode,
                latency_ms=latency_ms,
            )

        except Exception as exc:
            latency_ms = (time.monotonic() - start_time) * 1000
            logger.error("Expression error: %s", exc)
            result = ExpressionResult(
                source_text=text,
                english_text="",
                status=ExpressionStatus.ERROR,
                input_mode=mode,
                latency_ms=latency_ms,
            )

        self._notify(result)
        return result

    def _build_prompts(self, text: str) -> tuple[str, str]:
        """构建表达辅助 Prompt。"""
        glossary_str = self._glossary.format_for_prompt()
        context_str = self._context.format_for_prompt()

        default_scene = self._scene_manager.get_default()
        scene_desc = default_scene.description if default_scene else ""

        return self._prompt_manager.render_expression(
            text=text,
            glossary=glossary_str,
            recent_context=context_str,
            scene_description=scene_desc,
        )

    async def _stream_and_collect(
        self, source_text: str, system_prompt: str, user_prompt: str
    ) -> str:
        """通过 LLM Streaming 获取英文表达。"""
        chunks: list[str] = []
        async for token in self._llm.stream(
            user_prompt,
            system_prompt=system_prompt,
            temperature=0.3,
            max_tokens=1024,
        ):
            chunks.append(token)
            self._notify_streaming_token(token)

        return "".join(chunks).strip()

    # ------------------------------------------------------------------
    # 回调通知
    # ------------------------------------------------------------------
    def _notify(self, result: ExpressionResult) -> None:
        """通知所有结果回调。"""
        for callback in self._callbacks:
            try:
                callback(result)
            except Exception:
                logger.exception("Expression callback error")

    def _notify_streaming_token(self, token: str) -> None:
        """通知所有流式 token 回调。"""
        for callback in self._streaming_callbacks:
            try:
                callback(token)
            except Exception:
                logger.exception("Streaming token callback error")

    # ------------------------------------------------------------------
    # 状态查询
    # ------------------------------------------------------------------
    @property
    def context(self) -> ContextWindow:
        return self._context

    @property
    def has_asr(self) -> bool:
        return self._asr is not None
