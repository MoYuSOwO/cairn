"""
TUI 客户端 — WebSocket 连接到后端 ChatService。

替代直接调用 ChatService，所有 Agent 交互通过 WebSocket 代理。
事件分两路：chat 事件（text_delta / tool_* / done / error）走 send_message 迭代器；
UI 事件（todo / subagent / ask_user）走 ui_events 队列，由 TUI 后台消费。
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, AsyncIterator

_CHAT_EVENT_TYPES = frozenset({"text_delta", "tool_started", "tool_finished", "done", "error"})


class ChatClient:
    """WebSocket 代理，对 TUI 暴露与 ChatService 兼容的接口。"""

    def __init__(self, server_url: str = "ws://127.0.0.1:8720/ws"):
        self._url = server_url
        self._ws: Any = None
        self._recv_task: asyncio.Task | None = None
        self._chat_queue: asyncio.Queue[dict] | None = None
        self.ui_events: asyncio.Queue[dict] = asyncio.Queue()

    async def connect(self) -> None:
        try:
            import websockets
        except ImportError:
            raise ImportError("client 模式需要安装 websockets: pip install websockets")

        self._ws = await websockets.connect(self._url)
        self._recv_task = asyncio.create_task(self._drain())

    async def _drain(self) -> None:
        """后台持续从 WS 读消息，按类型路由到对应队列。"""
        try:
            async for raw in self._ws:
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                msg_type = msg.get("type", "")
                if msg_type in _CHAT_EVENT_TYPES:
                    if self._chat_queue is not None:
                        await self._chat_queue.put(msg)
                else:
                    await self.ui_events.put(msg)
        except Exception:
            pass

    async def disconnect(self) -> None:
        if self._recv_task:
            self._recv_task.cancel()
            try:
                await self._recv_task
            except asyncio.CancelledError:
                pass
        if self._ws:
            await self._ws.close()

    async def send_message(self, text: str) -> AsyncIterator[dict]:
        """发送聊天消息，返回 chat 事件的异步迭代器。"""
        self._chat_queue = asyncio.Queue()
        await self._ws.send(json.dumps({"type": "chat", "text": text}))
        try:
            while True:
                msg = await self._chat_queue.get()
                yield msg
                if msg.get("type") in ("done", "error"):
                    return
        finally:
            self._chat_queue = None

    async def send_ask_user_response(self, request_id: str, submitted: bool, answers: dict) -> None:
        await self._ws.send(json.dumps({
            "type": "ask_user_response",
            "request_id": request_id,
            "submitted": submitted,
            "answers": answers,
        }))

    async def clear(self) -> None:
        await self._ws.send(json.dumps({"type": "clear"}))
