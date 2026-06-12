"""装配流水线 (a3 §4)。

将 recall + compact + injection 串成单轮装配流水线。
返回可直接传给 agent.run_stream_events() 的 message_history。
"""

from __future__ import annotations

import logging
from typing import Any

from pydantic_ai.messages import ModelRequest, ModelResponse, SystemPromptPart, TextPart, UserPromptPart

from cairn.compact.manager import CompactManager
from cairn.compact.schemas import CompactResult
from cairn.memory.recall import RecallPipeline
from cairn.prompts.injection import build_injection

logger = logging.getLogger(__name__)


class AssemblyPipeline:
    """单轮装配流水线 (a3 §4)。

    每轮执行:
      召回 (recall) → 压缩检查 (compact) → 注入块前置 → 返回组装后的消息列表

    用法:
        pipeline = AssemblyPipeline(recall=recall_pipeline, compact=compact_manager)
        assembled = await pipeline.assemble(user_input, messages)
        async for event in agent.run_stream_events(
            user_input, deps=deps, message_history=assembled
        ):
            ...
    """

    def __init__(
        self,
        recall: RecallPipeline | None = None,
        compact: CompactManager | None = None,
    ) -> None:
        self.recall = recall
        self.compact = compact

    async def assemble(
        self,
        user_input: str,
        messages: list[Any],
        mood_vec: list[float] | None = None,
        episodic_k: int = 20,
        semantic_k: int = 10,
        procedural_k: int = 10,
    ) -> list[Any]:
        """执行一轮装配，返回组装后的 message_history。

        顺序: [injection] + [summary] + [compressed pairs] + [tail]

        返回的消息列表不含当前轮用户消息——由 agent.run_stream_events 自动追加。

        Args:
            user_input: 当前轮用户输入（用于 recall 查询）
            messages: 上一轮的完整消息历史
            mood_vec: Cairn 当前心境向量
            episodic_k: 情景记忆召回数量
            semantic_k: 语义记忆召回数量
            procedural_k: 程序记忆召回数量
        """
        # 1. Recall
        injection = ""
        if self.recall:
            try:
                bundle = await self.recall.recall(
                    user_input,
                    current_mood_vec=mood_vec,
                    episodic_k=episodic_k,
                    semantic_k=semantic_k,
                    procedural_k=procedural_k,
                )
                injection = build_injection(bundle)
            except Exception:
                logger.warning("Recall failed, continuing without injection", exc_info=True)

        # 2. Compact
        base_messages: list[Any]
        if self.compact:
            try:
                compact_result = await self.compact.compact(messages)
            except Exception:
                logger.warning("Compact failed, keeping original messages", exc_info=True)
                compact_result = None

            if compact_result is not None:
                base_messages = _from_compact(compact_result)
            else:
                base_messages = list(messages)
        else:
            base_messages = list(messages)

        # 3. 构建注入块并前置（用 SystemPromptPart 标记，compact 时可识别并剔除）
        if injection:
            injection_msg = ModelRequest(parts=[SystemPromptPart(content=injection)])
            return [injection_msg] + base_messages

        return base_messages


def _from_compact(result: CompactResult) -> list[Any]:
    """将 CompactResult 转换为消息列表 (a3 §4.1 message_history 物理结构)。

    顺序: summary → compressed_turns (交替 Request/Response) → tail
    """
    msgs: list[Any] = []

    # Summary — 纯文本，一条 ModelRequest
    if result.summary:
        msgs.append(ModelRequest(parts=[UserPromptPart(content=result.summary)]))

    # Compressed turns — 保留 User-Assistant 对话结构
    for turn in result.compressed_turns:
        if turn.user_content:
            msgs.append(ModelRequest(parts=[UserPromptPart(content=turn.user_content)]))
        if turn.assistant_content:
            msgs.append(ModelResponse(parts=[TextPart(content=turn.assistant_content)]))

    # Tail — 最近原文，完全不动
    msgs.extend(result.tail_messages)

    return msgs
