# cairn

极简教学版 AI 编程助手（TUI），参考 Claude Code 的交互形态，用更少的代码把核心机制讲清楚：工具调用、事件驱动 UI、子代理与 MCP。

> v0.3.x 起对外只保证 **TUI 行为** 稳定，内部模块/API 允许 breaking change。

## 已实现能力（当前）

### 基础 Coding 能力

- 文件相关：读/写/精确替换编辑（`read_file` / `write_file` / `edit_file`）
- 代码检索：glob + 内容搜索（`glob_files` / `grep_search`）
- Shell：前台执行与后台任务（`bash` / `bash_output` / `kill_shell`）
- 任务列表：模型可写 todo，TUI 实时展示（`todo_write`）

### 子代理（SubAgent）

- `task(wait=True)`：默认等待子代理完成并返回结果，主 Agent 可以直接整合继续推理
- `task(wait=False)`：后台启动多个子任务
- `wait_subagents()`：等待所有后台子任务结束并汇总输出

### MCP（Model Context Protocol）

- **启动阶段预加载** MCP servers 与 toolsets（不再运行中懒加载）
- toolsets 会注入主 Agent 与子代理（避免每次创建重复加载）
- 缺少可选依赖时默认降级为空；可用 `cairn_MCP_STRICT=1` 强制启动失败（适合 CI/严格环境）

### TUI 体验（Textual）

- 流式输出 + 自动滚动到末尾
- 工具调用行：直接消费 `agent.run_stream_events()` 的 ToolCall/ToolResult 事件（running/completed/failed）
- 多行输入：`Ctrl+J` 换行；`Enter` 发送
- `@` 引用文件：输入 `@` + 片段弹出候选，`↑/↓` 选择，`Enter/Tab` 插入路径
- ask_user 面板：工具向用户发起选择题/多选题并阻塞等待

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
cairn
# 或
python -m cairn
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

配置文件位置优先级：

1. 工作目录下的 `.cairn/mcp.json`
2. 全局 `~/.cairn/mcp.json`

启用 MCP 需要安装可选依赖：

```bash
pip install "cairn[mcp]"
# 或
uv pip install "cairn[mcp]"
```

严格模式（配置错误/缺依赖时直接失败）：

```bash
export cairn_MCP_STRICT=1
```

### 系统提示词

`~/.cairn/AGENTS.md`：自定义系统提示词（建议写清“可选方案/等待子代理/何时 ask_user”）。

## 快捷键

| 快捷键 | 功能 |
| --- | --- |
| Enter | 发送消息 |
| Ctrl+J | 输入框换行 |
| Ctrl+C | 退出 |
| Ctrl+L | 清屏 |
| Esc | 取消/关闭候选 |

## 项目结构（v0.3.x）

```
cairn/
├── cli.py       # 入口（仅启动 TUI）
├── core/        # 运行时/模型/事件总线/MCP 预加载
├── tools/       # 工具实现（按职责拆分）
└── tui/         # Textual TUI（消费 stream events + event_bus）
```

## 开发

```bash
git clone https://github.com/TokenRollAI/cairn.git
cd cairn
uv sync
uv run cairn
```

Textual 开发模式：

```bash
uv run textual run --dev cairn.tui.app:cairnApp
textual console
```

运行测试：

```bash
.venv/bin/pytest -q
```

## Roadmap（TODO）

- Slash Command：如 `/help`、`/clear`、`/model`、`/tasks`、`/mcp` 等
- 自定义 SubAgent：不同职责/不同系统提示词/不同工具集的 agent profile
- WebSearch / Fetch：联网检索与抓取（含缓存与引用）
- Skills：可复用技能包（如“重构”“修复测试”“写文档”等工作流）
- llmdoc：更完整的 Document-Driven Development（新增更多指南/参考/架构图）

## 文档（llmdoc）

- `llmdoc/index.md`：文档索引
- `llmdoc/guides/usage.md`：使用指南
- `llmdoc/guides/testing.md`：测试指南

## License

MIT
