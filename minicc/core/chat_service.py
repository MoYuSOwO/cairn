"""
ChatService — 主 Agent 生命周期管理。

拥有 Agent、消息历史、持久化。支持两种使用模式：
- 直接模式: send_message() → AsyncIterator[ChatEvent]（嵌入式 TUI）
- 队列模式: submit() 入队 → worker 顺序处理 → 广播事件到所有订阅者
"""

from __future__ import annotations

import asyncio
import os
import traceback
from typing import Any, AsyncIterator
from uuid import uuid4

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
from .persistence import MessageStore, _as_json_compatible


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

    - 直接模式：send_message(text) → async iterator，嵌入式 TUI 使用
    - 队列模式：submit(text) + events(rid)，server 使用，保证顺序执行
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
        self._request_queue: asyncio.Queue[tuple[str, str]] = asyncio.Queue()
        self._broadcast: asyncio.Queue[dict] = asyncio.Queue()
        self._worker_task: asyncio.Task | None = None

    @property
    def messages(self) -> list[Any]:
        return list(self._messages)

    # ---- 直接模式（嵌入式 TUI）----

    async def send_message(self, user_input: str) -> AsyncIterator[Any]:
        """直接运行对话，返回 ChatEvent 迭代器。会阻塞直到完成。"""
        async for event in self._process(user_input):
            yield event

    # ---- 队列模式（server）----

    async def submit(self, text: str) -> str:
        """提交请求到队列，立即返回 request_id。Agent 顺序处理。"""
        if self._worker_task is None:
            self._worker_task = asyncio.create_task(self._worker())
        request_id = uuid4().hex[:8]
        await self._request_queue.put((request_id, text))
        return request_id

    def subscribe(self) -> asyncio.Queue[dict]:
        """订阅广播事件流。返回的队列会收到所有请求的所有事件。"""
        q: asyncio.Queue[dict] = asyncio.Queue()
        return q

    async def _publish_snapshot(self) -> None:
        """广播当前完整消息历史快照。"""
        data = _as_json_compatible(self._messages)
        await self._broadcast.put({
            "kind": "history_snapshot",
            "message_count": len(self._messages),
            "messages": data,
        })

    async def _worker(self) -> None:
        """后台 worker，从队列取请求、顺序处理，事件通过 _broadcast 发布。"""
        while True:
            request_id, text = await self._request_queue.get()
            await self._broadcast.put({"request_id": request_id, "kind": "request_started", "text": text})
            try:
                async for event in self._process(text):
                    await self._broadcast.put({"request_id": request_id, "kind": "chat_event", "event": event})
            except Exception:
                pass
            finally:
                await self._broadcast.put({"request_id": request_id, "kind": "request_finished"})
                await self._publish_snapshot()

    # ---- 内部实现 ----

    async def _process(self, user_input: str) -> AsyncIterator[Any]:
        """运行一轮对话，产出 ChatEvent（TextDelta / ToolStarted / ToolFinished / Done / Error）。"""
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

    async def clear(self) -> None:
        self._messages = []
        self._store.save(self._messages)
        await self._publish_snapshot()
