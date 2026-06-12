"""向量检索层。

当前实现: 余弦相似度暴力搜索, 内存计算。
不做后端绑定 (sqlite-vec / ChromaDB), 留待后续按需替换。
"""

from __future__ import annotations

import math
from typing import Any

from cairn.memory.schemas import EpisodicNode, ProceduralNode, SemanticNode
from cairn.memory.store import MemoryStore


def cosine_sim(a: list[float], b: list[float]) -> float:
    """两个向量的余弦相似度。空向量返回 0.0。"""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


class VectorSearch:
    """暴力余弦相似度搜索。

    从 MemoryStore 加载向量到内存, 每次搜索遍历全部对应节点。
    节点量级 < 10 万时性能足够; 超过后替换为 sqlite-vec / ChromaDB 后端。
    """

    def __init__(self, store: MemoryStore) -> None:
        self._store = store

    # ============================================================
    # 公开 API
    # ============================================================

    def search_episodic(
        self,
        query_vec: list[float],
        top_k: int = 20,
        min_weight: float = 0.0,
    ) -> list[tuple[EpisodicNode, float]]:
        """搜索 episodic L3 节点, 按 semantic_vec 余弦相似度排序。

        只搜 L3——L1/L2 时期摘要不应参与向量检索 (a1 §5.1 Channel A Step 1)。
        """
        nodes = self._store.list_all_episodic_l3()
        return self._rank(nodes, query_vec, lambda n: n.semantic_vec, top_k, min_weight)

    def search_semantic(
        self,
        query_vec: list[float],
        top_k: int = 10,
        min_confidence: float = 0.0,
    ) -> list[tuple[SemanticNode, float]]:
        """搜索 semantic 节点, 用 topic_vec 作为主索引 (a1 §5.1 Channel B)。"""
        nodes = self._store.list_all_semantic()
        results = self._rank(nodes, query_vec, lambda n: n.topic_vec, top_k, 0.0)
        # 按 confidence × weight 重排 (a1 §5.1 Channel B Step 2)
        return sorted(
            [(n, s * n.confidence * n.weight) for n, s in results
             if n.confidence >= min_confidence],
            key=lambda x: x[1],
            reverse=True,
        )[:top_k]

    def search_procedural(
        self,
        query_vec: list[float],
        top_k: int = 10,
        min_activation: float = 0.0,
    ) -> list[tuple[ProceduralNode, float]]:
        """搜索 procedural 节点, 按 trigger_state_vec 余弦相似度排序 (a1 §5.1 Channel C)。"""
        nodes = self._store.list_all_procedural()
        results = self._rank(nodes, query_vec, lambda n: n.trigger_state_vec, top_k, 0.0)
        # 按 activation_strength × confidence 加权 (a1 §5.1 Channel C Step 3)
        return sorted(
            [(n, s * n.activation_strength * n.confidence) for n, s in results
             if n.activation_strength >= min_activation],
            key=lambda x: x[1],
            reverse=True,
        )[:top_k]

    # ============================================================
    # 内部
    # ============================================================

    def _rank(
        self,
        nodes: list[Any],
        query_vec: list[float],
        get_vec: Any,
        top_k: int,
        min_weight: float,
    ) -> list[tuple[Any, float]]:
        scored: list[tuple[Any, float]] = []
        for node in nodes:
            if getattr(node, "weight", 1.0) < min_weight:
                continue
            vec = get_vec(node)
            sim = cosine_sim(query_vec, vec)
            if sim > 0.0:
                scored.append((node, sim))
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:top_k]
