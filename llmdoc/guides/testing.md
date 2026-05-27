# 测试指南（v0.4.x）

本项目允许 breaking change，但要求核心行为（事件总线、ask_user、子代理等待、MCP 预加载、输入框交互、ChatService、持久化）可被单元测试覆盖。

## 运行测试

```bash
uv run pytest -q
```

## 现有用例覆盖点

- `tests/test_event_bus.py`：事件总线顺序与迭代
- `tests/test_ask_user_service.py`：ask_user 请求/取消语义
- `tests/test_ask_user_normalize.py`：ask_user 入参校验/归一化
- `tests/test_subagent_service.py`：子代理前台/后台执行与事件
- `tests/test_wait_subagents_tool.py`：`task(wait=False)` + `wait_subagents()` 汇总等待
- `tests/test_mcp_preload.py`：MCP 预加载缓存与严格模式
- `tests/test_runtime_preload.py`：启动阶段预加载 toolsets
- `tests/test_chat_input.py`：输入框 Enter 提交、Ctrl+J 换行、@ 面板拦截键
- `tests/test_persistence.py`：MessageStore 加载/保存/原子写入/损坏文件处理
- `tests/test_chat_service.py`：ChatService（18 个用例）
  - 直接模式：text_delta / tool_events / 持久化 / 错误处理 / 用户取消 / clear
  - 队列模式：submit 返回 request_id / 广播事件 / 请求串行化
  - Rollback：截断历史 / 捕捉轮次边界 / busy 时拒绝 / 发布 snapshot
