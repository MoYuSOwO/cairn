"""
MiniCC TUI 应用（多行输入 + @ 引用文件）

关键点：
- 输入使用 TextArea（ChatInput）：Enter 提交；Ctrl+J 换行
- 内嵌模式：直接消费 ChatService stream events
- 远程模式：通过 WebSocket 消费后端事件
- ask_user/todo/subagent：消费事件总线（内嵌）或 WS 事件（远程）
"""

from __future__ import annotations

import os
import re
import subprocess
import traceback
from typing import Any

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, VerticalScroll
from textual.widgets import Footer, Header

from minicc.core.chat_events import Done, Error, TextDelta, ToolFinished, ToolStarted
from minicc.core.config import load_config
from minicc.core.events import (
    AskUserRequested,
    SubAgentCreated,
    SubAgentUpdated,
    TodoUpdated,
)
from minicc.core.models import UserCancelledError
from minicc.core.runtime import MiniCCRuntime, build_runtime
from minicc.tui.ask_user_panel import AskUserPanel
from minicc.tui.chat_input import ChatInput
from minicc.tui.client import ChatClient
from minicc.tui.file_mention_panel import FileMentionPanel
from minicc.tui.widgets import BottomBar, MessagePanel, SubAgentLine, TodoDisplay, ToolCallLine


