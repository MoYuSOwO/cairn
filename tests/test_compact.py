"""M2 Compact 层单元测试 — 覆盖 token 估算 / 分区 / 摘要 / 逐轮压缩。"""

from __future__ import annotations

import pytest
import pytest_asyncio

from cairn.compact.manager import (
    CompactManager,
    _classify_content,
    _estimate_text_tokens,
    _extract_message_text,
    _find_turn_boundaries,
)
from cairn.compact.schemas import CompactResult, CompressedTurn, TokenCounts


# ============================================================
# 测试用 pydantic-ai 消息工厂
# ============================================================


def _make_request(text: str) -> object:
    from pydantic_ai.messages import ModelRequest, UserPromptPart
    return ModelRequest(parts=[UserPromptPart(content=text)])


def _make_response(text: str) -> object:
    from pydantic_ai.messages import ModelResponse, TextPart
    return ModelResponse(parts=[TextPart(content=text)])


def _make_messages(pairs: list[tuple[str, str]]) -> list:
    """从 (user, assistant) 文本对构造消息列表。"""
    msgs = []
    for user, assistant in pairs:
        msgs.append(_make_request(user))
        msgs.append(_make_response(assistant))
    return msgs


# ============================================================
# Mock LLM
# ============================================================


class StubLLM:
    """可编程的 mock LLM，返回预置响应或由回调生成。"""

    def __init__(self, responses: dict[str, str] | None = None):
        self.calls: list[tuple[str, str]] = []
        self._responses = responses or {}
        self._default = '{"user": "short", "assistant": "short", "tags": ["general"]}'

    def set_default(self, text: str) -> None:
        self._default = text

    async def __call__(self, system: str, user: str) -> str:
        self.calls.append((system, user))
        for key, val in (self._responses or {}).items():
            if key in user:
                return val
        return self._default


# ============================================================
# Token 估算
# ============================================================


class TestTokenEstimation:
    def test_empty_string(self):
        assert _estimate_text_tokens("") == 0

    def test_english_text(self):
        tokens = _estimate_text_tokens("Hello world, this is a test.")
        # ~35 chars / 4 ≈ 9
        assert 5 <= tokens <= 15

    def test_chinese_text(self):
        tokens = _estimate_text_tokens("你好世界这是一个测试")
        # 10 CJK chars / 1.5 ≈ 7
        assert 4 <= tokens <= 12

    def test_mixed_text(self):
        tokens = _estimate_text_tokens("Hello 你好 world 世界")
        assert tokens > 0

    def test_long_text_linear(self):
        """token 估算应大致线性。"""
        short = _estimate_text_tokens("hello world")
        long = _estimate_text_tokens("hello world " * 100)
        # 长文本应远大于短文本
        assert long > short * 50

    def test_estimate_messages(self):
        msgs = _make_messages([
            ("Hello, how are you?", "I'm fine, thank you!"),
            ("What about the project?", "It's going well."),
        ])
        manager = CompactManager()
        tokens = manager.estimate_tokens(msgs)
        assert tokens > 0

    def test_estimate_single_message(self):
        msg = _make_request("Hello world")
        manager = CompactManager()
        tokens = manager.estimate_tokens(msg)
        assert tokens > 0

    def test_estimate_string(self):
        manager = CompactManager()
        tokens = manager.estimate_tokens("Hello world")
        assert tokens > 0


# ============================================================
# 内容提取
# ============================================================


class TestExtractMessageText:
    def test_request_with_user_prompt(self):
        msg = _make_request("Hello, can you help me?")
        text = _extract_message_text(msg)
        assert "Hello" in text

    def test_response_with_text(self):
        msg = _make_response("Sure, what do you need?")
        text = _extract_message_text(msg)
        assert "Sure" in text

    def test_system_prompt_part_skipped(self):
        """SystemPromptPart 内容应在 compact 时被跳过（旧注入块）。"""
        from pydantic_ai.messages import ModelRequest, SystemPromptPart
        msg = ModelRequest(parts=[SystemPromptPart(content="injected recall context")])
        text = _extract_message_text(msg)
        # SystemPromptPart 应被跳过，返回空字符串
        assert text == ""

    def test_empty_parts(self):
        from pydantic_ai.messages import ModelRequest
        msg = ModelRequest(parts=[])
        text = _extract_message_text(msg)
        assert text == ""


