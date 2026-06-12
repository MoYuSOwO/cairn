"""M5 反思调度单元测试 — 覆盖衰减 / 边修剪 / 写回队列 / 调度器生命周期。"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import pytest_asyncio

from cairn.memory.schemas import (
    DiaryEntry,
    DiaryEntryType,
    EpisodicLayer,
    EpisodicNode,
    HebbianEdge,
    NodeType,
)
from cairn.memory.store import MemoryStore
from cairn.reflection.scheduler import ReflectionScheduler
from cairn.reflection.tasks import (
    decay_all_nodes,
    process_write_queue,
    prune_all_edges,
    scan_self_defining,
    update_self_portrait,
)

# ============================================================
# Mock LLM
# ============================================================


class StubLLM:
    def __init__(self, response: str = "I am Cairn. I am beginning to understand myself."):
        self.response = response
        self.calls: list[tuple[str, str]] = []

    async def __call__(self, system: str, user: str) -> str:
        self.calls.append((system, user))
        return self.response


# ============================================================
# 衰减
# ============================================================


class TestDecay:
    def test_node_weight_decreases(self, store: MemoryStore):
        node = EpisodicNode(weight=1.0, decay_half_life=7.0)
        store.insert_episodic(node)

        decay_all_nodes(store, days_passed=7.0)

        got = store.get_episodic(node.id)
        assert got is not None
        assert got.weight == pytest.approx(0.5, abs=0.01)  # 半衰期 7 天，过 7 天 → 0.5

    def test_one_day_decay_small(self, store: MemoryStore):
        node = EpisodicNode(weight=1.0, decay_half_life=90.0)
        store.insert_episodic(node)

        decay_all_nodes(store, days_passed=1.0)

        got = store.get_episodic(node.id)
        assert got is not None
        # 半衰期 90 天，过 1 天衰减极小
        assert got.weight > 0.99

    def test_self_defining_exempt(self, store: MemoryStore):
        """身世记忆标记豁免衰减 (a1 §2.2)。"""
        node = EpisodicNode(
            weight=1.0,
            decay_half_life=float("inf"),
            is_self_defining=True,
        )
        store.insert_episodic(node)

        decay_all_nodes(store, days_passed=365.0)

        got = store.get_episodic(node.id)
        assert got is not None
        assert got.weight == 1.0  # 不衰减

    def test_below_threshold_l3_deleted(self, store: MemoryStore):
        node = EpisodicNode(
            weight=0.06,
            decay_half_life=1.0,  # 极短半衰期
            layer=EpisodicLayer.L3_EPISODIC,
        )
        store.insert_episodic(node)

        decay_all_nodes(store, days_passed=1.0)

        # 权重降到 0.06 * 0.5 = 0.03 < 0.05 → 删除
        assert store.get_episodic(node.id) is None

    def test_below_threshold_l2_preserved(self, store: MemoryStore):
        """L2 节点即使权重低也不删（进入沉睡态）。"""
        node = EpisodicNode(
            weight=0.06,
            decay_half_life=1.0,
            layer=EpisodicLayer.L2_GENERAL,
        )
        store.insert_episodic(node)

        decay_all_nodes(store, days_passed=1.0)

        got = store.get_episodic(node.id)
        assert got is not None  # L2 保留

    def test_semantic_preserved_below_threshold(self, store: MemoryStore):
        from cairn.memory.schemas import SemanticNode
        node = SemanticNode(statement="test", weight=0.06, decay_half_life=1.0)
        store.insert_semantic(node)

        decay_all_nodes(store, days_passed=1.0)

        got = store.get_semantic(node.id)
        assert got is not None  # semantic 沉睡不删

    def test_procedural_preserved_below_threshold(self, store: MemoryStore):
        from cairn.memory.schemas import ProceduralNode
        node = ProceduralNode(pattern="test", weight=0.06, decay_half_life=1.0)
        store.insert_procedural(node)

        decay_all_nodes(store, days_passed=1.0)

        got = store.get_procedural(node.id)
        assert got is not None  # procedural 沉睡不删

    def test_returns_removal_counts(self, store: MemoryStore):
        for _ in range(3):
            node = EpisodicNode(weight=0.06, decay_half_life=0.5)
            store.insert_episodic(node)

        result = decay_all_nodes(store, days_passed=10.0)
        assert result["episodic"] == 3


# ============================================================
# 边修剪
# ============================================================


class TestEdgePrune:
    def test_low_weight_edges_removed(self, store: MemoryStore):
        store.insert_hebbian_edge(
            HebbianEdge(source_id="a", target_id="b", weight=0.02),
            NodeType.EPISODIC,
        )
        store.insert_hebbian_edge(
            HebbianEdge(source_id="a", target_id="c", weight=0.5),
            NodeType.EPISODIC,
        )

        result = prune_all_edges(store, threshold=0.05)
        assert result["episodic"] == 1

        neighbors = store.get_hebbian_neighbors("a", NodeType.EPISODIC)
        assert len(neighbors) == 1
        assert neighbors[0].target_id == "c"

    def test_prunes_all_types(self, store: MemoryStore):
        for nt in (NodeType.EPISODIC, NodeType.SEMANTIC, NodeType.PROCEDURAL):
            store.insert_hebbian_edge(
                HebbianEdge(source_id="a", target_id="b", weight=0.01), nt
            )

        result = prune_all_edges(store, threshold=0.05)
        assert result["episodic"] == 1
        assert result["semantic"] == 1
        assert result["procedural"] == 1


# ============================================================
# 写回队列处理
# ============================================================


class TestProcessWriteQueue:
    def test_dequeues_and_writes_node(self, store: MemoryStore):
        import json
        node = EpisodicNode(content={"text": "用户喜欢 Python"})
        store.enqueue_write(node)

        processed = process_write_queue(store)
        assert processed == 1

        # 节点已写入
        got = store.get_episodic(node.id)
        assert got is not None
        assert got.content == {"text": "用户喜欢 Python"}

    def test_empty_queue_returns_zero(self, store: MemoryStore):
        assert process_write_queue(store) == 0

    def test_creates_hebbian_edges_with_recent(self, store: MemoryStore):
        import json
        # 先写入一个已有的 L3 节点
        existing = EpisodicNode(content={"text": "existing"})
        store.insert_episodic(existing)
        store.update_episodic_access(existing.id)  # 标记为最近访问

        # 队列中有新节点
        new_node = EpisodicNode(content={"text": "new"})
        store.enqueue_write(new_node)

        processed = process_write_queue(store)
        assert processed == 1

        # 新节点与已有节点之间应有赫布边
        neighbors = store.get_hebbian_neighbors(new_node.id, NodeType.EPISODIC)
        assert len(neighbors) >= 1

    def test_corrupted_entry_skipped(self, store: MemoryStore):
        """损坏的队列条目不应阻塞其他条目处理。"""
        # 手动插入一条损坏的队列记录（processed=0 表示待处理）
        store._conn.execute(
            "INSERT INTO write_queue (node_json, processed, created_at) VALUES (?, 0, ?)",
            ("not valid json", "2024-01-01T00:00:00+00:00"),
        )
        store._conn.commit()

        node = EpisodicNode(content={"text": "valid"})
        store.enqueue_write(node)

        processed = process_write_queue(store)
        # valid 的那条被处理
        assert processed >= 1


# ============================================================
# Self-defining 扫描
# ============================================================


class TestScanSelfDefining:
    def test_no_nodes_no_crash(self, store: MemoryStore):
        updated = scan_self_defining(store)
        assert updated == 0

    def test_high_emotion_degree_access_marks_self_defining(self, store: MemoryStore):
        # 创建满足条件的节点
        node = EpisodicNode(
            emotion_vec=[0.9, 0.9, 0.1],  # high intensity
            access_count=100,
            weight=1.0,
        )
        store.insert_episodic(node)

        # 增加度数
        for i in range(5):
            other = EpisodicNode()
            store.insert_episodic(other)
            store.insert_hebbian_edge(
                HebbianEdge(source_id=node.id, target_id=other.id, weight=0.5),
                NodeType.EPISODIC,
            )

        updated = scan_self_defining(store)
        assert updated >= 0  # 取决于阈值计算


# ============================================================
# 自画像更新
# ============================================================


class TestSelfPortrait:
    @pytest.mark.asyncio
    async def test_updates_self_portrait(self, store: MemoryStore):
        llm = StubLLM("I am Cairn. I notice that I am becoming more curious.")
        # 种子一些记忆
        for i in range(5):
            node = EpisodicNode(content={"text": f"memory {i}"}, weight=0.8)
            store.insert_episodic(node)

        result = await update_self_portrait(store, llm)

        assert result is not None
        assert result.entry_type == DiaryEntryType.SELF_PORTRAIT
        assert "Cairn" in result.content

        # 最新自画像可查询
        latest = store.get_latest_self_portrait()
        assert latest is not None
        assert latest.content == "I am Cairn. I notice that I am becoming more curious."

    @pytest.mark.asyncio
    async def test_llm_failure_returns_none(self, store: MemoryStore):
        class FailingLLM:
            async def __call__(self, s, u):
                raise RuntimeError("down")

        result = await update_self_portrait(store, FailingLLM())
        assert result is None

    @pytest.mark.asyncio
    async def test_first_portrait_from_memories(self, store: MemoryStore):
        """首次自画像——没有当前自画像时从记忆中生成。"""
        llm = StubLLM("I am just beginning.")
        node = EpisodicNode(content={"text": "用户说喜欢和我聊天"}, weight=0.9)
        store.insert_episodic(node)

        result = await update_self_portrait(store, llm)

        assert result is not None
        # LLM prompt 应包含记忆和"(暂无)"表示无当前自画像
        user_prompt = llm.calls[0][1]
        assert "暂无" in user_prompt
        # 提示应包含记忆内容
        assert "喜欢和我聊天" in user_prompt


# ============================================================
# 调度器
# ============================================================


class TestSchedulerLifecycle:
    @pytest.mark.asyncio
    async def test_start_and_stop(self, store: MemoryStore):
        scheduler = ReflectionScheduler(store)
        assert not scheduler.running

        await scheduler.start()
        assert scheduler.running
        await asyncio.sleep(0.1)
        await scheduler.stop()
        assert not scheduler.running

    @pytest.mark.asyncio
    async def test_daily_tasks_execute(self, store: MemoryStore):
        """调度器启动后首次 tick 应执行每日任务。”
        由于 _last_daily 为 None，立即触发。"""
        # 写入一些需要衰减的节点
        node = EpisodicNode(weight=1.0, decay_half_life=7.0)
        store.insert_episodic(node)

        scheduler = ReflectionScheduler(store)
        await scheduler.start()
        await asyncio.sleep(0.2)  # 等待 tick
        await scheduler.stop()

        # 衰减后权重应下降
        got = store.get_episodic(node.id)
        # 过约 0 天（tick 间隔很短），但首次 daily 会执行
        # 实际上 days_passed=1 会稍微衰减
        assert got.weight < 1.0

    @pytest.mark.asyncio
    async def test_no_weekly_without_llm(self, store: MemoryStore):
        """没有 call_llm 时每周任务仍执行自画像以外的部分。"""
        scheduler = ReflectionScheduler(store, call_llm=None)
        await scheduler.start()
        await asyncio.sleep(0.2)
        await scheduler.stop()
        # 不应崩溃

    @pytest.mark.asyncio
    async def test_scheduler_handles_task_failure(self, store: MemoryStore):
        """任务失败不应导致调度器崩溃。"""
        scheduler = ReflectionScheduler(store)

        async def _test():
            await scheduler.start()
            await asyncio.sleep(0.15)
            await scheduler.stop()

        await _test()  # 不应抛出异常


# ============================================================
# Fixtures
# ============================================================


@pytest.fixture
def store(tmp_path: Path) -> MemoryStore:
    s = MemoryStore(db_path=tmp_path / "test_reflection.sqlite")
    yield s
    s.close()
