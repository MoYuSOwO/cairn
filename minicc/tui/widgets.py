"""
MiniCC TUI 组件

说明：本版本将“工具调用状态更新”作为一等能力，ToolCallLine/SubAgentLine 支持状态刷新。
"""

from __future__ import annotations

from rich.markdown import Markdown
from rich.panel import Panel
from rich.text import Text
from textual.message import Message
from textual.widgets import Static

from minicc.core.models import DiffLine, TodoItem


class MessagePanel(Static):
    class RollbackRequested(Message):
        """用户点击了某条消息，请求回滚到该位置。"""

        def __init__(self, index: int, content: str):
            self.index = index
            self.content = content
            super().__init__()

    def __init__(self, content: str, role: str = "user", history_index: int = -1, **kwargs):
        self.role = role
        self._content = content
        self.history_index = history_index
        super().__init__(content, markup=False, **kwargs)

    def set_content(self, content: str) -> None:
        self._content = content
        self.update(content)

    def on_click(self):
        if self.history_index >= 0:
            self.post_message(self.RollbackRequested(self.history_index, self._content))

    def render(self) -> Panel:
        role_style = {
            "user": ("blue", "You"),
            "assistant": ("green", "Assistant"),
            "system": ("magenta", "System"),
        }
        color, title = role_style.get(self.role, ("white", self.role.title()))
        markdown = Markdown(self._content or "", code_theme="monokai", justify="left")
        return Panel(markdown, title=title, border_style=color, expand=True)


class ToolCallLine(Static):
    def __init__(self, tool_name: str, args: dict | None, status: str = "running", **kwargs):
        self.tool_name = tool_name
        self.args = args or {}
        self.status = status
        super().__init__(**kwargs)

    def update_status(self, status: str) -> None:
        self.status = status
        self.refresh()

    def render(self) -> Text:
        text = Text()
        text.append("  🔧 ", style="yellow")
        text.append(self.tool_name, style="bold yellow")

        summary = self._get_summary()
        if summary:
            text.append(f" {summary}", style="dim")

        icon = {
            "pending": " ⏳",
            "running": " 🔄",
            "completed": " ✅",
            "failed": " ❌",
        }.get(self.status, " ❓")
        style = {"completed": "green", "failed": "red", "running": "yellow", "pending": "dim"}.get(
            self.status, "dim"
        )
        text.append(icon, style=style)
        return text

    def _get_summary(self) -> str:
        key_params = ["path", "file_path", "pattern", "command", "query", "prompt"]
        for key in key_params:
            if key in self.args:
                value = str(self.args[key])
                if len(value) > 40:
                    value = value[:40] + "..."
                return f"({value})"
        return ""


class SubAgentLine(Static):
    def __init__(self, task_id: str, prompt: str, status: str, **kwargs):
        self.task_id = task_id
        self.prompt = prompt
        self.status = status
        super().__init__(**kwargs)

    def update_status(self, status: str) -> None:
        self.status = status
        self.refresh()

    def render(self) -> Text:
        text = Text()
        text.append("  🤖 ", style="magenta")
        prompt_short = self.prompt[:50] + "..." if len(self.prompt) > 50 else self.prompt
        text.append(prompt_short, style="bold magenta")
        icon = {
            "pending": " ⏳",
            "running": " 🔄",
            "completed": " ✅",
            "failed": " ❌",
        }.get(self.status, " ❓")
        text.append(icon)
        return text


class DiffView(Static):
    def __init__(self, diff_lines: list[DiffLine], filename: str = "", **kwargs):
        self.diff_lines = diff_lines
        self.filename = filename
        super().__init__(**kwargs)

    def render(self) -> Panel:
        text = Text()
        for line in self.diff_lines:
            if line.type == "add":
                text.append(f"+ {line.content}\n", style="green")
            elif line.type == "remove":
                text.append(f"- {line.content}\n", style="red")
            else:
                text.append(f"  {line.content}\n", style="dim")
        title = f"Diff: {self.filename}" if self.filename else "Diff"
        return Panel(text, title=title, border_style="cyan", expand=True)


