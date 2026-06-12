"""层 B 动态注入块构建器 (a3 §4.1)。

每轮根据 RecallBundle 构建上下文注入块，以 ModelRequest(SystemPromptPart)
形式插入 message_history 最前面。
"""

from __future__ import annotations

from datetime import datetime

from cairn.memory.recall import RecallBundle, RecallResult


def _format_results(results: list[RecallResult], label: str) -> str:
    """将单通道召回结果格式化为自然语言列表。"""
    if not results:
        return ""
    lines = [f"{label}："]
    for r in results:
        content = _extract_node_content(r.node)
        if content:
            lines.append(f"- [{r.score:.2f}] {content}")
    return "\n".join(lines)


def _extract_node_content(node) -> str:
    """从节点提取可读文本内容。"""
    content = getattr(node, "content", None)
    if isinstance(content, str):
        return content[:500]
    if isinstance(content, dict):
        text = content.get("text", "") or str(content)
        return text[:500]
    statement = getattr(node, "statement", None)
    if statement:
        return statement[:500]
    pattern = getattr(node, "pattern", None)
    if pattern:
        return pattern[:500]
    return ""


def build_injection(recall_bundle: RecallBundle) -> str:
    """构建当前轮的上下文注入块 (a3 §4.1 Layer B Step 1)。

    将三通道召回结果格式化为自然语言，作为 Cairn 本轮可见的"你记起了这些"提示。
    不强制结构化格式——让 LLM 自然理解比 JSON 更有效。

    Args:
        recall_bundle: 三通道召回结果（各通道独立，不做跨通道合并）
    Returns:
        纯文本注入块，可直接包装为 ModelRequest(UserPromptPart)
    """
    parts: list[str] = []

    if recall_bundle.episodic:
        parts.append(_format_results(recall_bundle.episodic, "你回忆起这些相关经历"))

    if recall_bundle.semantic:
        parts.append(_format_results(recall_bundle.semantic, "你知道这些相关事实"))

    if recall_bundle.procedural:
        parts.append(_format_results(recall_bundle.procedural, "你倾向于这样回应"))

    if not parts:
        return ""

    # 导语说明这些记忆的用途，并附带当前时间
    now = datetime.now()
    header = (
        f"[当前时间: {now.strftime('%Y-%m-%d %H:%M:%S')}"
        f" ({now.astimezone().tzinfo})]\n\n"
        "[以下是你在本轮对话中记起的内容。"
        "它们来自你的长期记忆，帮助你保持连续性和理解上下文。"
        "你不需要逐条复述——让它们自然地融入你的回应中。]"
    )
    return header + "\n\n" + "\n\n".join(parts)
