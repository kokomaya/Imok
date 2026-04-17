"""术语表管理器 — 从 JSON 加载术语表，支持运行时增删，格式化为 Prompt 注入字符串。

单一职责：只负责术语表的加载、存储、查询和格式化。
不负责 Prompt 模板管理（由 PromptManager 负责）。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Dict, Optional

logger = logging.getLogger(__name__)


class GlossaryManager:
    """术语表管理器。

    术语表格式：``{"中文术语": "English term", ...}``

    使用方式：
        gm = GlossaryManager()
        gm.load(Path("config/glossary.json"))
        gm.add("看门狗", "watchdog")
        prompt_snippet = gm.format_for_prompt()
    """

    def __init__(self) -> None:
        self._entries: Dict[str, str] = {}
        self._source_path: Optional[Path] = None

    # ------------------------------------------------------------------
    # 加载 / 保存
    # ------------------------------------------------------------------
    def load(self, path: Path) -> None:
        """从 JSON 文件加载术语表。

        Args:
            path: JSON 文件路径，格式 ``{"源术语": "目标术语", ...}``

        Raises:
            FileNotFoundError: 文件不存在。
            json.JSONDecodeError: JSON 格式错误。
        """
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        if not isinstance(data, dict):
            raise ValueError(f"Glossary file must be a JSON object, got {type(data).__name__}")

        self._entries = {str(k): str(v) for k, v in data.items()}
        self._source_path = path
        logger.info("Loaded %d glossary entries from %s", len(self._entries), path)

    def save(self, path: Optional[Path] = None) -> None:
        """将当前术语表保存到 JSON 文件。

        Args:
            path: 目标路径。默认使用加载时的路径。
        """
        target = path or self._source_path
        if target is None:
            raise ValueError("No save path specified and no source path available")

        target.parent.mkdir(parents=True, exist_ok=True)
        with open(target, "w", encoding="utf-8") as f:
            json.dump(self._entries, f, ensure_ascii=False, indent=2)

        logger.info("Saved %d glossary entries to %s", len(self._entries), target)

    # ------------------------------------------------------------------
    # 增删查
    # ------------------------------------------------------------------
    def add(self, source: str, target: str) -> None:
        """添加或更新一条术语。"""
        self._entries[source] = target

    def remove(self, source: str) -> bool:
        """删除一条术语。返回是否存在并删除。"""
        if source in self._entries:
            del self._entries[source]
            return True
        return False

    def get(self, source: str) -> Optional[str]:
        """查询术语翻译。"""
        return self._entries.get(source)

    def contains(self, source: str) -> bool:
        """是否包含指定术语。"""
        return source in self._entries

    @property
    def entries(self) -> Dict[str, str]:
        """当前全部术语（只读副本）。"""
        return dict(self._entries)

    @property
    def size(self) -> int:
        """术语条目数。"""
        return len(self._entries)

    # ------------------------------------------------------------------
    # 格式化
    # ------------------------------------------------------------------
    def format_for_prompt(self) -> str:
        """将术语表格式化为可注入 Prompt 的字符串。

        格式：
            术语表：
            - 中文术语 → English term
            - ...

        空术语表返回空字符串。
        """
        if not self._entries:
            return ""

        lines = ["术语表（翻译时请使用以下对照）："]
        for source, target in self._entries.items():
            lines.append(f"- {source} → {target}")

        return "\n".join(lines)
