"""GlossaryManager 和 PromptManager 单元测试。

覆盖范围：
- GlossaryManager: 加载/保存 JSON、增删查、格式化输出、异常处理
- PromptManager: 模板渲染、变量注入、缺失变量保留、自定义模板、快捷方法
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.llm.glossary import GlossaryManager
from backend.llm.prompt_manager import PromptManager, PromptTemplate


# =========================================================================
# GlossaryManager 测试
# =========================================================================
class TestGlossaryLoad:
    """术语表加载测试。"""

    def test_load_from_json(self, tmp_path: Path):
        data = {"IPC": "IPC", "总线": "bus"}
        p = tmp_path / "glossary.json"
        p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

        gm = GlossaryManager()
        gm.load(p)

        assert gm.size == 2
        assert gm.get("IPC") == "IPC"
        assert gm.get("总线") == "bus"

    def test_load_default_glossary(self):
        """加载项目默认 config/glossary.json。"""
        default_path = Path(__file__).resolve().parents[2] / "config" / "glossary.json"
        if not default_path.exists():
            pytest.skip("Default glossary.json not found")

        gm = GlossaryManager()
        gm.load(default_path)
        assert gm.size > 0
        assert gm.contains("IPC")

    def test_load_file_not_found(self, tmp_path: Path):
        gm = GlossaryManager()
        with pytest.raises(FileNotFoundError):
            gm.load(tmp_path / "nonexistent.json")

    def test_load_invalid_json(self, tmp_path: Path):
        p = tmp_path / "bad.json"
        p.write_text("not json", encoding="utf-8")

        gm = GlossaryManager()
        with pytest.raises(json.JSONDecodeError):
            gm.load(p)

    def test_load_non_object_json(self, tmp_path: Path):
        p = tmp_path / "array.json"
        p.write_text('["a", "b"]', encoding="utf-8")

        gm = GlossaryManager()
        with pytest.raises(ValueError, match="JSON object"):
            gm.load(p)

    def test_load_overwrites_previous(self, tmp_path: Path):
        p1 = tmp_path / "g1.json"
        p1.write_text('{"a": "1"}', encoding="utf-8")
        p2 = tmp_path / "g2.json"
        p2.write_text('{"b": "2"}', encoding="utf-8")

        gm = GlossaryManager()
        gm.load(p1)
        assert gm.contains("a")

        gm.load(p2)
        assert not gm.contains("a")
        assert gm.contains("b")


class TestGlossarySave:
    """术语表保存测试。"""

    def test_save_to_path(self, tmp_path: Path):
        gm = GlossaryManager()
        gm.add("测试", "test")
        gm.add("术语", "term")

        out = tmp_path / "out.json"
        gm.save(out)

        data = json.loads(out.read_text(encoding="utf-8"))
        assert data == {"测试": "test", "术语": "term"}

    def test_save_to_source_path(self, tmp_path: Path):
        p = tmp_path / "glossary.json"
        p.write_text('{"a": "1"}', encoding="utf-8")

        gm = GlossaryManager()
        gm.load(p)
        gm.add("b", "2")
        gm.save()

        data = json.loads(p.read_text(encoding="utf-8"))
        assert "b" in data

    def test_save_no_path_raises(self):
        gm = GlossaryManager()
        with pytest.raises(ValueError, match="No save path"):
            gm.save()

    def test_save_creates_parent_dirs(self, tmp_path: Path):
        out = tmp_path / "sub" / "dir" / "glossary.json"
        gm = GlossaryManager()
        gm.add("x", "y")
        gm.save(out)
        assert out.exists()


class TestGlossaryCRUD:
    """术语增删查测试。"""

    def test_add_and_get(self):
        gm = GlossaryManager()
        gm.add("看门狗", "watchdog")
        assert gm.get("看门狗") == "watchdog"
        assert gm.size == 1

    def test_add_overwrites(self):
        gm = GlossaryManager()
        gm.add("test", "v1")
        gm.add("test", "v2")
        assert gm.get("test") == "v2"
        assert gm.size == 1

    def test_remove_existing(self):
        gm = GlossaryManager()
        gm.add("a", "1")
        assert gm.remove("a") is True
        assert gm.size == 0

    def test_remove_nonexistent(self):
        gm = GlossaryManager()
        assert gm.remove("nothing") is False

    def test_contains(self):
        gm = GlossaryManager()
        gm.add("x", "y")
        assert gm.contains("x") is True
        assert gm.contains("z") is False

    def test_get_nonexistent_returns_none(self):
        gm = GlossaryManager()
        assert gm.get("nothing") is None

    def test_entries_is_copy(self):
        gm = GlossaryManager()
        gm.add("a", "1")
        entries = gm.entries
        entries["b"] = "2"
        assert not gm.contains("b")


class TestGlossaryFormat:
    """术语表格式化测试。"""

    def test_format_empty(self):
        gm = GlossaryManager()
        assert gm.format_for_prompt() == ""

    def test_format_single_entry(self):
        gm = GlossaryManager()
        gm.add("总线", "bus")
        result = gm.format_for_prompt()
        assert "术语表" in result
        assert "总线 → bus" in result

    def test_format_multiple_entries(self):
        gm = GlossaryManager()
        gm.add("IPC", "IPC")
        gm.add("总线", "bus")
        gm.add("中间件", "middleware")
        result = gm.format_for_prompt()
        lines = result.strip().split("\n")
        assert len(lines) == 4  # header + 3 entries
        assert all("→" in line for line in lines[1:])

    def test_format_from_loaded_file(self, tmp_path: Path):
        data = {"AUTOSAR": "AUTOSAR", "RTOS": "RTOS"}
        p = tmp_path / "g.json"
        p.write_text(json.dumps(data), encoding="utf-8")

        gm = GlossaryManager()
        gm.load(p)
        result = gm.format_for_prompt()
        assert "AUTOSAR → AUTOSAR" in result
        assert "RTOS → RTOS" in result


# =========================================================================
# PromptManager 测试
# =========================================================================
class TestPromptManagerBasic:
    """PromptManager 基础测试。"""

    def test_default_templates_registered(self):
        pm = PromptManager()
        names = pm.template_names
        assert "translation" in names
        assert "expression" in names
        assert "summary" in names
        assert "merge_summary" in names

    def test_get_template(self):
        pm = PromptManager()
        t = pm.get_template("translation")
        assert isinstance(t, PromptTemplate)
        assert len(t.system) > 0
        assert len(t.user) > 0

    def test_get_unknown_template_raises(self):
        pm = PromptManager()
        with pytest.raises(KeyError, match="Unknown prompt template"):
            pm.get_template("nonexistent")

    def test_set_custom_template(self):
        pm = PromptManager()
        pm.set_template("custom", system="sys {x}", user="usr {y}")
        t = pm.get_template("custom")
        assert t.system == "sys {x}"

    def test_override_existing_template(self):
        pm = PromptManager()
        pm.set_template("translation", system="new sys", user="new usr")
        t = pm.get_template("translation")
        assert t.system == "new sys"


class TestPromptRender:
    """Prompt 渲染测试。"""

    def test_render_with_all_variables(self):
        pm = PromptManager()
        system, user = pm.render(
            "translation",
            text="hello",
            glossary="- IPC → IPC",
            recent_context="context here",
            target_language="英文",
        )
        assert "hello" in user
        assert "IPC → IPC" in system
        assert "context here" in user
        assert "英文" in system

    def test_render_missing_variable_preserved(self):
        """未提供的变量保留 {name} 占位符。"""
        pm = PromptManager()
        system, user = pm.render("translation", text="hello")
        # glossary not provided → {glossary} preserved in system
        # But since glossary defaults to "" in render_translation, 
        # test with raw render()
        assert "{recent_context}" in user
        assert "hello" in user

    def test_render_empty_variables(self):
        pm = PromptManager()
        system, user = pm.render(
            "translation",
            text="",
            glossary="",
            recent_context="",
            target_language="中文",
        )
        assert "中文" in system

    def test_render_custom_template(self):
        pm = PromptManager()
        pm.set_template("test", system="Hello {name}!", user="Query: {q}")
        system, user = pm.render("test", name="World", q="test")
        assert system == "Hello World!"
        assert user == "Query: test"


class TestPromptRenderTranslation:
    """翻译 Prompt 渲染测试。"""

    def test_basic_translation(self):
        pm = PromptManager()
        system, user = pm.render_translation("这是一段测试文本")
        assert "这是一段测试文本" in user
        assert "英文" in system  # default target_language

    def test_with_glossary_and_context(self):
        pm = PromptManager()
        system, user = pm.render_translation(
            "IPC 接口需要重构",
            glossary="术语表：\n- IPC → IPC",
            recent_context="之前讨论了模块化",
        )
        assert "IPC → IPC" in system
        assert "模块化" in user

    def test_custom_target_language(self):
        pm = PromptManager()
        system, _ = pm.render_translation("hello", target_language="日文")
        assert "日文" in system


class TestPromptRenderExpression:
    """闭麦表达 Prompt 渲染测试。"""

    def test_basic_expression(self):
        pm = PromptManager()
        system, user = pm.render_expression(
            "我觉得这个方案可行",
            scene_description="跨国团队内部技术讨论会",
        )
        assert "跨国团队内部技术讨论会" in system
        assert "我觉得这个方案可行" in user

    def test_expression_with_glossary(self):
        pm = PromptManager()
        system, _ = pm.render_expression(
            "text",
            glossary="- 中间件 → middleware",
            scene_description="tech meeting",
        )
        assert "middleware" in system


class TestPromptRenderSummary:
    """总结 Prompt 渲染测试。"""

    def test_summary_render(self):
        pm = PromptManager()
        system, user = pm.render_summary(
            "讨论了模块接口定义...",
            time_range="00:05:00 - 00:06:00",
            glossary="- 接口定义 → interface definition",
        )
        assert "interface definition" in system
        assert "00:05:00" in user
        assert "模块接口定义" in user

    def test_merge_summary_render(self):
        pm = PromptManager()
        system, user = pm.render_merge_summary(
            existing_summary="主题1: 架构讨论",
            new_segment_summary="主题2: 接口评审",
        )
        assert "架构讨论" in user
        assert "接口评审" in user
        assert "合并" in system


class TestPromptIntegrationWithGlossary:
    """PromptManager + GlossaryManager 集成测试。"""

    def test_glossary_injected_into_prompt(self, tmp_path: Path):
        """验证 GlossaryManager 输出可正确注入到 Prompt。"""
        data = {"IPC": "IPC", "中间件": "middleware", "总线": "bus"}
        p = tmp_path / "glossary.json"
        p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

        gm = GlossaryManager()
        gm.load(p)
        glossary_str = gm.format_for_prompt()

        pm = PromptManager()
        system, user = pm.render_translation(
            text="总线通信需要使用中间件",
            glossary=glossary_str,
            recent_context="",
        )

        assert "IPC → IPC" in system
        assert "中间件 → middleware" in system
        assert "总线 → bus" in system
        assert "总线通信需要使用中间件" in user

    def test_empty_glossary_no_injection(self):
        """空术语表不影响 Prompt。"""
        gm = GlossaryManager()
        glossary_str = gm.format_for_prompt()
        assert glossary_str == ""

        pm = PromptManager()
        system, _ = pm.render_translation("hello", glossary=glossary_str)
        # system should still be valid, just no glossary content
        assert "翻译" in system
