"""Build RAG-generate conversations from Video Q&A search hits."""

from __future__ import annotations

from typing import Any

_MAX_CONTEXT_CHARS = 12_000


def build_video_qa_conversation(query: str, items: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Messages for RAG_GENERATE_STREAM_URL using retrieved video Q&A entries."""
    parts: list[str] = []
    for it in items:
        ans = (it.get("answer") or "").strip()
        if not ans:
            continue
        qu = (it.get("question") or "").strip()
        item_id = (it.get("id") or "").strip()
        header = f"Video entry {item_id}:" if item_id else "Video entry:"
        if qu:
            parts.append(f"{header}\nQuestion: {qu}\nAnswer: {ans}")
        else:
            parts.append(f"{header}\nAnswer: {ans}")
    context = "\n\n---\n\n".join(parts)
    if len(context) > _MAX_CONTEXT_CHARS:
        context = context[:_MAX_CONTEXT_CHARS] + "…"
    conv: list[dict[str, str]] = []
    if context.strip():
        conv.append(
            {
                "role": "system",
                "content": (
                    "Answer the user using the following video Q&A knowledge when relevant. "
                    "Reply in clear, concise prose suitable for lip-sync video. "
                    "If the excerpts do not contain the answer, say so briefly.\n\n"
                    + context
                ),
            }
        )
    conv.append({"role": "user", "content": (query or "").strip()})
    return conv


def pick_fallback_answer(items: list[dict[str, Any]]) -> str:
    """Use top hit answer when RAG generate is unavailable."""
    for it in items:
        ans = (it.get("answer") or "").strip()
        if ans:
            return ans
    return ""
