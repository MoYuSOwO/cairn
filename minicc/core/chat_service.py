"""
ChatService — 主 Agent 生命周期管理。

拥有 Agent、消息历史、持久化。TUI 通过 send_message() 的 async iterator 消费流式事件。
"""

from __future__ import annotations

import os
import traceback
from typing import Any, AsyncIterator

from pydantic_ai import AgentRunResultEvent
from pydantic_ai.messages import (
    BuiltinToolCallEvent,
    BuiltinToolResultEvent,
    FunctionToolCallEvent,
    FunctionToolResultEvent,
    PartDeltaEvent,
    PartStartEvent,
    RetryPromptPart,
    TextPart,
    TextPartDelta,
    ToolReturnPart,
)

from .chat_events import Done, Error, TextDelta, ToolFinished, ToolStarted
from .models import MiniCCDeps, ToolResult, UserCancelledError
from .persistence import MessageStore


def _safe_get_args(part: Any) -> dict[str, Any] | None:
    try:
        return part.args_as_dict()
    except Exception:
        try:
            return part.args if isinstance(part.args, dict) else None
        except Exception:
            return None


def _tool_result_to_status(result_part: ToolReturnPart | RetryPromptPart) -> tuple[bool, str | None]:
    if isinstance(result_part, RetryPromptPart):
        return False, str(result_part.content)
    content = result_part.content
    if isinstance(content, ToolResult):
        return bool(content.success), content.error
    if hasattr(content, "success") and hasattr(content, "error"):
        try:
            ok = bool(getattr(content, "success"))
            err = getattr(content, "error", None)
            return ok, err
        except Exception:
            pass
    return True, None


class ChatService:
    """主 Agent 对话服务。

    拥有 Agent 实例和消息历史，提供流式对话接口。
    消息每轮成功后自动持久化，重启后自动恢复。
    """

    def __init__(
        self,
        agent: Any,
        deps: MiniCCDeps,
        store: MessageStore | None = None,
    ) -> None:
        self._agent = agent
        self._deps = deps
        self._store = store or MessageStore()
        self._messages: list[Any] = self._store.load()

    @property
    def messages(self) -> list[Any]:
        return list(self._messages)

    async def send_message(self, user_input: str) -> AsyncIterator[Any]:
        """运行一轮对话，逐个产出 ChatEvent。

        TextDelta / ToolStarted / ToolFinished / Done / Error
        """
        streamed_text = ""
        try:
            async for event in self._agent.run_stream_events(
                user_input,
                deps=self._deps,
                message_history=self._messages,
            ):
                if isinstance(event, PartStartEvent) and isinstance(event.part, TextPart):
                    streamed_text += event.part.content
                    yield TextDelta(content=streamed_text)

                elif isinstance(event, PartDeltaEvent) and isinstance(event.delta, TextPartDelta):
                    streamed_text += event.delta.content_delta
                    yield TextDelta(content=streamed_text)

                elif isinstance(event, (FunctionToolCallEvent, BuiltinToolCallEvent)):
                    part = event.part
                    yield ToolStarted(
                        tool_call_id=part.tool_call_id,
                        tool_name=part.tool_name,
                        args=_safe_get_args(part),
                    )

                elif isinstance(event, (FunctionToolResultEvent, BuiltinToolResultEvent)):
                    result_part = event.result
                    tool_name = getattr(result_part, "tool_name", "") or ""
                    ok, err = _tool_result_to_status(result_part)
                    yield ToolFinished(
                        tool_call_id=result_part.tool_call_id,
                        tool_name=tool_name,
                        ok=ok,
                        content=getattr(result_part, "content", None),
                        error=err,
                    )

                elif isinstance(event, AgentRunResultEvent):
                    self._messages = event.result.all_messages()
                    if not streamed_text:
                        output = str(event.result.output)
                        if output:
                            streamed_text = output
                            yield TextDelta(content=output)
                    usage = event.result.usage()
                    self._store.save(self._messages)
                    yield Done(usage=usage)
                    return

        except UserCancelledError:
            yield Error(exception=UserCancelledError("操作已取消"))
        except Exception as e:
            if os.environ.get("MINICC_DEBUG"):
                traceback.print_exc()
            yield Error(exception=e)

    def clear(self) -> None:
        self._messages = []
        self._store.save(self._messages)
