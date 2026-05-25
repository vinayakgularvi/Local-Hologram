"""Qdrant vector store for Video Q&A entries."""

from __future__ import annotations

import os
import uuid
from typing import Any

import httpx

from video_qa_urls import public_video_url, with_public_video_url

_VECTOR_SIZE = 768
_EMBED_MODEL_DEFAULT = "BAAI/bge-base-en-v1.5"
_embedder: Any = None


def _qdrant_url() -> str:
    return (os.environ.get("QDRANT_URL") or "http://10.29.145.124:6333").rstrip("/")


def _qdrant_api_key() -> str | None:
    key = (os.environ.get("QDRANT_API_KEY") or "").strip()
    return key or None


def _collection_name() -> str:
    return (
        os.environ.get("VIDEO_QA_QDRANT_COLLECTION")
        or os.environ.get("QDRANT_COLLECTION_NAME")
        or "video_qa"
    ).strip()


def is_configured() -> bool:
    return bool(_qdrant_url())


def public_status() -> dict[str, Any]:
    url = _qdrant_url()
    collection = _collection_name()
    out: dict[str, Any] = {
        "configured": is_configured(),
        "url": url,
        "collection": collection,
        "vector_size": _VECTOR_SIZE,
        "embed_model": os.environ.get("VIDEO_QA_EMBED_MODEL", _EMBED_MODEL_DEFAULT),
        "reachable": False,
        "points_count": None,
        "error": None,
    }
    if not is_configured():
        return out
    try:
        headers = _headers()
        with httpx.Client(timeout=8.0) as client:
            r = client.get(f"{url}/collections/{collection}", headers=headers)
            r.raise_for_status()
            info = r.json().get("result") or {}
            out["reachable"] = True
            out["points_count"] = info.get("points_count")
            out["status"] = info.get("status")
    except Exception as e:
        out["error"] = str(e)
    return out


def _headers() -> dict[str, str]:
    headers = {"Accept": "application/json"}
    key = _qdrant_api_key()
    if key:
        headers["api-key"] = key
    return headers


def _embed_text(text: str) -> list[float]:
    global _embedder
    text = (text or "").strip()
    if not text:
        raise ValueError("empty text for embedding")
    model_name = os.environ.get("VIDEO_QA_EMBED_MODEL", _EMBED_MODEL_DEFAULT).strip()
    if _embedder is None or getattr(_embedder, "_model_name", None) != model_name:
        from fastembed import TextEmbedding

        _embedder = TextEmbedding(model_name=model_name)
        _embedder._model_name = model_name  # type: ignore[attr-defined]
    vectors = list(_embedder.embed([text]))
    if not vectors:
        raise RuntimeError("embedding model returned no vectors")
    vec = vectors[0]
    if len(vec) != _VECTOR_SIZE:
        raise RuntimeError(
            f"embedding dimension {len(vec)} != Qdrant collection size {_VECTOR_SIZE}"
        )
    return [float(x) for x in vec]


def _embed_document(*, question: str, answer: str, processed: bool = False) -> list[float]:
    if processed:
        return _embed_text(answer)
    q = (question or "").strip()
    a = (answer or "").strip()
    text = q if not a or q == a else f"{q}\n{a}"
    return _embed_text(text)


def _point_uuid(item_id: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_OID, f"video-qa:{item_id}"))


def _payload_from_item(item: dict[str, Any]) -> dict[str, Any]:
    processed = bool(item.get("processed"))
    payload: dict[str, Any] = {
        "id": item["id"],
        "answer": item["answer"],
        "video_url": public_video_url(item),
        "garage_object_key": item.get("garage_object_key") or f"videos/{item['id']}.mp4",
        "filename": item.get("filename") or f"{item['id']}.mp4",
        "content_type": item.get("content_type") or "video/mp4",
        "size_bytes": int(item.get("size_bytes") or 0),
        "created_at": int(item.get("created_at") or 0),
        "updated_at": int(item.get("updated_at") or 0),
        "processed": processed,
        "trim_start_sec": float(item.get("trim_start_sec") or 0),
        "processed_at": int(item.get("processed_at") or 0),
    }
    if not processed:
        payload["question"] = str(item.get("question") or "")
    err = str(item.get("process_error") or "").strip()
    if err:
        payload["process_error"] = err
    return payload


