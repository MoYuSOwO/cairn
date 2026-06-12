from __future__ import annotations

import os
from dataclasses import dataclass

from agent_gear import FileSystem

from cairn.core.agent import create_agent
from cairn.core.chat_service import ChatService
from cairn.core.events import EventBus
from cairn.core.mcp import load_mcp_toolsets
from cairn.core.models import Config, cairnDeps
from cairn.core.persistence import MessageStore
from cairn.core.services.ask_user import AskUserService
from cairn.core.services.subagents import SubAgentService
from cairn.tools import register_tools


@dataclass
class cairnRuntime:
    config: Config
    cwd: str
    deps: cairnDeps
    chat_service: ChatService
    event_bus: EventBus
    fs: FileSystem
    toolsets: list

    def close(self) -> None:
        try:
            self.fs.close()
        except Exception:
            pass


def build_runtime(config: Config | None = None, cwd: str | None = None) -> cairnRuntime:
    cwd = cwd or os.getcwd()
    cfg = config or Config()

    event_bus: EventBus = EventBus()
    toolsets = load_mcp_toolsets(cwd)

    fs = FileSystem(cwd, auto_watch=True)

    deps = cairnDeps(config=cfg, cwd=cwd, fs=fs)
    deps.event_bus = event_bus
    deps.ask_user_service = AskUserService(event_bus)

    def _subagent_factory():
        return create_agent(cfg, cwd=cwd, toolsets=toolsets, register_tools=register_tools)

    deps.subagent_service = SubAgentService(deps=deps, event_bus=event_bus, agent_factory=_subagent_factory)

    agent = create_agent(cfg, cwd=cwd, toolsets=toolsets, register_tools=register_tools)
    store = MessageStore()
    # assembly 在配置好 embedder + compact LLM 后通过 build_runtime 参数注入，
    # 初期为 None（退化为直接模式）
    chat_service = ChatService(agent=agent, deps=deps, store=store)
    return cairnRuntime(config=cfg, cwd=cwd, deps=deps, chat_service=chat_service, event_bus=event_bus, fs=fs, toolsets=toolsets)

