from __future__ import annotations

import asyncio

from pydantic_ai import RunContext

from cairn.core.events import TodoUpdated
from cairn.core.models import cairnDeps, TodoItem, ToolResult


async def task(
    ctx: RunContext[cairnDeps],
    prompt: str,
    description: str,
    subagent_type: str = "general-purpose",
    wait: bool = True,
) -> ToolResult:
    """
    运行子代理任务。

    - 默认 wait=True：等待子代理完成后返回结果文本（便于主 Agent 整合后继续推理）。
    - wait=False：后台启动并立即返回 task_id，可用 wait_subagents 再统一等待与汇总。
    """
    service = ctx.deps.subagent_service
    if service is None:
        return ToolResult(success=False, output="", error="subagent_service 未初始化")

    task_id, result = await service.run(
        prompt=prompt,
        description=description,
        subagent_type=subagent_type,
        background=not wait,
    )

    if not wait:
        return ToolResult(success=True, output=f"已创建子任务 [{task_id}]: {description}")

    if result is None:
        return ToolResult(success=True, output=f"子任务 [{task_id}] 已完成（无输出）")

    return ToolResult(success=True, output=f"子任务 [{task_id}] 结果：\n{result}")


async def wait_subagents(ctx: RunContext[cairnDeps]) -> ToolResult:
    """
    等待当前所有后台子任务执行完毕，并返回汇总结果。

    适用于曾用 task(wait=False) 启动了多个子任务的场景。
    """
    tasks = list(ctx.deps.sub_agent_tasks.values())
    if not tasks:
        return ToolResult(success=True, output="当前没有运行中的子任务")

    await asyncio.gather(*tasks, return_exceptions=True)

    lines: list[str] = []
    for task_id, t in ctx.deps.sub_agents.items():
        if t.status in ("completed", "failed"):
            head = f"[{task_id}] {t.description or ''}".strip()
            lines.append(f"{head} ({t.status})")
            if t.result:
                lines.append(t.result)
                lines.append("")

    output = "\n".join(lines).rstrip() or "子任务已结束（无可用结果）"
    return ToolResult(success=True, output=output)


async def todo_write(ctx: RunContext[cairnDeps], todos: list[dict[str, str]]) -> ToolResult:
    try:
        new_todos: list[TodoItem] = []
        for item in todos:
            new_todos.append(
                TodoItem(
                    content=item.get("content", ""),
                    status=item.get("status", "pending"),
                    active_form=item.get("activeForm", item.get("active_form", "")),
                )
            )

        ctx.deps.todos = new_todos
        if ctx.deps.event_bus is not None:
            ctx.deps.event_bus.emit(TodoUpdated(todos=new_todos))

        summary_lines = []
        for todo in new_todos:
            status_icon = {"pending": "⏳", "in_progress": "🔄", "completed": "✅"}.get(todo.status, "?")
            summary_lines.append(f"{status_icon} {todo.content}")

        return ToolResult(success=True, output=f"已更新 {len(new_todos)} 个任务\n" + "\n".join(summary_lines))
    except Exception as e:
        return ToolResult(success=False, output="", error=str(e))
