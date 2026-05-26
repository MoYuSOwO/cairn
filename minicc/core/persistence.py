"""
消息持久化 — 主 Agent 消息历史的文件级存取。

不做 session 隔离，单文件对应单对话。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from minicc.core.config import CONFIG_DIR

logger = logging.getLogger(__name__)

HISTORY_FILE = CONFIG_DIR / "history.json"


class MessageStore:
    """基于 JSON 文件的消息持久化。

    序列化 pydantic_ai ModelMessage 列表到 ~/.minicc/history.json。
    加载时通过 pydantic TypeAdapter 做 discriminated-union 反序列化。
    """

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or HISTORY_FILE

    def load(self) -> list[Any]:
        if not self._path.exists():
            return []
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            from pydantic import TypeAdapter
            from pydantic_ai.messages import ModelMessage

            adapter = TypeAdapter(list[ModelMessage])
            return adapter.validate_python(data)
        except Exception:
            logger.warning("Failed to load message history, starting fresh", exc_info=True)
            return []

    def save(self, messages: list[Any]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        data = [msg.model_dump(mode="json") for msg in messages]
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self._path)
