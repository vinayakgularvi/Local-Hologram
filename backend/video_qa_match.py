"""High-confidence Video Q&A lookup for live avatar (skip LiveTalking when matched)."""

from __future__ import annotations

import math
import os
from typing import Any

from feature_flags import video_rag_enabled
from hologram_trace import trace
from video_qa_qdrant import (
    _embed_text,
    is_configured as qdrant_configured,
    text_similarity,
)
from video_qa_store import get_item, search_items, validate_item_id
from video_qa_urls import cdn_base, cdn_configured, public_video_url

_EXACT_MATCH_SCORE = 1.0


def _env_bool(name: str, default: bool = False) -> bool:
    raw = (os.environ.get(name) or "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


def is_voice_match_enabled() -> bool:
    return (
        video_rag_enabled()
        and _env_bool("VIDEO_QA_VOICE_MATCH", default=True)
        and qdrant_configured()
    )


def match_threshold() -> float:
    raw = (os.environ.get("VIDEO_QA_MATCH_THRESHOLD") or "0.9").strip()
    try:
        v = float(raw)
    except ValueError:
        v = 0.9
    return max(0.5, min(v, 0.999))


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na <= 0 or nb <= 0:
        return 0.0
    return max(0.0, min(1.0, dot / (na * nb)))


def _embedding_similarity(text_a: str, text_b: str) -> float | None:
    a = (text_a or "").strip()
    b = (text_b or "").strip()
    if not a or not b:
        return None
    try:
        return _cosine_similarity(_embed_text(a), _embed_text(b))
    except Exception as e:
        trace(f"Video Q&A match: embedding compare failed ({type(e).__name__}: {e})")
        return None


def _combined_similarity(query: str, target: str, vector_score: float) -> float:
    """Best of vector search score, text match, and embedding match."""
    scores: list[float] = [float(vector_score or 0.0), text_similarity(query, target)]
    emb = _embedding_similarity(query, target)
    if emb is not None:
        scores.append(emb)
    return max(scores)


def _is_exact_text_match(a: str, b: str) -> bool:
    return text_similarity(a, b) >= _EXACT_MATCH_SCORE


def _qa_pair_exact_match(question: str, answer: str, item: dict[str, Any]) -> bool:
    stored_q = str(item.get("question") or "").strip()
    stored_a = str(item.get("answer") or "").strip()
    if not stored_a:
        return False
    return _is_exact_text_match(question, stored_q) and _is_exact_text_match(answer, stored_a)


def find_existing_qdrant_entry(
    *,
    question: str,
    answer: str,
    item_id: str | None = None,
) -> dict[str, Any] | None:
    """
    Return a Qdrant Video Q&A row when question+answer are a 100% text match.
    If item_id is provided and exists, only a full Q+A match on that id counts.
    Otherwise search Qdrant for any id with the same Q+A pair.
    """
    if not qdrant_configured():
        return None
    q = (question or "").strip()
    a = (answer or "").strip()
    if not q or not a:
        return None

    raw_id = (item_id or "").strip()
    if raw_id:
        try:
            rid = validate_item_id(raw_id)
        except ValueError:
            rid = ""
        if rid:
            by_id = get_item(rid)
            if by_id and _qa_pair_exact_match(q, a, by_id):
                trace(
                    f"Video Q&A dedup: id={rid} question+answer 100% match → skip offline export"
                )
                return {
                    **by_id,
                    "match_reason": "id_and_qa",
                    "question_match_score": _EXACT_MATCH_SCORE,
                    "answer_match_score": _EXACT_MATCH_SCORE,
                }
            if by_id:
                trace(
                    f"Video Q&A dedup: id={rid} exists but Q/A differ → scan Qdrant for duplicate pair"
                )

    seen_ids: set[str] = set()
    for search_q in (q, a) if a != q else (q,):
        for hit in search_items(query=search_q, limit=40):
            hid = str(hit.get("id") or "").strip()
            if not hid or hid in seen_ids:
                continue
            seen_ids.add(hid)
            full = get_item(hid) or hit
            if not _qa_pair_exact_match(q, a, full):
                continue
            trace(
                f"Video Q&A dedup: found id={hid} question+answer 100% match "
                f"(search={search_q[:40]!r}…) → skip offline export"
            )
            return {
                **full,
                "match_reason": "qa_exact",
                "question_match_score": _EXACT_MATCH_SCORE,
                "answer_match_score": _EXACT_MATCH_SCORE,
            }
    return None


def find_high_confidence_match(user_query: str) -> dict[str, Any] | None:
    """
    Search Qdrant first; if question and answer confidence are both >= threshold,
    return the stored video Q&A entry (same clip for Q and A).
    """
    if not is_voice_match_enabled():
        trace("Video Q&A match: SKIPPED (VIDEO_QA_VOICE_MATCH off or Qdrant not configured)")
        return None
    q = (user_query or "").strip()
    if not q:
        trace("Video Q&A match: SKIPPED (empty query)")
        return None
    threshold = match_threshold()
    trace(f"Video Q&A match: START qdrant search threshold={threshold:.0%} query={q[:80]!r}")

    hits = search_items(query=q, limit=5)
    if not hits:
        trace("Video Q&A match: NO hits from Qdrant → will use LiveTalking")
        return None

    trace(f"Video Q&A match: {len(hits)} candidate(s) from Qdrant")
    for i, hit in enumerate(hits, start=1):
        item_id = str(hit.get("id") or "").strip()
        if not item_id:
            trace(f"  [{i}] SKIP (missing id)")
            continue

        full = get_item(item_id) or hit
        question = str(full.get("question") or hit.get("question") or "").strip()
        answer = str(full.get("answer") or hit.get("answer") or "").strip()
        if not answer:
            trace(f"  [{i}] id={item_id} SKIP (no answer)")
            continue

        vector_score = float(hit.get("score") or 0.0)
        search_method = str(hit.get("search_method") or "unknown")

        question_score = _combined_similarity(q, question, vector_score) if question else vector_score

        # Answer leg: user query vs answer text, or trust paired row when question matches strongly
        answer_direct = _combined_similarity(q, answer, 0.0)
        if question_score >= threshold:
            answer_score = max(answer_direct, question_score, vector_score)
        else:
            answer_score = answer_direct

        q_pct = question_score * 100
        a_pct = answer_score * 100
        t_pct = threshold * 100
        ok = question_score >= threshold and answer_score >= threshold
        trace(
            f"  [{i}] id={item_id} via={search_method} vector={vector_score:.3f} "
            f"question={q_pct:.1f}% answer={a_pct:.1f}% need>={t_pct:.0f}% "
            f"stored_q={question[:40]!r}…"
            f" → {'MATCH' if ok else 'below threshold'}"
        )
        if not ok:
            continue

        video_url = public_video_url(full)
        trace(
            f"Video Q&A match: HIT id={item_id} → play cached video {video_url} "
            f"(skip LiveTalking)"
        )
        return {
            **full,
            **hit,
            "question": question,
            "answer": answer,
            "video_url": video_url,
            "question_score": round(question_score, 4),
            "answer_score": round(answer_score, 4),
            "vector_score": round(vector_score, 4),
            "match_threshold": threshold,
            "search_method": search_method,
            "playback_mode": "cached_video",
        }

    trace("Video Q&A match: no candidate met both scores → LiveTalking path")
    return None


def public_config() -> dict[str, Any]:
    return {
        "video_rag_enabled": video_rag_enabled(),
        "enabled": is_voice_match_enabled(),
        "threshold": match_threshold(),
        "video_cdn_base": cdn_base(),
        "video_cdn_configured": cdn_configured(),
    }
