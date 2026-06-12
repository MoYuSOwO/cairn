"""M4 写回筛选单元测试 — LLM 驱动，覆盖解析/失败降级/多条目/边界。"""

from __future__ import annotations

import pytest
import pytest_asyncio

from cairn.core.writeback import (
    WriteBackCandidate,
    WriteBackFilter,
    _extract_json,
    _format_recent_context,
)


# ============================================================
# Mock LLM
# ============================================================


class StubLLM:
    """返回预置 JSON 响应的 mock LLM。"""

    def __init__(self, response: str = '{"should_remember": false, "entries": []}'):
        self.response = response
        self.calls: list[tuple[str, str]] = []

    async def __call__(self, system: str, user: str) -> str:
        self.calls.append((system, user))
        return self.response


class FailingLLM:
    """模拟 LLM 调用失败。"""

    async def __call__(self, system: str, user: str) -> str:
        raise RuntimeError("API error")


# ============================================================
# JSON 解析
# ============================================================


class TestExtractJson:
    def test_valid_json(self):
        data = _extract_json('{"should_remember": true, "entries": []}')
        assert data is not None
        assert data["should_remember"] is True

    def test_json_with_markdown_wrapper(self):
        data = _extract_json('```json\n{"should_remember": false}\n```')
        assert data is not None
        assert data["should_remember"] is False

    def test_json_with_prefix_text(self):
        data = _extract_json('Here is the result:\n{"should_remember": true, "entries": [{"content": "test", "signal": "other", "importance": 0.5}]}')
        assert data is not None
        assert len(data["entries"]) == 1

    def test_invalid_json_returns_none(self):
        assert _extract_json("not json") is None
        assert _extract_json("") is None


# ============================================================
# 近期上下文格式化
# ============================================================


class TestFormatRecentContext:
    def test_empty(self):
        assert "(" in _format_recent_context([])

    def test_single_turn(self):
        ctx = [{"user": "hello", "assistant": "hi"}]
        result = _format_recent_context(ctx)
        assert "hello" in result
        assert "hi" in result

    def test_truncates_to_last_3(self):
        ctx = [
            {"user": f"turn {i}", "assistant": f"resp {i}"}
            for i in range(10)
        ]
        result = _format_recent_context(ctx)
        # 只包含最后 3 轮
        assert "turn 7" in result
        assert "turn 8" in result
        assert "turn 9" in result
        assert "turn 0" not in result

    def test_numbering_order(self):
        """编号从 -3 到 -1（从早到晚），按时间顺序排列。"""
        ctx = [
            {"user": "oldest", "assistant": ""},
            {"user": "middle", "assistant": ""},
            {"user": "newest", "assistant": ""},
        ]
        result = _format_recent_context(ctx)
        # oldest 编号最小（-3），最早出现；newest 编号最大（-1），最后出现
        idx_oldest = result.find("oldest")
        idx_newest = result.find("newest")
        assert idx_oldest < idx_newest, f"oldest should appear before newest: {result}"
        assert "Turn -3" in result
        assert "Turn -1" in result

    def test_long_content_preserved(self):
        ctx = [{"user": "x" * 1000, "assistant": "y" * 1000}]
        result = _format_recent_context(ctx)
        assert "x" * 1000 in result
        assert "y" * 1000 in result


# ============================================================
# WriteBackFilter — LLM 调用
# ============================================================


