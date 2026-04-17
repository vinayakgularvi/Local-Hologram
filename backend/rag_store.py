"""ChromaDB persistent RAG store for Avatar Studio (PDF, DOCX, TXT)."""

from __future__ import annotations

import hashlib
import io
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_CHROMA_PATH = Path(__file__).resolve().parent / "data" / "chroma"
_lock = threading.Lock()
_client: Any = None
_collection: Any = None

# Tunables via env in main or here
DEFAULT_CHUNK_SIZE = 900
DEFAULT_CHUNK_OVERLAP = 120
MAX_TEXT_CHARS = 2_000_000


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _get_collection():
    global _client, _collection
    with _lock:
        if _collection is not None:
            return _collection
        import chromadb

        _CHROMA_PATH.mkdir(parents=True, exist_ok=True)
        _client = chromadb.PersistentClient(path=str(_CHROMA_PATH))
        _collection = _client.get_or_create_collection(
            name="avatar_knowledge",
            metadata={"description": "Avatar Studio RAG corpus"},
        )
        return _collection


def _safe_stem(name: str) -> str:
    base = Path(name).name
    if not base or base in (".", ".."):
        return "document"
    return re.sub(r"[^a-zA-Z0-9._-]+", "_", base)[:180]


def _source_id(filename: str, content_bytes: bytes) -> str:
    h = hashlib.sha256()
    h.update(_safe_stem(filename).encode("utf-8", errors="replace"))
    h.update(b"\0")
    h.update(content_bytes[: min(len(content_bytes), 65536)])
    return h.hexdigest()[:32]


def _extract_text_pdf(data: bytes) -> str:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    parts: list[str] = []
    for page in reader.pages:
        t = page.extract_text()
        if t:
            parts.append(t)
    return "\n\n".join(parts)


def _extract_text_docx(data: bytes) -> str:
    from docx import Document

    doc = Document(io.BytesIO(data))
    return "\n\n".join(p.text for p in doc.paragraphs if p.text.strip())


def _extract_text_plain(data: bytes) -> str:
    for enc in ("utf-8", "utf-8-sig", "cp1252", "latin-1"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def extract_text(filename: str, data: bytes) -> str:
    lower = filename.lower()
    if lower.endswith(".pdf"):
        return _extract_text_pdf(data)
    if lower.endswith(".docx"):
        return _extract_text_docx(data)
    if lower.endswith(".txt") or lower.endswith(".md"):
        return _extract_text_plain(data)
    raise ValueError(f"Unsupported type: {filename}")


def _normalize_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip())


def chunk_text(text: str, chunk_size: int, overlap: int) -> list[str]:
    text = _normalize_whitespace(text)
    if not text:
        return []
    if len(text) > MAX_TEXT_CHARS:
        text = text[:MAX_TEXT_CHARS]
    chunks: list[str] = []
    i = 0
    step = max(1, chunk_size - overlap)
    while i < len(text):
        chunks.append(text[i : i + chunk_size])
        i += step
    return chunks


def delete_by_source_id(source_id: str) -> int:
    col = _get_collection()
    try:
        existing = col.get(where={"source_id": source_id}, include=[])
    except Exception:
        existing = col.get(where={"source_id": {"$eq": source_id}}, include=[])
    ids = existing.get("ids") or []
    if not ids:
        return 0
    col.delete(ids=ids)
    return len(ids)


def ingest_file(
    *,
    filename: str,
    data: bytes,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
) -> dict[str, Any]:
    text = extract_text(filename, data)
    text = _normalize_whitespace(text)
    if not text:
        raise ValueError("No extractable text in file (empty or unsupported content).")

    sid = _source_id(filename, data)
    removed = delete_by_source_id(sid)

    chunks = chunk_text(text, chunk_size, chunk_overlap)
    if not chunks:
        raise ValueError("Chunking produced no segments.")

    col = _get_collection()
    ids = [f"{sid}_{i}" for i in range(len(chunks))]
    metadatas = [
        {
            "source_id": sid,
            "filename": _safe_stem(filename),
            "chunk_index": i,
            "ingested_at": _utc_iso(),
        }
        for i in range(len(chunks))
    ]
    col.add(ids=ids, documents=chunks, metadatas=metadatas)
    return {
        "source_id": sid,
        "filename": _safe_stem(filename),
        "chunks_added": len(chunks),
        "chunks_removed": removed,
        "chars": len(text),
    }


def get_status() -> dict[str, Any]:
    col = _get_collection()
    # Chroma count() on collection
    try:
        n = col.count()
    except Exception:
        n = 0
    return {
        "chroma_path": str(_CHROMA_PATH.resolve()),
        "collection": "avatar_knowledge",
        "chunk_count": n,
    }


def list_sources() -> list[dict[str, Any]]:
    col = _get_collection()
    try:
        raw = col.get(include=["metadatas"], limit=50_000)
    except Exception:
        return []
    metas = raw.get("metadatas") or []
    by_sid: dict[str, dict[str, Any]] = {}
    for m in metas:
        if not m:
            continue
        sid = m.get("source_id")
        if not sid:
            continue
        fn = m.get("filename", "")
        ing = m.get("ingested_at", "")
        if sid not in by_sid:
            by_sid[sid] = {"source_id": sid, "filename": fn, "ingested_at": ing, "chunks": 0}
        by_sid[sid]["chunks"] += 1
        if ing and (not by_sid[sid].get("ingested_at") or ing > by_sid[sid]["ingested_at"]):
            by_sid[sid]["ingested_at"] = ing
    return sorted(by_sid.values(), key=lambda x: x.get("ingested_at") or "", reverse=True)


def query_documents(query: str, n_results: int = 6) -> dict[str, Any]:
    col = _get_collection()
    n_results = max(1, min(n_results, 50))
    res = col.query(query_texts=[query], n_results=n_results)
    docs = (res.get("documents") or [[]])[0]
    metas = (res.get("metadatas") or [[]])[0]
    dists = (res.get("distances") or [[]])[0] if res.get("distances") else None
    out = []
    for i, doc in enumerate(docs):
        item: dict[str, Any] = {
            "text": doc,
            "metadata": metas[i] if i < len(metas) else {},
        }
        if dists is not None and i < len(dists):
            item["distance"] = dists[i]
        out.append(item)
    return {"results": out}


def delete_source(source_id: str) -> int:
    return delete_by_source_id(source_id)


def reset_collection() -> None:
    """Delete all vectors (dev / admin only — protect in API)."""
    global _collection, _client
    import chromadb

    with _lock:
        _CHROMA_PATH.mkdir(parents=True, exist_ok=True)
        _client = chromadb.PersistentClient(path=str(_CHROMA_PATH))
        try:
            _client.delete_collection("avatar_knowledge")
        except Exception:
            pass
        _collection = _client.get_or_create_collection(
            name="avatar_knowledge",
            metadata={"description": "Avatar Studio RAG corpus"},
        )
