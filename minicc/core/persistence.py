"""
消息持久化 — 主 Agent 消息历史的文件级存取。

不做 session 隔离，单文件对应单对话。
pydantic_ai 消息是 dataclass（非 Pydantic model），序列化用 dataclasses.asdict，
反序列化按 kind/part_kind 字段重建。
"""

from __future__ import annotations

import dataclasses
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from minicc.core.config import CONFIG_DIR

logger = logging.getLogger(__name__)

HISTORY_FILE = CONFIG_DIR / "history.json"

# kind → 消息类映射
_MSG_CLASSES: dict[str, type] = {}
_PART_CLASSES: dict[str, type] = {}


def _register_msg(kind: str):
    def dec(cls):
        _MSG_CLASSES[kind] = cls
        return cls
    return dec


def _register_part(part_kind: str):
    def dec(cls):
        _PART_CLASSES[part_kind] = cls
        return cls
    return dec


# 延迟导入避免循环依赖，同时注册 kind 映射
def _ensure_registry() -> None:
    if _MSG_CLASSES:
        return
    from pydantic_ai.messages import (
        ModelRequest,
        ModelResponse,
        RetryPromptPart,
        TextPart,
        ToolCallPart,
        ToolReturnPart,
        UserPromptPart,
    )

    _MSG_CLASSES["request"] = ModelRequest
    _MSG_CLASSES["response"] = ModelResponse

    _PART_CLASSES["user-prompt"] = UserPromptPart
    _PART_CLASSES["text"] = TextPart
    _PART_CLASSES["tool-call"] = ToolCallPart
    _PART_CLASSES["tool-return"] = ToolReturnPart
    _PART_CLASSES["retry-prompt"] = RetryPromptPart


def _as_json_compatible(obj: Any) -> Any:
    """递归转换 dataclass/datetime 为 JSON 兼容类型。"""
    if dataclasses.is_dataclass(obj):
        return {f.name: _as_json_compatible(getattr(obj, f.name)) for f in dataclasses.fields(obj)}
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {k: _as_json_compatible(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_as_json_compatible(v) for v in obj]
    return obj


def _reconstruct(obj: dict) -> Any:
    """从 JSON dict 重建 dataclass 对象。"""
    kind = obj.get("kind")
    if kind in _MSG_CLASSES:
        cls = _MSG_CLASSES[kind]
        fields = {f.name for f in dataclasses.fields(cls)}
        kwargs = {}
        for k, v in obj.items():
            if k == "parts":
                kwargs[k] = [_reconstruct_part(p) for p in v]
            elif k in fields:
                kwargs[k] = _parse_value(k, v)
        return cls(**kwargs)
    return obj


def _reconstruct_part(obj: dict) -> Any:
    part_kind = obj.get("part_kind")
    if part_kind in _PART_CLASSES:
        cls = _PART_CLASSES[part_kind]
        fields = {f.name for f in dataclasses.fields(cls)}
        kwargs = {}
        for k, v in obj.items():
            if k in fields or k not in ("part_kind",):
                if k in fields:
                    kwargs[k] = _parse_value(k, v)
        return cls(**kwargs)
    return obj


def _parse_value(field_name: str, value: Any) -> Any:
    if isinstance(value, str) and field_name == "timestamp":
        try:
            return datetime.fromisoformat(value)
        except (ValueError, TypeError):
            return value
    return value


class MessageStore:
    """基于 JSON 文件的消息持久化。"""

    def __init__(self, path: Path | str | None = None) -> None:
        self._path = Path(path) if path else HISTORY_FILE

    def load(self) -> list[Any]:
        if not self._path.exists():
            return []
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            _ensure_registry()
            return [_reconstruct(d) for d in data]
        except Exception:
            logger.warning("Failed to load message history, starting fresh", exc_info=True)
            return []

    def save(self, messages: list[Any]) -> None:
        _ensure_registry()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        data = _as_json_compatible(messages)
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self._path)
