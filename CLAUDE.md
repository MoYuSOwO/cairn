# CLAUDE.md — cairn

## 常用命令

```bash
uv sync                    # 安装依赖
uv run cairn               # 内嵌模式 TUI
uv run cairn serve         # 后端服务 (FastAPI + WS)
uv run cairn tui           # TUI 客户端 (连后端)
uv run pytest tests -q     # 运行测试 (45 个)
```

## 架构要点

- **三种运行模式**：`cairn`（内嵌 ChatService 直调）、`cairn serve`（HTTP + WS 后端）、`cairn tui`（WS 客户端）
- **ChatService** (`cairn/core/chat_service.py`) 拥有 Agent 生命周期 + 请求队列 + 全局广播
- **消息持久化** (`cairn/core/persistence.py`)：`~/.cairn/history.json`，pydantic-ai dataclass 序列化（非 Pydantic model_dump）
- **事件广播**：所有 WS 客户端收到相同事件流，fan-out 模式，12 种下行事件
- **回滚安全**：`_busy` 标记防止运行中回滚，自动捕捉轮次起点

架构详见 → `llmdoc/architecture/modules.md`

## 关键约定

- pydantic-ai 的 `ModelRequest` / `ModelResponse` 是 **dataclass**，不是 Pydantic BaseModel。序列化用 `dataclasses.asdict()`，不要用 `model_dump()`
- 工具事件 (`TextDelta` / `ToolStarted` / `ToolFinished` / `Done` / `Error`) 是 frozen dataclass，定义在 `cairn/core/chat_events.py`
- 测试可以 import `MessageStore` 和 `ChatService` 直接测，Agent 用 `FakeAgent` 注入
- `uv.lock` 用 `uv lock` 更新，不要手动改

## 设计文档

- 模块架构 → `llmdoc/architecture/modules.md`
- WS 协议 + 数据模型 → `llmdoc/reference/schemas.md`
- UI 组件接口 → `llmdoc/reference/ui-components.md`
- 使用指南 → `llmdoc/guides/usage.md`
- 测试覆盖 → `llmdoc/guides/testing.md`

## 未来方向

记忆系统 / 日记系统 / 压缩策略的设计方案在 `structuredoc/`：
- `memory.md` — 双层存储 (SQLite + ChromaDB)，多维向量，权重衰减，定期反思
- `diary.md` — 日常日记 + 14 天自我画像更新
- `compact.md` — 三分区多轮压缩策略

## 偏好

- 不要自动 commit，等用户明确要求
- 修改 `pyproject.toml` 的 version 时，询问是否发布到 PyPI
