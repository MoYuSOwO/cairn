"""长期记忆 SQLite 存储层。

七表落地 (a1 §11.1): 三张节点表 + 三张赫布边表 + 一张派生边表。
纯 sqlite3 标准库, WAL 模式, 无外部依赖。
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from cairn.core.config import CONFIG_DIR
from cairn.memory.schemas import (
    BaseNode,
    ContainsEdge,
    DerivationEdge,
    DiaryEntry,
    EpisodicLayer,
    EpisodicNode,
    HebbianEdge,
    MetricEvent,
    NodeType,
    ProceduralNode,
    SemanticNode,
)

# SQL 注入防御: 表名只能来自此白名单
_VALID_TABLE_NAMES = {
    "episodic_nodes",
    "semantic_nodes",
    "procedural_nodes",
    "episodic_edges",
    "semantic_edges",
    "procedural_edges",
}


def _validate_table(name: str) -> str:
    if name not in _VALID_TABLE_NAMES:
        raise ValueError(f"invalid table name: {name}")
    return name

logger = logging.getLogger(__name__)

DB_PATH = CONFIG_DIR / "memory.sqlite"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_dt(raw: str | None) -> datetime | None:
    if not raw:
        return None
    dt = datetime.fromisoformat(raw)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


# ============================================================
# 序列化辅助
# ============================================================


def _vec_to_json(vec: list[float]) -> str:
    return json.dumps(vec)


def _json_to_vec(raw: str | None) -> list[float]:
    if not raw:
        return []
    return json.loads(raw)


def _opt_json_dumps(obj: Any) -> str | None:
    if obj is None:
        return None
    return json.dumps(obj)


def _opt_json_loads(raw: str | None) -> Any:
    if not raw:
        return None
    return json.loads(raw)


# ============================================================
# MemoryStore
# ============================================================


class MemoryStore:
    """长期记忆 SQLite 存储。

    线程安全 (每个线程独立 connection), WAL 模式。
    """

    def __init__(self, db_path: Path | str | None = None) -> None:
        self._db_path = Path(db_path) if db_path else DB_PATH
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._init_db()

    # -------- 连接管理 --------

    @property
    def _conn(self) -> sqlite3.Connection:
        if not hasattr(self._local, "conn") or self._local.conn is None:
            self._local.conn = sqlite3.connect(str(self._db_path))
            self._local.conn.row_factory = sqlite3.Row
            self._local.conn.execute("PRAGMA journal_mode=WAL")
            self._local.conn.execute("PRAGMA foreign_keys=ON")
        return self._local.conn

    def _init_db(self) -> None:
        conn = self._conn
        conn.executescript("""
        -- 节点表 (3)
        CREATE TABLE IF NOT EXISTS episodic_nodes (
            id TEXT PRIMARY KEY,
            layer TEXT NOT NULL DEFAULT 'L3_episodic',
            content TEXT,
            created_at TEXT NOT NULL,
            last_accessed_at TEXT NOT NULL,
            access_count INTEGER NOT NULL DEFAULT 0,
            weight REAL NOT NULL DEFAULT 1.0,
            decay_half_life REAL NOT NULL DEFAULT 7.0,
            semantic_vec TEXT DEFAULT '[]',
            scene_vec TEXT DEFAULT '[]',
            emotion_vec TEXT DEFAULT '[]',
            topic_vec TEXT DEFAULT '[]',
            is_self_defining INTEGER NOT NULL DEFAULT 0,
            is_flashbulb INTEGER NOT NULL DEFAULT 0,
            span_start TEXT,
            span_end TEXT,
            theme TEXT,
            member_node_ids TEXT
        );

        CREATE TABLE IF NOT EXISTS semantic_nodes (
            id TEXT PRIMARY KEY,
            statement TEXT NOT NULL DEFAULT '',
            content TEXT,
            created_at TEXT NOT NULL,
            last_accessed_at TEXT NOT NULL,
            access_count INTEGER NOT NULL DEFAULT 0,
            weight REAL NOT NULL DEFAULT 1.0,
            decay_half_life REAL NOT NULL DEFAULT 90.0,
            topic_vec TEXT DEFAULT '[]',
            semantic_vec TEXT DEFAULT '[]',
            confidence REAL NOT NULL DEFAULT 1.0
        );

        CREATE TABLE IF NOT EXISTS procedural_nodes (
            id TEXT PRIMARY KEY,
            pattern TEXT NOT NULL DEFAULT '',
            content TEXT,
            created_at TEXT NOT NULL,
            last_accessed_at TEXT NOT NULL,
            access_count INTEGER NOT NULL DEFAULT 0,
            weight REAL NOT NULL DEFAULT 1.0,
            decay_half_life REAL NOT NULL DEFAULT 90.0,
            trigger_state_vec TEXT DEFAULT '[]',
            response_tendency TEXT DEFAULT '{}',
            activation_strength REAL NOT NULL DEFAULT 1.0,
            confidence REAL NOT NULL DEFAULT 1.0
        );

        -- 赫布边表 (3)
        CREATE TABLE IF NOT EXISTS episodic_edges (
            id TEXT PRIMARY KEY,
            source_id TEXT NOT NULL,
            target_id TEXT NOT NULL,
            weight REAL NOT NULL DEFAULT 0.5,
            last_coactivated_at TEXT NOT NULL,
            coactivation_count INTEGER NOT NULL DEFAULT 0,
            relation TEXT DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS semantic_edges (
            id TEXT PRIMARY KEY,
            source_id TEXT NOT NULL,
            target_id TEXT NOT NULL,
            weight REAL NOT NULL DEFAULT 0.5,
            last_coactivated_at TEXT NOT NULL,
            coactivation_count INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS procedural_edges (
            id TEXT PRIMARY KEY,
            source_id TEXT NOT NULL,
            target_id TEXT NOT NULL,
            weight REAL NOT NULL DEFAULT 0.5,
            last_coactivated_at TEXT NOT NULL,
            coactivation_count INTEGER NOT NULL DEFAULT 0
        );

        -- 派生边表 (1)
        CREATE TABLE IF NOT EXISTS derivation_edges (
            id TEXT PRIMARY KEY,
            source_id TEXT NOT NULL,
            source_type TEXT NOT NULL,
            target_id TEXT NOT NULL,
            target_type TEXT NOT NULL DEFAULT 'episodic',
            created_at TEXT NOT NULL,
            confidence_at_derivation REAL NOT NULL DEFAULT 1.0
        );

        -- 写回队列
        CREATE TABLE IF NOT EXISTS write_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            node_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            processed INTEGER NOT NULL DEFAULT 0
        );

        -- Diary
        CREATE TABLE IF NOT EXISTS diary_entries (
            id TEXT PRIMARY KEY,
            entry_type TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TEXT NOT NULL,
            covers_start TEXT,
            covers_end TEXT,
            source_node_ids TEXT DEFAULT '[]',
            embedding TEXT DEFAULT '[]'
        );

        -- 指标
        CREATE TABLE IF NOT EXISTS metrics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            session_id TEXT NOT NULL DEFAULT '',
            metric_name TEXT NOT NULL,
            metric_value REAL NOT NULL,
            metadata TEXT DEFAULT '{}'
        );

        -- 索引
        CREATE INDEX IF NOT EXISTS idx_episodic_layer ON episodic_nodes(layer);
        CREATE INDEX IF NOT EXISTS idx_episodic_self_defining ON episodic_nodes(is_self_defining);
        CREATE INDEX IF NOT EXISTS idx_episodic_edges_src ON episodic_edges(source_id);
        CREATE INDEX IF NOT EXISTS idx_episodic_edges_tgt ON episodic_edges(target_id);
        CREATE INDEX IF NOT EXISTS idx_semantic_edges_src ON semantic_edges(source_id);
        CREATE INDEX IF NOT EXISTS idx_semantic_edges_tgt ON semantic_edges(target_id);
        CREATE INDEX IF NOT EXISTS idx_procedural_edges_src ON procedural_edges(source_id);
        CREATE INDEX IF NOT EXISTS idx_procedural_edges_tgt ON procedural_edges(target_id);
        CREATE INDEX IF NOT EXISTS idx_derivation_source ON derivation_edges(source_id);
        CREATE INDEX IF NOT EXISTS idx_derivation_target ON derivation_edges(target_id);
        CREATE INDEX IF NOT EXISTS idx_metrics_name ON metrics(metric_name);
        CREATE INDEX IF NOT EXISTS idx_metrics_timestamp ON metrics(timestamp);
        """)
        conn.commit()

    # ============================================================
    # Episodic Node CRUD
    # ============================================================

    def insert_episodic(self, node: EpisodicNode) -> str:
        self._conn.execute(
            """INSERT INTO episodic_nodes
               (id, layer, content, created_at, last_accessed_at,
                access_count, weight, decay_half_life,
                semantic_vec, scene_vec, emotion_vec, topic_vec,
                is_self_defining, is_flashbulb,
                span_start, span_end, theme, member_node_ids)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                node.id,
                node.layer.value,
                json.dumps(node.content, ensure_ascii=False),
                node.created_at.isoformat(),
                node.last_accessed_at.isoformat(),
                node.access_count,
                node.weight,
                node.decay_half_life,
                _vec_to_json(node.semantic_vec),
                _vec_to_json(node.scene_vec),
                _vec_to_json(node.emotion_vec),
                _vec_to_json(node.topic_vec),
                int(node.is_self_defining),
                int(node.is_flashbulb),
                node.span[0].isoformat() if node.span else None,
                node.span[1].isoformat() if node.span else None,
                node.theme,
                _opt_json_dumps(node.member_node_ids),
            ),
        )
        self._conn.commit()
        logger.debug("inserted episodic node %s layer=%s", node.id, node.layer.value)
        return node.id

    def get_episodic(self, node_id: str) -> EpisodicNode | None:
        row = self._conn.execute(
            "SELECT * FROM episodic_nodes WHERE id = ?", (node_id,)
        ).fetchone()
        if row is None:
            return None
        return self._row_to_episodic(row)

    def update_episodic_weight(self, node_id: str, weight: float) -> None:
        self._conn.execute(
            "UPDATE episodic_nodes SET weight = ? WHERE id = ?", (weight, node_id)
        )
        self._conn.commit()

    def update_episodic_access(self, node_id: str) -> None:
        self._conn.execute(
            "UPDATE episodic_nodes SET last_accessed_at = ?, access_count = access_count + 1 WHERE id = ?",
            (_now(), node_id),
        )
        self._conn.commit()

    def delete_episodic(self, node_id: str) -> None:
        self._conn.execute("DELETE FROM episodic_nodes WHERE id = ?", (node_id,))
        self._conn.commit()

    def list_episodic_by_layer(
        self, layer: EpisodicLayer, limit: int = 100
    ) -> list[EpisodicNode]:
        rows = self._conn.execute(
            "SELECT * FROM episodic_nodes WHERE layer = ? ORDER BY created_at DESC LIMIT ?",
            (layer.value, limit),
        ).fetchall()
        return [self._row_to_episodic(r) for r in rows]

    def list_all_episodic(self, limit: int = 10000) -> list[EpisodicNode]:
        rows = self._conn.execute(
            "SELECT * FROM episodic_nodes ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [self._row_to_episodic(r) for r in rows]

    def list_all_episodic_l3(self, limit: int = 10000) -> list[EpisodicNode]:
        """仅返回 L3 节点，供向量检索使用（防止 L1/L2 时期摘要污染召回）。"""
        rows = self._conn.execute(
            "SELECT * FROM episodic_nodes WHERE layer = 'L3_episodic' ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [self._row_to_episodic(r) for r in rows]

    # ============================================================
    # Semantic Node CRUD
    # ============================================================

    def insert_semantic(self, node: SemanticNode) -> str:
        self._conn.execute(
            """INSERT INTO semantic_nodes
               (id, statement, content, created_at, last_accessed_at,
                access_count, weight, decay_half_life,
                topic_vec, semantic_vec, confidence)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                node.id,
                node.statement,
                json.dumps(node.content, ensure_ascii=False),
                node.created_at.isoformat(),
                node.last_accessed_at.isoformat(),
                node.access_count,
                node.weight,
                node.decay_half_life,
                _vec_to_json(node.topic_vec),
                _vec_to_json(node.semantic_vec),
                node.confidence,
            ),
        )
        self._conn.commit()
        return node.id

    def get_semantic(self, node_id: str) -> SemanticNode | None:
        row = self._conn.execute(
            "SELECT * FROM semantic_nodes WHERE id = ?", (node_id,)
        ).fetchone()
        if row is None:
            return None
        return self._row_to_semantic(row)

    def update_semantic_access(self, node_id: str) -> None:
        self._conn.execute(
            "UPDATE semantic_nodes SET last_accessed_at = ?, access_count = access_count + 1 WHERE id = ?",
            (_now(), node_id),
        )
        self._conn.commit()

    def update_semantic_confidence(self, node_id: str, confidence: float) -> None:
        self._conn.execute(
            "UPDATE semantic_nodes SET confidence = ? WHERE id = ?",
            (confidence, node_id),
        )
        self._conn.commit()

    def delete_semantic(self, node_id: str) -> None:
        self._conn.execute("DELETE FROM semantic_nodes WHERE id = ?", (node_id,))
        self._conn.commit()

    def list_all_semantic(self, limit: int = 10000) -> list[SemanticNode]:
        rows = self._conn.execute(
            "SELECT * FROM semantic_nodes ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [self._row_to_semantic(r) for r in rows]

    # ============================================================
    # Procedural Node CRUD
    # ============================================================

    def insert_procedural(self, node: ProceduralNode) -> str:
        self._conn.execute(
            """INSERT INTO procedural_nodes
               (id, pattern, content, created_at, last_accessed_at,
                access_count, weight, decay_half_life,
                trigger_state_vec, response_tendency,
                activation_strength, confidence)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                node.id,
                node.pattern,
                json.dumps(node.content, ensure_ascii=False),
                node.created_at.isoformat(),
                node.last_accessed_at.isoformat(),
                node.access_count,
                node.weight,
                node.decay_half_life,
                _vec_to_json(node.trigger_state_vec),
                json.dumps(node.response_tendency, ensure_ascii=False),
                node.activation_strength,
                node.confidence,
            ),
        )
        self._conn.commit()
        return node.id

    def get_procedural(self, node_id: str) -> ProceduralNode | None:
        row = self._conn.execute(
            "SELECT * FROM procedural_nodes WHERE id = ?", (node_id,)
        ).fetchone()
        if row is None:
            return None
        return self._row_to_procedural(row)

    def update_procedural_access(self, node_id: str) -> None:
        self._conn.execute(
            "UPDATE procedural_nodes SET last_accessed_at = ?, access_count = access_count + 1 WHERE id = ?",
            (_now(), node_id),
        )
        self._conn.commit()

    def update_procedural_activation(self, node_id: str, strength: float) -> None:
        self._conn.execute(
            "UPDATE procedural_nodes SET activation_strength = ? WHERE id = ?",
            (strength, node_id),
        )
        self._conn.commit()

    def delete_procedural(self, node_id: str) -> None:
        self._conn.execute("DELETE FROM procedural_nodes WHERE id = ?", (node_id,))
        self._conn.commit()

    def list_all_procedural(self, limit: int = 10000) -> list[ProceduralNode]:
        rows = self._conn.execute(
            "SELECT * FROM procedural_nodes ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [self._row_to_procedural(r) for r in rows]

    # ============================================================
    # Hebbian Edge CRUD
    # ============================================================

    def _edge_table(self, node_type: NodeType) -> str:
        return _validate_table(f"{node_type.value}_edges")

    def _node_table(self, node_type: NodeType) -> str:
        return _validate_table(f"{node_type.value}_nodes")

    def insert_hebbian_edge(self, edge: HebbianEdge, node_type: NodeType) -> str:
        table = self._edge_table(node_type)
        if node_type == NodeType.EPISODIC:
            relation = edge.relation if isinstance(edge, ContainsEdge) else ""
            self._conn.execute(
                f"""INSERT INTO {table}
                    (id, source_id, target_id, weight, last_coactivated_at,
                     coactivation_count, relation)
                    VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    edge.id,
                    edge.source_id,
                    edge.target_id,
                    edge.weight,
                    edge.last_coactivated_at.isoformat(),
                    edge.coactivation_count,
                    relation,
                ),
            )
        else:
            self._conn.execute(
                f"""INSERT INTO {table}
                    (id, source_id, target_id, weight, last_coactivated_at,
                     coactivation_count)
                    VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    edge.id,
                    edge.source_id,
                    edge.target_id,
                    edge.weight,
                    edge.last_coactivated_at.isoformat(),
                    edge.coactivation_count,
                ),
            )
        self._conn.commit()
        return edge.id

    def strengthen_hebbian_edge(
        self, source_id: str, target_id: str, node_type: NodeType, delta: float = 0.05
    ) -> None:
        table = self._edge_table(node_type)
        cur = self._conn.execute(
            f"""UPDATE {table}
                SET weight = MIN(1.0, weight + ?),
                    last_coactivated_at = ?,
                    coactivation_count = coactivation_count + 1
                WHERE source_id = ? AND target_id = ?""",
            (delta, _now(), source_id, target_id),
        )
        if cur.rowcount == 0:
            from uuid import uuid4
            initial_weight = max(delta, 0.3)  # 新边权重至少 0.3, 确保可被邻居查询检索
            if node_type == NodeType.EPISODIC:
                self._conn.execute(
                    f"""INSERT INTO {table}
                        (id, source_id, target_id, weight, last_coactivated_at,
                         coactivation_count, relation)
                        VALUES (?, ?, ?, ?, ?, ?, '')""",
                    (str(uuid4()), source_id, target_id, initial_weight, _now(), 1),
                )
            else:
                self._conn.execute(
                    f"""INSERT INTO {table}
                        (id, source_id, target_id, weight, last_coactivated_at,
                         coactivation_count)
                        VALUES (?, ?, ?, ?, ?, ?)""",
                    (str(uuid4()), source_id, target_id, initial_weight, _now(), 1),
                )
        self._conn.commit()

    def get_hebbian_neighbors(
        self, node_id: str, node_type: NodeType, min_weight: float = 0.1
    ) -> list[HebbianEdge]:
        table = self._edge_table(node_type)
        rows = self._conn.execute(
            f"SELECT * FROM {table} WHERE (source_id = ? OR target_id = ?) AND weight >= ?",
            (node_id, node_id, min_weight),
        ).fetchall()
        return [self._row_to_hebbian_edge(r, node_type) for r in rows]

    def prune_hebbian_edges(
        self, node_type: NodeType, threshold: float = 0.05
    ) -> int:
        table = self._edge_table(node_type)
        cur = self._conn.execute(
            f"DELETE FROM {table} WHERE weight < ?", (threshold,)
        )
        self._conn.commit()
        return cur.rowcount

    # ============================================================
    # Derivation Edge CRUD
    # ============================================================

    def insert_derivation_edge(self, edge: DerivationEdge) -> str:
        self._conn.execute(
            """INSERT INTO derivation_edges
               (id, source_id, source_type, target_id, target_type,
                created_at, confidence_at_derivation)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                edge.id,
                edge.source_id,
                edge.source_type.value,
                edge.target_id,
                edge.target_type.value,
                edge.created_at.isoformat(),
                edge.confidence_at_derivation,
            ),
        )
        self._conn.commit()
        return edge.id

    def get_derived_from(self, target_id: str) -> list[DerivationEdge]:
        """查询哪些高层节点派生自该源节点 (级联更新用, a1 §6.3)。"""
        rows = self._conn.execute(
            "SELECT * FROM derivation_edges WHERE target_id = ?", (target_id,)
        ).fetchall()
        return [self._row_to_derivation_edge(r) for r in rows]

    def get_source_episodics(self, source_id: str) -> list[DerivationEdge]:
        """查询该高层节点派生于哪些 episodic 源 (佐证用)。"""
        rows = self._conn.execute(
            "SELECT * FROM derivation_edges WHERE source_id = ?", (source_id,)
        ).fetchall()
        return [self._row_to_derivation_edge(r) for r in rows]

    # ============================================================
    # Write Queue
    # ============================================================

    def enqueue_write(self, node: BaseNode) -> int:
        cur = self._conn.execute(
            "INSERT INTO write_queue (node_json, created_at) VALUES (?, ?)",
            (node.model_dump_json(), _now()),
        )
        self._conn.commit()
        return cur.lastrowid

    def dequeue_pending(self, limit: int = 50) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM write_queue WHERE processed = 0 ORDER BY id ASC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    def mark_queue_processed(self, queue_id: int) -> None:
        self._conn.execute(
            "UPDATE write_queue SET processed = 1 WHERE id = ?", (queue_id,)
        )
        self._conn.commit()

    def prune_queue(self, older_than_days: int = 30) -> int:
        """删除已处理且超过保留期限的队列条目。"""
        cur = self._conn.execute(
            "DELETE FROM write_queue WHERE processed = 1 AND created_at < ?",
            ((datetime.now(timezone.utc) - timedelta(days=older_than_days)).isoformat(),),
        )
        self._conn.commit()
        return cur.rowcount

    # ============================================================
    # Diary
    # ============================================================

    def insert_diary(self, entry: DiaryEntry) -> str:
        self._conn.execute(
            """INSERT INTO diary_entries
               (id, entry_type, content, created_at,
                covers_start, covers_end, source_node_ids, embedding)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                entry.id,
                entry.entry_type.value,
                entry.content,
                entry.created_at.isoformat(),
                entry.covers_period[0].isoformat() if entry.covers_period else None,
                entry.covers_period[1].isoformat() if entry.covers_period else None,
                json.dumps(entry.source_node_ids),
                _vec_to_json(entry.embedding),
            ),
        )
        self._conn.commit()
        return entry.id

    def get_latest_self_portrait(self) -> DiaryEntry | None:
        row = self._conn.execute(
            """SELECT * FROM diary_entries
                WHERE entry_type = 'self_portrait'
                ORDER BY created_at DESC LIMIT 1"""
        ).fetchone()
        if row is None:
            return None
        return self._row_to_diary(row)

    # ============================================================
    # Metrics
    # ============================================================

    def record_metric(self, event: MetricEvent) -> None:
        self._conn.execute(
            """INSERT INTO metrics (timestamp, session_id, metric_name, metric_value, metadata)
               VALUES (?, ?, ?, ?, ?)""",
            (
                event.timestamp.isoformat(),
                event.session_id,
                event.metric_name,
                event.metric_value,
                json.dumps(event.metadata, ensure_ascii=False),
            ),
        )
        self._conn.commit()

    def get_metrics(
        self, metric_name: str, limit: int = 100
    ) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM metrics WHERE metric_name = ? ORDER BY timestamp DESC LIMIT ?",
            (metric_name, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    # ============================================================
    # 健康度查询
    # ============================================================

    def count_nodes(self, node_type: NodeType | None = None) -> int:
        if node_type:
            table = self._node_table(node_type)
            row = self._conn.execute(f"SELECT COUNT(*) as cnt FROM {table}").fetchone()
        else:
            total = 0
            for t in NodeType:
                row = self._conn.execute(
                    f"SELECT COUNT(*) as cnt FROM {self._node_table(t)}"
                ).fetchone()
                total += row["cnt"]
            return total
        return row["cnt"] if row else 0

    def count_edges(self, node_type: NodeType | None = None) -> int:
        if node_type:
            table = self._edge_table(node_type)
            row = self._conn.execute(f"SELECT COUNT(*) as cnt FROM {table}").fetchone()
        else:
            total = 0
            for t in NodeType:
                row = self._conn.execute(
                    f"SELECT COUNT(*) as cnt FROM {self._edge_table(t)}"
                ).fetchone()
                total += row["cnt"]
            return total
        return row["cnt"] if row else 0

    def weight_distribution(self, node_type: NodeType) -> list[float]:
        table = self._node_table(node_type)
        rows = self._conn.execute(f"SELECT weight FROM {table}").fetchall()
        return [r["weight"] for r in rows]

    # ============================================================
    # 衰减计算 (M5 用, 此处只提供读写字段)
    # ============================================================

    def iter_nodes_for_decay(
        self, node_type: NodeType
    ) -> list[tuple[str, float, float, bool]]:
        """返回 [(node_id, weight, decay_half_life, is_self_defining), ...]。

        供每日反思任务遍历衰减计算。
        is_self_defining 仅 episodic 有实际含义，semantic/procedural 固定为 False。
        """
        if node_type == NodeType.EPISODIC:
            rows = self._conn.execute(
                "SELECT id, weight, decay_half_life, is_self_defining FROM episodic_nodes"
            ).fetchall()
            return [
                (r["id"], r["weight"], r["decay_half_life"], bool(r["is_self_defining"]))
                for r in rows
            ]
        else:
            table = self._node_table(node_type)
            rows = self._conn.execute(
                f"SELECT id, weight, decay_half_life FROM {table}"
            ).fetchall()
            return [(r["id"], r["weight"], r["decay_half_life"], False) for r in rows]

    # ============================================================
    # 行 → 对象 反序列化
    # ============================================================

    def _row_to_episodic(self, row: sqlite3.Row) -> EpisodicNode:
        span = None
        if row["span_start"] and row["span_end"]:
            span = (
                _parse_dt(row["span_start"]),
                _parse_dt(row["span_end"]),
            )
        return EpisodicNode(
            id=row["id"],
            layer=EpisodicLayer(row["layer"]),
            content=_opt_json_loads(row["content"]),
            created_at=_parse_dt(row["created_at"]),
            last_accessed_at=_parse_dt(row["last_accessed_at"]),
            access_count=row["access_count"],
            weight=row["weight"],
            decay_half_life=row["decay_half_life"],
            semantic_vec=_json_to_vec(row["semantic_vec"]),
            scene_vec=_json_to_vec(row["scene_vec"]),
            emotion_vec=_json_to_vec(row["emotion_vec"]),
            topic_vec=_json_to_vec(row["topic_vec"]),
            is_self_defining=bool(row["is_self_defining"]),
            is_flashbulb=bool(row["is_flashbulb"]),
            span=span,
            theme=row["theme"],
            member_node_ids=_opt_json_loads(row["member_node_ids"]),
        )

    def _row_to_semantic(self, row: sqlite3.Row) -> SemanticNode:
        return SemanticNode(
            id=row["id"],
            statement=row["statement"],
            content=_opt_json_loads(row["content"]),
            created_at=_parse_dt(row["created_at"]),
            last_accessed_at=_parse_dt(row["last_accessed_at"]),
            access_count=row["access_count"],
            weight=row["weight"],
            decay_half_life=row["decay_half_life"],
            topic_vec=_json_to_vec(row["topic_vec"]),
            semantic_vec=_json_to_vec(row["semantic_vec"]),
            confidence=row["confidence"],
        )

    def _row_to_procedural(self, row: sqlite3.Row) -> ProceduralNode:
        return ProceduralNode(
            id=row["id"],
            pattern=row["pattern"],
            content=_opt_json_loads(row["content"]),
            created_at=_parse_dt(row["created_at"]),
            last_accessed_at=_parse_dt(row["last_accessed_at"]),
            access_count=row["access_count"],
            weight=row["weight"],
            decay_half_life=row["decay_half_life"],
            trigger_state_vec=_json_to_vec(row["trigger_state_vec"]),
            response_tendency=_opt_json_loads(row["response_tendency"]) or {},
            activation_strength=row["activation_strength"],
            confidence=row["confidence"],
        )

    def _row_to_hebbian_edge(self, row: sqlite3.Row, node_type: NodeType) -> HebbianEdge:
        if node_type == NodeType.EPISODIC:
            relation = row["relation"]
            if relation:
                return ContainsEdge(
                    id=row["id"],
                    source_id=row["source_id"],
                    target_id=row["target_id"],
                    weight=row["weight"],
                    last_coactivated_at=_parse_dt(row["last_coactivated_at"]),
                    coactivation_count=row["coactivation_count"],
                    relation=relation,
                )
        return HebbianEdge(
            id=row["id"],
            source_id=row["source_id"],
            target_id=row["target_id"],
            weight=row["weight"],
            last_coactivated_at=_parse_dt(row["last_coactivated_at"]),
            coactivation_count=row["coactivation_count"],
        )

    def _row_to_derivation_edge(self, row: sqlite3.Row) -> DerivationEdge:
        return DerivationEdge(
            id=row["id"],
            source_id=row["source_id"],
            source_type=NodeType(row["source_type"]),
            target_id=row["target_id"],
            target_type=NodeType(row["target_type"]),
            created_at=_parse_dt(row["created_at"]),
            confidence_at_derivation=row["confidence_at_derivation"],
        )

    def _row_to_diary(self, row: sqlite3.Row) -> DiaryEntry:
        from cairn.memory.schemas import DiaryEntryType
        covers = None
        if row["covers_start"] and row["covers_end"]:
            covers = (
                _parse_dt(row["covers_start"]),
                _parse_dt(row["covers_end"]),
            )
        return DiaryEntry(
            id=row["id"],
            entry_type=DiaryEntryType(row["entry_type"]),
            content=row["content"],
            created_at=_parse_dt(row["created_at"]),
            covers_period=covers,
            source_node_ids=_opt_json_loads(row["source_node_ids"]) or [],
            embedding=_json_to_vec(row["embedding"]),
        )

    def close(self) -> None:
        if hasattr(self._local, "conn") and self._local.conn:
            self._local.conn.close()
            self._local.conn = None
