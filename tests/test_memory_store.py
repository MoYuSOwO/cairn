"""M0 数据层单元测试 — 覆盖 CRUD + 连接表 + 衰减字段。"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from cairn.memory.schemas import (
    ContainsEdge,
    DerivationEdge,
    DiaryEntry,
    DiaryEntryType,
    EpisodicLayer,
    EpisodicNode,
    HebbianEdge,
    MetricEvent,
    NodeType,
    ProceduralNode,
    SemanticNode,
)
from cairn.memory.store import MemoryStore, _validate_table


class TestEpisodicNodeCRUD:
    def test_insert_and_get(self, store: MemoryStore):
        node = EpisodicNode(
            content={"text": "用户说喜欢 Python"},
            semantic_vec=[0.1, 0.2, 0.3],
            emotion_vec=[0.7, 0.5, 0.4],
            layer=EpisodicLayer.L3_EPISODIC,
        )
        store.insert_episodic(node)

        got = store.get_episodic(node.id)
        assert got is not None
        assert got.content == {"text": "用户说喜欢 Python"}
        assert got.layer == EpisodicLayer.L3_EPISODIC
        assert got.emotion_vec == [0.7, 0.5, 0.4]
        assert got.access_count == 0
        assert got.weight == 1.0

    def test_update_weight(self, store: MemoryStore):
        node = EpisodicNode()
        store.insert_episodic(node)

        store.update_episodic_weight(node.id, 0.5)
        got = store.get_episodic(node.id)
        assert got.weight == 0.5

    def test_update_access(self, store: MemoryStore):
        node = EpisodicNode()
        store.insert_episodic(node)

        store.update_episodic_access(node.id)
        got = store.get_episodic(node.id)
        assert got.access_count == 1

    def test_delete(self, store: MemoryStore):
        node = EpisodicNode()
        store.insert_episodic(node)

        store.delete_episodic(node.id)
        assert store.get_episodic(node.id) is None

    def test_list_by_layer(self, store: MemoryStore):
        for i in range(3):
            node = EpisodicNode(
                content={"n": i}, layer=EpisodicLayer.L3_EPISODIC
            )
            store.insert_episodic(node)
        l2 = EpisodicNode(
            content={"type": "general"},
            layer=EpisodicLayer.L2_GENERAL,
        )
        store.insert_episodic(l2)

        l3_nodes = store.list_episodic_by_layer(EpisodicLayer.L3_EPISODIC)
        assert len(l3_nodes) == 3
        l2_nodes = store.list_episodic_by_layer(EpisodicLayer.L2_GENERAL)
        assert len(l2_nodes) == 1


class TestEpisodicLayers:
    def test_l1_lifetime_fields(self, store: MemoryStore):
        span = (datetime(2024, 1, 1, tzinfo=timezone.utc), datetime(2024, 6, 1, tzinfo=timezone.utc))
        node = EpisodicNode(
            layer=EpisodicLayer.L1_LIFETIME,
            span=span,
            theme="用户找工作那半年",
            member_node_ids=["n1", "n2"],
        )
        store.insert_episodic(node)

        got = store.get_episodic(node.id)
        assert got.layer == EpisodicLayer.L1_LIFETIME
        assert got.span == span
        assert got.theme == "用户找工作那半年"
        assert got.member_node_ids == ["n1", "n2"]

    def test_self_defining_fields(self, store: MemoryStore):
        node = EpisodicNode(
            is_self_defining=True,
            is_flashbulb=True,
            decay_half_life=700.0,
        )
        store.insert_episodic(node)

        got = store.get_episodic(node.id)
        assert got.is_self_defining is True
        assert got.is_flashbulb is True
        assert got.decay_half_life == 700.0

    def test_emotion_intensity_property(self):
        node = EpisodicNode(emotion_vec=[0.8, 0.9, 0.1])
        intensity = node.emotion_intensity
        assert 0.0 <= intensity <= 1.0


class TestSemanticNodeCRUD:
    def test_insert_and_get(self, store: MemoryStore):
        node = SemanticNode(
            statement="用户喜欢 Python",
            semantic_vec=[0.1, 0.2],
            confidence=0.9,
        )
        store.insert_semantic(node)

        got = store.get_semantic(node.id)
        assert got is not None
        assert got.statement == "用户喜欢 Python"
        assert got.confidence == 0.9

    def test_update_confidence(self, store: MemoryStore):
        node = SemanticNode(statement="test")
        store.insert_semantic(node)

        store.update_semantic_confidence(node.id, 0.3)
        got = store.get_semantic(node.id)
        assert got.confidence == 0.3

    def test_update_access(self, store: MemoryStore):
        node = SemanticNode(statement="test")
        store.insert_semantic(node)

        store.update_semantic_access(node.id)
        got = store.get_semantic(node.id)
        assert got.access_count == 1

    def test_delete(self, store: MemoryStore):
        node = SemanticNode(statement="test")
        store.insert_semantic(node)

        store.delete_semantic(node.id)
        assert store.get_semantic(node.id) is None


class TestProceduralNodeCRUD:
    def test_insert_and_get(self, store: MemoryStore):
        node = ProceduralNode(
            pattern="当用户聊到死亡话题时，倾向放慢、留白",
            trigger_state_vec=[0.5, 0.3, 0.8],
            response_tendency={"pace": "slow", "tone": "gentle"},
            activation_strength=0.8,
        )
        store.insert_procedural(node)

        got = store.get_procedural(node.id)
        assert got is not None
        assert "死亡话题" in got.pattern
        assert got.response_tendency == {"pace": "slow", "tone": "gentle"}
        assert got.activation_strength == 0.8

    def test_update_activation(self, store: MemoryStore):
        node = ProceduralNode(pattern="test")
        store.insert_procedural(node)

        store.update_procedural_activation(node.id, 0.2)
        got = store.get_procedural(node.id)
        assert got.activation_strength == 0.2

    def test_update_access(self, store: MemoryStore):
        node = ProceduralNode(pattern="test")
        store.insert_procedural(node)

        store.update_procedural_access(node.id)
        got = store.get_procedural(node.id)
        assert got.access_count == 1

    def test_delete(self, store: MemoryStore):
        node = ProceduralNode(pattern="test")
        store.insert_procedural(node)

        store.delete_procedural(node.id)
        assert store.get_procedural(node.id) is None


class TestHebbianEdges:
    def test_insert_and_query_neighbors(self, store: MemoryStore):
        edge = HebbianEdge(source_id="a", target_id="b", weight=0.6)
        store.insert_hebbian_edge(edge, NodeType.EPISODIC)

        neighbors = store.get_hebbian_neighbors("a", NodeType.EPISODIC)
        assert len(neighbors) == 1
        assert neighbors[0].source_id == "a"
        assert neighbors[0].target_id == "b"
        assert neighbors[0].weight == 0.6

    def test_query_from_target_side(self, store: MemoryStore):
        edge = HebbianEdge(source_id="a", target_id="b")
        store.insert_hebbian_edge(edge, NodeType.EPISODIC)

        neighbors = store.get_hebbian_neighbors("b", NodeType.EPISODIC)
        assert len(neighbors) == 1

    def test_strengthen(self, store: MemoryStore):
        edge = HebbianEdge(source_id="a", target_id="b", weight=0.3)
        store.insert_hebbian_edge(edge, NodeType.EPISODIC)

        store.strengthen_hebbian_edge("a", "b", NodeType.EPISODIC, delta=0.1)
        neighbors = store.get_hebbian_neighbors("a", NodeType.EPISODIC)
        assert neighbors[0].weight == 0.4

    def test_strengthen_capped_at_1(self, store: MemoryStore):
        edge = HebbianEdge(source_id="a", target_id="b", weight=0.99)
        store.insert_hebbian_edge(edge, NodeType.EPISODIC)

        store.strengthen_hebbian_edge("a", "b", NodeType.EPISODIC, delta=0.1)
        neighbors = store.get_hebbian_neighbors("a", NodeType.EPISODIC)
        assert neighbors[0].weight == 1.0

    def test_min_weight_filter(self, store: MemoryStore):
        store.insert_hebbian_edge(
            HebbianEdge(source_id="a", target_id="b", weight=0.05), NodeType.EPISODIC
        )
        store.insert_hebbian_edge(
            HebbianEdge(source_id="a", target_id="c", weight=0.5), NodeType.EPISODIC
        )

        neighbors = store.get_hebbian_neighbors("a", NodeType.EPISODIC, min_weight=0.1)
        assert len(neighbors) == 1
        assert neighbors[0].target_id == "c"

    def test_prune(self, store: MemoryStore):
        store.insert_hebbian_edge(
            HebbianEdge(source_id="a", target_id="b", weight=0.02), NodeType.EPISODIC
        )
        store.insert_hebbian_edge(
            HebbianEdge(source_id="a", target_id="c", weight=0.5), NodeType.EPISODIC
        )

        removed = store.prune_hebbian_edges(NodeType.EPISODIC, threshold=0.05)
        assert removed == 1

    def test_contains_edge(self, store: MemoryStore):
        edge = ContainsEdge(source_id="L1", target_id="L2", relation="contains")
        store.insert_hebbian_edge(edge, NodeType.EPISODIC)

        neighbors = store.get_hebbian_neighbors("L1", NodeType.EPISODIC)
        assert len(neighbors) == 1
        assert isinstance(neighbors[0], ContainsEdge)
        assert neighbors[0].relation == "contains"

    def test_semantic_edges_independent(self, store: MemoryStore):
        store.insert_hebbian_edge(
            HebbianEdge(source_id="s1", target_id="s2"), NodeType.SEMANTIC
        )
        neighbors = store.get_hebbian_neighbors("s1", NodeType.SEMANTIC)
        assert len(neighbors) == 1
        assert store.get_hebbian_neighbors("s1", NodeType.EPISODIC) == []

    def test_procedural_edges_independent(self, store: MemoryStore):
        store.insert_hebbian_edge(
            HebbianEdge(source_id="p1", target_id="p2"), NodeType.PROCEDURAL
        )
        neighbors = store.get_hebbian_neighbors("p1", NodeType.PROCEDURAL)
        assert len(neighbors) == 1


class TestDerivationEdges:
    def test_insert_and_query_target(self, store: MemoryStore):
        edge = DerivationEdge(
            source_id="sem_1",
            source_type=NodeType.SEMANTIC,
            target_id="ep_1",
            target_type=NodeType.EPISODIC,
            confidence_at_derivation=0.85,
        )
        store.insert_derivation_edge(edge)

        derived = store.get_derived_from("ep_1")
        assert len(derived) == 1
        assert derived[0].source_id == "sem_1"
        assert derived[0].source_type == NodeType.SEMANTIC

    def test_query_source(self, store: MemoryStore):
        edge = DerivationEdge(
            source_id="sem_1",
            source_type=NodeType.SEMANTIC,
            target_id="ep_1",
        )
        store.insert_derivation_edge(edge)

        sources = store.get_source_episodics("sem_1")
        assert len(sources) == 1
        assert sources[0].target_id == "ep_1"


class TestWriteQueue:
    def test_enqueue_and_dequeue(self, store: MemoryStore):
        node = EpisodicNode(content={"text": "hello"})
        queue_id = store.enqueue_write(node)

        pending = store.dequeue_pending()
        assert len(pending) >= 1
        assert json.loads(pending[0]["node_json"])["content"] == {"text": "hello"}

    def test_mark_processed(self, store: MemoryStore):
        node = EpisodicNode(content={"text": "hi"})
        queue_id = store.enqueue_write(node)

        store.mark_queue_processed(queue_id)
        pending = store.dequeue_pending()
        assert all(p["id"] != queue_id for p in pending)

    def test_enqueue_accepts_semantic(self, store: MemoryStore):
        node = SemanticNode(statement="a fact")
        qid = store.enqueue_write(node)
        pending = store.dequeue_pending()
        assert len(pending) >= 1

    def test_enqueue_accepts_procedural(self, store: MemoryStore):
        node = ProceduralNode(pattern="a pattern")
        qid = store.enqueue_write(node)
        pending = store.dequeue_pending()
        assert len(pending) >= 1

    def test_prune_queue(self, store: MemoryStore):
        node = EpisodicNode(content={"t": "old"})
        qid = store.enqueue_write(node)
        store.mark_queue_processed(qid)
        # 手动把创建时间改到 60 天前
        store._conn.execute(
            "UPDATE write_queue SET created_at = ? WHERE id = ?",
            ((datetime.now() - timedelta(days=60)).isoformat(), qid),
        )
        store._conn.commit()

        removed = store.prune_queue(older_than_days=30)
        assert removed >= 1


class TestDiary:
    def test_insert_and_get_self_portrait(self, store: MemoryStore):
        entry = DiaryEntry(
            entry_type=DiaryEntryType.SELF_PORTRAIT,
            content="我是一个喜欢安静的存在",
            source_node_ids=["n1", "n2"],
        )
        store.insert_diary(entry)

        got = store.get_latest_self_portrait()
        assert got is not None
        assert got.entry_type == DiaryEntryType.SELF_PORTRAIT
        assert got.content == "我是一个喜欢安静的存在"

    def test_latest_self_portrait_returns_newest(self, store: MemoryStore):
        old = DiaryEntry(
            entry_type=DiaryEntryType.SELF_PORTRAIT,
            content="version 1",
            created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        )
        new = DiaryEntry(
            entry_type=DiaryEntryType.SELF_PORTRAIT,
            content="version 2",
            created_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
        )
        store.insert_diary(old)
        store.insert_diary(new)

        got = store.get_latest_self_portrait()
        assert got.content == "version 2"


class TestMetrics:
    def test_record_and_get(self, store: MemoryStore):
        event = MetricEvent(
            session_id="s1",
            metric_name="A7_node_count",
            metric_value=42.0,
            metadata={"type": "episodic"},
        )
        store.record_metric(event)

        metrics = store.get_metrics("A7_node_count")
        assert len(metrics) == 1
        assert metrics[0]["metric_value"] == 42.0
        assert json.loads(metrics[0]["metadata"]) == {"type": "episodic"}


class TestDecay:
    def test_decay_half_life_defaults_per_type(self):
        ep = EpisodicNode()
        sem = SemanticNode(statement="test")
        proc = ProceduralNode(pattern="test")
        assert ep.decay_half_life == 7.0
        assert sem.decay_half_life == 90.0
        assert proc.decay_half_life == 90.0

    def test_iter_nodes_for_decay_returns_fields(self, store: MemoryStore):
        node = EpisodicNode(
            decay_half_life=700.0,
            is_self_defining=True,
        )
        store.insert_episodic(node)

        rows = store.iter_nodes_for_decay(NodeType.EPISODIC)
        assert len(rows) == 1
        nid, weight, half_life, is_sd = rows[0]
        assert half_life == 700.0
        assert is_sd is True
        assert weight == 1.0

    def test_iter_nodes_for_decay_semantic_has_false_is_sd(self, store: MemoryStore):
        store.insert_semantic(SemanticNode(statement="fact"))
        rows = store.iter_nodes_for_decay(NodeType.SEMANTIC)
        assert len(rows) == 1
        assert rows[0][3] is False  # semantic 的 is_self_defining 固定 False

    def test_self_defining_never_decays_when_exempt(self, store: MemoryStore):
        """身世记忆标记验证: decay_half_life 极高 + is_self_defining=True (a1 §2.2)。"""
        birth = EpisodicNode(
            content={"kind": "birth_memory"},
            layer=EpisodicLayer.L3_EPISODIC,
            is_self_defining=True,
            decay_half_life=float("inf"),
        )
        store.insert_episodic(birth)

        got = store.get_episodic(birth.id)
        assert got.is_self_defining is True
        assert got.decay_half_life == float("inf")


class TestHealthQueries:
    def test_count_nodes_by_type(self, store: MemoryStore):
        store.insert_episodic(EpisodicNode())
        store.insert_episodic(EpisodicNode())
        store.insert_semantic(SemanticNode(statement="test"))

        assert store.count_nodes(NodeType.EPISODIC) == 2
        assert store.count_nodes(NodeType.SEMANTIC) == 1
        assert store.count_nodes(NodeType.PROCEDURAL) == 0

    def test_count_nodes_total(self, store: MemoryStore):
        store.insert_episodic(EpisodicNode())
        store.insert_semantic(SemanticNode(statement="test"))
        store.insert_procedural(ProceduralNode(pattern="test"))

        assert store.count_nodes() == 3

    def test_weight_distribution(self, store: MemoryStore):
        store.insert_episodic(EpisodicNode(weight=1.0))
        store.insert_episodic(EpisodicNode(weight=0.5))
        store.insert_episodic(EpisodicNode(weight=0.2))

        dist = store.weight_distribution(NodeType.EPISODIC)
        assert sorted(dist) == [0.2, 0.5, 1.0]


class TestTableValidation:
    def test_valid_table_names_pass(self):
        assert _validate_table("episodic_nodes") == "episodic_nodes"
        assert _validate_table("semantic_edges") == "semantic_edges"

    def test_invalid_table_name_raises(self):
        with pytest.raises(ValueError, match="invalid table name"):
            _validate_table("episodic_nodes; DROP TABLE episodic_nodes")


# ============================================================
# Fixtures
# ============================================================


@pytest.fixture
def store(tmp_path: Path) -> MemoryStore:
    s = MemoryStore(db_path=tmp_path / "test_memory.sqlite")
    yield s
    s.close()
