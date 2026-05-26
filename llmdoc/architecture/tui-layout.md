# TUI 布局架构

## 1. Identity

- **What it is:** cairn 终端用户界面的整体布局和组件组织
- **Purpose:** 为用户提供清晰、高效的聊天交互界面

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

### Header
显示应用标题和实时时钟。Textual 内置组件，无需自定义。

### chat_container (VerticalScroll)
**文件:** `cairn/tui/app.py` (`VerticalScroll`)

主消息区域，包含：
- `MessagePanel`: 用户/助手消息（支持 Markdown）
- `ToolCallLine`: 工具调用单行显示（`🔧 name (param) 🔄/✅/❌`）
- `SubAgentLine`: SubAgent 任务单行显示（`🤖 prompt ⏳/🔄/✅/❌`）

消息自动滚动到最新。滚动使用 `call_after_refresh(scroll_end)`，避免“先滚动后布局”导致看不到最后一行。

### Input
用户输入框，提交时触发 `on_input_submitted` 事件。

### BottomBar
**文件:** `cairn/tui/widgets.py` (BottomBar 组件)

分区块显示：
- `📦 模型`: provider:model
- `📁 目录`: 当前 cwd
- `🌿 分支`: git 分支名
- `↑↓ Token`: 输入/输出 token 数（使用通用字符，避免终端 emoji 宽度问题）

实时更新，不可折叠。

### Footer
显示快捷键列表。Textual 内置组件。

## 4. 消息流处理

消息处理链：
```
Input.Submitted
  → _process_message(user_input)
    → agent.run_stream_events() [异步流处理]
      → PartStartEvent → 开始流式输出
      → PartDeltaEvent → 累积 text delta
      → Function/Builtin ToolCallEvent → 生成 ToolCallLine(running)
      → Function/Builtin ToolResultEvent → 更新 ToolCallLine(completed/failed)
      → 最终事件 (AgentRunResultEvent)
         → _update_tokens() [更新 BottomBar token]
         → 结束流式输出并固化 MessagePanel
  → 布局刷新后滚动到底部
```

关键方法：
- `_process_message()` (`cairn/tui/app.py`): 消息处理入口（含工具事件采集）
- `_consume_events()` (`cairn/tui/app.py`): 消费事件总线（todo/ask_user/subagent）
- `_scroll_chat_end()` (`cairn/tui/app.py`): 布局后滚动到底部

## 5. 快捷键

| 快捷键 | 功能 |
|--------|------|
| Ctrl+C | 退出应用 |
| Ctrl+L | 清屏并重置 |
| Escape | 取消当前操作 |

## 6. 设计演进

### v0.x (初版)
- 水平布局: chat_container + side_panel
- 侧边栏显示: StatusBar + info_card + TabbedContent
- 可折叠面板: CollapsibleToolPanel / SubAgentPanel

### v0.3.0 (当前)
- 纵向布局: Header → chat_container → TodoDisplay → ask_user_container → Input → BottomBar → Footer
- **事件驱动**：工具调用行由 stream events 直接驱动，不再依赖 tools 内部回调
- **流式助手输出**：边生成边更新 MessagePanel，并保持滚动在底部
- **子任务语义**：`task(wait=True)` 默认等待并返回结果；可并行启动后 `wait_subagents` 汇总等待

### 设计优势
- **视觉清洁:** 移除冗余信息和可折叠交互
- **信息密度高:** BottomBar 在一行内显示 4 项关键信息
- **空间利用率:** 聊天区域宽度增加 ~30-40%
- **交互简化:** 消息与工具调用内联，无需切换 Tab
