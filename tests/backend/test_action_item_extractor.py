"""Action Items 提取模块单元测试。

覆盖范围：
- ActionItem 数据类
- ActionItemExtractor.extract_from_text: 从完整摘要中提取
- ActionItemExtractor.extract_from_lines: 从预提取列表中解析
- 责任人解析（中文冒号、英文冒号、破折号、@前缀）
- 截止时间解析（中文、英文、日期格式）
- 边界情况（空文本、无 Action Items、格式异常）
"""

from __future__ import annotations

import pytest

from backend.summary.action_item_extractor import (
    ActionItem,
    ActionItemExtractor,
    ActionItemStatus,
    _extract_action_lines,
)


# =========================================================================
# Fixtures
# =========================================================================


@pytest.fixture
def extractor() -> ActionItemExtractor:
    return ActionItemExtractor()


FULL_SUMMARY = """\
## 讨论主题
- AUTOSAR 架构迁移方案
- CAN 总线通信问题

## 关键结论
- 决定采用 Classic AUTOSAR 4.4
- CAN 总线延迟问题已定位到硬件驱动层

## Action Items
- 张三：下周一完成 BSW 模块适配
- 李四：周五前提交 CAN 驱动修复 patch
- Alice: review the integration test plan by Friday
- 王五 - 准备下次会议的性能测试报告

## 风险项
- GPU 显存不足可能影响推理性能
"""


# =========================================================================
# ActionItem 数据类 Tests
# =========================================================================


class TestActionItem:
    def test_has_assignee(self):
        item = ActionItem(description="Do X", assignee="张三")
        assert item.has_assignee

    def test_no_assignee(self):
        item = ActionItem(description="Do X")
        assert not item.has_assignee

    def test_has_deadline(self):
        item = ActionItem(description="Do X", deadline="下周一")
        assert item.has_deadline

    def test_no_deadline(self):
        item = ActionItem(description="Do X")
        assert not item.has_deadline

    def test_default_status(self):
        item = ActionItem(description="Do X")
        assert item.status == ActionItemStatus.OPEN

    def test_status_values(self):
        assert ActionItemStatus.OPEN == "open"
        assert ActionItemStatus.IN_PROGRESS == "in_progress"
        assert ActionItemStatus.DONE == "done"


# =========================================================================
# extract_from_text Tests
# =========================================================================


class TestExtractFromText:
    def test_basic_extraction(self, extractor: ActionItemExtractor):
        """从完整摘要中提取 Action Items。"""
        items = extractor.extract_from_text(FULL_SUMMARY)
        assert len(items) == 4

    def test_assignee_chinese_colon(self, extractor: ActionItemExtractor):
        """中文冒号分隔责任人。"""
        items = extractor.extract_from_text(FULL_SUMMARY)
        zhang = next(i for i in items if "张三" in (i.assignee or i.description))
        assert zhang.assignee == "张三"
        assert "BSW" in zhang.description

    def test_assignee_english_colon(self, extractor: ActionItemExtractor):
        """英文冒号分隔责任人。"""
        items = extractor.extract_from_text(FULL_SUMMARY)
        alice = next(i for i in items if "Alice" in (i.assignee or i.description))
        assert alice.assignee == "Alice"
        assert "review" in alice.description

    def test_assignee_dash(self, extractor: ActionItemExtractor):
        """破折号分隔责任人。"""
        items = extractor.extract_from_text(FULL_SUMMARY)
        wang = next(i for i in items if "王五" in (i.assignee or i.description))
        assert wang.assignee == "王五"
        assert "性能测试" in wang.description

    def test_deadline_chinese_weekday(self, extractor: ActionItemExtractor):
        """中文截止时间：下周一。"""
        items = extractor.extract_from_text(FULL_SUMMARY)
        zhang = next(i for i in items if i.assignee == "张三")
        assert zhang.deadline == "下周一"

    def test_deadline_chinese_before(self, extractor: ActionItemExtractor):
        """中文截止时间：周五前。"""
        items = extractor.extract_from_text(FULL_SUMMARY)
        li = next(i for i in items if i.assignee == "李四")
        assert li.deadline == "周五前"

    def test_deadline_english(self, extractor: ActionItemExtractor):
        """英文截止时间：by Friday。"""
        items = extractor.extract_from_text(FULL_SUMMARY)
        alice = next(i for i in items if i.assignee == "Alice")
        assert alice.deadline == "by Friday"

    def test_source_propagated(self, extractor: ActionItemExtractor):
        """source 参数传递到每个 ActionItem。"""
        items = extractor.extract_from_text(FULL_SUMMARY, source="00:00 - 05:00")
        assert all(i.source == "00:00 - 05:00" for i in items)

    def test_empty_text(self, extractor: ActionItemExtractor):
        """空文本返回空列表。"""
        assert extractor.extract_from_text("") == []

    def test_whitespace_text(self, extractor: ActionItemExtractor):
        """纯空白返回空列表。"""
        assert extractor.extract_from_text("   \n  ") == []

    def test_no_action_section(self, extractor: ActionItemExtractor):
        """无 Action Items 段落。"""
        text = "## 讨论主题\n- 主题A\n## 结论\n- 结论1"
        assert extractor.extract_from_text(text) == []

    def test_alternative_heading_names(self, extractor: ActionItemExtractor):
        """不同的 Action Items 标题名。"""
        for heading in ["## Action Items", "## 行动项", "## 待办事项", "## TODO"]:
            text = f"{heading}\n- 完成任务A"
            items = extractor.extract_from_text(text)
            assert len(items) == 1, f"Failed for heading: {heading}"

    def test_colon_heading(self, extractor: ActionItemExtractor):
        """冒号结尾标题。"""
        text = "Action Items:\n- Do X\n- Do Y"
        items = extractor.extract_from_text(text)
        assert len(items) == 2

    def test_chinese_colon_heading(self, extractor: ActionItemExtractor):
        """中文冒号标题。"""
        text = "行动项：\n- 任务A\n- 任务B"
        items = extractor.extract_from_text(text)
        assert len(items) == 2