def _item_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
    item_id = str(payload.get("id") or "").strip()
    if not item_id:
        raise ValueError("Qdrant payload missing id")
    return with_public_video_url(
        {
            "id": item_id,
            "question": str(payload.get("question") or ""),
            "answer": str(payload.get("answer") or ""),
            "garage_object_key": str(payload.get("garage_object_key") or f"videos/{item_id}.mp4"),
            "filename": str(payload.get("filename") or f"{item_id}.mp4"),
            "content_type": str(payload.get("content_type") or "video/mp4"),
            "size_bytes": int(payload.get("size_bytes") or 0),
            "created_at": int(payload.get("created_at") or 0),
            "updated_at": int(payload.get("updated_at") or 0),
            "processed": bool(payload.get("processed")),
            "trim_start_sec": float(payload.get("trim_start_sec") or 0),
            "processed_at": int(payload.get("processed_at") or 0),
            "process_error": str(payload.get("process_error") or ""),
        }
    )


def _find_point_id(item_id: str) -> str | None:
    body = {
        "filter": {"must": [{"key": "id", "match": {"value": item_id}}]},
        "limit": 1,
        "with_payload": False,
        "with_vector": False,
    }
    url = f"{_qdrant_url()}/collections/{_collection_name()}/points/scroll"
    with httpx.Client(timeout=15.0) as client:
        r = client.post(url, headers={**_headers(), "Content-Type": "application/json"}, json=body)
        r.raise_for_status()
        points = (r.json().get("result") or {}).get("points") or []
    if not points:
        return None
    return str(points[0].get("id"))


def _scroll_point(item_id: str, *, with_vector: bool = False) -> dict[str, Any] | None:
    if not is_configured():
        return None
    body = {
        "filter": {"must": [{"key": "id", "match": {"value": item_id}}]},
        "limit": 1,
        "with_payload": True,
        "with_vector": with_vector,
    }
    url = f"{_qdrant_url()}/collections/{_collection_name()}/points/scroll"
    with httpx.Client(timeout=15.0) as client:
        r = client.post(url, headers={**_headers(), "Content-Type": "application/json"}, json=body)
        r.raise_for_status()
        points = (r.json().get("result") or {}).get("points") or []
    return points[0] if points else None


def _vector_from_point(point: dict[str, Any]) -> list[float] | None:
    raw = point.get("vector")
    if isinstance(raw, list) and raw and isinstance(raw[0], (int, float)):
        return [float(x) for x in raw]
    if isinstance(raw, dict):
        for val in raw.values():
            if isinstance(val, list) and val:
                return [float(x) for x in val]
    return None


def upsert_item(item: dict[str, Any]) -> None:
    if not is_configured():
        return
    payload = _payload_from_item(item)
    point_id = _find_point_id(payload["id"]) or _point_uuid(payload["id"])
    vector: list[float] | None = None
    embed_error: Exception | None = None
    try:
        vector = _embed_document(
            question=str(item.get("question") or ""),
            answer=str(item.get("answer") or ""),
            processed=bool(item.get("processed")),
        )
    except Exception as e:
        embed_error = e
        existing = _scroll_point(payload["id"], with_vector=True)
        if existing:
            vector = _vector_from_point(existing)
            point_id = str(existing.get("id") or point_id)

    if not vector:
        msg = "failed to build embedding vector for Qdrant"
        if embed_error:
            raise RuntimeError(f"{msg}: {embed_error}") from embed_error
        raise RuntimeError(msg)

    body = {
        "points": [
            {
                "id": point_id,
                "vector": vector,
                "payload": payload,
            }
        ]
    }
    url = f"{_qdrant_url()}/collections/{_collection_name()}/points"
    with httpx.Client(timeout=30.0) as client:
        r = client.put(url, headers={**_headers(), "Content-Type": "application/json"}, json=body)
        r.raise_for_status()


def delete_by_item_id(item_id: str) -> None:
    if not is_configured():
        return
    body = {
        "filter": {
            "must": [{"key": "id", "match": {"value": item_id}}],
        }
    }
    url = f"{_qdrant_url()}/collections/{_collection_name()}/points/delete"
    with httpx.Client(timeout=20.0) as client:
        r = client.post(url, headers={**_headers(), "Content-Type": "application/json"}, json=body)
        r.raise_for_status()


