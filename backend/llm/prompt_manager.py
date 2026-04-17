"""Prompt 模板管理器 — 管理翻译、闭麦表达、总结等 Prompt 模板。

单一职责：只负责 Prompt 模板的定义、变量注入和渲染。
不负责术语表管理（由 GlossaryManager 负责）、不负责 LLM 调用（由 LLMClient 负责）。

设计决策：
- 模板使用 Python str.format_map() 进行变量替换，变量用 {name} 标记
- 未提供的变量保留原始占位符（通过 SafeDict）
- 内置翻译/表达/总结三类 Prompt 模板，支持外部覆盖
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, Optional

logger = logging.getLogger(__name__)


class _SafeDict(dict):
    """format_map 时，缺失的 key 保留原始 {key} 占位符而非抛异常。"""

    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


# =========================================================================
# 内置 Prompt 模板
# =========================================================================

_TRANSLATION_SYSTEM_PROMPT = """\
你是一个专业的会议实时翻译助手。你的任务是将会议中的发言翻译为{target_language}。

要求：
1. 保持原意，表达自然流畅
2. 专业术语请严格按照术语表翻译
3. 如果原文已经是{target_language}，直接输出原文
4. 只输出翻译结果，不要添加解释或标注

{glossary}"""

_TRANSLATION_USER_PROMPT = """\
上下文（最近的对话）：
{recent_context}

请翻译以下内容：
{text}"""

_EXPRESSION_SYSTEM_PROMPT = """\
你是一个专业的英语表达助手，帮助用户在会议中用英语准确表达想法。

当前会议场景：{scene_description}

要求：
1. 将用户的中文输入转化为地道的英文表达
2. 语气和措辞要符合当前会议场景
3. 专业术语请严格按照术语表翻译
4. 只输出英文表达结果，不要添加中文解释

{glossary}"""

_EXPRESSION_USER_PROMPT = """\
上下文（最近的对话）：
{recent_context}

请将以下内容用英语表达：
{text}"""

_SUMMARY_SYSTEM_PROMPT = """\
你是一个专业的会议记录助手。你的任务是对会议转写文本进行结构化摘要。

要求：
1. 提取讨论主题和关键结论
2. 识别 Action Items（含责任人和截止时间，如有提及）
3. 标注重要的技术决策和风险项
4. 使用简洁的条目式输出

严格按照以下格式输出（使用 - 列表，不要使用表格）：

## 主题
- 主题1
- 主题2

## 结论
- 结论1
- 结论2

## Action Items
- 责任人：任务描述（截止时间）
- 责任人：任务描述

## 风险
- 风险1

{glossary}"""

_SUMMARY_USER_PROMPT = """\
请对以下会议段落进行摘要：

时间范围：{time_range}
{text}"""

_MERGE_SUMMARY_SYSTEM_PROMPT = """\
你是一个专业的会议记录助手。你的任务是将多个段落摘要合并为一份结构化的全局会议总结。

要求：
1. 合并相同主题，去除重复内容
2. 按讨论顺序组织内容
3. 保留所有 Action Items，不要遗漏

严格按照以下格式输出（使用 - 列表，不要使用表格）：

## 主题
- 主题1
- 主题2

## 结论
- 结论1
- 结论2

## Action Items
- 责任人：任务描述（截止时间）

## 风险
- 风险1"""

_MERGE_SUMMARY_USER_PROMPT = """\
已有的全局摘要：
{existing_summary}

新增的段落摘要：
{new_segment_summary}