# ============================================================
# 内容分型
# ============================================================


class TestContentClassification:
    def test_emotional_content(self):
        tags = _classify_content("我今天很难过，感觉被朋友背叛了")
        assert "emotional" in tags

    def test_technical_content(self):
        tags = _classify_content("这个 bug 需要在 deploy 之前 fix，修改 config 文件")
        assert "technical" in tags

    def test_mixed_content(self):
        tags = _classify_content("我很喜欢这个项目的代码结构，部署也很顺利")
        assert "emotional" in tags
        assert "technical" in tags

    def test_general_fallback(self):
        tags = _classify_content("今天天气不错")
        assert "general" in tags


# ============================================================
# Turn 边界检测
# ============================================================


class TestTurnBoundaries:
    def test_single_turn(self):
        msgs = _make_messages([("hello", "hi")])
        boundaries = _find_turn_boundaries(msgs)
        assert len(boundaries) == 1
        assert boundaries[0] == (0, 2)

    def test_multiple_turns(self):
        msgs = _make_messages([
            ("first question", "first answer"),
            ("second question", "second answer"),
            ("third question", "third answer"),
        ])
        boundaries = _find_turn_boundaries(msgs)
        assert len(boundaries) == 3
        assert boundaries[0] == (0, 2)
        assert boundaries[1] == (2, 4)
        assert boundaries[2] == (4, 6)

    def test_empty_messages(self):
        boundaries = _find_turn_boundaries([])
        assert boundaries == []

    def test_request_only(self):
        msgs = [_make_request("hello")]
        boundaries = _find_turn_boundaries(msgs)
        assert len(boundaries) == 1
        assert boundaries[0] == (0, 1)

    def test_injection_msg_skipped_as_turn_boundary(self):
        """纯 SystemPromptPart 的注入消息不应作为 turn 起点。"""
        from pydantic_ai.messages import ModelRequest, SystemPromptPart
        injection = ModelRequest(parts=[SystemPromptPart(content="injected recall")])
        user = _make_request("hello")
        asst = _make_response("hi")
        msgs = [injection, user, asst]
        boundaries = _find_turn_boundaries(msgs)
        # 只有一个 turn（user, asst），注入消息不计入
        assert len(boundaries) == 1
        start, end = boundaries[0]
        # turn 从 user 开始（索引 1），不是从 injection（索引 0）开始
        assert start == 1


# ============================================================
# 分区逻辑
# ============================================================


class TestPartition:
    def test_all_fit_in_tail(self, manager: CompactManager):
        """消息很少时全部落入 tail。"""
        msgs = _make_messages([("hi", "hello")])
        to_sum, to_comp, tail, spans = manager._partition(msgs)
        assert len(to_sum) == 0
        assert len(to_comp) == 0
        assert len(tail) == 2
        assert spans == []

    def test_large_history_partitions(self, manager: CompactManager):
        """大量消息时触发完整三分区。"""
        msgs = _make_messages(
            [("question " + str(i), "answer " + str(i)) for i in range(100)]
        )
        to_sum, to_comp, tail, spans = manager._partition(msgs)
        assert len(tail) > 0
        assert len(to_sum) + len(to_comp) + len(tail) == len(msgs)

    def test_tail_preserves_recent(self, manager: CompactManager):
        """tail 应包含最后的消息。"""
        msgs = _make_messages(
            [("q" + str(i), "a" + str(i)) for i in range(50)]
        )
        _, _, tail, _ = manager._partition(msgs)
        last_msg = msgs[-1]
        assert last_msg in tail

    def test_summary_is_older_than_compress(self, manager: CompactManager):
        """Summary 区消息应比 Compress 区更旧（索引更小）。"""
        msgs = _make_messages(
            [("q" + str(i), "a" + str(i)) for i in range(50)]
        )
        to_sum, to_comp, tail, spans = manager._partition(msgs)
        if to_sum and to_comp:
            sum_indices = {id(m) for m in to_sum}
            comp_indices = {id(m) for m in to_comp}
            assert sum_indices.isdisjoint(comp_indices)
            max_sum_idx = max(msgs.index(m) for m in to_sum)
            min_comp_idx = min(msgs.index(m) for m in to_comp)
            assert max_sum_idx < min_comp_idx

    def test_zones_non_overlapping_and_ordered(self, manager: CompactManager):
        """三段无重叠且有序：summary < compress < tail（按原始索引）。"""
        msgs = _make_messages(
            [("q" + str(i), "a" + str(i)) for i in range(50)]
        )
        to_sum, to_comp, tail, spans = manager._partition(msgs)
        # 收集每段在原始列表中的索引范围
        def index_range(slice_msgs):
            if not slice_msgs:
                return None
            return (msgs.index(slice_msgs[0]), msgs.index(slice_msgs[-1]))
        sum_range = index_range(to_sum)
        comp_range = index_range(to_comp)
        tail_range = index_range(tail)
        # 验证三段按顺序排列（存在的段之间）
        if sum_range and comp_range:
            assert sum_range[1] < comp_range[0], f"summary {sum_range} should be before compress {comp_range}"
        if comp_range and tail_range:
            assert comp_range[1] < tail_range[0], f"compress {comp_range} should be before tail {tail_range}"
        if sum_range and tail_range:
            assert sum_range[1] < tail_range[0]

    def test_compress_spans_are_original_indices(self, manager: CompactManager):
        """compress_spans 中的索引指向原始消息列表的正确位置。"""
        msgs = _make_messages(
            [("q" + str(i), "a" + str(i)) for i in range(50)]
        )
        _, to_comp, _, spans = manager._partition(msgs)
        if spans:
            for start, end in spans:
                # span 范围内的消息应在 to_compress 中
                for idx in range(start, end):
                    assert msgs[idx] in to_comp


