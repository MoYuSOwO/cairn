# cairn 项目概述

## 项目目标

实现一个极简版 AI 编程助手，支持前后端分离、消息持久化与事件驱动的 TUI 界面。

## 设计原则

1. **代码精简**: 核心机制清晰可读
2. **架构清晰**: 模块职责分明，前后端解耦
3. **易于扩展**: 基于 pydantic-ai 的工具注册机制
4. **事件驱动**: 聊天事件、EventBus 事件统一广播

## 核心能力

### 工具 (Tools)
- **文件操作**: read_file, write_file, edit_file
- **搜索**: glob_files, grep_search
- **命令行**: bash, bash_output, kill_shell
- **任务管理**: task, wait_subagents, todo_write
- **用户交互**: ask_user

### 对话管理
- **ChatService**: Agent 生命周期 + 请求队列 + 全局广播
- **消息持久化**: JSON 文件，dataclass 序列化，重启恢复
- **历史回滚**: 点击消息 → 预填 → 按轮次起点截断

### 前后端分离
- **三种模式**: `cairn`（内嵌）、`cairn serve`（后端）、`cairn tui`（客户端）
- **HTTP + WebSocket**: `/inject` fire-and-forget 注入 + `/ws` 全双工事件流
- **广播模式**: 所有事件对所有客户端可见，人人平等

### 子代理 (SubAgent)
- `task(wait=True)`：等待完成并返回结果
- `task(wait=False)`：后台启动，`wait_subagents()` 汇总

### MCP
- 启动阶段预加载，注入主 Agent 与子代理

## 技术决策

| 决策项 | 选择 | 理由 |
|--------|------|------|
| LLM 后端 | Anthropic + OpenAI | 覆盖主流提供商 |
| Agent 框架 | pydantic-ai | 流式事件、工具注册、消息模型 |
| 文件系统操作 | agent-gear FileSystem | 内存索引 + LRU 缓存 |
| 搜索引擎 | ripgrepy + wcmatch | ripgrep 核心库 |
| 消息持久化 | JSON + dataclass 序列化 | pydantic-ai 消息是 dataclass，非 Pydantic |
| 前后端通信 | FastAPI + WebSocket | 双通道：fire-and-forget + 事件流 |
| TUI | Textual | 异步组件、流式渲染 |

## v0.4.x 架构变化

- **ChatService 重构**：Agent 生命周期、持久化、请求队列独立为服务层
- **前后端分离**：HTTP + WebSocket，三种运行模式
- **全局广播**：fan-out 模式，多客户端事件同步
- **历史回滚**：轮次索引 + 点击重发
- **消息持久化**：`~/.cairn/history.json`
- **ChatEvent**：`TextDelta` / `ToolStarted` / `ToolFinished` / `Done` / `Error`
- 改名：`minicc` → `cairn`