class MiniCCApp(App):
    TITLE = "MiniCC"
    CSS_PATH = "styles.tcss"

    BINDINGS = [
        Binding("ctrl+c", "quit", "退出", priority=True),
        Binding("ctrl+l", "clear", "清屏"),
        Binding("escape", "cancel", "取消"),
    ]

    def __init__(self, runtime: MiniCCRuntime | None = None, server_url: str | None = None):
        super().__init__()
        self._client: ChatClient | None = None
        if server_url:
            self._mode = "client"
            self._client = ChatClient(server_url)
            self._cwd = os.getcwd()
            self._model_info = "remote"
        else:
            self._mode = "embedded"
            config = load_config()
            self.runtime = runtime or build_runtime(config=config, cwd=os.getcwd())
            self._cwd = self.runtime.cwd
            self._model_info = f"{self.runtime.config.provider.value}:{self.runtime.config.model}"
        self._is_processing = False
        self._git_branch = self._get_git_branch()

        self._tool_lines: dict[str, ToolCallLine] = {}
        self._subagent_lines: dict[str, SubAgentLine] = {}
        self._current_ask_panel: AskUserPanel | None = None
        self._streaming_assistant_panel: MessagePanel | None = None

        # @ 引用文件
        self._mention_active = False
        self._mention_at_pos: int | None = None  # 当前行内 @ 的索引
        self._mention_query = ""
        self._mention_items: list[str] = []
        self._mention_selected = 0

    def _get_git_branch(self) -> str | None:
        try:
            result = subprocess.run(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                capture_output=True,
                text=True,
                cwd=self._cwd,
                timeout=2,
            )
            if result.returncode == 0:
                return result.stdout.strip()
        except Exception:
            pass
        return None

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield VerticalScroll(id="chat_container")
        yield TodoDisplay(id="todo_display")
        yield Container(id="ask_user_container")
        yield Container(id="mention_container")
        yield ChatInput(
            id="input",
            placeholder="输入消息… Enter 发送，Ctrl+J 换行",
            soft_wrap=True,
            show_line_numbers=False,
        )
        yield BottomBar(
            model=self._model_info,
            cwd=self._cwd,
            git_branch=self._git_branch,
            id="bottom_bar",
        )
        yield Footer(id="footer")

    def on_mount(self) -> None:
        input_widget = self.query_one("#input", ChatInput)
        input_widget.focus()
        input_widget.set_mention_key_handler(self._handle_mention_key)

        self.query_one("#todo_display", TodoDisplay).display = False
        self.query_one("#ask_user_container", Container).display = False
        self.query_one("#mention_container", Container).display = False
        self._show_welcome()
        if self._mode == "embedded":
            self._wait_fs_ready()
            self._consume_events()
        else:
            self._connect_ws()

    @work(thread=True, group="startup")
    def _wait_fs_ready(self) -> None:
        try:
            self.runtime.fs.wait_ready(timeout=30.0)
        except Exception:
            pass

    def _show_welcome(self) -> None:
        self._append_message("**MiniCC** - 极简 AI 编程助手\n\n输入问题开始对话，Ctrl+C 退出", role="system")

    async def on_chat_input_submitted(self, event: ChatInput.Submitted) -> None:
        raw_input = event.value
        if not raw_input.strip():
            return

        if self._is_processing:
            self._append_message("⚠️ 请等待当前请求完成...", role="system")
            return

        input_widget = self.query_one("#input", ChatInput)
        self._hide_mention_panel()
        input_widget.text = ""
        input_widget.cursor_location = (0, 0)
        input_widget.call_after_refresh(input_widget.scroll_cursor_visible)

        user_input = raw_input
        self._append_message(user_input, role="user")

        self._streaming_assistant_panel = None
        self._process_message(user_input)

    def on_text_area_changed(self, event: ChatInput.Changed) -> None:
        if getattr(event, "text_area", None) is None or event.text_area.id != "input":
            return

        if self._current_ask_panel is not None:
            self._hide_mention_panel()
            return

        input_widget = self.query_one("#input", ChatInput)
        cursor_row, cursor_col = input_widget.cursor_location
        lines = input_widget.text.split("\n")
        current_line = lines[cursor_row] if 0 <= cursor_row < len(lines) else ""
        prefix = current_line[:cursor_col]

        at_info = _find_at_reference(prefix, len(prefix))
        if at_info is None:
            self._hide_mention_panel()
            return

        at_pos, query = at_info
        if query == "":
            self._show_mention_panel(at_pos, query, [])
            return

        items = self._search_files_for_mention(query)
        self._show_mention_panel(at_pos, query, items)

    @work(exclusive=True, group="chat")
    async def _process_message(self, user_input: str) -> None:
        if self._mode == "client":
            await self._client.submit(user_input)
        else:
            await self._process_message_embedded(user_input)

    async def _process_message_embedded(self, user_input: str) -> None:
        self._is_processing = True
        streamed_text = ""
        try:
            async for event in self.runtime.chat_service.send_message(user_input):
                if isinstance(event, TextDelta):
                    streamed_text = event.content
                    self._update_streaming_assistant(streamed_text)

                elif isinstance(event, ToolStarted):
                    line = ToolCallLine(event.tool_name, event.args, status="running")
                    self._tool_lines[event.tool_call_id] = line
                    self._chat_container().mount(line)
                    self._ensure_stream_panel_last()
                    self._scroll_chat_end()

                elif isinstance(event, ToolFinished):
                    line = self._tool_lines.get(event.tool_call_id)
                    if line is None:
                        line = ToolCallLine(event.tool_name, {}, status="running")
                        self._tool_lines[event.tool_call_id] = line
                        self._chat_container().mount(line)
                    line.update_status("completed" if event.ok else "failed")
                    self._ensure_stream_panel_last()
                    self._scroll_chat_end()

                elif isinstance(event, Done):
                    if self._streaming_assistant_panel is not None:
                        self._streaming_assistant_panel.set_content(streamed_text)
                        self._scroll_chat_end()
                    else:
                        self._append_message(streamed_text, role="assistant")
                    if event.usage:
                        self._update_tokens(event.usage)

                elif isinstance(event, Error):
                    if isinstance(event.exception, UserCancelledError):
                        self._append_message("⚠️ 操作已取消", role="system")
                    else:
                        msg = str(event.exception)
                        if os.environ.get("MINICC_DEBUG"):
                            tb = traceback.format_exc()
                            msg = f"{msg}\n\n```text\n{tb}\n```"
                        self._append_message(f"❌ 错误: {msg}", role="system")

        finally:
            self._is_processing = False
            self._scroll_chat_end()

    @work(group="startup")
    async def _connect_ws(self) -> None:
        try:
            await self._client.connect()
        except Exception as e:
            self._append_message(f"❌ 连接后端失败: {e}", role="system")
            return
        self._append_message("已连接到后端", role="system")
        self._consume_ws_events()

    @work(group="events")
    async def _consume_ws_events(self) -> None:
        rid_streaming: dict[str, str] = {}  # request_id → accumulated text
        while True:
            msg = await self._client.stream.get()
            etype = msg.get("type", "")
            rid = msg.get("request_id", "")

            if etype == "request_started":
                self._append_message(f"处理中... ({rid[:6]}: {msg.get('text', '')[:50]})", role="system")
                rid_streaming[rid] = ""
                self._streaming_assistant_panel = None

            elif etype == "text_delta":
                if rid in rid_streaming:
                    rid_streaming[rid] = msg["content"]
                    if self._streaming_assistant_panel is None:
                        self._streaming_assistant_panel = self._append_message("", role="assistant")
                    self._streaming_assistant_panel.set_content(msg["content"])
                    self._scroll_chat_end()

            elif etype == "tool_started":
                line = ToolCallLine(msg["tool_name"], msg.get("args"), status="running")
                self._tool_lines[msg["tool_call_id"]] = line
                self._chat_container().mount(line)
                self._ensure_stream_panel_last()
                self._scroll_chat_end()

            elif etype == "tool_finished":
                line = self._tool_lines.get(msg["tool_call_id"])
                if line is None:
                    line = ToolCallLine(msg["tool_name"], {}, status="running")
                    self._tool_lines[msg["tool_call_id"]] = line
                    self._chat_container().mount(line)
                line.update_status("completed" if msg.get("ok") else "failed")
                self._ensure_stream_panel_last()
                self._scroll_chat_end()

            elif etype == "done":
                text = rid_streaming.pop(rid, "")
                if self._streaming_assistant_panel is not None:
                    self._streaming_assistant_panel.set_content(text)
                    self._scroll_chat_end()
                    self._streaming_assistant_panel = None
                elif text:
                    self._append_message(text, role="assistant")
                usage = msg.get("usage")
                if usage:
                    self._update_tokens(usage)

            elif etype == "request_finished":
                rid_streaming.pop(rid, None)
                self._streaming_assistant_panel = None

            elif etype == "error":
                rid_streaming.pop(rid, None)
                err_msg = msg.get("message", "")
                if "操作已取消" in err_msg:
                    self._append_message("⚠️ 操作已取消", role="system")
                else:
                    self._append_message(f"❌ 错误: {err_msg}", role="system")
                self._streaming_assistant_panel = None

            elif etype == "history_snapshot":
                self._rebuild_from_snapshot(msg.get("messages", []))

            elif etype == "todo_updated":
                self._handle_todo_dict(msg)
            elif etype == "ask_user_requested":
                self._handle_ask_user_dict(msg)
            elif etype == "subagent_created":
                self._handle_subagent_created_dict(msg)
            elif etype == "subagent_updated":
                self._handle_subagent_updated_dict(msg)

    @work(group="events")
    async def _consume_events(self) -> None:
        async for ev in self.runtime.event_bus.iter():
            if isinstance(ev, TodoUpdated):
                self._on_todo_updated(ev)
            elif isinstance(ev, AskUserRequested):
                self._on_ask_user_requested(ev)
            elif isinstance(ev, SubAgentCreated):
                self._on_subagent_created(ev)
            elif isinstance(ev, SubAgentUpdated):
                self._on_subagent_updated(ev)

    def _on_todo_updated(self, ev: TodoUpdated) -> None:
        todo_display = self.query_one("#todo_display", TodoDisplay)
        todo_display.update_todos(ev.todos)
        todo_display.display = len(ev.todos) > 0

    def _on_ask_user_requested(self, ev: AskUserRequested) -> None:
        container = self.query_one("#ask_user_container", Container)
        container.remove_children()
        panel = AskUserPanel(ev.request_id, ev.questions)
        self._current_ask_panel = panel
        container.mount(panel)
        container.display = True

        main_input = self.query_one("#input", ChatInput)
        main_input.disabled = True
        self.call_later(panel.focus)

    def _hide_ask_panel(self) -> None:
        try:
            container = self.query_one("#ask_user_container", Container)
            container.remove_children()
            container.display = False
        except Exception:
            pass

        self._current_ask_panel = None
        try:
            main_input = self.query_one("#input", ChatInput)
            main_input.disabled = False
            main_input.focus()
        except Exception:
            pass

        self._hide_mention_panel()

    def on_ask_user_panel_submitted(self, event: AskUserPanel.Submitted) -> None:
        if self._mode == "client":
            import asyncio
            asyncio.create_task(self._client.send_ask_user_response(event.request_id, True, event.answers))
        else:
            self.runtime.deps.ask_user_service.resolve(event.request_id, submitted=True, answers=event.answers)
        self._hide_ask_panel()

    def on_ask_user_panel_cancelled(self, event: AskUserPanel.Cancelled) -> None:
        if self._mode == "client":
            import asyncio
            asyncio.create_task(self._client.send_ask_user_response(event.request_id, False, {}))
        else:
            self.runtime.deps.ask_user_service.resolve(event.request_id, submitted=False, answers={})
        self._hide_ask_panel()

    def _on_subagent_created(self, ev: SubAgentCreated) -> None:
        line = SubAgentLine(task_id=ev.task_id, prompt=ev.description or ev.prompt, status="pending")
        self._subagent_lines[ev.task_id] = line
        self._chat_container().mount(line)
        self._ensure_stream_panel_last()
        self._scroll_chat_end()

    def _on_subagent_updated(self, ev: SubAgentUpdated) -> None:
        line = self._subagent_lines.get(ev.task_id)
        if line is None:
            line = SubAgentLine(task_id=ev.task_id, prompt=ev.task_id, status=ev.status)
            self._subagent_lines[ev.task_id] = line
            self._chat_container().mount(line)
        line.update_status(ev.status)
        self._ensure_stream_panel_last()
        self._scroll_chat_end()

    # ---- dict-based handlers (WS client mode) ----

    def _handle_todo_dict(self, msg: dict) -> None:
        todo_display = self.query_one("#todo_display", TodoDisplay)
        todo_display.update_todos(msg.get("todos", []))
        todo_display.display = len(msg.get("todos", [])) > 0

    def _handle_ask_user_dict(self, msg: dict) -> None:
        container = self.query_one("#ask_user_container", Container)
        container.remove_children()
        panel = AskUserPanel(msg["request_id"], msg.get("questions", []))
        self._current_ask_panel = panel
        container.mount(panel)
        container.display = True
        main_input = self.query_one("#input", ChatInput)
        main_input.disabled = True
        self.call_later(panel.focus)

    def _handle_subagent_created_dict(self, msg: dict) -> None:
        line = SubAgentLine(task_id=msg["task_id"], prompt=msg.get("description") or msg.get("prompt", ""), status="pending")
        self._subagent_lines[msg["task_id"]] = line
        self._chat_container().mount(line)
        self._ensure_stream_panel_last()
        self._scroll_chat_end()

    def _handle_subagent_updated_dict(self, msg: dict) -> None:
        line = self._subagent_lines.get(msg["task_id"])
        if line is None:
            line = SubAgentLine(task_id=msg["task_id"], prompt=msg["task_id"], status=msg.get("status", ""))
            self._subagent_lines[msg["task_id"]] = line
            self._chat_container().mount(line)
        line.update_status(msg.get("status", ""))
        self._ensure_stream_panel_last()
        self._scroll_chat_end()

    def on_message_panel_rollback_requested(self, event: MessagePanel.RollbackRequested) -> None:
        input_widget = self.query_one("#input", ChatInput)
        input_widget.text = event.content
        input_widget.focus()
        if self._mode == "client":
            import asyncio
            asyncio.create_task(self._client.rollback_to(event.index))
        else:
            import asyncio
            asyncio.create_task(self.runtime.chat_service.rollback_to(event.index))

    def on_todo_display_closed(self, message: TodoDisplay.Closed) -> None:
        todo_display = self.query_one("#todo_display", TodoDisplay)
        todo_display.update_todos([])
        todo_display.display = False
        if self._mode == "embedded":
            self.runtime.deps.todos = []

    def action_clear(self) -> None:
        chat = self._chat_container()
        for child in list(chat.children):
            child.remove()
        if self._mode == "client":
            import asyncio
            asyncio.create_task(self._client.clear())
        else:
            import asyncio
            asyncio.create_task(self.runtime.chat_service.clear())
            self.runtime.deps.todos = []
        self._tool_lines.clear()
        self._subagent_lines.clear()
        self._streaming_assistant_panel = None

        try:
            bottom_bar = self.query_one(BottomBar)
            bottom_bar.update_info(input_tokens=0, output_tokens=0)
        except Exception:
            pass

        todo_display = self.query_one("#todo_display", TodoDisplay)
        todo_display.update_todos([])
        todo_display.display = False
        self._show_welcome()

    def action_quit(self) -> None:
        if self._mode == "client":
            import asyncio
            asyncio.create_task(self._client.disconnect())
        else:
            self.runtime.close()
        self.exit()

    def action_cancel(self) -> None:
        if self._is_processing:
            self._append_message("⚠️ 正在取消...", role="system")

    def _chat_container(self) -> VerticalScroll:
        return self.query_one("#chat_container", VerticalScroll)

    def _append_message(self, content: str, role: str = "assistant", history_index: int = -1) -> MessagePanel:
        panel = MessagePanel(content, role=role, history_index=history_index)
        chat = self._chat_container()
        chat.mount(panel)
        self._scroll_chat_end()
        return panel

    def _scroll_chat_end(self) -> None:
        chat = self._chat_container()
        chat.call_after_refresh(chat.scroll_end, animate=False)

    def _update_streaming_assistant(self, content: str) -> None:
        if self._streaming_assistant_panel is None:
            self._streaming_assistant_panel = self._append_message("", role="assistant")
        self._streaming_assistant_panel.set_content(content)
        self._scroll_chat_end()

    def _rebuild_from_snapshot(self, messages: list[dict]) -> None:
        chat = self._chat_container()
        for child in list(chat.children):
            if isinstance(child, MessagePanel):
                child.remove()
        self._tool_lines.clear()
        self._subagent_lines.clear()
        for i, msg in enumerate(messages):
            kind = msg.get("kind", "")
            role = "user" if kind == "request" else "assistant"
            parts = msg.get("parts", [])
            content_parts: list[str] = []
            for p in parts:
                c = p.get("content", "")
                if c:
                    content_parts.append(c)
            content = "\n".join(content_parts).strip()
            if content:
                self._append_message(content, role=role, history_index=i)

    def _ensure_stream_panel_last(self) -> None:
        if self._streaming_assistant_panel is None:
            return
        chat = self._chat_container()
        try:
            self._streaming_assistant_panel.remove()
        except Exception:
            return
        chat.mount(self._streaming_assistant_panel)

    def _update_tokens(self, usage: Any) -> None:
        try:
            bottom_bar = self.query_one(BottomBar)
            if isinstance(usage, dict):
                input_tokens = usage.get("input_tokens", 0)
                output_tokens = usage.get("output_tokens", 0)
            else:
                input_tokens = getattr(usage, "request_tokens", 0) or getattr(usage, "input_tokens", 0)
                output_tokens = getattr(usage, "response_tokens", 0) or getattr(usage, "output_tokens", 0)
            bottom_bar.add_tokens(input_tokens, output_tokens)
        except Exception:
            pass

    # ---------------- @ 引用文件 ----------------

    def _show_mention_panel(self, at_pos: int, query: str, items: list[str]) -> None:
        self._mention_active = True
        self._mention_at_pos = at_pos
        self._mention_query = query
        self._mention_items = items
        self._mention_selected = 0
        self._refresh_mention_panel()
        self.call_later(self.query_one("#input", ChatInput).focus)

    def _refresh_mention_panel(self) -> None:
        container = self.query_one("#mention_container", Container)
        container.remove_children()
        panel = FileMentionPanel(self._mention_query, self._mention_items, self._mention_selected)
        container.mount(panel)
        container.display = True

    def _hide_mention_panel(self) -> None:
        if not self._mention_active:
            return
        self._mention_active = False
        self._mention_at_pos = None
        self._mention_query = ""
        self._mention_items = []
        self._mention_selected = 0
        try:
            container = self.query_one("#mention_container", Container)
            container.remove_children()
            container.display = False
        except Exception:
            pass

    def _handle_mention_key(self, key: str) -> bool:
        if not self._mention_active:
            return False
        if key == "escape":
            self._hide_mention_panel()
            return True
        if key in ("up", "down"):
            if not self._mention_items:
                return True
            if key == "up":
                self._mention_selected = max(0, self._mention_selected - 1)
            else:
                self._mention_selected = min(len(self._mention_items) - 1, self._mention_selected + 1)
            self._refresh_mention_panel()
            return True
        if key in ("tab", "enter"):
            if self._mention_items:
                self._accept_mention()
                return True
        return False

    def _accept_mention(self) -> None:
        if not self._mention_items or self._mention_at_pos is None:
            self._hide_mention_panel()
            return

        selected = self._mention_items[self._mention_selected]
        input_widget = self.query_one("#input", ChatInput)
        cursor_row, cursor_col = input_widget.cursor_location
        lines = (input_widget.text.split("\n") or [""])
        if cursor_row >= len(lines):
            lines += [""] * (cursor_row - len(lines) + 1)

        line = lines[cursor_row]
        at_pos = self._mention_at_pos
        before = line[: at_pos + 1]
        after = line[cursor_col:]
        insert = selected + " "
        lines[cursor_row] = before + insert + after
        input_widget.text = "\n".join(lines)
        input_widget.cursor_location = (cursor_row, len(before) + len(insert))
        input_widget.call_after_refresh(input_widget.scroll_cursor_visible)

        self._hide_mention_panel()
        self.call_later(input_widget.focus)

    def _search_files_for_mention(self, query: str) -> list[str]:
        fs = getattr(self.runtime, "fs", None) if self._mode == "embedded" else None
        ignored = {".git", ".venv", "dist", "__pycache__", ".pytest_cache"}

        def is_ignored(p: str) -> bool:
            parts = p.split("/")
            return any(x in ignored for x in parts)

        patterns: list[str]
        if "/" in query or query.startswith("."):
            patterns = [f"{query}*"]
        else:
            patterns = [f"**/*{query}*"]

        results: list[str] = []
        seen: set[str] = set()
        for pat in patterns:
            try:
                matches = fs.glob(pat) if fs is not None else []
            except Exception:
                matches = []
            for m in matches:
                if m in seen:
                    continue
                if is_ignored(m):
                    continue
                try:
                    if not (self._cwd and os.path.isfile(os.path.join(self._cwd, m))):
                        continue
                except Exception:
                    continue
                seen.add(m)
                results.append(m)
                if len(results) >= 100:
                    return results
        return results


_AT_PATTERN = re.compile(r"(^|[\\s\\(\\[\\{\\\"'])@([^\\s@]*)$")


def _find_at_reference(text: str, cursor_pos: int) -> tuple[int, str] | None:
    prefix = text[:cursor_pos]
    m = _AT_PATTERN.search(prefix)
    if not m:
        return None
    at_pos = m.start(0) + len(m.group(1))
    query = m.group(2)
    return at_pos, query


def run_embedded() -> None:
    MiniCCApp().run()


def run_client(server_url: str = "ws://127.0.0.1:8720/ws") -> None:
    MiniCCApp(server_url=server_url).run()
