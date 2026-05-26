"""
ChatService 流式事件类型 — 替代 EventBus 中的 ToolCallStarted/ToolCallFinished，
由 ChatService.send_message() 的 async iterator 产出，TUI 直接消费。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class TextDelta:
    """累积的 assistant 文本（非逐字符增量）。"""

    content: str


@dataclass(frozen=True)
class ToolStarted:
    tool_call_id: str
    tool_name: str
    args: dict[str, Any] | None


@dataclass(frozen=True)
class ToolFinished:
    tool_call_id: str
    tool_name: str
    ok: bool
    content: Any
    error: str | None = None


@dataclass(frozen=True)
class Done:
    """Agent 成功完成一轮对话。消息已在 ChatService 中持久化。"""

    usage: Any | None


@dataclass(frozen=True)
class Error:
    """Agent 执行出错。历史消息未变更。"""

    exception: Exception
