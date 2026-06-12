"""长期记忆数据模型。

三层记忆图谱 + 赫布边 + 派生边 + Diary 条目。
设计依据: new-structre-doc/a1_memory.md §2-3, §10.3, §4.4
"""

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

# ============================================================
# 枚举
# ============================================================


class NodeType(str, Enum):
    EPISODIC = "episodic"
    SEMANTIC = "semantic"
    PROCEDURAL = "procedural"


class EpisodicLayer(str, Enum):
    L1_LIFETIME = "L1_lifetime"
    L2_GENERAL = "L2_general"
    L3_EPISODIC = "L3_episodic"


class DiaryEntryType(str, Enum):
    REFLECTION = "reflection"
    SELF_PORTRAIT = "self_portrait"
    EVENT_ANCHOR = "event_anchor"


# ============================================================
# 共通基类
# ============================================================


class BaseNode(BaseModel):
    """所有记忆节点的共通字段 (a1 §2.1)。"""

    id: str = Field(default_factory=lambda: str(uuid4()))
    node_type: NodeType
    content: Any = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    last_accessed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    access_count: int = 0
    weight: float = Field(default=1.0, ge=0.0, le=1.0)
    decay_half_life: float = Field(default=7.0, gt=0.0)


# ============================================================
# Episodic 系
# ============================================================


class EpisodicNode(BaseNode):
    """情景记忆节点 (a1 §2.2)。

    L1/L2/L3 三层共用此模型，用 layer 字段区分。
    """

    node_type: NodeType = NodeType.EPISODIC

    # 多维向量 (a1 §2.2)
    semantic_vec: list[float] = Field(default_factory=list)
    scene_vec: list[float] = Field(default_factory=list)
    emotion_vec: list[float] = Field(default_factory=list)  # VAD: (Valence, Arousal, Dominance)
    topic_vec: list[float] = Field(default_factory=list)

    # 派生量
    @property
    def emotion_intensity(self) -> float:
        """sqrt(V² + A² + (1-D)²) / sqrt(3)"""
        if len(self.emotion_vec) < 3:
            return 0.0
        v, a, d = self.emotion_vec[:3]
        return ((v**2 + a**2 + (1 - d) ** 2) ** 0.5) / (3**0.5)

    # 三层标识
    layer: EpisodicLayer = EpisodicLayer.L3_EPISODIC

    # L3 专属
    is_self_defining: bool = False
    is_flashbulb: bool = False

    # L1, L2 专属
    span: Optional[tuple[datetime, datetime]] = None
    theme: Optional[str] = None
    member_node_ids: Optional[list[str]] = None


# ============================================================
# Semantic 系
# ============================================================


class SemanticNode(BaseNode):
    """语义记忆节点 (a1 §2.3)。"""

    node_type: NodeType = NodeType.SEMANTIC

    statement: str = ""
    topic_vec: list[float] = Field(default_factory=list)
    semantic_vec: list[float] = Field(default_factory=list)

    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    decay_half_life: float = Field(default=90.0, gt=0.0)


# ============================================================
# Procedural 系
# ============================================================


class ProceduralNode(BaseNode):
    """程序记忆节点 (a1 §2.4)。"""

    node_type: NodeType = NodeType.PROCEDURAL

    pattern: str = ""
    trigger_state_vec: list[float] = Field(default_factory=list)
    response_tendency: dict[str, Any] = Field(default_factory=dict)

    activation_strength: float = Field(default=1.0, ge=0.0, le=1.0)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    decay_half_life: float = Field(default=90.0, gt=0.0)


# ============================================================
# 边
# ============================================================


class HebbianEdge(BaseModel):
    """类型内赫布边 (a1 §3.1)。

    纯赫布: 无边类型标签, 关系语义涌现于激活模式。
    """

    id: str = Field(default_factory=lambda: str(uuid4()))
    source_id: str
    target_id: str
    weight: float = Field(default=0.5, ge=0.0, le=1.0)
    last_coactivated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    coactivation_count: int = 0


class ContainsEdge(HebbianEdge):
    """Episodic 内层级链边 (a1 §3.2)。

    赫布纯净性的唯一例外: relation 字段仅用于分层快速下钻,
    weight 固定, 不参与共激活强化。
    """

    relation: str = "contains"


class DerivationEdge(BaseModel):
    """跨类型派生边 (a1 §3.3)。

    单向 (从抽象指向源经历), 不参与赫布, 不随时间变化。
    用途: 追溯 / 级联更新 / 佐证。
    """

    id: str = Field(default_factory=lambda: str(uuid4()))
    source_id: str
    source_type: NodeType
    target_id: str
    target_type: NodeType = NodeType.EPISODIC
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    confidence_at_derivation: float = Field(default=1.0, ge=0.0, le=1.0)


# ============================================================
# Diary
# ============================================================


class DiaryEntry(BaseModel):
    """反思日记条目 (a4 §4.4)。"""

    id: str = Field(default_factory=lambda: str(uuid4()))
    entry_type: DiaryEntryType
    content: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    covers_period: Optional[tuple[datetime, datetime]] = None
    source_node_ids: list[str] = Field(default_factory=list)
    embedding: list[float] = Field(default_factory=list)


# ============================================================
# 指标
# ============================================================


class MetricEvent(BaseModel):
    """运行时指标事件 (PHASE1_PLAN §4)。

    由 chat_service 每轮结束后通过 EventBus 发射。
    """

    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    session_id: str = ""
    metric_name: str
    metric_value: float
    metadata: dict[str, Any] = Field(default_factory=dict)
