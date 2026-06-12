"""M3 装配层单元测试 — 覆盖稳定前缀 / 注入块 / 装配流水线。"""

from __future__ import annotations

import pytest
import pytest_asyncio

from cairn.compact.manager import CompactManager
from cairn.compact.schemas import CompactResult, CompressedTurn, TokenCounts
from cairn.core.assembly import AssemblyPipeline, _from_compact
from cairn.memory.recall import RecallBundle, RecallResult
from cairn.memory.schemas import (
    EpisodicLayer,
    EpisodicNode,
    ProceduralNode,
    SemanticNode,
)
from cairn.prompts.injection import build_injection
from cairn.prompts.stable import (
    CONSTITUTION,
    DEFAULT_SEED_CONFIG,
    build_stable_prefix,
)


# ============================================================
# 测试节点工厂
# ============================================================


def _make_ep_node(text: str, score: float = 0.8) -> RecallResult:
    node = EpisodicNode(
        content={"text": text},
        layer=EpisodicLayer.L3_EPISODIC,
    )
    return RecallResult(node=node, score=score, channel="episodic")


def _make_sem_node(statement: str, score: float = 0.7) -> RecallResult:
    node = SemanticNode(statement=statement)
    return RecallResult(node=node, score=score, channel="semantic")


def _make_proc_node(pattern: str, score: float = 0.6) -> RecallResult:
    node = ProceduralNode(pattern=pattern)
    return RecallResult(node=node, score=score, channel="procedural")


def _make_bundle(
    episodic: list[RecallResult] | None = None,
    semantic: list[RecallResult] | None = None,
    procedural: list[RecallResult] | None = None,
) -> RecallBundle:
    return RecallBundle(
        episodic=episodic or [],
        semantic=semantic or [],
        procedural=procedural or [],
    )


# ============================================================
# Stub recall / compact
# ============================================================


class StubRecall:
    """返回预设 RecallBundle 的 recall pipeline。"""

    def __init__(self, bundle: RecallBundle | None = None):
        self.bundle = bundle or _make_bundle()
        self.calls: list[tuple] = []

    async def recall(self, query, **kwargs) -> RecallBundle:
        self.calls.append((query, kwargs))
        return self.bundle


class StubCompact:
    """返回预设 CompactResult 或 None 的 compact manager。"""

    def __init__(self, result: CompactResult | None = None):
        self.result = result
        self.calls: list = []

    async def compact(self, messages):
        self.calls.append(messages)
        return self.result


# ============================================================
# 稳定前缀
# ============================================================


class TestStablePrefix:
    def test_defaults(self):
        prefix = build_stable_prefix()
        assert "我叫 Cairn" in prefix
        assert "情绪基线" in prefix
        assert "我刚刚开始存在" in prefix

    def test_includes_constitution(self):
        prefix = build_stable_prefix()
        assert "我是谁" in prefix
        assert "1:1 的关系" in prefix

    def test_includes_seed_config(self):
        prefix = build_stable_prefix()
        assert "效价" in prefix or "valence" in prefix
        assert "冗长度" in prefix or "verbosity" in prefix

    def test_custom_self_portrait(self):
        portrait = "我是一个安静而好奇的存在，喜欢在沉默中观察。"
        prefix = build_stable_prefix(self_portrait=portrait)
        assert portrait in prefix
        assert "我刚刚开始存在" not in prefix

    def test_custom_constitution(self):
        prefix = build_stable_prefix(constitution="我是测试用的 Cairn。")
        assert "我是测试用的 Cairn" in prefix

    def test_custom_seed(self):
        seed = {"emotional_baseline": {"valence": 0.8, "arousal": 0.2, "dominance": 0.9},
                "rhythm": {"response_pace": "slow", "silence_tolerance": 0.9, "reflection_density": 0.3},
                "style": {"verbosity": 0.2, "directness": 0.8, "warmth_expression": 0.1}}
        prefix = build_stable_prefix(seed_config=seed)
        assert "0.8" in prefix
        assert "0.9" in prefix


# ============================================================
# 注入块
# ============================================================