def get_item(item_id: str) -> dict[str, Any] | None:
    if not is_configured():
        return None
    body = {
        "filter": {"must": [{"key": "id", "match": {"value": item_id}}]},
        "limit": 1,
        "with_payload": True,
        "with_vector": False,
    }
    url = f"{_qdrant_url()}/collections/{_collection_name()}/points/scroll"
    with httpx.Client(timeout=15.0) as client:
        r = client.post(url, headers={**_headers(), "Content-Type": "application/json"}, json=body)
        r.raise_for_status()
        points = (r.json().get("result") or {}).get("points") or []
    if not points:
        return None
    payload = points[0].get("payload") or {}
    return _item_from_payload(payload)


def list_items(*, limit: int = 50) -> list[dict[str, Any]]:
    if not is_configured():
        return []
    limit = max(1, min(int(limit), 200))
    body = {"limit": limit, "with_payload": True, "with_vector": False}
    url = f"{_qdrant_url()}/collections/{_collection_name()}/points/scroll"
    items: list[dict[str, Any]] = []
    offset: str | None = None
    with httpx.Client(timeout=30.0) as client:
        while len(items) < limit:
            req = dict(body)
            if offset:
                req["offset"] = offset
            r = client.post(
                url,
                headers={**_headers(), "Content-Type": "application/json"},
                json=req,
            )
            r.raise_for_status()
            result = r.json().get("result") or {}
            points = result.get("points") or []
            for pt in points:
                payload = pt.get("payload") or {}
                try:
                    items.append(_item_from_payload(payload))
                except ValueError:
                    continue
                if len(items) >= limit:
                    break
            offset = result.get("next_page_offset")
            if not offset or not points:
                break
    items.sort(key=lambda x: (x.get("updated_at") or 0, x.get("id") or ""), reverse=True)
    return items[:limit]


def _normalize_match_text(text: str) -> str:
    import re

    t = (text or "").lower().strip()
    t = re.sub(r"\s+", " ", t)
    return t.strip(".,!?;:")


def text_similarity(text_a: str, text_b: str) -> float:
    """0–1 similarity without embeddings (exact / substring / difflib ratio)."""
    a = _normalize_match_text(text_a)
    b = _normalize_match_text(text_b)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    if a in b or b in a:
        return 0.95
    from difflib import SequenceMatcher

    return float(SequenceMatcher(None, a, b).ratio())


def _text_filter_search(*, query: str, limit: int) -> list[dict[str, Any]]:
    q = query.lower()
    matches: list[dict[str, Any]] = []
    for item in list_items(limit=500):
        hay = (
            f"{item.get('id','')} {item.get('question','')} {item.get('answer','')} "
            f"{item.get('process_error','')}"
        ).lower()
        if q in hay:
            q_text = str(item.get("question") or "")
            a_text = str(item.get("answer") or "")
            item = dict(item)
            item["score"] = max(
                text_similarity(query, q_text),
                text_similarity(query, a_text),
                text_similarity(query, f"{q_text} {a_text}"),
            )
            item["search_method"] = "text_filter"
            matches.append(item)
        if len(matches) >= limit:
            break
    matches.sort(key=lambda x: float(x.get("score") or 0), reverse=True)
    return matches


def search_items(*, query: str, limit: int = 20) -> list[dict[str, Any]]:
    if not is_configured():
        return []
    q = (query or "").strip()
    if not q:
        return []
    limit = max(1, min(int(limit), 100))
    try:
        vector = _embed_text(q)
    except Exception as e:
        from hologram_trace import trace

        trace(f"Qdrant search: embed query failed ({type(e).__name__}) → text filter fallback")
        return _text_filter_search(query=q, limit=limit)

    body = {
        "vector": vector,
        "limit": limit,
        "with_payload": True,
        "with_vector": False,
    }
    url = f"{_qdrant_url()}/collections/{_collection_name()}/points/search"
    try:
        with httpx.Client(timeout=20.0) as client:
            r = client.post(
                url,
                headers={**_headers(), "Content-Type": "application/json"},
                json=body,
            )
            r.raise_for_status()
            points = (r.json().get("result") or [])
    except Exception as e:
        from hologram_trace import trace

        trace(f"Qdrant search: vector HTTP failed ({type(e).__name__}) → text filter fallback")
        return _text_filter_search(query=q, limit=limit)

    out: list[dict[str, Any]] = []
    for pt in points:
        payload = pt.get("payload") or {}
        try:
            item = _item_from_payload(payload)
            score = pt.get("score")
            if score is not None:
                item["score"] = float(score)
            item["search_method"] = "vector"
            out.append(item)
        except ValueError:
            continue
    return out
