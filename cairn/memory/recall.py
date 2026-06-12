"""多通道融合召回 (a1 §5)。

三通道独立检索 (episodic / semantic / procedural) → 赫布扩散 → 各通道独立 top-K。
三通道结果不做跨通道合并排序，分别送入上下文 (a1 §5.1)。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from cairn.memory.embedder import Embedder
from cairn.memory.schemas import EpisodicNode, HebbianEdge, NodeType, ProceduralNode, SemanticNode
from cairn.memory.store import MemoryStore
from cairn.memory.vector import VectorSearch, cosine_sim

logger = logging.getLogger(__name__)


@dataclass
class RecallResult:
    node: EpisodicNode | SemanticNode | ProceduralNode
    score: float
    channel: str  # 'episodic' | 'semantic' | 'procedural'


@dataclass
class RecallBundle:
    """三通道召回结果，各通道独立，不做跨通道排序 (a1 §5.1)。"""
    episodic: list[RecallResult]
    semantic: list[RecallResult]
    procedural: list[RecallResult]

    def all_nodes(self) -> list[RecallResult]:
        """按通道顺序展开，仅用于 access 更新等需要遍历全部节点的场景。"""
        return self.episodic + self.semantic + self.procedural


class RecallPipeline:
    """多通道融合召回管道。

    用法:
        pipeline = RecallPipeline(store, embedder)
        results = await pipeline.recall("用户今天想写 Python")
    """

    def __init__(self, store: MemoryStore, embedder: Embedder) -> None:
        self.store = store
        self.embedder = embedder
        self.vector = VectorSearch(store)

    async def recall(
        self,
        query: str,
        current_mood_vec: list[float] | None = None,
        episodic_k: int = 20,
        semantic_k: int = 10,
        procedural_k: int = 10,
        hebbian_spread: int = 3,
        self_defining_boost: float = 0.1,
    ) -> RecallBundle:
        """执行三通道独立召回，返回 RecallBundle（各通道不合并排序）。"""
        query_vec = await self.embedder.embed_one(query)

        # 1. 三通道独立检索
        ep_results = self._channel_episodic(query_vec, current_mood_vec, episodic_k, self_defining_boost)
        sem_results = self._channel_semantic(query_vec, semantic_k)
        proc_results = self._channel_procedural(query_vec, procedural_k)

        # 2. 赫布扩散（各通道内部）
        ep_results = self._hebbian_spread(ep_results, NodeType.EPISODIC, hebbian_spread)
        sem_results = self._hebbian_spread(sem_results, NodeType.SEMANTIC, hebbian_spread)
        proc_results = self._hebbian_spread(proc_results, NodeType.PROCEDURAL, hebbian_spread)

        bundle = RecallBundle(episodic=ep_results, semantic=sem_results, procedural=proc_results)

        # 3. 召回后更新 (a1 §5.2): access 计数 + 赫布共激活
        self._post_recall_update(bundle.all_nodes())

        return bundle

    # ============================================================
    # 三通道
    # ============================================================

    def _channel_episodic(
        self,
        query_vec: list[float],
        current_mood_vec: list[float] | None,
        top_k: int,
        self_defining_boost: float,
    ) -> list[RecallResult]:
        """Episodic 通道 (a1 §5.1 Channel A)。

        score = α·semantic_sim + β·scene_sim + γ·topic_sim
              + δ·emotion_sim + ε·timestamp_recency + ζ·is_self_defining
        """
        raw = self.vector.search_episodic(query_vec, top_k=top_k * 2)
        results: list[RecallResult] = []
        now = datetime.now(tz=timezone.utc)

        for node, sem_sim in raw:
            score = sem_sim * 0.5  # α

            # scene_sim (a1 §5.1 Channel A Step 3)
            if node.scene_vec:
                score += cosine_sim(query_vec, node.scene_vec) * 0.1  # β

            # topic_sim
            if node.topic_vec:
                score += cosine_sim(query_vec, node.topic_vec) * 0.15  # γ

            # emotion_sim: mood-congruent recall
            if current_mood_vec and node.emotion_vec:
                score += cosine_sim(current_mood_vec, node.emotion_vec) * 0.15  # δ

            # timestamp recency
            age_days = (now - node.created_at).days
            recency = max(0.0, 1.0 - age_days / 365.0)
            score += recency * 0.1  # ε

            # self-defining boost
            if node.is_self_defining:
                score += self_defining_boost  # ζ

            results.append(RecallResult(node=node, score=score, channel="episodic"))

        results.sort(key=lambda r: r.score, reverse=True)
        return results[:top_k]

    def _channel_semantic(
        self, query_vec: list[float], top_k: int
    ) -> list[RecallResult]:
        raw = self.vector.search_semantic(query_vec, top_k=top_k)
        return [RecallResult(node=node, score=score, channel="semantic") for node, score in raw]

    def _channel_procedural(
        self, query_vec: list[float], top_k: int
    ) -> list[RecallResult]:
        raw = self.vector.search_procedural(query_vec, top_k=top_k)
        return [RecallResult(node=node, score=score, channel="procedural") for node, score in raw]

    # ============================================================
    # 召回后更新 (a1 §5.2)
    # ============================================================

    def _post_recall_update(self, results: list[RecallResult]) -> None:
        """更新 access 计数 + 共激活赫布强化。"""
        if not results:
            return

        type_groups: dict[NodeType, list[RecallResult]] = {}
        for r in results:
            type_groups.setdefault(r.node.node_type, []).append(r)

        # 更新 access
        for r in results:
            self._update_access(r.node.id, r.node.node_type)

        # 同类型内 top 结果之间赫布共激活强化 (top 5 × top 5)
        for node_type, group in type_groups.items():
            top = group[:5]
            for i in range(len(top)):
                for j in range(i + 1, len(top)):
                    self.store.strengthen_hebbian_edge(
                        top[i].node.id, top[j].node.id, node_type, delta=0.02
                    )

    def _update_access(self, node_id: str, node_type: NodeType) -> None:
        if node_type == NodeType.EPISODIC:
            self.store.update_episodic_access(node_id)
        elif node_type == NodeType.SEMANTIC:
            self.store.update_semantic_access(node_id)
        elif node_type == NodeType.PROCEDURAL:
            self.store.update_procedural_access(node_id)

    # ============================================================
    # 赫布扩散
    # ============================================================

    def _hebbian_spread(
        self,
        results: list[RecallResult],
        node_type: NodeType,
        spread: int,
    ) -> list[RecallResult]:
        """取 top-spread 个结果, 拉入邻居, 邻居分数 = 原分数 × 边权重。"""
        if not results:
            return results

        existing_ids = {r.node.id for r in results}
        new_results: list[RecallResult] = []

        for r in results[:spread]:
            edges = self.store.get_hebbian_neighbors(r.node.id, node_type, min_weight=0.3)
            for edge in edges:
                neighbor_id = edge.target_id if edge.source_id == r.node.id else edge.source_id
                if neighbor_id in existing_ids:
                    continue
                existing_ids.add(neighbor_id)

                neighbor_node = self._lookup_node(neighbor_id, node_type)
                if neighbor_node is not None:
                    new_results.append(
                        RecallResult(
                            node=neighbor_node,
                            score=r.score * edge.weight,
                            channel=r.channel,
                        )
                    )

        return results + new_results

    def _lookup_node(self, node_id: str, node_type: NodeType):
        if node_type == NodeType.EPISODIC:
            return self.store.get_episodic(node_id)
        elif node_type == NodeType.SEMANTIC:
            return self.store.get_semantic(node_id)
        elif node_type == NodeType.PROCEDURAL:
            return self.store.get_procedural(node_id)
        return None