class TestInjection:
    def test_empty_bundle_produces_empty_string(self):
        result = build_injection(_make_bundle())
        assert result == ""

    def test_includes_current_time(self):
        bundle = _make_bundle(episodic=[_make_ep_node("test")])
        result = build_injection(bundle)
        assert "当前时间" in result

    def test_episodic_only(self):
        bundle = _make_bundle(episodic=[_make_ep_node("用户喜欢 Python")])
        result = build_injection(bundle)
        assert "回忆起" in result
        assert "Python" in result

    def test_semantic_only(self):
        bundle = _make_bundle(semantic=[_make_sem_node("用户喜欢 Python")])
        result = build_injection(bundle)
        assert "事实" in result
        assert "Python" in result

    def test_procedural_only(self):
        bundle = _make_bundle(procedural=[_make_proc_node("在严肃话题上放慢节奏")])
        result = build_injection(bundle)
        assert "倾向于" in result
        assert "放慢节奏" in result

    def test_all_channels(self):
        bundle = _make_bundle(
            episodic=[_make_ep_node("用户说过喜欢 Python")],
            semantic=[_make_sem_node("用户喜欢 Python")],
            procedural=[_make_proc_node("放慢节奏")],
        )
        result = build_injection(bundle)
        assert "回忆起" in result
        assert "事实" in result
        assert "倾向于" in result

    def test_scores_included(self):
        bundle = _make_bundle(episodic=[_make_ep_node("test", score=0.95)])
        result = build_injection(bundle)
        assert "0.95" in result

    def test_multiple_per_channel(self):
        bundle = _make_bundle(
            episodic=[_make_ep_node("事件1"), _make_ep_node("事件2"), _make_ep_node("事件3")],
        )
        result = build_injection(bundle)
        lines = result.split("\n")
        # 格式为 "- [0.80] ..."
        assert sum(1 for l in lines if l.strip().startswith("- [0.8")) == 3


# ============================================================
# _from_compact
# ============================================================


class TestFromCompact:
    def test_summary_becomes_model_request(self):
        from pydantic_ai.messages import ModelRequest
        result = CompactResult(
            summary="This is a summary.",
            compressed_turns=[],
            tail_messages=[],
        )
        msgs = _from_compact(result)
        assert len(msgs) == 1
        assert isinstance(msgs[0], ModelRequest)
        assert "summary" in str(getattr(msgs[0], 'parts', []))

    def test_compressed_turns_preserve_structure(self):
        from pydantic_ai.messages import ModelRequest, ModelResponse
        turn = CompressedTurn(
            turn_span=(0, 2),
            user_content="user msg",
            assistant_content="assistant msg",
            tags=["general"],
        )
        result = CompactResult(
            summary="",
            compressed_turns=[turn],
            tail_messages=[],
        )
        msgs = _from_compact(result)
        assert len(msgs) == 2
        assert isinstance(msgs[0], ModelRequest)
        assert isinstance(msgs[1], ModelResponse)

    def test_tail_appended_verbatim(self):
        tail_msg = object()
        result = CompactResult(
            summary="",
            compressed_turns=[],
            tail_messages=[tail_msg],
        )
        msgs = _from_compact(result)
        assert msgs[0] is tail_msg

    def test_full_compact_result(self):
        from pydantic_ai.messages import ModelRequest, ModelResponse
        turns = [
            CompressedTurn(turn_span=(0, 2), user_content="u1", assistant_content="a1"),
            CompressedTurn(turn_span=(2, 4), user_content="u2", assistant_content="a2"),
        ]
        tail = [object(), object()]
        result = CompactResult(
            summary="Summary text.",
            compressed_turns=turns,
            tail_messages=tail,
        )
        msgs = _from_compact(result)
        # summary(1) + turns(2*2=4) + tail(2) = 7
        assert len(msgs) == 7
        assert isinstance(msgs[0], ModelRequest)  # summary
        assert isinstance(msgs[1], ModelRequest)  # turn1 user
        assert isinstance(msgs[2], ModelResponse)  # turn1 assistant
        assert msgs[-2] is tail[0]
        assert msgs[-1] is tail[1]

    def test_empty_compact_result(self):
        result = CompactResult(summary="", compressed_turns=[], tail_messages=[])
        msgs = _from_compact(result)
        assert msgs == []


# ============================================================
# AssemblyPipeline
# ============================================================


