from __future__ import annotations

import json
import re
from collections import Counter
from json import JSONDecodeError

from pydantic_ai import RunContext
from pydantic import TypeAdapter, ValidationError

from cairn.core.models import cairnDeps, Question, QuestionOption, ToolResult


def _normalize_ask_user_questions(questions: list[Question]) -> list[Question]:
    """
    归一化 ask_user 的 questions 输入。

    - 清理 header 空白并保证唯一（避免答案 key 覆盖）
    - question 为空时回退为 header
    - 选项 label 做 strip（展示更稳定）
    """

    normalized: list[Question] = []
    provisional_headers: list[str] = []

    for i, q in enumerate(questions):
        question_text = (q.question or "").strip()
        header = (q.header or "").strip()
        header = re.sub(r"\s+", " ", header).strip()
        provisional_headers.append(header)

        options = [
            QuestionOption(label=(opt.label or "").strip(), description=opt.description or "")
            for opt in q.options or []
        ]

        normalized.append(
            Question(
                question=question_text or header,
                header=header,
                options=options,
                multi_select=bool(q.multi_select),
            )
        )

    # 保证 header 非空且唯一，避免 answers_out key 覆盖。
    counts = Counter(provisional_headers)
    used: dict[str, int] = {}
    for idx, question in enumerate(normalized):
        header = question.header
        if counts[header] <= 1:
            continue
        used[header] = used.get(header, 0) + 1
        normalized[idx] = question.model_copy(update={"header": f"{header}#{used[header]}"})

    return normalized


def _validate_and_normalize_ask_user_questions(payload: object) -> list[Question]:
    """
    将 LLM 传入的 ask_user 参数转为 list[Question]，并做强校验，避免 UI 异常与难懂的 AttributeError。
    """

    data: object = payload
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except JSONDecodeError as e:
            raise ValueError(
                "ask_user 参数错误：`questions` 必须是 JSON 数组（list），每项包含 `header`、`question`、`options`。"
            ) from e

    if isinstance(data, dict) and "questions" in data:
        data = data["questions"]

    try:
        questions = TypeAdapter(list[Question]).validate_python(data)
    except ValidationError as e:
        first = e.errors()[0] if e.errors() else {"loc": (), "msg": str(e)}
        loc = ".".join(str(x) for x in first.get("loc", ())) or "questions"
        msg = first.get("msg", "参数不合法")
        raise ValueError(f"ask_user 参数错误：{loc} - {msg}") from e

    for i, q in enumerate(questions):
        header = (q.header or "").strip()
        if not header:
            raise ValueError(f"ask_user 参数错误：第 {i + 1} 题缺少 `header`（必须提供且唯一）")

        if not q.options or all(not (opt.label or "").strip() for opt in q.options):
            raise ValueError(f"ask_user 参数错误：第 {i + 1} 题缺少 `options`（至少 1 个，且每个需有 label）")

    return _normalize_ask_user_questions(questions)


async def ask_user(ctx: RunContext[cairnDeps], questions: list[Question]) -> ToolResult:
    """
    向用户提出 1~N 个选择题/多选题，并等待用户在 TUI 面板中提交或取消。

    重要：每个问题必须提供 `header` 与 `options`：
    - `header`：简短且唯一，用作答案 key（避免重复导致覆盖）
    - `question`：给用户看的问题文本
    - `options`：至少 1 个选项（推荐 2~6 个）

    UI 会额外提供“其他（自定义输入）”一项，允许用户自由输入。
    """
    service = ctx.deps.ask_user_service
    if service is None:
        return ToolResult(success=False, output="", error="ask_user_service 未初始化")

    try:
        parsed_questions = _validate_and_normalize_ask_user_questions(questions)
    except ValueError as e:
        return ToolResult(
            success=False,
            output="",
            error=(
                f"{e}\n示例："
                '\n[{"header":"语言","question":"你主要用什么编程语言？","options":[{"label":"Python"},{"label":"TypeScript"}]}]'
            ),
        )

    result = await service.ask(parsed_questions)
    return ToolResult(success=True, output=json.dumps(result.answers, ensure_ascii=False))
