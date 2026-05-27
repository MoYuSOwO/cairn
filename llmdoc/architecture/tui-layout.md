# TUI 布局架构

## 1. 概述

cairn TUI 支持两种运行模式：
- **嵌入式**：TUI 直接调用 `ChatService.send_message()`，单进程
- **客户端**：TUI 通过 WebSocket 连接后端，事件从统一 `stream` 队列消费

两种模式下 UI 布局和组件一致，区别只在消息处理链。

## 2. 整体布局

```
┌──────────────────────────────────────┐
│        Header (cairn 时钟)          │
├──────────────────────────────────────┤
│     chat_container (VerticalScroll)  │
│  ├─ MessagePanel (用户/助手消息)    │
│  ├─ ToolCallLine (工具调用单行)     │
│  ├─ SubAgentLine (SubAgent 单行)   │
│  └─ (流式助手输出面板)              │
├──────────────────────────────────────┤
│     TodoDisplay (任务列表，可隐藏)   │
├──────────────────────────────────────┤
│ ask_user_container (问答面板容器)    │
├──────────────────────────────────────┤
│     Input (用户输入框，焦点)          │
├──────────────────────────────────────┤
│ 📦 Model │ 📁 CWD │ 🌿 Branch │ Token│
├──────────────────────────────────────┤
│  Footer (快捷键: Ctrl+C退出, Ctrl+L清屏)
└──────────────────────────────────────┘
```

## 3. 核心组件

### MessagePanel
**文件:** `cairn/tui/widgets.py`

消息面板，支持 Markdown 渲染。新增字段：
- `history_index`：该消息的轮次起点索引（点击回滚时使用）
- `on_click`：如果有 `history_index >= 0`，发送 `RollbackRequested` 消息

### chat_container (VerticalScroll)
**文件:** `cairn/tui/app.py`

主消息区域，包含用户/助手消息、工具调用行、子代理行。消息自动滚动到最新。

### Input
用户输入框，提交时触发消息处理。

### BottomBar
分区块显示：📦 模型 / 📁 目录 / 🌿 分支 / ↑↓ Token。实时更新，不可折叠。

## 4. 消息流处理

### 嵌入式模式

```
Input.Submitted
  → _process_message_embedded(user_input)
    → chat_service.send_message(text)
      → async for event → TextDelta / ToolStarted / ToolFinished / Done / Error
        → 实时渲染到 chat_container
      → 布局刷新后滚动到底部
```

### 客户端模式

```
Input.Submitted
  → client.submit(user_input)  // fire-and-forget via WS
  → 主循环 _consume_ws_events()
    → await client.stream.get()
      → request_started: 创建流式追踪
      → text_delta: 更新 MessagePanel (per request_id)
      → tool_started / tool_finished: mount / update ToolCallLine
      → done: 固化 MessagePanel, 更新 tokens
      → error: 显示错误
      → request_finished: 清理追踪
      → history_snapshot: _rebuild_from_snapshot()
      → todo_updated / ask_user_requested / subagent_*: 委托 handler
```

### 历史回滚流程

```
点击 MessagePanel (history_index >= 0)
  → post_message(RollbackRequested(index, content))
  → on_message_panel_rollback_requested()
    → 预填输入框 (content)
    → 聚焦输入框
    → 嵌入式: await chat_service.rollback_to(index)
    → 客户端: await client.rollback_to(index)
  → history_snapshot 抵达
    → _rebuild_from_snapshot(messages)
    → 清空 chat_container
    → 按 messages 重建 MessagePanel（带 history_index=轮次起点）
```

## 5. 快捷键

| 快捷键 | 功能 |
|--------|------|
| Enter | 发送消息 |
| Ctrl+J | 输入框换行 |
| Ctrl+C | 退出 |
| Ctrl+L | 清屏 |
| Escape | 取消当前操作 |

## 6. 设计演进

### v0.3.0
- 纵向布局：Header → chat_container → TodoDisplay → ask_user_container → Input → BottomBar → Footer
- 事件驱动：工具调用行由 stream events 直接驱动

### v0.4.x
- **双模式**：嵌入式 + WS 客户端，统一 stream 消费
- **历史回滚**：点击消息 → 预填 → 回滚 → snapshot 重建
- **轮次索引**：history_index 指向轮次起点（用户消息）
