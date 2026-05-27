# 模块架构（v0.4.x）

## 整体架构

```
                              ┌──────────────┐
                              │  cairn/cli   │  CLI 入口 (3 种子命令)
                              └──────┬───────┘
                                     │
              ┌──────────────────────┼──────────────────────┐
              │                      │                      │
              ▼                      ▼                      ▼
        ┌──────────┐          ┌──────────┐          ┌──────────┐
        │ embedded │          │  serve   │          │   tui    │
        │ (direct) │          │ (FastAPI)│          │ (WS cli) │
        └────┬─────┘          └────┬─────┘          └────┬─────┘
             │                     │                      │
             │               ┌─────▼──────┐               │
             │               │ ChatService│◄──────────────┘
             │               │  (queue +  │    websockets
             │               │  broadcast)│
             │               └─────┬──────┘
             │                     │
             └──────────┬──────────┘
                        ▼
                ┌──────────────┐     ┌──────────────┐
                │    Agent     │────▶│   EventBus   │
                └──────┬───────┘     └──────────────┘
                       │
                       ▼
               ┌──────────────┐
               │    Tools     │
               └──────────────┘
```

## 关键设计点

- **ChatService 拥有 Agent 生命周期**：消息历史、持久化、请求队列、广播
- **三种运行模式共享 ChatService**：内嵌模式直接调用 `send_message()`，server 模式通过队列 `submit()` + 广播
- **广播模式**：一个 worker 顺序处理请求，事件通过 fan-out 推送给所有 WS 客户端，人人平等
- **TUI 双模式**：内嵌模式直接消费 `async for event in send_message()`，客户端模式从 WS 的 `stream` 队列统一消费
- **回滚安全**：`_busy` 标记防止运行中回滚，自动捕捉轮次起点

## 模块职责

### cairn/core/models.py
数据模型定义。

核心模型：
- `Config` / `Provider` / `PromptCache`
- `ToolResult` / `DiffLine`
- `AgentTask` / `TodoItem` / `BackgroundShell`
- `Question*` / `AskUserRequest` / `AskUserResponse` / `UserCancelledError`
- `cairnDeps`：依赖注入容器（包含 `event_bus`、`ask_user_service`、`subagent_service`）

### cairn/core/config.py
配置管理（`~/.cairn/config.json`、`AGENTS.md`、MCP 配置查找、历史文件路径）。

### cairn/core/persistence.py
消息持久化（`MessageStore`）：
- JSON 文件存储（`~/.cairn/history.json`）
- `dataclasses.asdict()` 序列化 pydantic-ai 消息（非 Pydantic model_dump）
- 自定义 `_as_json_compatible()` / `_reconstruct()` 循环序列化
- 原子写入：write `.tmp` → rename

### cairn/core/chat_events.py
聊天事件类型（frozen dataclass）：
- `TextDelta(content)` — 流式文本增量
- `ToolStarted(tool_call_id, tool_name, args)` — 工具开始调用
- `ToolFinished(tool_call_id, tool_name, ok, content, error)` — 工具调用完成
- `Done(usage)` — 本轮对话完成
- `Error(exception)` — 错误

### cairn/core/chat_service.py
**核心服务**，拥有 Agent 生命周期：

**直接模式**（嵌入式 TUI）：
- `send_message(text) → AsyncIterator[ChatEvent]`：直接运行对话

**队列模式**（server）：
- `submit(text) → request_id`：入队立即返回，惰性启动 worker
- worker 从 `_request_queue` 取请求顺序处理，事件通过 `_broadcast` 发布
- `rollback_to(index) → bool`：截断历史到轮次起点，busy 时拒绝
- `_publish_snapshot()`：广播 `history_snapshot`（全量消息）
- `_busy` 标记防止并发处理和回滚冲突

### cairn/core/events.py
事件总线与事件类型：
- `EventBus`：`emit()` + `iter()` 消费
- `ToolCallStarted/Finished`、`TodoUpdated`、`AskUserRequested`、`SubAgentCreated/Updated`

### cairn/core/agent.py
Agent 创建：
- `create_agent(config, cwd, toolsets, register_tools)`

### cairn/core/runtime.py
运行时组装：
- `build_runtime()`：创建 FileSystem、MessageStore、ChatService、预加载 MCP toolsets、构造 deps 与 services

### cairn/server.py
FastAPI 服务端：
- `GET /health` — 健康检查
- `POST /inject` — fire-and-forget 消息注入
- `WS /ws` — WebSocket 事件流
- fan-out：从 `chat_service._broadcast` + `event_bus` 读取，拷贝到所有客户端队列
- 事件序列化注册（`@_register_event` 装饰器模式）

### cairn/core/services/*
服务层：
- `AskUserService`：通过事件总线请求 UI，等待 UI resolve
- `SubAgentService`：支持前台/后台运行

### cairn/tools/*
工具实现（按职责拆分）：
- `file.py`：read_file / write_file / edit_file
- `search.py`：glob_files / grep_search
- `shell.py`：bash / bash_output / kill_shell
- `task.py`：task / todo_write / wait_subagents
- `interact.py`：ask_user
- `common.py`：工具通用函数
- `registry.py`：工具注册

### cairn/tui/*
Textual TUI：
- `app.py`：主应用，双模式（嵌入式中消费 `send_message()`，客户端中消费 WS `stream` 队列）
- `client.py`：`ChatClient` — WebSocket 代理，统一 `stream` 队列
- `widgets.py`：组件（MessagePanel 支持 history_index + 点击回滚；ToolCallLine 状态更新；BottomBar）
- `ask_user_panel.py`：问答面板
- `chat_input.py`：输入框
- `file_mention_panel.py`：@ 引用文件候选

## WS 协议

### 下行（Server → Client）

| 事件类型 | 触发时机 |
|---------|---------|
| `request_started` | 请求从队列取出开始处理 |
| `text_delta` | 流式文本增量 |
| `tool_started` | 工具调用开始 |
| `tool_finished` | 工具调用完成 |
| `done` | 本轮对话完成 |
| `error` | 错误 |
| `request_finished` | 请求处理完成 |
| `history_snapshot` | 回滚/清空/请求完成后全量广播 |
| `todo_updated` | 任务列表更新 |
| `ask_user_requested` | ask_user 触发 |
| `subagent_created` | 子代理创建 |
| `subagent_updated` | 子代理状态更新 |

### 上行（Client → Server）

| 消息类型 | 说明 |
|---------|------|
| `chat` | 发送聊天消息 |
| `rollback` | 回滚到指定轮次 |
| `clear` | 清空历史 |
| `ask_user_response` | 回答 ask_user 问题 |
