"""M1 召回层单元测试 — 覆盖 embedder / 向量检索 / 三通道 / 融合打分。"""

from __future__ import annotations

from pathlib import Path

import pytest
import pytest_asyncio

from cairn.memory.embedder import Embedder
from cairn.memory.recall import RecallBundle, RecallPipeline, RecallResult
from cairn.memory.schemas import (
    EpisodicLayer,
    EpisodicNode,
    HebbianEdge,
    NodeType,
    ProceduralNode,
    SemanticNode,
)
from cairn.memory.store import MemoryStore
from cairn.memory.vector import VectorSearch, cosine_sim


def _hash_vec(text: str, dim: int = 16) -> list[float]:
    import hashlib
    h = hashlib.sha256(text.encode()).digest()
    vec = [(h[i] / 255.0) for i in range(min(len(h), dim))]
    while len(vec) < dim:
        vec.append(0.0)
    return vec


# ============================================================
# Stub embedder for deterministic testing
# ============================================================


class StubEmbedder:
    """测试用 embedder — 返回固定向量, 不调 API。"""

    def __init__(self, dim: int = 16):
        self._dim = dim

    @property
    def dim(self) -> int:
        return self._dim

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [_hash_vec(t, self._dim) for t in texts]

    async def embed_one(self, text: str) -> list[float]:
        return _hash_vec(text, self._dim)

    async def close(self) -> None:
        pass


# ============================================================
# cosine_sim
# ============================================================


