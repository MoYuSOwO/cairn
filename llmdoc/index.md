# cairn 文档索引

cairn 是一个 AI 编程助手（TUI），支持前后端分离、消息持久化、子代理与 MCP。

## 快速导航

| 文档类型 | 路径 | 说明 |
|---------|------|------|
| 概述 | [/llmdoc/overview/](./overview/) | 项目背景、设计目标、技术选型 |
| 指南 | [/llmdoc/guides/](./guides/) | 安装使用、开发调试指南 |
| 架构 | [/llmdoc/architecture/](./architecture/) | 系统架构、模块设计、TUI 布局 |
| 参考 | [/llmdoc/reference/](./reference/) | API 规范、数据模型、WS 协议 |

v0.3.0 迁移说明：
- [/llmdoc/guides/migration-v0.3.0.md](./guides/migration-v0.3.0.md)

开发指南：
- [/llmdoc/guides/testing.md](./guides/testing.md)

## 最近更新

### v0.4.x — ChatService 重构 + 前后端分离

- **ChatService**：Agent 生命周期管理，请求队列顺序执行，全局广播
- **消息持久化**：`~/.cairn/history.json`，dataclass 循环序列化，重启恢复
- **HTTP + WebSocket**：`POST /inject`（fire-and-forget）+ `WS /ws`（全双工事件流）
- **全局广播**：所有事件对全部 WS 客户端可见，TUI 通过统一 stream 队列消费
- **历史回滚**：点击任意消息 → 预填输入框 → 按轮次起点截断重发
- **轮次索引**：message 的 `history_index` 指向轮次起点（用户消息），rollback 自动捕捉
- **三种运行模式**：`cairn`（内嵌）、`cairn serve`（后端）、`cairn tui`（客户端）
- **ChatEvent 类型**：`TextDelta` / `ToolStarted` / `ToolFinished` / `Done` / `Error`
- 12 种下行事件 + 4 种上行消息（详见 [modules.md](./architecture/modules.md)）
- 改名：`minicc` → `cairn`

### v0.3.2 (2025-12-18)
- **ask_user 稳定性**：工具层改为强校验 + 友好报错（避免 `str has no attribute get`、空选项导致空面板）
- **提示词约束**：系统提示词明确 `ask_user` 每题必须提供 `header/question/options`
- **问答面板**：自定义项显示为"其他（自定义输入）"，减少误解
- 相关：`cairn/tools/interact.py`、`cairn/prompts/system.md`、`cairn/tui/ask_user_panel.py`、`tests/test_ask_user_normalize.py`

### MCP 载入与工具提示完善 (v0.2.3 - 2025-12-13)
- **MCP 载入**: 改为启动时静态加载 toolsets（避免 DynamicToolset 引发 anyio CancelScope 跨 Task 报错）
- **可选依赖**: 增加 `cairn[mcp]` 安装方式
- **UI 提示**: MCP 工具调用也会显示 `🔧` 工具调用行
- **调试**: 增加 `cairn_DEBUG=1` 在 TUI 显示完整 traceback

### 大重构：事件驱动 UI + 模块拆分 (v0.3.0 - 2025-12-14)
- **事件通知更新**：TUI 直接消费 `agent.run_stream_events()` 的工具事件，ToolCallLine 支持 running/completed/failed 状态
- **结构重整**：新增 `cairn/core`、`cairn/tools`、`cairn/tui` 三层，移除旧的单文件堆叠实现
- **MCP 预加载**：启动阶段加载并按配置路径缓存（可选 `cairn_MCP_STRICT=1` 严格模式）
- **子任务等待**：`task(wait=True)` 默认等待子代理完成并返回结果；`wait_subagents` 可等待所有后台子任务
- **流式滚动**：助手流式输出实时更新，并在布局刷新后自动滚动到底部

### ask_user 工具新增 (v0.2.1 - 2025-12-01)
- **新增工具**: `ask_user` - 向用户提问选择题
- **功能特性**:
  - 支持一次提出多个问题
  - 支持单选题/多选题
  - 每个问题自动添加"其他"选项，允许用户自定义输入
  - 提交/取消后自动移除问答面板
  - 取消时抛出 `UserCancelledError` 终止 Agent 循环
- **schemas.py 变更**: 新增 `QuestionOption`, `Question`, `AskUserRequest`, `AskUserResponse`, `UserCancelledError` 模型
- **tools.py 变更**: 新增 `ask_user` 异步工具函数
- **ui/widgets.py 变更**: 新增 `AskUserPanel` 可交互组件
- **app.py 变更**: 添加 `on_ask_user` 回调和事件处理

