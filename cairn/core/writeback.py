"""写回筛选器 (a3 §5)。

每轮结束后由 LLM 异步评估——输入本轮对话 + 近期上下文，
输出结构化 JSON 候选列表。LLM 调用在实时路径之外，失败时静默跳过。

信号类型: emotion / explicit_remember / relationship_event / fact_update / key_experience / other
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)

# ============================================================
# 数据模型
# ============================================================


@dataclass
class WriteBackCandidate:
    """LLM 筛选产出的单条写回候选。

    携带信号类型、重要性、类别（episodic/semantic），
    调用方负责用 embedder 补全向量后构造对应节点并入队。
    """

    content: str  # 纯文本，应被记住的事实/经历
    signal: str  # emotion / explicit_remember / relationship_event / fact_update / key_experience / other
    importance: float = 0.5  # [0, 1]
    category: str = "episodic"  # episodic / semantic


# ============================================================
# LLM prompt 模板
# ============================================================

_DEFAULT_SYSTEM_PROMPT = """\
You are a memory curator for an AI companion named Cairn. \
Your job is to read the latest conversation turn and decide if anything is worth remembering long-term.

You receive:
- The current turn (user message + assistant response)
- Recent conversation context (last few turns, if available)
- Optional: tool events, emotion vectors (for context only)

Rules:
1. Only extract USER-SIDE facts and experiences. The assistant's words are context, not facts to remember.
2. One turn can produce zero, one, or multiple entries.
3. Each entry must have a signal type and importance score.
4. Signal types:
   - "emotion": emotional peak or significant emotional content
   - "explicit_remember": user explicitly asked to remember something
   - "relationship_event": relationship turning point or significant moment
   - "fact_update": a fact about the user changed (preference, situation, etc.)
   - "key_experience": significant tool use, activity, or shared experience
   - "other": something worth remembering that doesn't fit above
5. Category:
   - "episodic": a specific event or experience (has time context)
   - "semantic": a stable fact about the user (timeless)
6. Importance: 0.0-1.0. 0.9+ for explicit remembers and relationship events.
   0.5-0.7 for general facts. Below 0.3 won't be saved.
7. If nothing is worth remembering, return {"should_remember": false, "entries": []}.

Output ONLY valid JSON (no markdown, no extra text):
{"should_remember": true/false, "entries": [{"content": "...", "signal": "...", "importance": 0.X, "category": "..."}]}"""

_DEFAULT_USER_PROMPT = """\
Recent context:
{recent_context}

Current turn:
User: {user_input}
Assistant: {assistant_response}

Tool events: {tool_events}
Emotion vector (VAD): {emotion_vec}

Evaluate this turn for memory-worthy content."""


# ============================================================
# WriteBackFilter
# ============================================================


class WriteBackFilter:
    """LLM 驱动的写回筛选器 (a3 §5)。

    每轮结束异步调用 LLM 评估，产出零到多条候选。
    LLM 调用失败时静默返回空列表，不影响主对话流程。

    用法:
        f = WriteBackFilter(call_llm=my_llm_func)
        for candidate in await f.evaluate(user_input, assistant, recent_context=ctx):
            store.enqueue_write(make_node(candidate))
    """

    def __init__(
        self,
        call_llm: Callable[[str, str], Awaitable[str]] | None = None,
        system_prompt: str | None = None,
    ) -> None:
        self._call_llm = call_llm
        self._system_prompt = system_prompt or _DEFAULT_SYSTEM_PROMPT

    async def evaluate(
        self,
        user_input: str,
        assistant_response: str = "",
        *,
        recent_context: list[dict[str, str]] | None = None,
        emotion_vec: list[float] | None = None,
        tool_events: list[dict[str, Any]] | None = None,
    ) -> list[WriteBackCandidate]:
        """LLM 评估一轮对话，返回写回候选列表（可能为空）。"""
        if self._call_llm is None:
            return []

        # 构建近期上下文文本
        ctx_text = _format_recent_context(recent_context) if recent_context else "(无) "

        user_prompt = _DEFAULT_USER_PROMPT.format(
            recent_context=ctx_text,
            user_input=user_input,
            assistant_response=assistant_response,
            tool_events=json.dumps(tool_events or [], ensure_ascii=False),
            emotion_vec=json.dumps(emotion_vec or []),
        )

        try:
            response = await self._call_llm(self._system_prompt, user_prompt)
            return self._parse_response(response)
        except Exception:
            logger.debug("Write-back LLM call failed, skipping turn", exc_info=True)
            return []

    def _parse_response(self, response: str) -> list[WriteBackCandidate]:
        """解析 LLM 返回的 JSON 为候选列表。"""
        data = _extract_json(response)
        if data is None:
            return []
        if not data.get("should_remember", False):
            return []
        entries = data.get("entries", [])
        if not isinstance(entries, list):
            return []

        candidates: list[WriteBackCandidate] = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            content = entry.get("content", "")
            if not content:
                continue
            signal = entry.get("signal", "other")
            try:
                importance = max(0.0, min(1.0, float(entry.get("importance", 0.5))))
            except (ValueError, TypeError):
                importance = 0.5
            if importance < 0.3:  # 低重要性跳过
                continue
            category = entry.get("category", "episodic")
            candidates.append(WriteBackCandidate(
                content=str(content),
                signal=str(signal),
                importance=importance,
                category=str(category),
            ))
        return candidates


# ============================================================
# 辅助
# ============================================================


def _format_recent_context(turns: list[dict[str, str]]) -> str:
    """将最近几轮上下文格式化为纯文本。"""
    lines: list[str] = []
    recent = turns[-3:]  # 最多 3 轮
    for i, turn in enumerate(recent):
        label = -len(recent) + i  # -3, -2, -1（从早到晚）
        user = turn.get("user", "")
        asst = turn.get("assistant", "")
        if user:
            lines.append(f"[Turn {label}] User: {user}")
        if asst:
            lines.append(f"[Turn {label}] Assistant: {asst}")
    return "\n".join(lines) if lines else "(无) "


def _extract_json(text: str) -> dict | None:
    """从 LLM 响应中提取 JSON 对象。"""
    stripped = text.strip()
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        pass
    # 括号匹配提取
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
