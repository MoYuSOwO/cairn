"""
cairn - 极简教学版 AI 编程助手

基于 pydantic-ai 和 textual 实现的轻量级 Claude Code 替代品，
用于学习 AI Agent 的核心实现原理。

基本用法:
    $ cairn              # 启动 TUI 界面
    $ python -m cairn    # 等效启动方式

编程接口:
    from cairn import cairnApp, create_agent

    # 使用应用
    app = cairnApp()
    app.run()

    # 直接使用 Agent
    agent = create_agent(config)
    result = await agent.run("你的问题")

配置:
    配置文件位于 ~/.cairn/config.json
    系统提示词位于 ~/.cairn/AGENTS.md
"""

__version__ = "0.3.2"
__author__ = "cairn Contributors"

# 仅对外暴露 TUI 入口
from cairn.cli import main
from cairn.tui.app import cairnApp

__all__ = ["__version__", "__author__", "cairnApp", "main"]