# ============================================================
# 触发逻辑
# ============================================================


class TestShouldCompact:
    def test_below_threshold(self, manager: CompactManager):
        msgs = _make_messages([("hi", "hello")])
        assert not manager.should_compact(msgs)

    def test_empty_messages(self, manager: CompactManager):
        assert not manager.should_compact([])


# ============================================================
# Compact 端到端 (Mock LLM)
# ============================================================


class TestCompactEndToEnd:
    @pytest_asyncio.fixture
    async def llm(self) -> StubLLM:
        return StubLLM()

    @pytest.mark.asyncio
    async def test_compact_below_threshold_returns_none(
        self, llm: StubLLM
    ):
        manager = CompactManager(call_llm=llm, token_budget=100_000)
        msgs = _make_messages([("hi", "hello")])
        result = await manager.compact(msgs)
        assert result is None

    @pytest.mark.asyncio
    async def test_compact_with_mock_llm(
        self, llm: StubLLM
    ):
        """用 mock LLM 跑完整 compact 流程。"""
        # 设置小预算使 compact 触发
        manager = CompactManager(call_llm=llm, token_budget=500, trigger_pct=0.3)
        # 构造足够多的消息触发 compact
        msgs = _make_messages(
            [("a long question number " + str(i), "a detailed answer number " + str(i))
             for i in range(50)]
        )
        result = await manager.compact(msgs)

        assert result is not None
        assert isinstance(result, CompactResult)
        # Summary 和 compressed turns 至少有一个非空
        assert result.summary or result.compressed_turns
        # tail 应该保留
        assert len(result.tail_messages) > 0
        # token 统计
        assert result.token_counts.before > 0
        assert result.token_counts.after > 0
        # LLM 被调用过
        assert len(llm.calls) > 0

    @pytest.mark.asyncio
    async def test_summary_prompt_contains_conversation(
        self, llm: StubLLM
    ):
        llm.set_default("Summary of the chat.")
        manager = CompactManager(call_llm=llm, token_budget=500, trigger_pct=0.3)
        msgs = _make_messages(
            [("I love Python programming", "That's great! Python is awesome.")
             for _ in range(50)]
        )
        result = await manager.compact(msgs)

        assert result is not None
        # 检查 summary 调用中包含对话内容 (user prompt 以 "Summarize" 开头)
        summary_calls = [c for c in llm.calls if "Summarize" in c[1]]
        assert len(summary_calls) > 0
        _, user_prompt = summary_calls[0]
        assert "Python" in user_prompt

    @pytest.mark.asyncio
    async def test_compress_preserves_turn_structure(
        self, llm: StubLLM
    ):
        """压缩结果每个 CompressedTurn 都有 user_content 和 assistant_content。"""
        llm.set_default(
            '{"user": "compressed user msg", "assistant": "compressed assistant msg", "tags": ["general"]}'
        )
        manager = CompactManager(call_llm=llm, token_budget=500, trigger_pct=0.3, tail_ratio=0.1)
        msgs = _make_messages(
            [("question " + str(i), "answer " + str(i)) for i in range(50)]
        )
        result = await manager.compact(msgs)

        assert result is not None
        for turn in result.compressed_turns:
            assert turn.user_content
            assert turn.assistant_content
            assert isinstance(turn.tags, list)

    @pytest.mark.asyncio
    async def test_compact_result_has_token_stats(
        self, llm: StubLLM
    ):
        llm.set_default(
            '{"user": "q", "assistant": "a", "tags": ["general"]}'
        )
        manager = CompactManager(call_llm=llm, token_budget=500, trigger_pct=0.3, tail_ratio=0.1)
        msgs = _make_messages(
            [("question " + str(i), "answer " + str(i)) for i in range(50)]
        )
        result = await manager.compact(msgs)

        assert result is not None
        tc = result.token_counts
        assert tc.before > tc.after  # 压缩应该减少 token
        assert tc.tail_tokens > 0
        assert tc.compression_ratio > 1.0
        assert tc.savings > 0

    @pytest.mark.asyncio
    async def test_no_llm_returns_none(self):
        """未配置 LLM 时 compact 返回 None。"""
        manager = CompactManager(call_llm=None, token_budget=500, trigger_pct=0.3)
        msgs = _make_messages(
            [("q" + str(i), "a" + str(i)) for i in range(50)]
        )
        result = await manager.compact(msgs)
        assert result is None

    @pytest.mark.asyncio
    async def test_too_few_messages_returns_none(self, llm: StubLLM):
        """少于 4 条消息时不触发 compact。"""
        manager = CompactManager(call_llm=llm, token_budget=100, trigger_pct=0.1)
        msgs = _make_messages([("hi", "hello")])
        result = await manager.compact(msgs)
        assert result is None

    @pytest.mark.asyncio
    async def test_token_reduction(
        self, llm: StubLLM
    ):
        """CompactResult 包含 token_reduction (a3 §3.4)。"""
        llm.set_default(
            '{"user": "q", "assistant": "a", "tags": ["general"]}'
        )
        manager = CompactManager(call_llm=llm, token_budget=500, trigger_pct=0.3, tail_ratio=0.1)
        msgs = _make_messages(
            [("question " + str(i), "answer " + str(i)) for i in range(50)]
        )
        result = await manager.compact(msgs)

        assert result is not None
        before, after = result.token_reduction
        assert before > 0
        assert after >= 0
        # 压缩后应小于压缩前
        assert after < before

    @pytest.mark.asyncio
    async def test_turn_span_is_original_index(
        self, llm: StubLLM
    ):
        """CompressedTurn.turn_span 指向原始消息列表索引。"""
        llm.set_default(
            '{"user": "q", "assistant": "a", "tags": ["general"]}'
        )
        manager = CompactManager(call_llm=llm, token_budget=500, trigger_pct=0.3, tail_ratio=0.1)
        msgs = _make_messages(
            [("question " + str(i), "answer " + str(i)) for i in range(50)]
        )
        result = await manager.compact(msgs)

        assert result is not None
        for turn in result.compressed_turns:
            start, end = turn.turn_span
            assert start >= 0
            assert end <= len(msgs)
            assert start < end
            # span 内的消息应该在原列表中存在
            for idx in range(start, end):
                assert idx < len(msgs)


