# 数据模型参考（v0.4.x）

## Provider (枚举)

```python
class Provider(str, Enum):
    ANTHROPIC = "anthropic"
    OPENAI = "openai"
```

## Config

应用配置结构，存储在 `~/.cairn/config.json`

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| provider | Provider | ANTHROPIC | LLM 提供商 |
| model | str | claude-sonnet-4-20250514 | 模型名称 |
| api_key | Optional[str] | None | API 密钥 |
| base_url | Optional[str] | None | 自定义 API 端点 |
| prompt_cache | PromptCache | {} | Prompt Cache 配置 |

## ChatEvent 类型

v0.4.x 新增的聊天事件（`cairn/core/chat_events.py`），frozen dataclass：

### TextDelta
流式文本输出。

| 字段 | 类型 | 说明 |
|------|------|------|
| content | str | 当前累积的完整文本（非增量） |

### ToolStarted
工具调用开始。

| 字段 | 类型 | 说明 |
|------|------|------|
| tool_call_id | str | 工具调用 ID |
| tool_name | str | 工具名称 |
| args | dict[str, Any] \| None | 调用参数 |

### ToolFinished
工具调用完成。

| 字段 | 类型 | 说明 |
|------|------|------|
| tool_call_id | str | 工具调用 ID |
| tool_name | str | 工具名称 |
| ok | bool | 是否成功 |
| content | Any | 返回内容 |
| error | str \| None | 错误信息（失败时） |

### Done
本轮对话完成。

| 字段 | 类型 | 说明 |
|------|------|------|
| usage | Any \| None | token 用量 |

### Error
错误。

| 字段 | 类型 | 说明 |
|------|------|------|
| exception | Exception | 异常对象 |

## ToolResult

工具执行结果。

| 字段 | 类型 | 说明 |
|------|------|------|
| success | bool | 是否成功 |
| output | str | 执行输出 |
| error | Optional[str] | 错误信息 |

## AgentTask

子任务状态。

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| task_id | str | - | 唯一任务 ID |
| description | str | "" | 简短描述 |
| prompt | str | - | 任务提示词 |
| subagent_type | str | general-purpose | 代理类型 |
| status | str | "pending" | pending/running/completed/failed |
| result | Optional[str] | None | 执行结果 |

## cairnDeps（依赖注入容器）

由 `build_runtime()` 组装（`cairn/core/runtime.py`）：

| 字段 | 类型 | 说明 |
|------|------|------|
| config | Config | 应用配置 |
| cwd | str | 工作目录 |
| fs | Any | agent-gear FileSystem |
| todos | list[TodoItem] | 任务列表 |
| background_shells | dict | 后台命令进程 |
| sub_agents | dict[str, AgentTask] | 子代理状态 |
| sub_agent_tasks | dict[str, Any] | 子代理 asyncio 任务句柄 |
| event_bus | Any | 事件总线 |
| ask_user_service | Any | ask_user 服务 |
| subagent_service | Any | 子代理服务 |

## WS 协议（v0.4.x）

### 下行事件（Server → Client）

| 事件类型 | 携带字段 | 说明 |
|---------|---------|------|
| request_started | request_id, text | 请求开始处理 |
| text_delta | request_id, content | 流式文本 |
| tool_started | request_id, tool_call_id, tool_name, args | 工具开始 |
| tool_finished | request_id, tool_call_id, tool_name, ok, content, error | 工具完成 |
| done | request_id, usage | 本轮完成 |
| error | request_id, exception | 错误 |
| request_finished | request_id | 请求处理结束 |
| history_snapshot | message_count, messages | 全量消息快照 |
| todo_updated | todos | 任务列表更新 |
| ask_user_requested | request_data | ask_user 触发 |
| subagent_created | task_id, description, prompt | 子代理创建 |
| subagent_updated | task_id, status, result | 子代理更新 |

### 上行消息（Client → Server）

| 消息类型 | 携带字段 | 说明 |
|---------|---------|------|
| chat | text | 发送消息 |
| rollback | index | 回滚到指定位置 |
| clear | — | 清空历史 |
| ask_user_response | request_id, submitted, answers | 回答 ask_user |

## MessageStore（持久化）

`cairn/core/persistence.py`：

- `load() → list`：从 `~/.cairn/history.json` 加载
- `save(messages)`：原子写入（`.tmp` → rename）
- 序列化：`_as_json_compatible()` 将 pydantic-ai dataclass 消息转为 JSON
- 反序列化：`_reconstruct()` 通过 `kind` 区分 `ModelRequest` / `ModelResponse`
