# cairn

极简 AI 编程助手（TUI），参考 Claude Code 的交互形态，用更少的代码把核心机制讲清楚：工具调用、事件驱动 UI、子代理、MCP、前后端分离与消息持久化。

> v0.3.x 起对外只保证 **TUI 行为** 稳定，内部模块/API 允许 breaking change。

## 已实现能力

### 基础 Coding 能力

- 文件相关：读/写/精确替换编辑（`read_file` / `write_file` / `edit_file`）
- 代码检索：glob + 内容搜索（`glob_files` / `grep_search`）
- Shell：前台执行与后台任务（`bash` / `bash_output` / `kill_shell`）
- 任务列表：模型可写 todo，TUI 实时展示（`todo_write`）

### 子代理（SubAgent）

- `task(wait=True)`：默认等待子代理完成并返回结果
- `task(wait=False)`：后台启动多个子任务
- `wait_subagents()`：等待所有后台子任务结束并汇总输出

### MCP（Model Context Protocol）

- **启动阶段预加载** MCP servers 与 toolsets
- toolsets 注入主 Agent 与子代理（避免重复加载）
- 可选依赖降级；`cairn_MCP_STRICT=1` 强制启动失败

### 前后端分离（v0.4.x）

- **ChatService**：Agent 生命周期管理，请求队列顺序执行
- **消息持久化**：`~/.cairn/history.json`，重启自动恢复
- **全局广播**：所有事件对所有 WS 客户端可见
- **HTTP + WebSocket 双通道**：
  - `POST /inject` — fire-and-forget 注入消息
  - `WS /ws` — 全双工事件流（12 种下行事件 + 4 种上行消息）

### TUI 体验（Textual）

- 流式输出 + 自动滚动
- 工具调用行：直接消费 stream events（running/completed/failed）
- 多行输入：`Ctrl+J` 换行，`Enter` 发送
- `@` 引用文件：输入 `@` + 片段弹出候选
- ask_user 面板：选择题/多选题交互
- **历史回滚**：点击任意消息 → 预填输入框 → 按轮次起点回滚重发

## 快速开始

### 安装

```bash
# uv
uv pip install cairn

# pip
pip install cairn
```

### 配置 API Key

```bash
export ANTHROPIC_API_KEY="sk-ant-xxx"
# 或
export OPENAI_API_KEY="sk-xxx"
```

### 启动

```bash
# 模式 1：内嵌模式（单进程 TUI）
cairn

# 模式 2：启动后端服务
cairn serve --port 8720

# 模式 3：TUI 客户端（连后端）
cairn tui --url ws://127.0.0.1:8720/ws
```

也可以直接运行（无需手动安装）：

```bash
uvx cairn
```

## 配置

### `~/.cairn/config.json`

```json
{
  "provider": "anthropic",
  "model": "claude-sonnet-4-20250514",
  "api_key": null,
  "base_url": null,
  "prompt_cache": {
    "instructions": false,
    "messages": false,
    "tool_definitions": false
  }
}
```

### MCP（可选）

```bash
pip install "cairn[mcp]"
```

配置文件位置优先级：
1. 工作目录下的 `.cairn/mcp.json`
2. 全局 `~/.cairn/mcp.json`

### 前后端分离（可选）

```bash
pip install "cairn[server]"
```

### 系统提示词

`~/.cairn/AGENTS.md`：自定义系统提示词。

## 快捷键

| 快捷键 | 功能 |
| --- | --- |
| Enter | 发送消息 |
| Ctrl+J | 输入框换行 |
| Ctrl+C | 退出 |
| Ctrl+L | 清屏 |
| Esc | 取消/关闭候选 |

## 项目结构

```
cairn/
├── cli.py                # CLI 入口（3 种子命令）
├── server.py             # FastAPI + WebSocket 服务端
├── core/                 # 运行时 / 模型 / 事件 / ChatService / 持久化
│   ├── agent.py          # Agent 工厂
│   ├── chat_events.py    # 聊天事件类型（TextDelta / ToolStarted / Done 等）
│   ├── chat_service.py   # Agent 生命周期管理 + 请求队列 + 广播
│   ├── config.py         # 配置管理
│   ├── events.py         # 事件总线（EventBus）
│   ├── mcp.py            # MCP 预加载
│   ├── models.py         # 数据模型（Config / cairnDeps 等）
│   ├── persistence.py    # 消息持久化（MessageStore）
│   ├── runtime.py        # 运行时组装（build_runtime）
│   └── services/         # ask_user / subagents 服务
├── tools/                # 工具实现
│   ├── file.py           # read / write / edit
│   ├── search.py         # glob / grep
│   ├── shell.py          # bash / bash_output / kill_shell
│   ├── task.py           # task / todo_write / wait_subagents
│   ├── interact.py       # ask_user
│   ├── common.py         # 通用工具函数
│   └── registry.py       # 工具注册
├── tui/                  # Textual TUI
│   ├── app.py            # 主应用（嵌入式 / 客户端双模式）
│   ├── client.py         # WebSocket 客户端
│   ├── widgets.py        # UI 组件（MessagePanel / ToolCallLine 等）
│   ├── ask_user_panel.py # ask_user 交互面板
│   ├── chat_input.py     # 输入框组件
│   └── file_mention_panel.py # @ 引用文件候选
└── prompts/              # 系统提示词
    └── system.md
```

## 开发

```bash
git clone https://github.com/MoYuSOwO/cairn.git
cd cairn
uv sync
uv run cairn
```

Textual 开发模式：

```bash
uv run textual run --dev cairn.tui.app:CairnApp
textual console
```

运行测试：

```bash
uv run pytest -q
```

## 文档（llmdoc）

- `llmdoc/index.md`：文档索引
- `llmdoc/guides/usage.md`：使用指南
- `llmdoc/guides/testing.md`：测试指南

## License

MIT
