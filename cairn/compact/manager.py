"""Compact 管理器 (a3 §3)。

三分区策略:
  Tail（最近原文不动）→ Summary（结构化摘要）→ Compress（逐轮压缩，保留对话结构）

触发条件: 总 token 超过窗口预算的 trigger_pct 时自动触发。
内容分型: 情感/个人内容保守压缩，技术/代码内容可激进压缩 (a3 §3.2)。
"""

from __future__ import annotations

import json
import logging
from typing import Any, Awaitable, Callable

from cairn.compact.schemas import CompactResult, CompressedTurn, TokenCounts

logger = logging.getLogger(__name__)

# 摘要 prompt 模板
_SUMMARY_SYSTEM = """\
You compress conversation history into structured summaries. Be factual and concise. \
Never invent information not present in the conversation. \
Write in the same language as the conversation."""

_SUMMARY_USER = """\
Summarize this conversation segment. Cover these aspects:

- 主题线 (main topics discussed)
- 关系线 (relationship/emotional dynamics)
- 情绪线 (emotional tone and shifts)
- 未决问题 (unresolved questions)
- 已达成共识 (agreements reached)

If an aspect has no relevant content, skip it.

Conversation:
---
{conversation}
---"""

# 逐轮压缩 prompt 模板
_COMPRESS_SYSTEM = """\
You compress individual conversation turns. Each turn has a user message and an assistant response.

Compression rules:
- Keep the User/Assistant structure (output both messages)
- Emotional/personal/relationship content: be CONSERVATIVE, preserve key phrases and emotional anchors verbatim
- Technical/code/tool content: be AGGRESSIVE, keep only decisions, outcomes, and failures
- The compressed version should read like a shorter version of the same exchange

Output as a single JSON object per turn:
{{"user": "<compressed user message>", "assistant": "<compressed assistant response>", "tags": ["emotional", "technical", ...]}}

Tags to choose from: emotional, technical, decision, personal, tool_use, question, clarification"""

_COMPRESS_USER = """\
Compress this turn:

User: {user_content}
Assistant: {assistant_content}"""

# 内容分型关键词 (a3 §3.2 的启发式后备，LLM 标注是主力)
_EMOTIONAL_KEYWORDS = [
    "感觉", "觉得", "喜欢", "讨厌", "爱", "恨", "开心", "难过", "担心",
    "害怕", "生气", "失望", "希望", "想念", "怀念", "珍惜", "感动",
    "伤心", "痛苦", "幸福", "焦虑", "紧张", "放松", "信任", "感谢",
    "feel", "love", "hate", "happy", "sad", "angry", "afraid", "worried",
    "miss", "grateful", "touched", "lonely", "excited", "upset", "care",
    "关系", "relationship", "朋友", "friend", "家人", "family",
]

_TECHNICAL_KEYWORDS = [
    "代码", "函数", "class", "def ", "import ", "bug", "fix", "error",
    "编译", "部署", "deploy", "测试", "test", "API", "接口", "数据库",
    "SQL", "query", "配置", "config", "安装", "install", "运行", "run",
    "code", "function", "server", "client", "request", "response",
    "npm", "pip", "git", "docker", "kubernetes", "build", "compile",
    "日志", "log", "调试", "debug", "性能", "performance",
]


def _estimate_text_tokens(text: str) -> int:
    """估算文本 token 数。

    CJK 字符 ~1.5 chars/token, ASCII ~4 chars/token。
    不含 tiktoken 依赖，用于触发决策的粗估。
    """
    if not text:
        return 0
    cjk = sum(1 for c in text if "一" <= c <= "鿿" or "　" <= c <= "〿")
    ascii_chars = len(text) - cjk
    return max(1, int(cjk / 1.5 + ascii_chars / 4))


def _extract_message_text(msg: Any) -> str:
    """从 pydantic-ai 消息对象提取文本内容。"""
    parts = getattr(msg, "parts", [])
    if not parts:
        return ""
    texts: list[str] = []
    for part in parts:
        # SystemPromptPart 是每轮注入块，compact 时跳过（旧注入已失效）
        part_kind = getattr(part, "part_kind", None)
        if part_kind == "system-prompt":
            continue
        content = getattr(part, "content", None)
        if isinstance(content, str):
            texts.append(content)
        elif isinstance(content, list):
            for item in content:
                if isinstance(item, str):
                    texts.append(item)
                elif hasattr(item, "text"):
                    texts.append(getattr(item, "text", ""))
        if hasattr(part, "tool_name"):
            texts.append(f"[tool:{part.tool_name}]")
        args = getattr(part, "args", None)
        if args is not None:
            if isinstance(args, str):
                texts.append(args)
            elif isinstance(args, dict):
                texts.append(json.dumps(args, ensure_ascii=False))
    return " ".join(texts)