# =========================================================================
# extract_from_lines Tests
# =========================================================================


class TestExtractFromLines:
    def test_basic(self, extractor: ActionItemExtractor):
        """从字符串列表解析。"""
        lines = [
            "张三：完成 BSW 模块适配",
            "李四：周五前提交修复",
        ]
        items = extractor.extract_from_lines(lines)
        assert len(items) == 2
        assert items[0].assignee == "张三"
        assert items[1].assignee == "李四"

    def test_empty_lines_ignored(self, extractor: ActionItemExtractor):
        """空行被忽略。"""
        lines = ["任务A", "", "  ", "任务B"]
        items = extractor.extract_from_lines(lines)
        assert len(items) == 2

    def test_source_propagated(self, extractor: ActionItemExtractor):
        """source 参数传递。"""
        items = extractor.extract_from_lines(["任务A"], source="01:00 - 02:00")
        assert items[0].source == "01:00 - 02:00"

    def test_no_assignee(self, extractor: ActionItemExtractor):
        """无法识别责任人时 assignee 为空。"""
        items = extractor.extract_from_lines(["完成系统测试"])
        assert items[0].assignee == ""
        assert items[0].description == "完成系统测试"

    def test_at_prefix(self, extractor: ActionItemExtractor):
        """@前缀责任人。"""
        items = extractor.extract_from_lines(["@张三：完成任务"])
        assert items[0].assignee == "张三"


# =========================================================================
# 截止时间解析 Tests
# =========================================================================


class TestDeadlineParsing:
    def test_next_weekday(self, extractor: ActionItemExtractor):
        items = extractor.extract_from_lines(["下周三提交报告"])
        assert items[0].deadline == "下周三"

    def test_this_weekday(self, extractor: ActionItemExtractor):
        items = extractor.extract_from_lines(["本周五完成"])
        assert items[0].deadline == "本周五"

    def test_tomorrow(self, extractor: ActionItemExtractor):
        items = extractor.extract_from_lines(["明天发送邮件"])
        assert items[0].deadline == "明天"

    def test_days_within(self, extractor: ActionItemExtractor):
        items = extractor.extract_from_lines(["3天内完成"])
        assert items[0].deadline == "3天内"

    def test_date_format(self, extractor: ActionItemExtractor):
        items = extractor.extract_from_lines(["2026-04-20 前完成"])
        assert items[0].deadline == "2026-04-20"

    def test_chinese_date(self, extractor: ActionItemExtractor):
        items = extractor.extract_from_lines(["4月20日前提交"])
        assert items[0].deadline == "4月20日前"

    def test_by_eod(self, extractor: ActionItemExtractor):
        items = extractor.extract_from_lines(["finish the report by EOD"])
        assert items[0].deadline == "by EOD"

    def test_no_deadline(self, extractor: ActionItemExtractor):
        items = extractor.extract_from_lines(["完成代码审查"])
        assert items[0].deadline == ""


# =========================================================================
# _extract_action_lines Tests
# =========================================================================


class TestExtractActionLines:
    def test_markdown_heading(self):
        text = "## Action Items\n- Item A\n- Item B\n## 其他"
        assert _extract_action_lines(text) == ["Item A", "Item B"]

    def test_numbered_list(self):
        text = "## Action Items\n1. Item A\n2. Item B"
        assert _extract_action_lines(text) == ["Item A", "Item B"]

    def test_stops_at_next_heading(self):
        text = "## Action Items\n- Item A\n## 风险\n- Risk 1"
        assert _extract_action_lines(text) == ["Item A"]

    def test_empty(self):
        assert _extract_action_lines("") == []