请合并输出更新后的全局会议总结。"""


# =========================================================================
# 数据类
# =========================================================================

@dataclass
class PromptTemplate:
    """Prompt 模板对，包含 system prompt 和 user prompt。"""

    system: str
    user: str


# =========================================================================
# PromptManager
# =========================================================================

class PromptManager:
    """Prompt 模板管理器。

    管理翻译、闭麦表达、段落总结、全局合并四类 Prompt 模板。
    支持变量注入渲染，变量未提供时保留占位符。

    使用方式：
        pm = PromptManager()
        system, user = pm.render_translation(
            text="这个 IPC 接口需要重构",
            glossary="术语表：\\n- IPC → IPC",
            recent_context="之前讨论了模块化设计",
            target_language="英文",
        )
    """

    def __init__(self) -> None:
        self._templates: Dict[str, PromptTemplate] = {
            "translation": PromptTemplate(
                system=_TRANSLATION_SYSTEM_PROMPT,
                user=_TRANSLATION_USER_PROMPT,
            ),
            "expression": PromptTemplate(
                system=_EXPRESSION_SYSTEM_PROMPT,
                user=_EXPRESSION_USER_PROMPT,
            ),
            "summary": PromptTemplate(
                system=_SUMMARY_SYSTEM_PROMPT,
                user=_SUMMARY_USER_PROMPT,
            ),
            "merge_summary": PromptTemplate(
                system=_MERGE_SUMMARY_SYSTEM_PROMPT,
                user=_MERGE_SUMMARY_USER_PROMPT,
            ),
        }

    # ------------------------------------------------------------------
    # 模板管理
    # ------------------------------------------------------------------
    def get_template(self, name: str) -> PromptTemplate:
        """获取指定名称的模板。

        Raises:
            KeyError: 模板不存在。
        """
        if name not in self._templates:
            raise KeyError(f"Unknown prompt template: '{name}'. Available: {list(self._templates.keys())}")
        return self._templates[name]

    def set_template(self, name: str, *, system: str, user: str) -> None:
        """设置或覆盖一个模板。"""
        self._templates[name] = PromptTemplate(system=system, user=user)
        logger.info("Prompt template '%s' updated", name)

    @property
    def template_names(self) -> list[str]:
        """所有已注册的模板名称。"""
        return list(self._templates.keys())

    # ------------------------------------------------------------------
    # 渲染方法
    # ------------------------------------------------------------------
    def render(self, template_name: str, **variables: str) -> tuple[str, str]:
        """渲染指定模板，返回 (system_prompt, user_prompt)。

        Args:
            template_name: 模板名称（translation / expression / summary / merge_summary）。
            **variables: 变量键值对，如 text="...", glossary="...", etc.

        Returns:
            (system_prompt, user_prompt) 元组。
        """
        template = self.get_template(template_name)
        safe = _SafeDict(variables)
        return (
            template.system.format_map(safe),
            template.user.format_map(safe),
        )

    def render_translation(
        self,
        text: str,
        *,
        glossary: str = "",
        recent_context: str = "",
        target_language: str = "英文",
    ) -> tuple[str, str]:
        """渲染翻译 Prompt。"""
        return self.render(
            "translation",
            text=text,
            glossary=glossary,
            recent_context=recent_context,
            target_language=target_language,
        )

    def render_expression(
        self,
        text: str,
        *,
        glossary: str = "",
        recent_context: str = "",
        scene_description: str = "",
    ) -> tuple[str, str]:
        """渲染闭麦表达 Prompt。"""
        return self.render(
            "expression",
            text=text,
            glossary=glossary,
            recent_context=recent_context,
            scene_description=scene_description,
        )

    def render_summary(
        self,
        text: str,
        *,
        glossary: str = "",
        time_range: str = "",
    ) -> tuple[str, str]:
        """渲染段落总结 Prompt。"""
        return self.render(
            "summary",
            text=text,
            glossary=glossary,
            time_range=time_range,
        )

    def render_merge_summary(
        self,
        existing_summary: str,
        new_segment_summary: str,
    ) -> tuple[str, str]:
        """渲染全局合并总结 Prompt。"""
        return self.render(
            "merge_summary",
            existing_summary=existing_summary,
            new_segment_summary=new_segment_summary,
        )