class TestAssemblyPipeline:
    @pytest.mark.asyncio
    async def test_no_recall_no_compact_returns_original(self):
        """没有 recall 和 compact 时，返回原消息列表。"""
        pipeline = AssemblyPipeline()
        msgs = ["msg1", "msg2"]
        result = await pipeline.assemble("hello", msgs)
        assert result == msgs

    @pytest.mark.asyncio
    async def test_with_recall_prepends_injection(self):
        pipeline = AssemblyPipeline()
        bundle = _make_bundle(episodic=[_make_ep_node("test memory")])
        pipeline.recall = StubRecall(bundle)
        result = await pipeline.assemble("hello", ["msg1"])
        assert len(result) == 2  # injection + original
        assert "test memory" in str(result[0])

    @pytest.mark.asyncio
    async def test_with_compact_replaces_history(self):
        pipeline = AssemblyPipeline()
        turn = CompressedTurn(turn_span=(0, 2), user_content="u", assistant_content="a")
        compact_result = CompactResult(
            summary="Summary.",
            compressed_turns=[turn],
            tail_messages=[],
        )
        pipeline.compact = StubCompact(compact_result)
        result = await pipeline.assemble("hello", ["old1", "old2", "old3", "old4"])
        assert len(result) == 3  # summary + user + assistant
        assert "Summary" in str(result[0])

    @pytest.mark.asyncio
    async def test_compact_returns_none_keeps_original(self):
        """compact 返回 None（无需压缩）时保持原消息。"""
        pipeline = AssemblyPipeline()
        pipeline.compact = StubCompact(None)
        result = await pipeline.assemble("hello", ["msg1", "msg2"])
        assert result == ["msg1", "msg2"]

    @pytest.mark.asyncio
    async def test_recall_and_compact_combined(self):
        """recall + compact 同时生效。"""
        pipeline = AssemblyPipeline()
        bundle = _make_bundle(semantic=[_make_sem_node("fact")])
        pipeline.recall = StubRecall(bundle)

        turn = CompressedTurn(turn_span=(0, 2), user_content="u", assistant_content="a")
        compact_result = CompactResult(
            summary="Summary.",
            compressed_turns=[turn],
            tail_messages=["tail1"],
        )
        pipeline.compact = StubCompact(compact_result)

        result = await pipeline.assemble("hello", ["old1", "old2"])
        # injection + summary + compressed_pair(2) + tail(1) = 5
        assert len(result) == 5
        # injection 在最前面
        assert "fact" in str(result[0])
        # summary 在第二位
        assert "Summary" in str(result[1])

    @pytest.mark.asyncio
    async def test_recall_failure_graceful(self):
        """recall 失败不应阻断装配。"""
        pipeline = AssemblyPipeline()

        class FailingRecall:
            async def recall(self, query, **kwargs):
                raise RuntimeError("embedding API down")

        pipeline.recall = FailingRecall()
        result = await pipeline.assemble("hello", ["msg1"])
        assert result == ["msg1"]

    @pytest.mark.asyncio
    async def test_compact_failure_graceful(self):
        """compact 失败不应阻断装配。"""
        pipeline = AssemblyPipeline()

        class FailingCompact:
            async def compact(self, messages):
                raise RuntimeError("compact LLM down")

        pipeline.compact = FailingCompact()
        result = await pipeline.assemble("hello", ["msg1"])
        assert result == ["msg1"]

    @pytest.mark.asyncio
    async def test_empty_recall_no_injection(self):
        """空 recall 不产生注入块，消息历史不变。"""
        pipeline = AssemblyPipeline()
        pipeline.recall = StubRecall(_make_bundle())
        result = await pipeline.assemble("hello", ["msg1", "msg2"])
        assert result == ["msg1", "msg2"]

    @pytest.mark.asyncio
    async def test_injection_msg_is_model_request(self):
        from pydantic_ai.messages import ModelRequest, SystemPromptPart
        pipeline = AssemblyPipeline()
        bundle = _make_bundle(episodic=[_make_ep_node("memory")])
        pipeline.recall = StubRecall(bundle)
        result = await pipeline.assemble("hello", [])
        assert isinstance(result[0], ModelRequest)
        parts = getattr(result[0], "parts", [])
        assert any(getattr(p, "part_kind", None) == "system-prompt" for p in parts)

    @pytest.mark.asyncio
    async def test_compact_with_only_summary_no_turns(self):
        """compact 只有 summary，没有 compressed turns。"""
        pipeline = AssemblyPipeline()
        compact_result = CompactResult(
            summary="Only summary.",
            compressed_turns=[],
            tail_messages=["tail1", "tail2"],
        )
        pipeline.compact = StubCompact(compact_result)
        result = await pipeline.assemble("hello", ["old1", "old2"])
        assert len(result) == 3  # summary + tail(2)
        assert "Only summary" in str(result[0])

    @pytest.mark.asyncio
    async def test_compact_with_tail_prepended_by_injection(self):
        """injection 在最前面，然后 compact 产出，tail 在最后。"""
        pipeline = AssemblyPipeline()
        bundle = _make_bundle(episodic=[_make_ep_node("remembered")])
        pipeline.recall = StubRecall(bundle)

        compact_result = CompactResult(
            summary="",
            compressed_turns=[],
            tail_messages=["tail1", "tail2"],
        )
        pipeline.compact = StubCompact(compact_result)

        result = await pipeline.assemble("hello", ["old1"])
        # injection(1) + tail(2) = 3
        assert len(result) == 3
        assert "remembered" in str(result[0])
        assert result[1] == "tail1"
        assert result[2] == "tail2"