class TestWriteBackFilter:
    @pytest.mark.asyncio
    async def test_no_llm_returns_empty(self):
        f = WriteBackFilter(call_llm=None)
        result = await f.evaluate("hello", "hi")
        assert result == []

    @pytest.mark.asyncio
    async def test_llm_says_no_remember(self):
        llm = StubLLM('{"should_remember": false, "entries": []}')
        f = WriteBackFilter(call_llm=llm)
        result = await f.evaluate("hello", "hi")
        assert result == []

    @pytest.mark.asyncio
    async def test_single_entry(self):
        llm = StubLLM(
            '{"should_remember": true, "entries": ['
            '{"content": "用户喜欢喝咖啡", "signal": "fact_update", "importance": 0.8, "category": "semantic"}'
            ']}'
        )
        f = WriteBackFilter(call_llm=llm)
        result = await f.evaluate("我喜欢喝咖啡", "好的我记住了")
        assert len(result) == 1
        c = result[0]
        assert c.content == "用户喜欢喝咖啡"
        assert c.signal == "fact_update"
        assert c.importance == 0.8
        assert c.category == "semantic"

    @pytest.mark.asyncio
    async def test_multiple_entries(self):
        llm = StubLLM(
            '{"should_remember": true, "entries": ['
            '{"content": "用户今天很难过", "signal": "emotion", "importance": 0.9, "category": "episodic"},'
            '{"content": "用户换了新工作", "signal": "fact_update", "importance": 0.85, "category": "semantic"}'
            ']}'
        )
        f = WriteBackFilter(call_llm=llm)
        result = await f.evaluate("我今天很难过，因为我换了新工作", "我理解")
        assert len(result) == 2
        assert result[0].signal == "emotion"
        assert result[1].signal == "fact_update"

    @pytest.mark.asyncio
    async def test_low_importance_filtered(self):
        """importance < 0.3 的条目被过滤。"""
        llm = StubLLM(
            '{"should_remember": true, "entries": ['
            '{"content": "important fact", "signal": "fact_update", "importance": 0.8},'
            '{"content": "trivial thing", "signal": "other", "importance": 0.1}'
            ']}'
        )
        f = WriteBackFilter(call_llm=llm)
        result = await f.evaluate("test", "test")
        assert len(result) == 1
        assert result[0].content == "important fact"

    @pytest.mark.asyncio
    async def test_llm_failure_graceful(self):
        f = WriteBackFilter(call_llm=FailingLLM())
        result = await f.evaluate("hello", "hi")
        assert result == []  # 静默跳过

    @pytest.mark.asyncio
    async def test_invalid_json_graceful(self):
        llm = StubLLM("not valid json at all")
        f = WriteBackFilter(call_llm=llm)
        result = await f.evaluate("hello", "hi")
        assert result == []

    @pytest.mark.asyncio
    async def test_prompt_includes_context(self):
        llm = StubLLM('{"should_remember": false, "entries": []}')
        f = WriteBackFilter(call_llm=llm)
        await f.evaluate(
            "我喜欢 Python",
            "Python 是很好的语言",
            recent_context=[{"user": "之前聊过编程", "assistant": "是的"}],
        )
        user_prompt = llm.calls[0][1]
        assert "之前聊过编程" in user_prompt
        assert "我喜欢 Python" in user_prompt

    @pytest.mark.asyncio
    async def test_prompt_includes_tool_events(self):
        llm = StubLLM('{"should_remember": false, "entries": []}')
        f = WriteBackFilter(call_llm=llm)
        await f.evaluate(
            "帮我部署", "好的",
            tool_events=[{"tool_name": "deploy", "ok": True}],
        )
        user_prompt = llm.calls[0][1]
        assert "deploy" in user_prompt

    @pytest.mark.asyncio
    async def test_prompt_includes_emotion_vec(self):
        llm = StubLLM('{"should_remember": false, "entries": []}')
        f = WriteBackFilter(call_llm=llm)
        await f.evaluate("hello", "hi", emotion_vec=[0.9, 0.8, 0.1])
        user_prompt = llm.calls[0][1]
        assert "0.9" in user_prompt

    @pytest.mark.asyncio
    async def test_custom_system_prompt(self):
        custom = "Custom memory curator prompt."
        llm = StubLLM('{"should_remember": false, "entries": []}')
        f = WriteBackFilter(call_llm=llm, system_prompt=custom)
        await f.evaluate("hello", "hi")
        assert llm.calls[0][0] == custom

    @pytest.mark.asyncio
    async def test_empty_entries_skipped(self):
        llm = StubLLM(
            '{"should_remember": true, "entries": ['
            '{"content": "", "signal": "other", "importance": 0.5}'
            ']}'
        )
        f = WriteBackFilter(call_llm=llm)
        result = await f.evaluate("hello", "hi")
        assert result == []

    @pytest.mark.asyncio
    async def test_missing_should_remember_field(self):
        llm = StubLLM('{"entries": [{"content": "test", "signal": "other", "importance": 0.5}]}')
        f = WriteBackFilter(call_llm=llm)
        result = await f.evaluate("hello", "hi")
        assert result == []  # should_remember 默认 falsy

    @pytest.mark.asyncio
    async def test_importance_clamped(self):
        """LLM 返回越界 importance 时 clamp 到 [0, 1]。"""
        llm = StubLLM(
            '{"should_remember": true, "entries": ['
            '{"content": "too high", "signal": "other", "importance": 1.5},'
            '{"content": "too low", "signal": "other", "importance": -0.5},'
            '{"content": "normal", "signal": "other", "importance": 0.8}'
            ']}'
        )
        f = WriteBackFilter(call_llm=llm)
        result = await f.evaluate("hello", "hi")
        # -0.5 → 0.0 → < 0.3 threshold → filtered out
        assert len(result) == 2
        assert result[0].importance == 1.0
        assert result[1].importance == 0.8

    @pytest.mark.asyncio
    async def test_default_signal_and_category(self):
        llm = StubLLM(
            '{"should_remember": true, "entries": [{"content": "something"}]}'
        )
        f = WriteBackFilter(call_llm=llm)
        result = await f.evaluate("hello", "hi")
        assert len(result) == 1
        assert result[0].signal == "other"
        assert result[0].category == "episodic"
        assert result[0].importance == 0.5


# ============================================================
# WriteBackCandidate 数据类
# ============================================================


class TestCandidate:
    def test_fields(self):
        c = WriteBackCandidate(
            content="用户喜欢 Python",
            signal="fact_update",
            importance=0.9,
            category="semantic",
        )
        assert c.content == "用户喜欢 Python"
        assert c.signal == "fact_update"
        assert c.importance == 0.9
        assert c.category == "semantic"
