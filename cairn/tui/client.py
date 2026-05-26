"""
TUI 客户端 — WebSocket 连接到后端 ChatService。

广播模式：所有 WS 事件（任意请求的 chat 事件 + EventBus 事件）统一从 stream 队列消费。
TUI 无需等待响应——submit 后立即返回，事件异步抵达。
"""

from __future__ import annotations

import asyncio
import json
from typing import Any


class ChatClient:
    """WebSocket 代理，所有事件通过 stream 队列广播。"""

    def __init__(self, server_url: str = "ws://127.0.0.1:8720/ws"):
        self._url = server_url
        self._ws: Any = None
        self._drain_task: asyncio.Task | None = None
        self.stream: asyncio.Queue[dict] = asyncio.Queue()

    async def connect(self) -> None:
        try:
            import websockets
        except ImportError:
            raise ImportError("client 模式需要安装 websockets: pip install websockets")

        self._ws = await websockets.connect(self._url)
        self._drain_task = asyncio.create_task(self._drain())

    async def _drain(self) -> None:
        """持续从 WS 读取，所有消息进 stream 队列。"""
        try:
            async for raw in self._ws:
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                await self.stream.put(msg)
        except Exception:
            pass

    async def disconnect(self) -> None:
        if self._drain_task:
            self._drain_task.cancel()
            try:
                await self._drain_task
            except asyncio.CancelledError:
                pass
        if self._ws:
            await self._ws.close()

    async def submit(self, text: str) -> None:
        """发送聊天消息。不等待响应，事件通过 stream 异步抵达。"""
        await self._ws.send(json.dumps({"type": "chat", "text": text}))

    async def send_ask_user_response(self, request_id: str, submitted: bool, answers: dict) -> None:
        await self._ws.send(json.dumps({
            "type": "ask_user_response",
            "request_id": request_id,
            "submitted": submitted,
            "answers": answers,
        }))

    async def rollback_to(self, index: int) -> None:
        await self._ws.send(json.dumps({"type": "rollback", "index": index}))

    async def clear(self) -> None:
        await self._ws.send(json.dumps({"type": "clear"}))