# ============================================================
# 启发式压缩 (无 LLM 降级)
# ============================================================


class TestHeuristicCompress:
    def test_emotional_truncation_longer(self, manager: CompactManager):
        """情感内容启发式压缩保留更多字符 (800 vs 300)。"""
        emotional_user = "我觉得很难过，因为我的朋友不理我了 " * 50
        emotional_asst = "我理解你的感受，这真的很令人伤心 " * 50

        result = manager._heuristic_compress((0, 1), emotional_user, emotional_asst)
        assert len(result.user_content) <= 803  # 800 + "..."
        assert "emotional" in result.tags

    def test_technical_truncation_shorter(self, manager: CompactManager):
        """技术内容启发式压缩保留更少字符。"""
        tech_user = "请帮我修复这个 bug：error in deploy script " * 50
        tech_asst = "需要修改 config 文件和 docker 配置 " * 50

        result = manager._heuristic_compress((0, 1), tech_user, tech_asst)
        # 技术内容截断应比情感内容短
        assert len(result.user_content) <= 303  # 300 + "..."
        assert "technical" in result.tags

    def test_short_content_not_truncated(self, manager: CompactManager):
        result = manager._heuristic_compress((0, 1), "hi", "hello")
        assert result.user_content == "hi"
        assert result.assistant_content == "hello"