def _classify_content(text: str) -> list[str]:
    """基于关键词的内容分型启发式 (a3 §3.2)。

    LLM 标注是主力，此函数仅作 fallback。
    """
    tags: list[str] = []
    text_lower = text.lower()
    if any(kw.lower() in text_lower for kw in _EMOTIONAL_KEYWORDS):
        tags.append("emotional")
    if any(kw.lower() in text_lower for kw in _TECHNICAL_KEYWORDS):
        tags.append("technical")
    if not tags:
        tags.append("general")
    return tags


def _is_injection_msg(msg: Any) -> bool:
    """判断是否为注入消息（纯 SystemPromptPart 的 ModelRequest）。"""
    if getattr(msg, "kind", None) != "request":
        return False
    parts = getattr(msg, "parts", [])
    return bool(parts) and all(
        getattr(p, "part_kind", None) == "system-prompt" for p in parts
    )


def _find_turn_boundaries(messages: list[Any]) -> list[tuple[int, int]]:
    """找到消息列表中的轮次边界。

    一个 turn 从 ModelRequest (kind="request") 开始，
    到下一个 ModelRequest 之前结束。
    注入消息（纯 SystemPromptPart）不作为 turn 起点——它们应被 compact 层剔除。
    返回 [(start, end), ...] 索引对，end 不包含。
    """
    boundaries: list[tuple[int, int]] = []
    # 跳过开头的注入消息
    turn_start = 0
    while turn_start < len(messages) and _is_injection_msg(messages[turn_start]):
        turn_start += 1
    for i in range(turn_start + 1, len(messages)):
        msg = messages[i]
        if getattr(msg, "kind", None) == "request" and not _is_injection_msg(msg):
            boundaries.append((turn_start, i))
            turn_start = i
    # 最后一个 turn
    if turn_start < len(messages):
        boundaries.append((turn_start, len(messages)))
    return boundaries