class TestCosineSim:
    def test_identical(self):
        assert cosine_sim([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)

    def test_orthogonal(self):
        assert cosine_sim([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)

    def test_opposite(self):
        assert cosine_sim([1.0, 0.0], [-1.0, 0.0]) == pytest.approx(-1.0)

    def test_empty(self):
        assert cosine_sim([], [1.0]) == 0.0

    def test_different_lengths(self):
        assert cosine_sim([0.1, 0.2], [0.1, 0.2, 0.3]) == 0.0

    def test_zero_norm(self):
        assert cosine_sim([0.0, 0.0], [1.0, 0.0]) == 0.0


# ============================================================
# VectorSearch
# ============================================================


class TestVectorSearch:
    def test_search_episodic_returns_ranked(self, store: MemoryStore):
        n1 = EpisodicNode(semantic_vec=[1.0, 0.0, 0.0])
        n2 = EpisodicNode(semantic_vec=[0.01, 1.0, 0.0])  # 非正交, sim > 0
        n3 = EpisodicNode(semantic_vec=[1.0, 0.1, 0.0])
        for n in (n1, n2, n3):
            store.insert_episodic(n)

        vs = VectorSearch(store)
        results = vs.search_episodic([1.0, 0.0, 0.0], top_k=3)
        assert len(results) == 3
        assert results[0][0].id == n1.id
        assert results[0][1] == pytest.approx(1.0)

    def test_search_episodic_excludes_l1_l2(self, store: MemoryStore):
        l3 = EpisodicNode(semantic_vec=[1.0, 0.0])
        l2 = EpisodicNode(semantic_vec=[1.0, 0.0], layer=EpisodicLayer.L2_GENERAL)
        store.insert_episodic(l3)
        store.insert_episodic(l2)

        vs = VectorSearch(store)
        results = vs.search_episodic([1.0, 0.0], top_k=5)
        ids = {r[0].id for r in results}
        assert l3.id in ids
        assert l2.id not in ids  # L2 不应出现在向量检索结果中

    def test_search_semantic_uses_topic_vec_as_primary(self, store: MemoryStore):
        # topic_vec 是 semantic 搜索的主索引
        s1 = SemanticNode(statement="a", topic_vec=[1.0, 0.0], semantic_vec=[0.0, 0.0], confidence=1.0)
        s2 = SemanticNode(statement="b", topic_vec=[0.0, 0.0], semantic_vec=[1.0, 0.0], confidence=1.0)
        store.insert_semantic(s1)
        store.insert_semantic(s2)

        vs = VectorSearch(store)
        results = vs.search_semantic([1.0, 0.0], top_k=2)
        # s1 的 topic_vec 匹配 query → 应排第一
        assert results[0][0].id == s1.id

    def test_search_semantic_uses_confidence_weight(self, store: MemoryStore):
        s1 = SemanticNode(statement="a", topic_vec=[1.0, 0.0], confidence=0.9, weight=1.0)
        s2 = SemanticNode(statement="b", topic_vec=[1.0, 0.0], confidence=0.1, weight=1.0)
        store.insert_semantic(s1)
        store.insert_semantic(s2)

        vs = VectorSearch(store)
        results = vs.search_semantic([1.0, 0.0], top_k=2)
        assert results[0][0].id == s1.id

    def test_search_procedural(self, store: MemoryStore):
        p1 = ProceduralNode(pattern="test", trigger_state_vec=[1.0, 0.0], activation_strength=1.0)
        store.insert_procedural(p1)

        vs = VectorSearch(store)
        results = vs.search_procedural([1.0, 0.0], top_k=5)
        assert len(results) == 1


# ============================================================
# RecallPipeline
# ============================================================


class TestRecallPipeline:
    @pytest_asyncio.fixture
    async def embedder(self) -> StubEmbedder:
        return StubEmbedder(dim=16)

    @pytest.mark.asyncio
    async def test_recall_finds_relevant_episodic(
        self, store: MemoryStore, embedder: StubEmbedder
    ):
        node = EpisodicNode(semantic_vec=_hash_vec("hello"))
        store.insert_episodic(node)

        pipeline = RecallPipeline(store, embedder)
        bundle = await pipeline.recall("hello")

        assert len(bundle.episodic) >= 1
        assert bundle.episodic[0].node.id == node.id
        assert bundle.episodic[0].channel == "episodic"

    @pytest.mark.asyncio
    async def test_recall_returns_all_channels(
        self, store: MemoryStore, embedder: StubEmbedder
    ):
        """三类节点都有时，三通道都应有产出。"""
        vec = _hash_vec("common query")
        store.insert_episodic(EpisodicNode(semantic_vec=vec))
        store.insert_semantic(SemanticNode(statement="fact", topic_vec=vec))
        store.insert_procedural(ProceduralNode(pattern="habit", trigger_state_vec=vec))

        pipeline = RecallPipeline(store, embedder)
        bundle = await pipeline.recall("common query")

        assert len(bundle.episodic) >= 1
        assert len(bundle.semantic) >= 1
        assert len(bundle.procedural) >= 1

    @pytest.mark.asyncio
    async def test_self_defining_nodes_get_boost(
        self, store: MemoryStore, embedder: StubEmbedder
    ):
        vec = _hash_vec("important")
        regular = EpisodicNode(semantic_vec=vec)
        sd = EpisodicNode(semantic_vec=vec, is_self_defining=True)
        store.insert_episodic(regular)
        store.insert_episodic(sd)

        pipeline = RecallPipeline(store, embedder)
        bundle = await pipeline.recall("important")

        ep = bundle.episodic
        sd_idx = next(i for i, r in enumerate(ep) if r.node.id == sd.id)
        reg_idx = next(i for i, r in enumerate(ep) if r.node.id == regular.id)
        assert sd_idx < reg_idx

    @pytest.mark.asyncio
    async def test_hebbian_spread_includes_neighbors(
        self, store: MemoryStore, embedder: StubEmbedder
    ):
        vec = _hash_vec("spread test")
        center = EpisodicNode(semantic_vec=vec)
        neighbor = EpisodicNode(semantic_vec=[0.9] * 16)
        store.insert_episodic(center)
        store.insert_episodic(neighbor)

        from cairn.memory.schemas import HebbianEdge
        edge = HebbianEdge(source_id=center.id, target_id=neighbor.id, weight=0.8)
        store.insert_hebbian_edge(edge, NodeType.EPISODIC)

        pipeline = RecallPipeline(store, embedder)
        bundle = await pipeline.recall("spread test", hebbian_spread=3)

        ids = {r.node.id for r in bundle.episodic}
        assert neighbor.id in ids

    @pytest.mark.asyncio
    async def test_max_total_caps_results(
        self, store: MemoryStore, embedder: StubEmbedder
    ):
        pipeline = RecallPipeline(store, embedder)
        bundle = await pipeline.recall("anything")
        assert len(bundle.all_nodes()) <= 30  # default per-channel caps

    @pytest.mark.asyncio
    async def test_empty_store_does_not_crash(
        self, store: MemoryStore, embedder: StubEmbedder
    ):
        pipeline = RecallPipeline(store, embedder)
        bundle = await pipeline.recall("anything")
        assert bundle.episodic == []
        assert bundle.semantic == []
        assert bundle.procedural == []

    @pytest.mark.asyncio
    async def test_post_recall_updates_access_count(
        self, store: MemoryStore, embedder: StubEmbedder
    ):
        vec = _hash_vec("access test")
        ep = EpisodicNode(semantic_vec=vec)
        store.insert_episodic(ep)

        pipeline = RecallPipeline(store, embedder)
        await pipeline.recall("access test")

        got = store.get_episodic(ep.id)
        assert got.access_count == 1

    @pytest.mark.asyncio
    async def test_post_recall_strengthens_coactivation(
        self, store: MemoryStore, embedder: StubEmbedder
    ):
        vec = _hash_vec("coact test")
        ep1 = EpisodicNode(semantic_vec=vec)
        ep2 = EpisodicNode(semantic_vec=vec)
        store.insert_episodic(ep1)
        store.insert_episodic(ep2)

        pipeline = RecallPipeline(store, embedder)
        await pipeline.recall("coact test")

        # 两个节点同批召回 → 应建立或强化赫布边
        neighbors = store.get_hebbian_neighbors(ep1.id, NodeType.EPISODIC)
        assert len(neighbors) >= 1
        assert neighbors[0].target_id == ep2.id or neighbors[0].source_id == ep2.id

    @pytest.mark.asyncio
    async def test_procedural_hebbian_spread(
        self, store: MemoryStore, embedder: StubEmbedder
    ):
        vec = _hash_vec("proc spread")
        center = ProceduralNode(pattern="main", trigger_state_vec=vec, activation_strength=1.0)
        neighbor = ProceduralNode(pattern="nearby", trigger_state_vec=[0.8] * 16, activation_strength=1.0)
        store.insert_procedural(center)
        store.insert_procedural(neighbor)

        edge = HebbianEdge(source_id=center.id, target_id=neighbor.id, weight=0.7)
        store.insert_hebbian_edge(edge, NodeType.PROCEDURAL)

        pipeline = RecallPipeline(store, embedder)
        bundle = await pipeline.recall("proc spread", hebbian_spread=3)

        ids = {r.node.id for r in bundle.procedural}
        assert neighbor.id in ids


# ============================================================
# Fixtures
# ============================================================


@pytest.fixture
def store(tmp_path: Path) -> MemoryStore:
    s = MemoryStore(db_path=tmp_path / "test_recall.sqlite")
    yield s
    s.close()
