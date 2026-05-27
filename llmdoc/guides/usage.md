# 使用指南

## 安装

```bash
# 使用 uv
uv pip install cairn

# 使用 pip
pip install cairn
```

## 配置 API Key

```bash
# Anthropic
export ANTHROPIC_API_KEY="sk-ant-xxx"

# OpenAI
export OPENAI_API_KEY="sk-xxx"
```

或者编辑 `~/.cairn/config.json` 中的 `api_key` 字段。

## 启动应用

```bash
# 模式 1：内嵌模式（单进程 TUI，最简单）
cairn

# 模式 2：启动后端服务（默认端口 8720）
cairn serve --port 8720

# 模式 3：TUI 客户端（连接后端）
cairn tui --url ws://127.0.0.1:8720/ws
```

### 前后端分离（可选）

```bash
# 安装 server 依赖
pip install "cairn[server]"

# 终端 1：启动后端
cairn serve

# 终端 2：启动 TUI 客户端
cairn tui

# 支持多客户端同时连接，所有事件广播可见
```

## 快捷键

| 快捷键 | 功能 |
|--------|------|
| Enter | 发送消息 |
| Ctrl+J | 输入框换行 |
| Ctrl+C | 退出 |
| Ctrl+L | 清屏 |
| Escape | 取消当前操作 |

## 历史回滚

点击聊天窗口中的任意消息 → 输入框自动预填该消息的原文 → 后台将对话历史截断到该轮起点 → 你可以编辑文本后重新发送。

- 回滚按"轮次"截断：一轮 = 用户消息到下一个用户消息之前
- Agent 正在处理时无法回滚（被拒绝）
- 回滚后所有已连接的客户端显示同步更新

## 输入框 @ 引用文件

输入 `@` + 文件名片段触发候选列表：
- `↑/↓`：选择候选
- `Enter` / `Tab`：插入路径
- `Esc`：关闭候选

## 配置文件

### ~/.cairn/config.json

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

### MCP 配置（可选）

```bash
pip install "cairn[mcp]"
```

配置文件位置优先级：
1. 工作目录下的 `.cairn/mcp.json`
2. 全局 `~/.cairn/mcp.json`

```json
{
  "mcpServers": {
    "github": {
      "command": "uvx",
      "args": ["mcp-server-github"]
    },
    "local_http": {
      "url": "http://127.0.0.1:8000/mcp"
    }
  }
}
```

### ~/.cairn/AGENTS.md

自定义系统提示词，可以修改 Agent 的行为和工具使用策略。

## HTTP API（server 模式）

### GET /health
健康检查。

```bash
curl http://127.0.0.1:8720/health
# {"status": "ok", "model": "claude-sonnet-4-20250514", "provider": "anthropic"}
```

### POST /inject
Fire-and-forget 消息注入。不返回 LLM 结果，事件通过 WebSocket 推送。

```bash
curl -X POST http://127.0.0.1:8720/inject \
  -H "Content-Type: application/json" \
  -d '{"text": "hello"}'
# {"status": "ok", "request_id": "c1c2de0f"}
```

## 开发调试

```bash
# 使用 textual 开发模式
uv run textual run --dev cairn.tui.app:CairnApp

# 在另一个终端查看日志
textual console
```

### 错误堆栈显示

```bash
export cairn_DEBUG=1
cairn
```