class CompactManager:
    """会话 Compact 管理器 (a3 §3)。

    用法:
        manager = CompactManager(call_llm=my_llm_func)
        if manager.should_compact(messages):
            result = await manager.compact(messages)
            # result.summary, result.compressed_turns, result.tail_messages
    """

    def __init__(
        self,
        call_llm: Callable[[str, str], Awaitable[str]] | None = None,
        token_budget: int = 100_000,
        trigger_pct: float = 0.55,
        tail_ratio: float = 0.20,
    ) -> None:
        self._call_llm = call_llm
        self.token_budget = token_budget
        self.trigger_pct = trigger_pct
        self.tail_ratio = tail_ratio

    @property
    def trigger_threshold(self) -> int:
        return int(self.token_budget * self.trigger_pct)

    # ============================================================
    # 公开 API
    # ============================================================

    def estimate_tokens(self, content: str | list[Any] | Any) -> int:
        """估算消息列表或文本的 token 数。"""
        if isinstance(content, str):
            return _estimate_text_tokens(content)
        if isinstance(content, list):
            return sum(_estimate_text_tokens(_extract_message_text(m)) for m in content)
        return _estimate_text_tokens(_extract_message_text(content))

    def should_compact(self, messages: list[Any]) -> bool:
        """检查是否超过触发阈值，需要 compact。"""
        return self.estimate_tokens(messages) > self.trigger_threshold

    async def compact(self, messages: list[Any]) -> CompactResult | None:
        """执行完整 compact 流程。

        若无需 compact（未达阈值或消息太少）返回 None。
        返回 CompactResult 供装配层 (M3) 重建 message_history。
        """
        total_tokens = self.estimate_tokens(messages)
        if total_tokens <= self.trigger_threshold:
            return None
        if len(messages) < 4:
            # 至少两轮对话才有压缩意义
            return None

        # 1. 三分区
        to_summarize, to_compress, tail, compress_spans = self._partition(messages)

        if not to_summarize and not to_compress:
            return None

        # 2. 被压缩部分的原始 token 估算 (a3 §3.4 token_reduction)
        source_before = self.estimate_tokens(to_summarize) + self.estimate_tokens(to_compress)

        # 3. LLM 压缩（若未配置 LLM 则跳过）
        if self._call_llm is None:
            return None

        summary = ""
        if to_summarize:
            summary = await self._generate_summary(to_summarize)

        compressed_turns: list[CompressedTurn] = []
        if to_compress:
            compressed_turns = await self._compress_turns(to_compress, compress_spans)

        # 4. 统计
        tail_tokens = self.estimate_tokens(tail)
        summary_tokens = _estimate_text_tokens(summary)
        c_tokens = sum(
            _estimate_text_tokens(t.user_content) + _estimate_text_tokens(t.assistant_content)
            for t in compressed_turns
        )
        after_tokens = summary_tokens + c_tokens + tail_tokens
        token_counts = TokenCounts(
            before=total_tokens,
            after=after_tokens,
            tail_tokens=tail_tokens,
            summary_tokens=summary_tokens,
            compressed_tokens=c_tokens,
        )

        return CompactResult(
            summary=summary,
            compressed_turns=compressed_turns,
            tail_messages=tail,
                token_reduction=(source_before, summary_tokens + c_tokens),
            token_counts=token_counts,
        )

    # ============================================================
    # 分区逻辑
    # ============================================================

    def _partition(
        self, messages: list[Any]
    ) -> tuple[list[Any], list[Any], list[Any], list[tuple[int, int]]]:
        """将消息列表划分为 (to_summarize, to_compress, tail, compress_spans)。

        - tail: 从末尾往前，最多占 tail_ratio * budget 的 token
        - 剩余按 50/50 分给 summary 和 compress 区
        - 切分边界对齐到 turn 边界
        - compress_spans: compress 区各 turn 在原始 messages 中的 (start, end) 索引
        """
        tail_budget = int(self.token_budget * self.tail_ratio)
        turns = _find_turn_boundaries(messages)

        # 从末尾找 tail
        tail_turns: list[tuple[int, int]] = []
        tail_token_acc = 0
        for start, end in reversed(turns):
            turn_msgs = messages[start:end]
            turn_tokens = self.estimate_tokens(turn_msgs)
            if tail_token_acc + turn_tokens > tail_budget and tail_turns:
                break
            tail_token_acc += turn_tokens
            tail_turns.insert(0, (start, end))

        tail_start = tail_turns[0][0] if tail_turns else len(messages)
        tail = messages[tail_start:]
        rest = messages[:tail_start]
        rest_turns = [t for t in turns if t[1] <= tail_start]

        if not rest_turns:
            return [], [], tail, []

        # rest 按 50/50 token 分给 summary 和 compress
        rest_tokens = self.estimate_tokens(rest)
        summary_budget = rest_tokens // 2

        summary_end_idx = 0
        summary_token_acc = 0
        for i, (start, end) in enumerate(rest_turns):
            turn_tokens = self.estimate_tokens(messages[start:end])
            if summary_token_acc + turn_tokens > summary_budget and i > 0:
                break
            summary_token_acc += turn_tokens
            summary_end_idx = i + 1

        summary_turns = rest_turns[:summary_end_idx]
        compress_turns = rest_turns[summary_end_idx:]

        to_summarize = []
        for s, e in summary_turns:
            to_summarize.extend(messages[s:e])
        to_compress = []
        for s, e in compress_turns:
            to_compress.extend(messages[s:e])

        return to_summarize, to_compress, tail, compress_turns

    # ============================================================
    # LLM 压缩操作
    # ============================================================

    async def _generate_summary(self, messages: list[Any]) -> str:
        """调用 LLM 生成结构化摘要 (a3 §3.2 Summary 区)。"""
        conversation = self._messages_to_text(messages)
        user_prompt = _SUMMARY_USER.format(conversation=conversation)

        assert self._call_llm is not None
        return await self._call_llm(_SUMMARY_SYSTEM, user_prompt)

    async def _compress_turns(
        self, messages: list[Any], original_spans: list[tuple[int, int]]
    ) -> list[CompressedTurn]:
        """逐轮调用 LLM 压缩 (a3 §3.2 Compress 区)。

        按 turn 分组，每个 turn 独立压缩，保留 User-Assistant 结构。
        original_spans 是每个 turn 在原始完整消息列表中的 (start, end) 索引。
        """
        turns = _find_turn_boundaries(messages)
        # to_compress 由 compress_turns 展开，turn 数量一定相等
        assert len(turns) == len(original_spans), (
            f"turn count mismatch: {len(turns)} in slice vs {len(original_spans)} in spans"
        )
        result: list[CompressedTurn] = []

        assert self._call_llm is not None
        for i, (local_start, local_end) in enumerate(turns):
            turn_msgs = messages[local_start:local_end]
            user_content, assistant_content = self._split_turn(turn_msgs)
            if not user_content and not assistant_content:
                continue

            orig_span = original_spans[i]

            user_prompt = _COMPRESS_USER.format(
                user_content=user_content[:3000],
                assistant_content=assistant_content[:4000],
            )
            try:
                response = await self._call_llm(_COMPRESS_SYSTEM, user_prompt)
                parsed = self._parse_compress_response(
                    response, user_content, assistant_content, turn_span=orig_span
                )
                result.append(parsed)
            except Exception:
                logger.warning("Failed to compress turn %d-%d, using heuristic fallback", *orig_span)
                result.append(
                    self._heuristic_compress(orig_span, user_content, assistant_content)
                )

        return result

    # ============================================================
    # 辅助方法
    # ============================================================

    def _messages_to_text(self, messages: list[Any]) -> str:
        """将消息列表转为可送入 LLM 的纯文本表示。"""
        lines: list[str] = []
        for msg in messages:
            kind = getattr(msg, "kind", "unknown")
            role = "User" if kind == "request" else "Assistant"
            text = _extract_message_text(msg)
            if text.strip():
                lines.append(f"[{role}]: {text}")
        return "\n".join(lines)

    def _split_turn(self, turn_msgs: list[Any]) -> tuple[str, str]:
        """将一个 turn 的消息拆成 user_content 和 assistant_content。"""
        user_parts: list[str] = []
        assistant_parts: list[str] = []
        for msg in turn_msgs:
            kind = getattr(msg, "kind", None)
            text = _extract_message_text(msg)
            if kind == "request":
                user_parts.append(text)
            else:
                assistant_parts.append(text)
        return " | ".join(filter(None, user_parts)), " | ".join(filter(None, assistant_parts))

    def _parse_compress_response(
        self,
        response: str,
        fallback_user: str,
        fallback_assistant: str,
        *,
        turn_span: tuple[int, int] = (0, 0),
    ) -> CompressedTurn:
        """解析 LLM 压缩响应。

        预期格式为 JSON: {"user": "...", "assistant": "...", "tags": [...]}
        解析失败则退回启发式压缩。
        """
        data = self._extract_json(response)
        if data is not None and "user" in data and "assistant" in data:
            return CompressedTurn(
                turn_span=turn_span,
                user_content=str(data.get("user", fallback_user)),
                assistant_content=str(data.get("assistant", fallback_assistant)),
                tags=data.get("tags", []),
            )
        return self._heuristic_compress(turn_span, fallback_user, fallback_assistant)

    @staticmethod
    def _extract_json(text: str) -> dict | None:
        """从 LLM 响应中提取 JSON 对象。先尝试整体解析，失败则用括号匹配提取。"""
        stripped = text.strip()
        try:
            return json.loads(stripped)
        except json.JSONDecodeError:
            pass
        # 括号匹配提取第一个完整 JSON 对象
        start = stripped.find("{")
        if start == -1:
            return None
        depth = 0
        in_string = False
        escape = False
        for i in range(start, len(stripped)):
            c = stripped[i]
            if escape:
                escape = False
                continue
            if c == "\\":
                escape = True
                continue
            if c == '"':
                in_string = not in_string
                continue
            if in_string:
                continue
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(stripped[start : i + 1])
                    except json.JSONDecodeError:
                        return None
        return None

    def _heuristic_compress(
        self, turn_span: tuple[int, int], user_content: str, assistant_content: str
    ) -> CompressedTurn:
        """启发式压缩 (a3 §3.2 降级路径)。

        情感内容保守截断，技术内容激进截断。
        """
        start, end = turn_span
        all_text = user_content + " " + assistant_content
        tags = _classify_content(all_text)

        is_emotional = "emotional" in tags
        # 情感内容保留更多字符
        max_chars = 800 if is_emotional else 300

        def _truncate(text: str) -> str:
            if len(text) <= max_chars:
                return text
            return text[:max_chars] + "..."

        return CompressedTurn(
            turn_span=(start, end),
            user_content=_truncate(user_content),
            assistant_content=_truncate(assistant_content),
            tags=tags,
        )
