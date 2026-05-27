# v0.3.0 迁移指南

v0.3.0 对 cairn 做了大规模重构。v0.4.x 进一步加入了前后端分离。

## 1. 目录结构（v0.4.x）

```
cairn/
├── cli.py              # CLI 入口（3 种子命令）
├── server.py           # FastAPI + WebSocket 服务端
├── core/               # 运行时 / 模型 / 事件 / ChatService / 持久化
│   ├── agent.py        # Agent 工厂
│   ├── chat_events.py  # 聊天事件类型
│   ├── chat_service.py # Agent 生命周期 + 请求队列 + 广播
│   ├── config.py       # 配置管理
│   ├── events.py       # 事件总线
│   ├── mcp.py          # MCP 预加载
│   ├── models.py       # 数据模型
│   ├── persistence.py  # 消息持久化
│   ├── runtime.py      # 运行时组装
│   └── services/       # ask_user / subagents
├── tools/              # 工具实现
├── tui/                # Textual TUI
│   ├── app.py          # 主应用（嵌入式 / 客户端双模式）
│   ├── client.py       # WebSocket 客户端
│   └── widgets.py      # UI 组件
└── prompts/            # 系统提示词
```

## 2. 启动方式

```bash
# 嵌入式（v0.3.0 模式保留）
cairn

# 新增：后端服务
cairn serve --port 8720

# 新增：TUI 客户端
cairn tui --url ws://127.0.0.1:8720/ws
```

## 3. 核心变化 v0.4.x

- **ChatService**：Agent 生命周期管理，请求队列顺序执行，全局广播
- **消息持久化**：`~/.cairn/history.json`，重启恢复
- **前后端分离**：HTTP + WebSocket 双通道
- **历史回滚**：点击消息 → 预填 → 按轮次起点截断
- **ChatEvent**：`TextDelta` / `ToolStarted` / `ToolFinished` / `Done` / `Error`
- **改名**：`minicc` → `cairn`

## 4. 常见行为变化

- **工具调用显示**：由 stream events 驱动
- **自动滚动**：流式输出实时更新
- **token 图标**：底边栏使用 `↑/↓`
- **回滚安全**：agent 忙碌时拒绝回滚
- **事件广播**：所有 WS 客户端收到相同事件流
