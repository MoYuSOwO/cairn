# UI 组件参考

## MessagePanel

**文件:** `cairn/tui/widgets.py`

显示用户或助手的单条消息，支持 Markdown 渲染和点击回滚。

**参数:**

| 参数 | 类型 | 说明 |
|------|------|------|
| content | str | 消息内容（Markdown） |
| role | str | "user" \| "assistant" \| "system" |
| history_index | int | 轮次起点索引，-1 表示不可回滚 |

**方法:** `set_content(content: str)` — 更新内容

**角色样式:** user(蓝) / assistant(绿) / system(洋红)

**点击行为（v0.4.x）：**
- `history_index >= 0` 时，`on_click` 发送 `RollbackRequested(index, content)` 消息
- App 收到后预填输入框、执行回滚、等待 history_snapshot 重建显示
- `history_index` 指向轮次起点（该轮的用户消息位置），非原始消息位置

## ToolCallLine

**文件:** `cairn/tui/widgets.py`

工具调用单行显示。

**参数:**

| 参数 | 类型 | 说明 |
|------|------|------|
| tool_name | str | 工具名称 |
| args | dict | 调用参数 |
| status | str | "pending" \| "running" \| "completed" \| "failed" |

**显示格式:** `🔧 {tool_name} ({key_param}) {status_icon}`

**参数选择优先级:** path > file_path > pattern > command > query > prompt (40字符截断)

**状态图标:** ⏳(pending) 🔄(running) ✅(completed) ❌(failed)

## SubAgentLine

**文件:** `cairn/tui/widgets.py`

SubAgent 任务单行显示。

**参数:**

| 参数 | 类型 | 说明 |
|------|------|------|
| task_id | str | 任务 ID |
| prompt | str | 任务描述（50字符截断） |
| status | str | "pending" \| "running" \| "completed" \| "failed" |

**显示格式:** `🤖 {prompt摘要} {status_icon}`

## DiffView

**文件:** `cairn/tui/widgets.py`

显示文件变更的 Diff 视图。

**参数:**

| 参数 | 类型 | 说明 |
|------|------|------|
| diff_lines | list[DiffLine] | Diff 行列表 |
| filename | Optional[str] | 可选文件名 |

## BottomBar

**文件:** `cairn/tui/widgets.py`

底边栏，恒定显示关键上下文。

**参数:**

| 参数 | 类型 | 说明 |
|------|------|------|
| model | str | provider:model |
| cwd | str | 工作目录（超长时显示尾部） |
| git_branch | Optional[str] | Git 分支名 |
| input_tokens | int | 累计输入 token |
| output_tokens | int | 累计输出 token |

**方法:**
- `update_info(**kwargs)` — 更新字段
- `add_tokens(input_delta, output_delta)` — 累加 token

**显示格式:** `📦 model │ 📁 cwd │ 🌿 branch │ ↑input ↓output`

## ChatClient（v0.4.x）

**文件:** `cairn/tui/client.py`

WebSocket 客户端代理，所有 WS 事件通过统一 `stream` 队列广播。

**核心接口:**
- `stream: asyncio.Queue[dict]` — 所有 WS 事件的统一消费队列
- `connect() → None` — 连接 WebSocket，启动 drain 任务
- `submit(text) → None` — fire-and-forget 发送消息
- `rollback_to(index) → None` — 回滚请求
- `clear() → None` — 清空历史
- `send_ask_user_response(request_id, submitted, answers) → None`

## TodoDisplay

**文件:** `cairn/tui/widgets.py`

任务列表显示，全部完成后可点击关闭。

## AskUserPanel

**文件:** `cairn/tui/ask_user_panel.py`

ask_user 工具的交互面板，单选/多选 + 自定义输入。

**交互:**
- `←/→`：切换问题
- `↑/↓`：移动选项
- `Enter`：选择/取消选择
- `S`：提交
- `Esc`：取消

## FileMentionPanel

**文件:** `cairn/tui/file_mention_panel.py`

输入 `@` + 文件名片段弹出候选列表：
- `↑/↓`：选择候选
- `Enter` / `Tab`：插入路径
- `Esc`：关闭候选