class BottomBar(Static):
    def __init__(
        self,
        model: str = "",
        cwd: str = "",
        git_branch: str | None = None,
        input_tokens: int = 0,
        output_tokens: int = 0,
        **kwargs,
    ):
        self.model = model
        self.cwd = cwd
        self.git_branch = git_branch
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        super().__init__(**kwargs)

    def update_info(
        self,
        model: str | None = None,
        cwd: str | None = None,
        git_branch: str | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
    ) -> None:
        if model is not None:
            self.model = model
        if cwd is not None:
            self.cwd = cwd
        if git_branch is not None:
            self.git_branch = git_branch
        if input_tokens is not None:
            self.input_tokens = input_tokens
        if output_tokens is not None:
            self.output_tokens = output_tokens
        self.refresh()

    def add_tokens(self, input_delta: int = 0, output_delta: int = 0) -> None:
        self.input_tokens += input_delta
        self.output_tokens += output_delta
        self.refresh()

    def render(self) -> Text:
        text = Text()
        text.append(" 📦 ", style="dim")
        text.append(self.model or "N/A", style="cyan")
        text.append("  │  ", style="dim")

        text.append("📁 ", style="dim")
        cwd_short = self.cwd
        if len(cwd_short) > 25:
            cwd_short = "..." + cwd_short[-22:]
        text.append(cwd_short, style="green")
        text.append("  │  ", style="dim")

        text.append("🌿 ", style="dim")
        text.append(self.git_branch or "N/A", style="magenta" if self.git_branch else "dim")
        text.append("  │  ", style="dim")

        # 说明：部分终端/字体对 emoji（如 ⬆️/⬇️）支持不佳，容易显示为方块或宽度异常；
        # 因此使用更通用的箭头字符。
        text.append("↑", style="dim")
        text.append(f"{self.input_tokens}", style="yellow")
        text.append(" ↓", style="dim")
        text.append(f"{self.output_tokens}", style="yellow")
        return text


class TodoDisplay(Static):
    class Closed(Message):
        pass

    def __init__(self, todos: list[TodoItem] | None = None, **kwargs):
        self.todos: list[TodoItem] = todos or []
        super().__init__(**kwargs)

    def update_todos(self, todos: list[TodoItem]) -> None:
        self.todos = todos
        self.refresh()

    def is_all_completed(self) -> bool:
        if not self.todos:
            return False
        return all(t.status == "completed" for t in self.todos)

    async def on_click(self, event) -> None:
        if self.is_all_completed():
            if event.x >= self.size.width - 6:
                self.post_message(self.Closed())

    def render(self) -> Panel:
        if not self.todos:
            return Panel(Text("暂无任务", style="dim"), title="📋 任务", border_style="dim")

        text = Text()
        pending = [t for t in self.todos if t.status in ("pending", "in_progress")]
        completed = [t for t in self.todos if t.status == "completed"]
        total = len(self.todos)
        done = len(completed)

        if pending:
            for todo in pending:
                if todo.status == "in_progress":
                    text.append("🔄 ", style="yellow")
                    text.append(f"{todo.active_form}\n", style="yellow bold")
                else:
                    text.append("⏳ ", style="dim")
                    text.append(f"{todo.content}\n", style="dim")

        if completed:
            if pending:
                text.append("─" * 20 + "\n", style="dim")
            text.append(f"✅ 已完成 {done} 项", style="green dim")
            recent = completed[-3:] if len(completed) > 3 else completed
            for todo in recent:
                text.append(f"\n   ✓ {todo.content}", style="green dim")
            if len(completed) > 3:
                text.append(f"\n   ... 及其他 {len(completed) - 3} 项", style="dim")

        all_done = done == total and total > 0
        title = "📋 任务 ✓ 全部完成 [×]" if all_done else f"📋 任务 [{done}/{total}]"
        border = "green" if all_done else "cyan"
        return Panel(text, title=title, title_align="left", border_style=border)
