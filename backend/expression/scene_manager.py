"""场景配置管理器 — 从 JSON 加载会议场景，支持增删和默认场景切换。

单一职责：只负责场景数据的加载、存储和查询。
不负责 Prompt 渲染（由 PromptManager 负责）、不负责 LLM 调用。
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class Scene:
    """一个会议场景配置。"""

    id: str
    name: str
    description: str
    is_default: bool = False


class SceneManager:
    """会议场景管理器。

    从 JSON 文件加载场景列表，支持增删自定义场景和切换默认场景。

    JSON 格式::

        {
          "scenes": [
            {"id": "internal_tech", "name": "...", "description": "...", "is_default": true},
            ...
          ]
        }

    使用方式：
        sm = SceneManager()
        sm.load(Path("config/scenes.json"))
        scene = sm.get_default()
        sm.set_default("customer_meeting")
    """

    def __init__(self) -> None:
        self._scenes: Dict[str, Scene] = {}
        self._source_path: Optional[Path] = None

    # ------------------------------------------------------------------
    # 加载 / 保存
    # ------------------------------------------------------------------
    def load(self, path: Path) -> None:
        """从 JSON 文件加载场景配置。

        Raises:
            FileNotFoundError: 文件不存在。
            json.JSONDecodeError: JSON 格式错误。
            ValueError: JSON 结构不符合预期。
        """
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        if not isinstance(data, dict) or "scenes" not in data:
            raise ValueError("Scene config must be a JSON object with a 'scenes' array")

        scenes_list = data["scenes"]
        if not isinstance(scenes_list, list):
            raise ValueError("'scenes' must be an array")

        self._scenes.clear()
        for item in scenes_list:
            scene = Scene(
                id=str(item["id"]),
                name=str(item["name"]),
                description=str(item["description"]),
                is_default=bool(item.get("is_default", False)),
            )
            self._scenes[scene.id] = scene

        self._source_path = path
        logger.info("Loaded %d scenes from %s", len(self._scenes), path)

    def save(self, path: Optional[Path] = None) -> None:
        """将当前场景配置保存到 JSON 文件。"""
        target = path or self._source_path
        if target is None:
            raise ValueError("No save path specified and no source path available")

        scenes_list = [asdict(s) for s in self._scenes.values()]
        data = {"scenes": scenes_list}

        target.parent.mkdir(parents=True, exist_ok=True)
        with open(target, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        logger.info("Saved %d scenes to %s", len(self._scenes), target)

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------
    def get(self, scene_id: str) -> Optional[Scene]:
        """按 ID 获取场景。"""
        return self._scenes.get(scene_id)

    def get_default(self) -> Optional[Scene]:
        """获取默认场景。无默认场景时返回 None。"""
        for scene in self._scenes.values():
            if scene.is_default:
                return scene
        return None

    def list_scenes(self) -> List[Scene]:
        """列出所有场景。"""
        return list(self._scenes.values())

    def contains(self, scene_id: str) -> bool:
        """是否包含指定 ID 的场景。"""
        return scene_id in self._scenes

    @property
    def size(self) -> int:
        """场景数量。"""
        return len(self._scenes)

    # ------------------------------------------------------------------
    # 增删改
    # ------------------------------------------------------------------
    def add(self, scene: Scene) -> None:
        """添加或更新一个场景。"""
        self._scenes[scene.id] = scene
        logger.info("Scene added/updated: %s", scene.id)

    def remove(self, scene_id: str) -> bool:
        """删除一个场景。返回是否存在并删除。"""
        if scene_id in self._scenes:
            del self._scenes[scene_id]
            logger.info("Scene removed: %s", scene_id)
            return True
        return False

    def set_default(self, scene_id: str) -> None:
        """设置默认场景。

        Raises:
            KeyError: 场景不存在。
        """
        if scene_id not in self._scenes:
            raise KeyError(
                f"Scene '{scene_id}' not found. Available: {list(self._scenes.keys())}"
            )
        for scene in self._scenes.values():
            scene.is_default = scene.id == scene_id
        logger.info("Default scene set to: %s", scene_id)
