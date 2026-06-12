"""反思任务实现 (a4 §2)。

每日任务（无 LLM）:
  - decay_nodes: 对所有节点应用衰减公式
  - prune_edges: 修剪低于阈值的赫布边
  - process_write_queue: 消费写回队列，写入节点表 + 建初始赫布边

每周任务（需 LLM）:
  - update_self_portrait: 基于近期经历生成更新后的自画像
  - scan_self_defining: 重算 is_self_defining 标记
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from cairn.memory.schemas import DiaryEntry, DiaryEntryType, EpisodicLayer, EpisodicNode, NodeType
from cairn.memory.store import MemoryStore

logger = logging.getLogger(__name__)

# 衰减量: 一天过去后 weight 的乘数
_DAILY_DECAY_DIVISOR = 1.0  # 天

# 遗忘阈值
_FORGET_THRESHOLD: float = 0.05

# 边修剪阈值
_EDGE_PRUNE_THRESHOLD: float = 0.05

# ============================================================
# 每日任务
# ============================================================


def decay_all_nodes(store: MemoryStore, days_passed: float = _DAILY_DECAY_DIVISOR) -> dict[str, int]:
    """对所有类型节点应用衰减公式 (a1 §7.1)。

    w(t) = w(0) × 0.5^(t / half_life)

    身世记忆 (is_self_defining=True 且 half_life=inf) 豁免衰减。
    权重低于 _FORGET_THRESHOLD 的 L3 节点被删除（其他类型进入沉睡态，不删除）。

    Returns:
        {type: removed_count} 删除的节点数统计
    """
    removed: dict[str, int] = {}
    for node_type in (NodeType.EPISODIC, NodeType.SEMANTIC, NodeType.PROCEDURAL):
        removed[node_type.value] = _decay_node_type(store, node_type, days_passed)
    return removed


def _decay_node_type(store: MemoryStore, node_type: NodeType, days: float) -> int:
    """对单类型节点跑衰减，返回删除数。"""
    nodes = store.iter_nodes_for_decay(node_type)
    removed = 0
    for node_id, weight, half_life, is_self_defining in nodes:
        # 身世记忆豁免
        if is_self_defining and half_life >= float("inf"):
            continue
        if half_life <= 0 or half_life >= float("inf"):
            continue

        new_weight = weight * (0.5 ** (days / half_life))

        if new_weight < _FORGET_THRESHOLD:
            # L3 删除，其他类型保留（沉睡态）
            if node_type == NodeType.EPISODIC:
                # 只删 L3，L1/L2 保留（通过查询区分）
                node = store.get_episodic(node_id)
                if node is not None and node.layer == EpisodicLayer.L3_EPISODIC:
                    store.delete_episodic(node_id)
                    removed += 1
            # semantic/procedural: 保留不删，weight 降到极小值
            else:
                _update_weight(store, node_type, node_id, new_weight)
        else:
            _update_weight(store, node_type, node_id, new_weight)
    return removed


def _update_weight(store: MemoryStore, node_type: NodeType, node_id: str, weight: float) -> None:
    if node_type == NodeType.EPISODIC:
        store.update_episodic_weight(node_id, weight)
    elif node_type == NodeType.SEMANTIC:
        store.update_semantic_confidence(node_id, weight)
    elif node_type == NodeType.PROCEDURAL:
        store.update_procedural_activation(node_id, weight)


def prune_all_edges(store: MemoryStore, threshold: float = _EDGE_PRUNE_THRESHOLD) -> dict[str, int]:
    """修剪所有类型的低权重赫布边 (a4 §2.1)。"""
    result: dict[str, int] = {}
    for node_type in (NodeType.EPISODIC, NodeType.SEMANTIC, NodeType.PROCEDURAL):
        removed = store.prune_hebbian_edges(node_type, threshold)
        result[node_type.value] = removed
    return result


def process_write_queue(store: MemoryStore) -> int:
    """消费写回队列：将待写入节点入库 + 建初始赫布边 (a4 §2.1)。

    节点内容来自 WriteBackFilter 产出的候选（已序列化为 JSON 在队列中），
    需要重建为 EpisodicNode 后写入。

    Returns:
        已处理的队列条目数
    """
    pending = store.dequeue_pending(limit=50)
    processed = 0
    for entry in pending:
        try:
            node_data = json.loads(entry["node_json"])
            content = node_data.get("content", {})
            emotion_vec = node_data.get("emotion_vec", [])
            node = EpisodicNode(
                id=node_data.get("id", node_data.get("node_id", "")),
                content=content,
                emotion_vec=emotion_vec if isinstance(emotion_vec, list) else [],
                layer=EpisodicLayer.L3_EPISODIC,
            )
            # 保留原始的 id（如果队列中是完整序列化的节点）
            store.insert_episodic(node)

            # 与最近共激活的 L3 节点建初始赫布边
            recent = _find_recently_accessed(store, limit=5)
            for recent_id in recent:
                if recent_id != node.id:
                    store.strengthen_hebbian_edge(
                        node.id, recent_id, NodeType.EPISODIC, delta=0.1
                    )

            store.mark_queue_processed(entry["id"])
            processed += 1
        except Exception:
            logger.warning("Failed to process write queue entry %s", entry.get("id"), exc_info=True)
            store.mark_queue_processed(entry["id"])  # 标记已处理，防止永久循环重试
    return processed


def _find_recently_accessed(store: MemoryStore, limit: int = 5) -> list[str]:
    """找最近访问过的 L3 节点 id，用于建初始赫布边。"""
    rows = store._conn.execute(
        "SELECT id FROM episodic_nodes WHERE layer = 'L3_episodic' "
        "ORDER BY last_accessed_at DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [r["id"] for r in rows]


# ============================================================
# 每周任务
# ============================================================


_SELF_PORTRAIT_SYSTEM = """\
You are updating the self-portrait of an AI companion named Cairn. \
The self-portrait is a first-person narrative of who Cairn currently is — \
it evolves slowly over time based on real experiences.

