"""
MiniCC 后端服务 — FastAPI + WebSocket。

启动方式: minicc serve [--port 8080]
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any

from .core.agent import create_agent
from .core.chat_events import Done, Error, TextDelta, ToolFinished, ToolStarted
from .core.config import load_config
from .core.events import (
    AskUserRequested,
    AskUserResolved,
    SubAgentCreated,
    SubAgentUpdated,
    TodoUpdated,
)
from .core.mcp import load_mcp_toolsets
from .core.models import MiniCCDeps
from .core.persistence import MessageStore
from .core.services.ask_user import AskUserService
from .core.services.subagents import SubAgentService
from .core.chat_service import ChatService
from .tools import register_tools

logger = logging.getLogger(__name__)

_event_serializers = {}
_chat_event_serializers = {}


def _register_event(event_cls):
    def decorator(serializer):
        _event_serializers[event_cls] = serializer
        return serializer
    return decorator


def _register_chat_event(event_cls):
    def decorator(serializer):
        _chat_event_serializers[event_cls] = serializer
        return serializer
    return decorator


# ---- EventBus event serializers ----


@_register_event(TodoUpdated)
def _ser_todo(ev: TodoUpdated) -> dict:
    todos_data = []
    for t in ev.todos:
        if hasattr(t, "model_dump"):
            todos_data.append(t.model_dump(mode="json"))
        else:
            todos_data.append({"content": str(t), "status": "pending", "active_form": ""})
    return {"type": "todo_updated", "todos": todos_data}


@_register_event(AskUserRequested)
def _ser_ask_user(ev: AskUserRequested) -> dict:
    questions_data = []
    for q in ev.questions:
        if hasattr(q, "model_dump"):
            questions_data.append(q.model_dump(mode="json"))
        else:
            questions_data.append(q)
    return {"type": "ask_user_requested", "request_id": ev.request_id, "questions": questions_data}


@_register_event(SubAgentCreated)
def _ser_sa_created(ev: SubAgentCreated) -> dict:
    return {"type": "subagent_created", "task_id": ev.task_id, "description": ev.description, "prompt": ev.prompt}


@_register_event(SubAgentUpdated)
def _ser_sa_updated(ev: SubAgentUpdated) -> dict:
    return {"type": "subagent_updated", "task_id": ev.task_id, "status": ev.status, "result": ev.result}


# ---- ChatEvent serializers ----


@_register_chat_event(TextDelta)
def _ser_text(ev: TextDelta) -> dict:
    return {"type": "text_delta", "content": ev.content}


@_register_chat_event(ToolStarted)
def _ser_tool_started(ev: ToolStarted) -> dict:
    return {"type": "tool_started", "tool_call_id": ev.tool_call_id, "tool_name": ev.tool_name, "args": ev.args}


@_register_chat_event(ToolFinished)
def _ser_tool_finished(ev: ToolFinished) -> dict:
    return {
        "type": "tool_finished",
        "tool_call_id": ev.tool_call_id,
        "tool_name": ev.tool_name,
        "ok": ev.ok,
        "error": ev.error,
    }


@_register_chat_event(Done)
def _ser_done(ev: Done) -> dict:
    usage = None
    if ev.usage is not None:
        usage = {
            "input_tokens": getattr(ev.usage, "request_tokens", 0) or getattr(ev.usage, "input_tokens", 0),
            "output_tokens": getattr(ev.usage, "response_tokens", 0) or getattr(ev.usage, "output_tokens", 0),
        }
    return {"type": "done", "usage": usage}


@_register_chat_event(Error)
def _ser_error(ev: Error) -> dict:
    return {"type": "error", "message": str(ev.exception)}


def serialize_event(event: Any) -> dict | None:
    for cls, ser in _event_serializers.items():
        if isinstance(event, cls):
            return ser(event)
    return None


def serialize_chat_event(event: Any) -> dict | None:
    for cls, ser in _chat_event_serializers.items():
        if isinstance(event, cls):
            return ser(event)
    return None


# ---- FastAPI app ----


def create_app(cwd: str | None = None) -> Any:
    try:
        from fastapi import FastAPI, WebSocket, WebSocketDisconnect
        from fastapi.responses import JSONResponse
    except ImportError:
        raise ImportError("server 模式需要安装 fastapi: pip install minicc[server]")

    cwd = cwd or os.getcwd()
    config = load_config()
    toolsets = load_mcp_toolsets(cwd)

    fs = None
    try:
        from agent_gear import FileSystem
        fs = FileSystem(cwd, auto_watch=False)
    except Exception:
        pass

    agent = create_agent(config, cwd=cwd, toolsets=toolsets, register_tools=register_tools)

    deps = MiniCCDeps(config=config, cwd=cwd, fs=fs)
    event_bus = deps.event_bus

    ask_user_svc = AskUserService(event_bus)
    deps.ask_user_service = ask_user_svc

    def _agent_factory():
        return create_agent(config, cwd=cwd, toolsets=toolsets, register_tools=register_tools)

    deps.subagent_service = SubAgentService(deps=deps, event_bus=event_bus, agent_factory=_agent_factory)

    store = MessageStore()
    chat_service = ChatService(agent=agent, deps=deps, store=store)

    app = FastAPI(title="MiniCC Server")

    @app.get("/health")
    async def health():
        return {"status": "ok", "model": config.model, "provider": config.provider.value}

    @app.post("/inject")
    async def inject(request: dict):
        text = request.get("text", "")
        if not text:
            return JSONResponse({"error": "text is required"}, status_code=400)
        try:
            async for _ in chat_service.send_message(text):
                pass
            return {"status": "ok"}
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=500)

    @app.websocket("/ws")
    async def ws_endpoint(ws: WebSocket):
        await ws.accept()
        send_lock = asyncio.Lock()
        chat_running = False

        async def send_json(data: dict):
            async with send_lock:
                await ws.send_json(data)

        async def forward_events():
            try:
                async for ev in event_bus.iter():
                    data = serialize_event(ev)
                    if data:
                        await send_json(data)
            except Exception:
                pass

        forward_task = asyncio.create_task(forward_events())

        async def process_chat(text: str):
            nonlocal chat_running
            try:
                async for chat_event in chat_service.send_message(text):
                    data = serialize_chat_event(chat_event)
                    if data:
                        await send_json(data)
            finally:
                chat_running = False

        try:
            async for raw in ws.iter_text():
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue

                msg_type = msg.get("type", "")

                if msg_type == "chat":
                    if chat_running:
                        await send_json({"type": "error", "message": "已有对话正在处理"})
                        continue
                    chat_running = True
                    asyncio.create_task(process_chat(msg["text"]))

                elif msg_type == "clear":
                    chat_service.clear()
                    await send_json({"type": "cleared"})

                elif msg_type == "ask_user_response":
                    ask_user_svc.resolve(
                        msg["request_id"],
                        submitted=msg.get("submitted", False),
                        answers=msg.get("answers", {}),
                    )

        except WebSocketDisconnect:
            pass
        finally:
            forward_task.cancel()
            try:
                await forward_task
            except asyncio.CancelledError:
                pass

    return app


def run_server(host: str = "127.0.0.1", port: int = 8720, cwd: str | None = None) -> None:
    try:
        import uvicorn
    except ImportError:
        raise ImportError("server 模式需要安装 uvicorn: pip install minicc[server]")

    app = create_app(cwd=cwd)
    uvicorn.run(app, host=host, port=port, log_level="info")