### Agent-Gear FileSystem 集成 (v0.2 - 2025-11-30)
- **新增依赖**: agent-gear>=0.1.0 (高性能文件系统操作)
- **schemas.py 变更**: 新增 `cairnDeps.fs` 字段存储 FileSystem 实例
- **app.py 集成**:
  - 初始化全局 FileSystem 实例：`self._fs = FileSystem(cwd, auto_watch=True)`
  - 添加 `_wait_fs_ready()` 后台方法等待索引就绪
  - 在 `action_quit()` 中关闭 FileSystem 释放资源
- **tools.py 性能优化** (1162 行 → 1259 行):
  - **read_file**: 使用 `fs.read_lines()` 进行分段读取，支持 offset/limit
  - **write_file**: 使用 `fs.write_file()` 原子写入（temp-fsync-rename）
  - **edit_file**: 结合 fs 接口实现原子编辑操作
  - **glob_files**: 使用 `fs.glob()` 利用内存索引 + LRU 缓存（2-3x 加速）
  - **grep_search**: 使用 `fs.grep()` 高性能搜索（基于 ripgrep 核心库）
  - 新增 fallback 函数保证兼容性：_read_file_fallback, _write_file_fallback, _edit_file_fallback, _grep_ripgrepy
- **性能收益**:
  - 内存文件索引 + LRU 缓存加速文件搜索
  - 并行批量读取文件
  - 原子写入保证数据完整性
  - 文件监听自动更新索引，无需手动刷新
- 详见：
  - [/llmdoc/overview/project.md](./overview/project.md) - 技术决策更新
  - [/llmdoc/architecture/modules.md](./architecture/modules.md) - 模块详细说明

### 工具系统重构完成 (v1.1 - 2025-11-28)
- **新增依赖**: ripgrepy (高性能搜索), wcmatch (高级 glob)
- **tools.py 扩展**: 760 行 → 1162 行，新增 10+ 工具
  - edit_file: 替代 update_file，精确替换 + 空白容错
  - glob_files: 替代 search_files，支持高级 glob 模式
  - grep_search: 替代 grep，使用 ripgrepy 高性能
  - bash_output / kill_shell: 后台任务管理
  - task / todo_write: 子任务和任务追踪
  - （注：Notebook 编辑能力在后续版本中已移除，文档以当前版本为准）
- **schemas.py 扩展**: 128 行 → 176 行
  - 新增 PromptCache (Anthropic 缓存配置)
  - 新增 TodoItem, BackgroundShell 模型
  - 扩展 AgentTask: 添加 description, subagent_type
  - 扩展 cairnDeps: 添加 todos, background_shells, on_todo_update
- **UI 新增**: TodoDisplay 组件 (任务列表显示)
- 详见：
  - [/llmdoc/overview/project.md](./overview/project.md) - 核心能力更新
  - [/llmdoc/architecture/modules.md](./architecture/modules.md) - 模块详细说明

### TUI 首页重构完成 (v1.0 - 2025-11-28)
- 移除侧边栏（SidePanel）和可折叠面板，采用单行简洁设计
- 新增 BottomBar 组件（模型/目录/分支/Token 显示）
- ToolCallLine/SubAgentLine: 单行简洁格式 `🔧 name (param) ✅/❌`
- 精简 ui/widgets.py: 434 行 → 230 行 (已更新为 272 行)
- 精简 schemas.py: 164 行 → 128 行 (已扩展为 176 行)

## 核心模块

```
cairn/
├── cli.py              # CLI 入口（3 种子命令）
├── server.py           # FastAPI + WebSocket 服务端
├── core/               # 运行时/模型/事件/ChatService/持久化
│   ├── chat_service.py # Agent 生命周期 + 请求队列 + 广播
│   ├── chat_events.py  # 聊天事件类型
│   ├── persistence.py  # 消息持久化
│   └── services/       # ask_user / subagents
├── tools/              # 工具实现
├── tui/                # Textual TUI
│   ├── app.py          # 主应用（嵌入式 / 客户端双模式）
│   ├── client.py       # WebSocket 客户端
│   └── widgets.py      # UI 组件
└── prompts/            # 系统提示词
```

## 技术栈

- **pydantic-ai**: Agent 框架，提供工具注册、流式输出
- **Textual**: TUI 框架，提供终端界面
- **FastAPI + uvicorn**: HTTP + WebSocket 服务端
- **websockets**: TUI 客户端的 WebSocket 连接
- **Pydantic**: 数据验证和序列化
