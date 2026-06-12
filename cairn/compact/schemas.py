"""Compact 层数据模型 (a3 §3.4)。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class CompressedTurn:
    """单轮压缩结果。

    保留 User-Assistant 对话结构，每条内容精简后仍是一来一回。
    """

    turn_span: tuple[int, int]  # 原始消息列表中的 (start, end) 索引
    user_content: str  # 精简后的用户消息
    assistant_content: str  # 精简后的助手回复
    tags: list[str] = field(default_factory=list)  # emotional / technical / decision / ...


@dataclass
class TokenCounts:
    """压缩前后的 token 估算。"""

    before: int = 0
    after: int = 0
    tail_tokens: int = 0
    summary_tokens: int = 0
    compressed_tokens: int = 0

    @property
    def compression_ratio(self) -> float:
        if self.after == 0:
            return 1.0
        return self.before / self.after

    @property
    def savings(self) -> int:
        return self.before - self.after


@dataclass
class CompactResult:
    """一次 compact 的完整产出。

    由 CompactManager.compact() 返回，供装配层 (M3) 重建 message_history。
    """

    summary: str  # 纯文本结构化摘要
    compressed_turns: list[CompressedTurn]  # 逐轮压缩，保留对话结构
    tail_messages: list  # 最近原文 ModelRequest/ModelResponse，完全不动
    token_reduction: tuple[int, int] = (0, 0)  # (压缩前 token 数, 压缩后 token 数)
    token_counts: TokenCounts = field(default_factory=TokenCounts)
    generated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
