# TUI 首页重构记录（历史 + v0.3.0 补充）

## 概述

cairn TUI 首页进行了重大重构，优化了界面布局和组件交互。重构移除了侧边栏，引入了可折叠面板和底边栏，创建了更清晰、信息密度更高的聊天界面。

**版本:** v1.0（历史）
**日期:** 2025-11-27
**影响（当时）:** cairn/app.py、cairn/ui/widgets.py、cairn/ui/__init__.py（这些路径在 v0.3.0 已移除）

> 注意：本文件中 v1.0 的“代码路径/实现细节”仅供历史回溯；当前实现以 `cairn/tui/*` 与 `llmdoc/architecture/tui-layout.md` 为准。

## v0.3.0 补充：事件驱动 UI（当前）

v0.3.0 做了进一步的大重构：
- TUI 代码迁移到 `cairn/tui/*`
- tools 按职责拆分到 `cairn/tools/*`
- UI 不再依赖 “tools 内部回调”，而是直接消费 `agent.run_stream_events()` 的工具调用事件
- 底边栏 token 图标改为 `↑/↓`（避免部分终端对 emoji 宽度支持不佳导致显示方块）
- 流式输出改为实时更新 MessagePanel，并在布局刷新后滚动到底部

## 移除的内容

### 侧边栏 (SidePanel) 及其子组件
- StatusBar: 状态栏
- info_card: 信息卡
- TabbedContent: 工具 Tab / SubAgents Tab

**原因:** 侧边栏占用宝贵的水平空间；后续改为在消息流中内联显示工具调用行与子任务行。

## 新增的内容

### 1. BottomBar 组件
**文件:** `cairn/ui/widgets.py:191-230`

分区块显示关键上下文信息，恒定显示在底部：
- `📦 模型`: provider:model (如 `anthropic:claude-sonnet-4`)
- `📁 工作目录`: 当前 cwd（超长时显示尾部）
- `🌿 Git 分支`: 当前分支名
- `⬆️⬇️ Token`: 累计输入/输出 token 数（v0.3.0 已改为 `↑/↓`）

**特点:** 恒定显示，实时更新，无需折叠。

### 2. ToolCallLine 组件
**文件:** `cairn/ui/widgets.py:44-85`

工具调用单行显示，简洁紧凑的格式：
- **格式:** `🔧 {工具名} ({参数摘要}) {状态图标}`
- **参数摘要:** 优先级 path > file_path > pattern > command > query > prompt (30字符截断)
- **状态图标:** ⏳(pending) 🔄(running) ✅(completed) ❌(failed)

**设计:** 相比原 CollapsibleToolPanel，不可折叠，一行显示完整信息，减少视觉噪音。

### 3. SubAgentLine 组件
**文件:** `cairn/ui/widgets.py:87-127`

SubAgent 任务单行显示，简洁紧凑的格式：
- **格式:** `🤖 {prompt摘要} {状态图标}`
- **提示词摘要:** 40 字符截断
- **状态图标:** ⏳(pending) 🔄(running) ✅(completed) ❌(failed)

**设计:** 相比原 SubAgentPanel，不可折叠，一行显示任务状态，与消息内联。

## 布局变化

**旧布局:**
```
Header
  ↓
Horizontal(chat_container + side_panel)
  ↓
Input
  ↓
Footer
```

**新布局:**
```
Header
  ↓
chat_container (消息 + 可折叠面板)
  ↓
Input
  ↓
BottomBar
  ↓
Footer
```

## 代码变更

### cairn/app.py (242 行)

**compose() 方法 (行 82-93)**
- 移除 Horizontal 容器和 side_panel
- 添加 BottomBar 组件（行 87-92）
- 改为纵向堆叠布局：Header → chat_container → Input → BottomBar → Footer

**_on_tool_call() 方法 (行 175-202)**
- 检测 spawn_agent 工具
- spawn_agent → mount SubAgentLine；其他 → mount ToolCallLine
- 自动 mount 到 chat_container 并滚动到底部
- 支持流式工具调用状态更新

**_update_tokens() 方法 (行 207-215)**
- 从 AgentRunResultEvent.result.usage() 提取 token（**bug fix: usage 是方法**）
- 调用 BottomBar.add_tokens() 更新 token 计数

### cairn/ui/widgets.py (230 行，精简 -204 行)

**新增组件:**
- `ToolCallLine` (行 44-85): 工具调用单行显示
- `SubAgentLine` (行 87-127): SubAgent 任务单行显示
- `BottomBar` (行 191-230): 底边栏组件

**保留的组件:**
- `MessagePanel` (行 15-41): 消息面板
- `DiffView` (行 129-189): Diff 视图

**已移除:**
- `ToolCallPanel` → 被 ToolCallLine 替代
- `CollapsibleToolPanel` → 被 ToolCallLine 替代
- `SubAgentPanel` → 被 SubAgentLine 替代
- `UsageDisplay` → 功能集成到 BottomBar
- `StatusBar` → 功能已弃用

### cairn/ui/__init__.py

导出更新：
- 移除：ToolCallPanel, CollapsibleToolPanel, SubAgentPanel, UsageDisplay, StatusBar
- 新增：ToolCallLine, SubAgentLine, BottomBar

### cairn/schemas.py (128 行，精简 -36 行)

**整理导入顺序:**
- 标准库 → 第三方库 → 本地模块

**保留的关键类:**
- `Config`: 应用配置
- `Provider`: LLM 提供商枚举
- `ToolResult`: 工具执行结果
- `DiffLine`: Diff 行信息
- `cairnDeps`: Agent 依赖注入容器

**已移除的未使用类:**
- `FileOperation`
- `Message`
- `ToolCall`

## 用户体验改进

1. **视觉清晰度:** 工具调用单行显示，不占用额外空间，信息简洁
2. **信息密度:** BottomBar 在一行内显示 4 项关键信息（模型/目录/分支/Token）
3. **空间利用:** 释放侧边栏占用的水平空间，聊天区域宽度增加 30-40%
4. **交互简化:** 消息与工具调用内联，无需切换 Tab 或点击展开
5. **性能提升:** 减少 UI 树深度和组件数量，更轻量级

## Bug 修复

### Token 使用量不更新
**问题:** BottomBar 中 token 计数未更新

**原因:** `event.result.usage` 是方法，需调用 `usage()` 获取数据

**修复 (app.py:207-215):**
```python
def _update_tokens(self, event: AgentRunResultEvent) -> None:
    if event.result and event.result.usage:
        usage = event.result.usage()  # 正确: 调用方法
        self.bottom_bar.add_tokens(
            input_delta=usage.input_tokens,
            output_delta=usage.output_tokens
        )
```

**关键点:**
- `usage` 是 `UsageAtEndType` 对象，是可调用的
- 返回 `Usage(input_tokens=int, output_tokens=int)`

## 迁移指南

**无须修改:** Agent 逻辑、数据模型、配置管理

**需要了解:**
- 新的工具调用回调机制 (`_on_tool_call()` 行 175-202)
- 新的组件 API (ToolCallLine、SubAgentLine、BottomBar)
- BottomBar 的 token 更新方式 (`add_tokens()` 累加)

## 相关文档

- [tui-layout.md](./tui-layout.md) - 布局和组件详细说明
- [/reference/ui-components.md](../reference/ui-components.md) - 组件接口参考
- [/overview/project.md](../overview/project.md) - 项目能力概述