# ============================================================
# TokenCounts 数据类
# ============================================================


class TestTokenCounts:
    def test_compression_ratio(self):
        tc = TokenCounts(before=1000, after=250)
        assert tc.compression_ratio == 4.0
        assert tc.savings == 750

    def test_ratio_zero_after(self):
        tc = TokenCounts(before=100, after=0)
        assert tc.compression_ratio == 1.0


# ============================================================
# CompressedTurn 数据类
# ============================================================


class TestCompressedTurn:
    def test_default_tags(self):
        turn = CompressedTurn(
            turn_span=(0, 2),
            user_content="hi",
            assistant_content="hello",
        )
        assert turn.tags == []

    def test_with_tags(self):
        turn = CompressedTurn(
            turn_span=(0, 2),
            user_content="I feel sad",
            assistant_content="I understand",
            tags=["emotional"],
        )
        assert "emotional" in turn.tags


# ============================================================
# 配置
# ============================================================


# ============================================================
# JSON 解析健壮性
# ============================================================


class TestJsonExtraction:
    def test_normal_key_order(self, manager: CompactManager):
        data = manager._extract_json(
            '{"user": "short q", "assistant": "short a", "tags": ["general"]}'
        )
        assert data is not None
        assert data["user"] == "short q"
        assert data["assistant"] == "short a"

    def test_reversed_key_order(self, manager: CompactManager):
        """JSON key 顺序颠倒时仍能正确解析（暴露问题 3）。"""
        data = manager._extract_json(
            '{"assistant": "short a", "tags": ["emotional"], "user": "short q"}'
        )
        assert data is not None
        assert data["user"] == "short q"
        assert data["assistant"] == "short a"
        assert data["tags"] == ["emotional"]

    def test_json_with_nested_braces(self, manager: CompactManager):
        """包含花括号的值能正确提取。"""
        data = manager._extract_json(
            '{"user": "try { this }", "assistant": "ok { done }", "tags": ["technical"]}'
        )
        assert data is not None
        assert data["user"] == "try { this }"

    def test_json_with_newlines(self, manager: CompactManager):
        data = manager._extract_json(
            '{\n  "user": "hello",\n  "assistant": "world",\n  "tags": []\n}'
        )
        assert data is not None
        assert data["user"] == "hello"

    def test_json_with_markdown_wrapper(self, manager: CompactManager):
        """LLM 常在 JSON 外加 markdown 代码块。"""
        data = manager._extract_json(
            '```json\n{"user": "hi", "assistant": "hello", "tags": ["general"]}\n```'
        )
        assert data is not None
        assert data["user"] == "hi"

    def test_response_with_prefix_text(self, manager: CompactManager):
        """JSON 前有额外文本。"""
        data = manager._extract_json(
            'Here is the compressed turn:\n{"user": "hi", "assistant": "hello", "tags": ["general"]}'
        )
        assert data is not None
        assert data["user"] == "hi"

    def test_invalid_json_returns_none(self, manager: CompactManager):
        data = manager._extract_json("not json at all")
        assert data is None

    def test_parse_response_reversed_keys(self, manager: CompactManager):
        """_parse_compress_response 在 key 顺序颠倒时不降级到启发式。"""
        result = manager._parse_compress_response(
            '{"assistant": "I understand", "tags": ["emotional"], "user": "I feel sad"}',
            "fallback_user",
            "fallback_assistant",
        )
        assert result.user_content == "I feel sad"
        assert result.assistant_content == "I understand"
        assert "emotional" in result.tags


class TestCompactManagerConfig:
    def test_trigger_threshold(self):
        m = CompactManager(token_budget=100_000, trigger_pct=0.55)
        assert m.trigger_threshold == 55_000

    def test_defaults(self):
        m = CompactManager()
        assert m.token_budget == 100_000
        assert m.trigger_pct == 0.55
        assert m.tail_ratio == 0.20


# ============================================================
# Fixtures
# ============================================================


@pytest.fixture
def manager() -> CompactManager:
    return CompactManager(token_budget=100_000, trigger_pct=0.55, tail_ratio=0.20)
