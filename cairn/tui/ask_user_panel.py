"""
cairn Ask User Panel 组件

提供 ask_user 工具的可交互问答面板。
"""

from __future__ import annotations

from rich.panel import Panel
from rich.text import Text
from textual.message import Message
from textual.widgets import Static

from cairn.core.models import Question


class AskUserPanel(Static, can_focus=True):
    BINDINGS = [
        ("left", "prev_question", "上一个问题"),
        ("right", "next_question", "下一个问题"),
        ("up", "prev_option", "上一个选项"),
        ("down", "next_option", "下一个选项"),
        ("enter", "select_option", "选择"),
        ("escape", "cancel", "取消"),
    ]

    class Submitted(Message):
        def __init__(self, request_id: str, answers: dict[str, str | list[str]]):
            self.request_id = request_id
            self.answers = answers
            super().__init__()

    class Cancelled(Message):
        def __init__(self, request_id: str):
            self.request_id = request_id
            super().__init__()

    def __init__(self, request_id: str, questions: list[Question], **kwargs):
        self.request_id = request_id
        self.questions = questions
        self.current_question = 0
        self.current_option = 0
        self.typing_mode = False
        self.typing_buffer = ""
        self.answers: dict[int, int | set[int]] = {}
        self.custom_inputs: dict[int, str] = {}

        for i, q in enumerate(self.questions):
            self.answers[i] = set() if q.multi_select else -1
        super().__init__(**kwargs)

    def render(self) -> Panel:
        text = Text()
        text.append("← ", style="dim")
        for i, q in enumerate(self.questions):
            if i == self.current_question:
                text.append(f" □ {q.header} ", style="bold reverse magenta")
            else:
                text.append(f" □ {q.header} ", style="green" if self._is_answered(i) else "dim")
        text.append(" →\n\n", style="dim")

        q = self.questions[self.current_question]
        multi_hint = "（可多选）" if q.multi_select else ""
        text.append(f"{q.question} {multi_hint}\n\n", style="bold cyan")

        for j, opt in enumerate(q.options):
            is_selected = self.current_option == j and not self.typing_mode
            is_checked = self._is_option_checked(self.current_question, j)

            text.append("❯ " if is_selected else "  ", style="bold yellow" if is_selected else "")
            text.append(f"{j + 1}. ", style="bold" if is_selected else "dim")

            if is_checked:
                text.append(opt.label, style="bold green")
                if q.multi_select:
                    text.append(" ✓", style="green")
            else:
                text.append(opt.label, style="bold" if is_selected else "")
            text.append("\n")
            if opt.description:
                text.append(f"    {opt.description}\n", style="dim italic")

        custom_idx = len(q.options)
        is_selected = self.current_option == custom_idx or self.typing_mode
        is_custom_active = self._is_custom_selected(self.current_question)
        text.append("❯ " if is_selected else "  ", style="bold yellow" if is_selected else "")
        text.append(f"{custom_idx + 1}. ", style="bold" if is_selected else "dim")

        custom_text = self.custom_inputs.get(self.current_question, "")
        if self.typing_mode:
            text.append("其他（自定义）：", style="dim")
            text.append(self.typing_buffer, style="bold cyan")
            text.append("█", style="bold cyan blink")
        elif custom_text:
            text.append(
                f"其他（自定义）：{custom_text}",
                style="bold green" if is_custom_active else "green",
            )
            if q.multi_select and is_custom_active:
                text.append(" ✓", style="green")
        else:
            text.append("其他（自定义输入）", style="bold" if is_selected else "dim italic")

        text.append("\n\n")

        all_answered = all(self._is_answered(i) for i in range(len(self.questions)))
        if self.typing_mode:
            text.append("输入内容，Enter 确认，Esc 取消", style="dim")
        elif all_answered:
            text.append("✔ 全部已选择，按 ", style="green")
            text.append("S", style="bold green reverse")
            text.append(" 提交 · Esc 取消", style="green")
        else:
            text.append("Enter 选择 · ←→ 切换问题 · ↑↓ 移动 · Esc 取消", style="dim")

        border_style = "#f59e0b" if self.has_focus else "cyan"
        return Panel(text, title="请回答以下问题", border_style=border_style, padding=(0, 1))

    def on_focus(self) -> None:
        self.refresh()

    def on_blur(self) -> None:
        self.refresh()

    def _is_answered(self, q_idx: int) -> bool:
        ans = self.answers.get(q_idx)
        if isinstance(ans, set):
            return len(ans) > 0
        if isinstance(ans, int):
            return ans >= 0
        return False

    def _is_option_checked(self, q_idx: int, opt_idx: int) -> bool:
        ans = self.answers.get(q_idx)
        if isinstance(ans, set):
            return opt_idx in ans
        if isinstance(ans, int):
            return ans == opt_idx
        return False

    def _is_custom_selected(self, q_idx: int) -> bool:
        ans = self.answers.get(q_idx)
        custom_idx = len(self.questions[q_idx].options)
        if isinstance(ans, set):
            return custom_idx in ans
        if isinstance(ans, int):
            return ans == custom_idx
        return False

    def action_prev_question(self) -> None:
        if self.typing_mode:
            return
        if self.current_question > 0:
            self.current_question -= 1
            self.current_option = 0
            self.refresh()

    def action_next_question(self) -> None:
        if self.typing_mode:
            return
        if self.current_question < len(self.questions) - 1:
            self.current_question += 1
            self.current_option = 0
            self.refresh()

    def action_prev_option(self) -> None:
        if self.typing_mode:
            return
        if self.current_option > 0:
            self.current_option -= 1
            self.refresh()

    def action_next_option(self) -> None:
        if self.typing_mode:
            return
        q = self.questions[self.current_question]
        max_option = len(q.options)
        if self.current_option < max_option:
            self.current_option += 1
            self.refresh()

    def action_select_option(self) -> None:
        if self.typing_mode:
            self._confirm_typing()
            return

        q = self.questions[self.current_question]
        custom_idx = len(q.options)

        if self.current_option == custom_idx:
            self.typing_mode = True
            self.typing_buffer = self.custom_inputs.get(self.current_question, "")
            self.refresh()
            return

        if q.multi_select:
            ans = self.answers[self.current_question]
            if isinstance(ans, set):
                if self.current_option in ans:
                    ans.remove(self.current_option)
                else:
                    ans.add(self.current_option)
        else:
            self.answers[self.current_question] = self.current_option
        self.refresh()

    def _confirm_typing(self) -> None:
        q = self.questions[self.current_question]
        custom_idx = len(q.options)
        value = self.typing_buffer.strip()

        self.custom_inputs[self.current_question] = value
        self.typing_mode = False

        if q.multi_select:
            ans = self.answers[self.current_question]
            if isinstance(ans, set):
                if value:
                    ans.add(custom_idx)
                else:
                    ans.discard(custom_idx)
        else:
            self.answers[self.current_question] = custom_idx if value else -1
        self.refresh()

    def on_key(self, event) -> None:
        if not self.typing_mode:
            if event.key == "s" and all(self._is_answered(i) for i in range(len(self.questions))):
                self._submit()
            return

        if event.key == "escape":
            self.typing_mode = False
            self.refresh()
            return

        if event.key == "backspace":
            self.typing_buffer = self.typing_buffer[:-1]
            self.refresh()
            return

        if event.character and len(event.character) == 1:
            self.typing_buffer += event.character
            self.refresh()

    def action_cancel(self) -> None:
        self.post_message(self.Cancelled(self.request_id))

    def _submit(self) -> None:
        answers_out: dict[str, str | list[str]] = {}
        for i, q in enumerate(self.questions):
            ans = self.answers.get(i)
            if isinstance(ans, set):
                selected: list[str] = []
                for idx in sorted(ans):
                    if idx < len(q.options):
                        selected.append(q.options[idx].label)
                    else:
                        selected.append(self.custom_inputs.get(i, ""))
                answers_out[q.header] = [v for v in selected if v]
            elif isinstance(ans, int):
                if ans < 0:
                    continue
                if ans < len(q.options):
                    answers_out[q.header] = q.options[ans].label
                else:
                    answers_out[q.header] = self.custom_inputs.get(i, "")
        self.post_message(self.Submitted(self.request_id, answers_out))
