"""闭麦辅助流水线 — 编排闭麦表达辅助的完整链路。

单一职责：组合 ExpressionAssistant、SceneManager 等组件，管理生命周期和输入模式切换。
不负责具体的表达转换逻辑（ExpressionAssistant）或场景管理（SceneManager）。

设计原则：
- DIP：依赖抽象接口（LLMClient、ASREngine）
- SRP：只负责流水线编排和模式切换
"""

from __future__ import annotations

import logging
from enum import Enum
from pathlib import Path
from typing import Optional

import numpy as np

from backend.asr.base import ASREngine
from backend.expression.assistant import (
    ExpressionAssistant,
    ExpressionCallback,
    ExpressionResult,
    InputMode,
    StreamingTokenCallback,
)
from backend.expression.scene_manager import SceneManager
from backend.llm.base import LLMClient
from backend.llm.glossary import GlossaryManager
from backend.llm.prompt_manager import PromptManager
from backend.translation.context_window import ContextWindow

logger = logging.getLogger(__name__)


class PipelineMode(str, Enum):
    """闭麦流水线当前输入模式。"""

    KEYBOARD = "keyboard"
    VOICE = "voice"


class MutePipeline:
    """闭麦辅助流水线。

    编排完整的闭麦辅助链路，支持在键盘和语音输入模式之间切换。

    使用方式：
        pipeline = MutePipeline(
            llm_client=client,
            glossary=glossary,
            prompt_manager=pm,
            scenes_path=Path("config/scenes.json"),
        )
        pipeline.on_result(callback)
        pipeline.start()

        # 键盘模式
        result = await pipeline.submit_text("我觉得这个方案可以优化")

        # 切换到语音模式
        pipeline.switch_mode(PipelineMode.VOICE, asr_engine=engine)
        result = await pipeline.submit_voice(audio_data)

        pipeline.stop()
    """

    def __init__(
        self,
        llm_client: LLMClient,
        glossary: GlossaryManager,
        prompt_manager: PromptManager,
        *,
        scenes_path: Optional[Path] = None,
        scene_manager: Optional[SceneManager] = None,
        asr_engine: Optional[ASREngine] = None,
        context_window: Optional[ContextWindow] = None,
        timeout_s: float = 10.0,
    ) -> None:
        # 场景管理器
        if scene_manager is not None:
            self._scene_manager = scene_manager
        else:
            self._scene_manager = SceneManager()
            if scenes_path and scenes_path.exists():
                self._scene_manager.load(scenes_path)

        self._context = context_window or ContextWindow()

        # 表达助手
        self._assistant = ExpressionAssistant(
            llm_client=llm_client,
            scene_manager=self._scene_manager,
            glossary=glossary,
            prompt_manager=prompt_manager,
            asr_engine=asr_engine,
            context_window=self._context,
            timeout_s=timeout_s,
        )

        self._mode = PipelineMode.KEYBOARD
        self._running = False

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------
    def start(self) -> None:
        """启动闭麦流水线。"""
        if self._running:
            return
        self._running = True
        logger.info("MutePipeline started (mode=%s)", self._mode.value)

    def stop(self) -> None:
        """停止闭麦流水线。"""
        if not self._running:
            return
        self._running = False
        logger.info("MutePipeline stopped")

    # ------------------------------------------------------------------
    # 输入模式切换
    # ------------------------------------------------------------------
    @property
    def mode(self) -> PipelineMode:
        return self._mode

    def switch_mode(
        self,
        mode: PipelineMode,
        *,
        asr_engine: Optional[ASREngine] = None,
    ) -> None:
        """切换输入模式。

        切换到语音模式时需提供 ASR 引擎（若尚未设置）。

        Raises:
            RuntimeError: 切换到语音模式但未提供也未预设 ASR 引擎。
        """
        if mode == PipelineMode.VOICE:
            if asr_engine is not None:
                self._assistant.set_asr_engine(asr_engine)
            elif not self._assistant.has_asr:
                raise RuntimeError(
                    "Cannot switch to voice mode: ASR engine not available. "
                    "Provide asr_engine parameter."
                )
        self._mode = mode
        logger.info("MutePipeline mode switched to: %s", mode.value)

    # ------------------------------------------------------------------
    # 输入提交
    # ------------------------------------------------------------------
    async def submit_text(self, text: str) -> ExpressionResult:
        """提交键盘文本输入。

        Raises:
            RuntimeError: 流水线未启动。
        """
        self._check_running()
        return await self._assistant.express_text(text)

    async def submit_voice(self, audio: np.ndarray) -> ExpressionResult:
        """提交语音音频输入。

        Raises:
            RuntimeError: 流水线未启动或未设置 ASR 引擎。
        """
        self._check_running()
        return await self._assistant.express_voice(audio)

    # ------------------------------------------------------------------
    # 回调代理
    # ------------------------------------------------------------------
    def on_result(self, callback: ExpressionCallback) -> None:
        """注册表达结果回调。"""
        self._assistant.on_result(callback)

    def on_streaming_token(self, callback: StreamingTokenCallback) -> None:
        """注册流式 token 回调。"""
        self._assistant.on_streaming_token(callback)

    # ------------------------------------------------------------------
    # 场景管理代理
    # ------------------------------------------------------------------
    @property
    def scene_manager(self) -> SceneManager:
        return self._scene_manager

    def set_scene(self, scene_id: str) -> None:
        """切换当前默认场景。"""
        self._scene_manager.set_default(scene_id)

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------
    def _check_running(self) -> None:
        if not self._running:
            raise RuntimeError("MutePipeline is not running. Call start() first.")

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def assistant(self) -> ExpressionAssistant:
        return self._assistant
