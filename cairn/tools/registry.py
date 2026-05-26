from __future__ import annotations

from pydantic_ai import Agent

from cairn.core.models import cairnDeps
from cairn.tools.file import edit_file, read_file, write_file
from cairn.tools.interact import ask_user
from cairn.tools.search import glob_files, grep_search
from cairn.tools.shell import bash, bash_output, kill_shell
from cairn.tools.task import task, todo_write, wait_subagents


def register_tools(agent: Agent[cairnDeps, str]) -> None:
    # 文件操作
    agent.tool(read_file)
    agent.tool(write_file)
    agent.tool(edit_file)

    # 搜索
    agent.tool(glob_files)
    agent.tool(grep_search)

    # 命令行
    agent.tool(bash)
    agent.tool(bash_output)
    agent.tool(kill_shell)

    # 任务与交互
    agent.tool(task)
    agent.tool(todo_write)
    agent.tool(wait_subagents)
    agent.tool(ask_user)