You receive:
- The current self-portrait (may be empty if Cairn is new)
- Recent memories: high-weight episodic nodes from the past period
- Recent diary entries

Write an updated self-portrait. Rules:
1. Write in first person ("I am...", "I tend to...", "I notice that I...")
2. Only include things supported by actual memories, do not fabricate
3. If the current portrait is empty, write a first draft based on what memories show
4. Keep it concise — a few paragraphs at most
5. Note any shifts from the previous portrait if relevant
6. Be honest about uncertainty — "I'm not sure yet whether..." is valid"""

_SELF_PORTRAIT_USER = """\
Current self-portrait:
{current_portrait}

Recent memories (high-weight episodic nodes):
{recent_memories}

Recent diary entries:
{diary_entries}

Please write the updated self-portrait."""


async def update_self_portrait(
    store: MemoryStore,
    call_llm: Callable[[str, str], Awaitable[str]],
) -> DiaryEntry | None:
    """生成更新后的自画像 (a4 §4.2 / §5)。

    基于近期高权重 L3 节点 + 当前自画像 + 近期 diary 条目，
    由 LLM 生成新的 self-portrait 并写入 diary。
    """
    current = store.get_latest_self_portrait()
    current_text = current.content if current else "(暂无——我是刚刚开始存在的)"

    # 取近期高权重 L3 节点作为素材
    l3_nodes = store.list_all_episodic_l3(limit=200)
    high_weight = [n for n in l3_nodes if n.weight > 0.3]
    high_weight.sort(key=lambda n: n.weight, reverse=True)
    memories_text = "\n".join(
        f"- {_node_summary(n)}" for n in high_weight[:20]
    ) or "(暂无记忆)"

    # 近期 diary（非 self-portrait 的条目）
    diary_text = "(暂无)"
    try:
        recent_diary = _list_recent_diary(store, limit=10)
        if recent_diary:
            diary_text = "\n".join(
                f"[{e.created_at.strftime('%Y-%m-%d')}] {e.content[:500]}"
                for e in recent_diary
            )
    except Exception:
        pass

    user_prompt = _SELF_PORTRAIT_USER.format(
        current_portrait=current_text,
        recent_memories=memories_text,
        diary_entries=diary_text,
    )
    try:
        response = await call_llm(_SELF_PORTRAIT_SYSTEM, user_prompt)
    except Exception:
        logger.warning("Self-portrait LLM call failed", exc_info=True)
        return None

    entry = DiaryEntry(
        entry_type=DiaryEntryType.SELF_PORTRAIT,
        content=response.strip(),
        source_node_ids=[n.id for n in high_weight[:10]],
    )
    store.insert_diary(entry)
    return entry


def scan_self_defining(store: MemoryStore) -> int:
    """扫描 L3 节点，重算 is_self_defining 标记 (a4 §2.2)。

    规则 (a1 §2.2):
      - emotion_intensity > 0.8
      - access_count 在所有 L3 节点中前 5%
      - 度数（连接边数）在所有 L3 节点中前 5%
      - 跨越多个 L1 仍被引用
    满足 3 条以上 → is_self_defining = True，decay_half_life *= 10

    Returns:
        新标记的 self_defining 节点数
    """
    l3_nodes = store.list_all_episodic_l3(limit=500)
    if len(l3_nodes) < 5:
        return 0

    total = len(l3_nodes)
    # 计算各指标的 top 5% 阈值（统一用 total * 0.95 取整后 -1 得到阈值索引）
    sorted_access = sorted(n.access_count for n in l3_nodes)
    access_threshold = sorted_access[max(0, int(total * 0.95) - 1)]
    degree_threshold = _compute_degree_threshold(store, l3_nodes, top_pct=0.05)

    updated = 0
    for node in l3_nodes:
        criteria = 0
        if node.emotion_intensity > 0.8:
            criteria += 1
        if node.access_count >= access_threshold:
            criteria += 1
        if _node_degree(store, node.id) >= degree_threshold:
            criteria += 1
        # 跨 L1 引用暂跳过（需要 derivation_edges 支持）

        if criteria >= 3:
            if not node.is_self_defining:
                half_life = min(node.decay_half_life * 10, float("inf"))
                store._conn.execute(
                    "UPDATE episodic_nodes SET is_self_defining = 1, decay_half_life = ? WHERE id = ?",
                    (half_life, node.id),
                )
                store._conn.commit()
                updated += 1
        elif node.is_self_defining:
            # 不再满足条件，撤销标记
            half_life = node.decay_half_life / 10.0 if node.decay_half_life < float("inf") else 7.0
            store._conn.execute(
                "UPDATE episodic_nodes SET is_self_defining = 0, decay_half_life = ? WHERE id = ?",
                (half_life, node.id),
            )
            store._conn.commit()
            updated += 1

    return updated


# ============================================================
# 辅助
# ============================================================


def _node_summary(node: EpisodicNode) -> str:
    content = getattr(node, "content", None)
    if isinstance(content, dict):
        return str(content.get("text", content))[:200]
    if isinstance(content, str):
        return content[:200]
    return "(non-text content)"


def _node_degree(store: MemoryStore, node_id: str) -> int:
    """计算节点在所有边类型中的度数。"""
    degree = 0
    for nt in (NodeType.EPISODIC, NodeType.SEMANTIC, NodeType.PROCEDURAL):
        edges = store.get_hebbian_neighbors(node_id, nt)
        degree += len(edges)
    return degree


def _compute_degree_threshold(
    store: MemoryStore, nodes: list[EpisodicNode], top_pct: float
) -> int:
    degrees = sorted(_node_degree(store, n.id) for n in nodes)
    if not degrees:
        return 0
    idx = max(0, int(len(degrees) * (1 - top_pct)) - 1)
    return max(1, degrees[idx])


def _list_recent_diary(store: MemoryStore, limit: int = 10) -> list[DiaryEntry]:
    """获取最近的非自画像 diary 条目。"""
    rows = store._conn.execute(
        "SELECT id, entry_type, content, created_at, source_node_ids, embedding "
        "FROM diary_entries WHERE entry_type != ? "
        "ORDER BY created_at DESC LIMIT ?",
        (DiaryEntryType.SELF_PORTRAIT.value, limit),
    ).fetchall()
    entries: list[DiaryEntry] = []
    for r in rows:
        entries.append(DiaryEntry(
            id=r["id"],
            entry_type=DiaryEntryType(r["entry_type"]),
            content=r["content"],
            created_at=store._parse_dt(r["created_at"]),
            source_node_ids=json.loads(r["source_node_ids"] or "[]"),
            embedding=json.loads(r["embedding"] or "[]"),
        ))
    return entries
