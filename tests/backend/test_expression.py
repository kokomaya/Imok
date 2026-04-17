"""Task 2.4 闭麦辅助表达服务 — 单元测试。

覆盖：
- SceneManager: 加载/保存/增删/默认场景切换
- ExpressionAssistant: 键盘输入/语音输入/流式/超时/离线/回调
- MutePipeline: 编排/模式切换/生命周期
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import AsyncIterator, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest

from backend.asr.base import ASREngine, TranscriptionResult
from backend.expression.assistant import (
    ExpressionAssistant,
    ExpressionResult,
    ExpressionStatus,
    InputMode,
)
from backend.expression.scene_manager import Scene, SceneManager
from backend.llm.base import ChatMessage, LLMClient, LLMClientState, LLMResponse
from backend.llm.glossary import GlossaryManager
from backend.llm.prompt_manager import PromptManager
from backend.pipeline.mute_pipeline import MutePipeline, PipelineMode
from backend.translation.context_window import ContextWindow


# =========================================================================
# Fixtures & Helpers
# =========================================================================

SAMPLE_SCENES = {
    "scenes": [
        {
            "id": "internal_tech",
            "name": "跨国团队内部技术讨论会",
            "description": "跨国团队内部技术讨论会，讨论嵌入式系统和汽车软件。",
            "is_default": True,
        },
        {
            "id": "customer_meeting",
            "name": "客户交流会议",
            "description": "与外部客户的正式交流会议。",
            "is_default": False,
        },
    ]
}


class FakeLLMClient(LLMClient):
    """测试用假 LLM 客户端。"""

    def __init__(
        self,
        *,
        response: str = "I think we can optimize this approach.",
        state: LLMClientState = LLMClientState.READY,
        stream_tokens: Optional[List[str]] = None,
        raise_on_stream: Optional[Exception] = None,
        delay_s: float = 0.0,
    ):
        self._response = response
        self._state = state
        self._stream_tokens = stream_tokens or list(response)
        self._raise_on_stream = raise_on_stream
        self._delay_s = delay_s

    async def complete(self, prompt, **kwargs) -> LLMResponse:
        if self._delay_s:
            await asyncio.sleep(self._delay_s)
        return LLMResponse(content=self._response, model="test")

    async def stream(self, prompt, **kwargs) -> AsyncIterator[str]:
        if self._raise_on_stream:
            raise self._raise_on_stream
        for token in self._stream_tokens:
            if self._delay_s:
                await asyncio.sleep(self._delay_s)
            yield token

    async def close(self) -> None:
        pass

    @property
    def state(self) -> LLMClientState:
        return self._state


class FakeASREngine(ASREngine):
    """测试用假 ASR 引擎。"""

    def __init__(
        self,
        text: str = "我觉得这个方案可以优化一下",
        language: str = "zh",
        raise_on_transcribe: Optional[Exception] = None,
    ):
        self._text = text
        self._language = language
        self._raise = raise_on_transcribe

    def transcribe(self, audio, language=None) -> TranscriptionResult:
        if self._raise:
            raise self._raise
        return TranscriptionResult(text=self._text, language=self._language)

    def get_supported_languages(self):
        return ["zh", "en"]

    @property
    def sample_rate(self) -> int:
        return 16000

    @property
    def is_loaded(self) -> bool:
        return True


@pytest.fixture
def scenes_file(tmp_path: Path) -> Path:
    p = tmp_path / "scenes.json"
    p.write_text(json.dumps(SAMPLE_SCENES, ensure_ascii=False), encoding="utf-8")
    return p


@pytest.fixture
def scene_manager(scenes_file: Path) -> SceneManager:
    sm = SceneManager()
    sm.load(scenes_file)
    return sm


@pytest.fixture
def glossary() -> GlossaryManager:
    gm = GlossaryManager()
    gm.add("IPC", "IPC")
    gm.add("看门狗", "watchdog")
    return gm


@pytest.fixture
def prompt_manager() -> PromptManager:
    return PromptManager()


@pytest.fixture
def llm_client() -> FakeLLMClient:
    return FakeLLMClient()


@pytest.fixture
def asr_engine() -> FakeASREngine:
    return FakeASREngine()


@pytest.fixture
def assistant(
    llm_client: FakeLLMClient,
    scene_manager: SceneManager,
    glossary: GlossaryManager,
    prompt_manager: PromptManager,
) -> ExpressionAssistant:
    return ExpressionAssistant(
        llm_client=llm_client,
        scene_manager=scene_manager,
        glossary=glossary,
        prompt_manager=prompt_manager,
        timeout_s=5.0,
    )


# =========================================================================
# SceneManager Tests
# =========================================================================


class TestSceneManager:
    """SceneManager 单元测试。"""

    def test_load_from_json(self, scene_manager: SceneManager):
        assert scene_manager.size == 2
        assert scene_manager.contains("internal_tech")
        assert scene_manager.contains("customer_meeting")

    def test_get_scene(self, scene_manager: SceneManager):
        scene = scene_manager.get("internal_tech")
        assert scene is not None
        assert scene.name == "跨国团队内部技术讨论会"
        assert scene.is_default is True

    def test_get_nonexistent(self, scene_manager: SceneManager):
        assert scene_manager.get("nonexistent") is None

    def test_get_default(self, scene_manager: SceneManager):
        default = scene_manager.get_default()
        assert default is not None
        assert default.id == "internal_tech"

    def test_get_default_none(self):
        sm = SceneManager()
        assert sm.get_default() is None

    def test_set_default(self, scene_manager: SceneManager):
        scene_manager.set_default("customer_meeting")
        default = scene_manager.get_default()
        assert default is not None
        assert default.id == "customer_meeting"
        # 原来的默认被取消
        old = scene_manager.get("internal_tech")
        assert old is not None
        assert old.is_default is False

    def test_set_default_nonexistent(self, scene_manager: SceneManager):
        with pytest.raises(KeyError, match="not found"):
            scene_manager.set_default("nonexistent")

    def test_add_scene(self, scene_manager: SceneManager):
        new_scene = Scene(
            id="standup", name="每日站会", description="每日站会场景"
        )
        scene_manager.add(new_scene)
        assert scene_manager.size == 3
        assert scene_manager.contains("standup")

    def test_remove_scene(self, scene_manager: SceneManager):
        assert scene_manager.remove("customer_meeting") is True
        assert scene_manager.size == 1
        assert not scene_manager.contains("customer_meeting")

    def test_remove_nonexistent(self, scene_manager: SceneManager):
        assert scene_manager.remove("nonexistent") is False

    def test_list_scenes(self, scene_manager: SceneManager):
        scenes = scene_manager.list_scenes()
        assert len(scenes) == 2
        ids = {s.id for s in scenes}
        assert ids == {"internal_tech", "customer_meeting"}

    def test_save_and_reload(self, scene_manager: SceneManager, tmp_path: Path):
        scene_manager.add(Scene(id="new", name="New", description="New scene"))
        save_path = tmp_path / "saved_scenes.json"
        scene_manager.save(save_path)

        sm2 = SceneManager()
        sm2.load(save_path)
        assert sm2.size == 3
        assert sm2.contains("new")

    def test_load_invalid_format(self, tmp_path: Path):
        p = tmp_path / "bad.json"
        p.write_text("[]", encoding="utf-8")
        sm = SceneManager()
        with pytest.raises(ValueError, match="JSON object"):
            sm.load(p)

    def test_load_missing_scenes_key(self, tmp_path: Path):
        p = tmp_path / "bad.json"
        p.write_text('{"data": []}', encoding="utf-8")
        sm = SceneManager()
        with pytest.raises(ValueError, match="scenes"):
            sm.load(p)

    def test_save_no_path(self):
        sm = SceneManager()
        with pytest.raises(ValueError, match="No save path"):
            sm.save()

    def test_load_real_config(self):
        """验证实际的 config/scenes.json 可以正常加载。"""
        real_path = Path("config/scenes.json")
        if not real_path.exists():
            pytest.skip("config/scenes.json not found")
        sm = SceneManager()
        sm.load(real_path)
        assert sm.size >= 1
        assert sm.get_default() is not None


# =========================================================================
# ExpressionAssistant Tests — 键盘输入模式
# =========================================================================


class TestExpressionAssistantKeyboard:
    """ExpressionAssistant 键盘输入模式测试。"""

    @pytest.mark.asyncio
    async def test_express_text_ok(self, assistant: ExpressionAssistant):
        result = await assistant.express_text("我觉得这个方案可以优化一下")
        assert result.status == ExpressionStatus.OK
        assert result.input_mode == InputMode.KEYBOARD
        assert result.english_text  # 非空
        assert result.latency_ms >= 0
        assert result.is_streaming is True

    @pytest.mark.asyncio
    async def test_express_text_callback(self, assistant: ExpressionAssistant):
        results: list[ExpressionResult] = []
        assistant.on_result(results.append)
        await assistant.express_text("测试回调")
        assert len(results) == 1
        assert results[0].status == ExpressionStatus.OK

    @pytest.mark.asyncio
    async def test_express_text_streaming_token_callback(
        self, assistant: ExpressionAssistant
    ):
        tokens: list[str] = []
        assistant.on_streaming_token(tokens.append)
        await assistant.express_text("测试流式")
        assert len(tokens) > 0

    @pytest.mark.asyncio
    async def test_express_text_offline(
        self,
        scene_manager: SceneManager,
        glossary: GlossaryManager,
        prompt_manager: PromptManager,
    ):
        client = FakeLLMClient(state=LLMClientState.OFFLINE)
        ast = ExpressionAssistant(
            llm_client=client,
            scene_manager=scene_manager,
            glossary=glossary,
            prompt_manager=prompt_manager,
        )
        result = await ast.express_text("测试离线")
        assert result.status == ExpressionStatus.OFFLINE
        assert result.english_text == ""

    @pytest.mark.asyncio
    async def test_express_text_timeout(
        self,
        scene_manager: SceneManager,
        glossary: GlossaryManager,
        prompt_manager: PromptManager,
    ):
        client = FakeLLMClient(delay_s=5.0)
        ast = ExpressionAssistant(
            llm_client=client,
            scene_manager=scene_manager,
            glossary=glossary,
            prompt_manager=prompt_manager,
            timeout_s=0.05,
        )
        result = await ast.express_text("测试超时")
        assert result.status == ExpressionStatus.TIMEOUT

    @pytest.mark.asyncio
    async def test_express_text_error(
        self,
        scene_manager: SceneManager,
        glossary: GlossaryManager,
        prompt_manager: PromptManager,
    ):
        client = FakeLLMClient(raise_on_stream=RuntimeError("LLM error"))
        ast = ExpressionAssistant(
            llm_client=client,
            scene_manager=scene_manager,
            glossary=glossary,
            prompt_manager=prompt_manager,
        )
        result = await ast.express_text("测试错误")
        assert result.status == ExpressionStatus.ERROR

    @pytest.mark.asyncio
    async def test_context_window_updated(self, assistant: ExpressionAssistant):
        await assistant.express_text("第一句话")
        assert assistant.context.size == 1
        await assistant.express_text("第二句话")
        assert assistant.context.size == 2

    @pytest.mark.asyncio
    async def test_scene_injected_in_prompt(
        self,
        scene_manager: SceneManager,
        glossary: GlossaryManager,
        prompt_manager: PromptManager,
    ):
        """验证场景描述被注入到 Prompt 中。"""
        captured_prompts: list[str] = []

        class CaptureLLM(FakeLLMClient):
            async def stream(self, prompt, **kwargs):
                captured_prompts.append(kwargs.get("system_prompt", ""))
                yield "test"

        ast = ExpressionAssistant(
            llm_client=CaptureLLM(),
            scene_manager=scene_manager,
            glossary=glossary,
            prompt_manager=prompt_manager,
        )
        await ast.express_text("测试")
        assert len(captured_prompts) == 1
        assert "嵌入式系统" in captured_prompts[0]  # 来自场景描述

    @pytest.mark.asyncio
    async def test_glossary_injected_in_prompt(
        self,
        scene_manager: SceneManager,
        glossary: GlossaryManager,
        prompt_manager: PromptManager,
    ):
        captured_prompts: list[str] = []

        class CaptureLLM(FakeLLMClient):
            async def stream(self, prompt, **kwargs):
                captured_prompts.append(kwargs.get("system_prompt", ""))
                yield "test"

        ast = ExpressionAssistant(
            llm_client=CaptureLLM(),
            scene_manager=scene_manager,
            glossary=glossary,
            prompt_manager=prompt_manager,
        )
        await ast.express_text("测试")
        assert "watchdog" in captured_prompts[0]


# =========================================================================
# ExpressionAssistant Tests — 语音输入模式
# =========================================================================


class TestExpressionAssistantVoice:
    """ExpressionAssistant 语音输入模式测试。"""

    @pytest.mark.asyncio
    async def test_express_voice_ok(
        self,
        assistant: ExpressionAssistant,
        asr_engine: FakeASREngine,
    ):
        assistant.set_asr_engine(asr_engine)
        audio = np.zeros(16000, dtype=np.float32)
        result = await assistant.express_voice(audio)
        assert result.status == ExpressionStatus.OK
        assert result.input_mode == InputMode.VOICE
        assert result.english_text

    @pytest.mark.asyncio
    async def test_express_voice_no_asr(self, assistant: ExpressionAssistant):
        audio = np.zeros(16000, dtype=np.float32)
        with pytest.raises(RuntimeError, match="ASR engine not set"):
            await assistant.express_voice(audio)

    @pytest.mark.asyncio
    async def test_express_voice_asr_error(
        self,
        assistant: ExpressionAssistant,
    ):
        engine = FakeASREngine(raise_on_transcribe=RuntimeError("ASR failed"))
        assistant.set_asr_engine(engine)
        audio = np.zeros(16000, dtype=np.float32)
        result = await assistant.express_voice(audio)
        assert result.status == ExpressionStatus.ASR_ERROR

    @pytest.mark.asyncio
    async def test_express_voice_empty_transcription(
        self,
        assistant: ExpressionAssistant,
    ):
        engine = FakeASREngine(text="")
        assistant.set_asr_engine(engine)
        audio = np.zeros(16000, dtype=np.float32)
        result = await assistant.express_voice(audio)
        assert result.status == ExpressionStatus.OK
        assert result.source_text == ""

    @pytest.mark.asyncio
    async def test_has_asr_property(self, assistant: ExpressionAssistant):
        assert assistant.has_asr is False
        assistant.set_asr_engine(FakeASREngine())
        assert assistant.has_asr is True


# =========================================================================
# ExpressionAssistant Tests — 流式输出
# =========================================================================


class TestExpressionAssistantStreaming:
    """ExpressionAssistant 流式输出测试。"""

    @pytest.mark.asyncio
    async def test_stream_expression(self, assistant: ExpressionAssistant):
        tokens: list[str] = []
        async for token in assistant.stream_expression("测试流式输出"):
            tokens.append(token)
        assert len(tokens) > 0
        full = "".join(tokens).strip()
        assert full  # 非空

    @pytest.mark.asyncio
    async def test_stream_expression_updates_context(
        self, assistant: ExpressionAssistant
    ):
        assert assistant.context.size == 0
        tokens = []
        async for token in assistant.stream_expression("测试上下文"):
            tokens.append(token)
        assert assistant.context.size == 1


# =========================================================================
# MutePipeline Tests
# =========================================================================


class TestMutePipeline:
    """MutePipeline 单元测试。"""

    @pytest.fixture
    def pipeline(
        self,
        llm_client: FakeLLMClient,
        glossary: GlossaryManager,
        prompt_manager: PromptManager,
        scenes_file: Path,
    ) -> MutePipeline:
        return MutePipeline(
            llm_client=llm_client,
            glossary=glossary,
            prompt_manager=prompt_manager,
            scenes_path=scenes_file,
        )

    def test_initial_mode(self, pipeline: MutePipeline):
        assert pipeline.mode == PipelineMode.KEYBOARD

    def test_start_stop(self, pipeline: MutePipeline):
        assert pipeline.is_running is False
        pipeline.start()
        assert pipeline.is_running is True
        pipeline.stop()
        assert pipeline.is_running is False

    def test_start_idempotent(self, pipeline: MutePipeline):
        pipeline.start()
        pipeline.start()  # 不抛异常
        assert pipeline.is_running is True

    def test_stop_idempotent(self, pipeline: MutePipeline):
        pipeline.stop()  # 未启动时也不抛异常

    @pytest.mark.asyncio
    async def test_submit_text_not_running(self, pipeline: MutePipeline):
        with pytest.raises(RuntimeError, match="not running"):
            await pipeline.submit_text("test")

    @pytest.mark.asyncio
    async def test_submit_text_ok(self, pipeline: MutePipeline):
        pipeline.start()
        result = await pipeline.submit_text("测试键盘输入")
        assert result.status == ExpressionStatus.OK
        assert result.input_mode == InputMode.KEYBOARD

    @pytest.mark.asyncio
    async def test_submit_voice_ok(
        self, pipeline: MutePipeline, asr_engine: FakeASREngine
    ):
        pipeline.start()
        pipeline.switch_mode(PipelineMode.VOICE, asr_engine=asr_engine)
        audio = np.zeros(16000, dtype=np.float32)
        result = await pipeline.submit_voice(audio)
        assert result.status == ExpressionStatus.OK
        assert result.input_mode == InputMode.VOICE

    @pytest.mark.asyncio
    async def test_submit_voice_not_running(self, pipeline: MutePipeline):
        with pytest.raises(RuntimeError, match="not running"):
            await pipeline.submit_voice(np.zeros(16000, dtype=np.float32))

    def test_switch_mode_to_voice_no_asr(self, pipeline: MutePipeline):
        with pytest.raises(RuntimeError, match="ASR engine not available"):
            pipeline.switch_mode(PipelineMode.VOICE)

    def test_switch_mode_to_voice_with_asr(
        self, pipeline: MutePipeline, asr_engine: FakeASREngine
    ):
        pipeline.switch_mode(PipelineMode.VOICE, asr_engine=asr_engine)
        assert pipeline.mode == PipelineMode.VOICE

    def test_switch_mode_to_keyboard(
        self, pipeline: MutePipeline, asr_engine: FakeASREngine
    ):
        pipeline.switch_mode(PipelineMode.VOICE, asr_engine=asr_engine)
        pipeline.switch_mode(PipelineMode.KEYBOARD)
        assert pipeline.mode == PipelineMode.KEYBOARD

    @pytest.mark.asyncio
    async def test_on_result_callback(self, pipeline: MutePipeline):
        results: list[ExpressionResult] = []
        pipeline.on_result(results.append)
        pipeline.start()
        await pipeline.submit_text("回调测试")
        assert len(results) == 1

    @pytest.mark.asyncio
    async def test_on_streaming_token_callback(self, pipeline: MutePipeline):
        tokens: list[str] = []
        pipeline.on_streaming_token(tokens.append)
        pipeline.start()
        await pipeline.submit_text("流式回调")
        assert len(tokens) > 0

    def test_set_scene(self, pipeline: MutePipeline):
        pipeline.set_scene("customer_meeting")
        default = pipeline.scene_manager.get_default()
        assert default is not None
        assert default.id == "customer_meeting"

    def test_set_scene_nonexistent(self, pipeline: MutePipeline):
        with pytest.raises(KeyError):
            pipeline.set_scene("nonexistent")

    def test_scene_manager_property(self, pipeline: MutePipeline):
        sm = pipeline.scene_manager
        assert sm.size == 2

    @pytest.mark.asyncio
    async def test_pipeline_with_injected_scene_manager(
        self,
        llm_client: FakeLLMClient,
        glossary: GlossaryManager,
        prompt_manager: PromptManager,
        scene_manager: SceneManager,
    ):
        """使用注入的 SceneManager 而非从文件加载。"""
        pipeline = MutePipeline(
            llm_client=llm_client,
            glossary=glossary,
            prompt_manager=prompt_manager,
            scene_manager=scene_manager,
        )
        pipeline.start()
        result = await pipeline.submit_text("注入场景管理器")
        assert result.status == ExpressionStatus.OK


# =========================================================================
# 回调异常安全测试
# =========================================================================


class TestCallbackSafety:
    """验证回调异常不影响正常流程。"""

    @pytest.mark.asyncio
    async def test_result_callback_exception_safe(
        self, assistant: ExpressionAssistant
    ):
        def bad_callback(r: ExpressionResult) -> None:
            raise ValueError("callback error")

        results: list[ExpressionResult] = []
        assistant.on_result(bad_callback)
        assistant.on_result(results.append)

        result = await assistant.express_text("测试回调异常")
        # 第二个回调仍然被调用
        assert len(results) == 1
        assert result.status == ExpressionStatus.OK

    @pytest.mark.asyncio
    async def test_streaming_callback_exception_safe(
        self, assistant: ExpressionAssistant
    ):
        def bad_callback(t: str) -> None:
            raise ValueError("token callback error")

        tokens: list[str] = []
        assistant.on_streaming_token(bad_callback)
        assistant.on_streaming_token(tokens.append)

        await assistant.express_text("测试流式回调异常")
        assert len(tokens) > 0
