"""Video Q&A store: question/answer pairs with associated video files."""

from __future__ import annotations

import re
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

from transcribe_client import transcribe_configured
from video_qa_garage import (
    delete_item_videos as garage_delete_item_videos,
    garage_object_key,
    head_object as garage_head,
    is_configured as garage_is_configured,
    iter_object_bytes as garage_iter,
    public_status as garage_public_status,
    upload_object as garage_upload,
)
from video_qa_qdrant import (
    delete_by_item_id as qdrant_delete,
    get_item as qdrant_get,
    is_configured as qdrant_is_configured,
    list_items as qdrant_list,
    public_status as qdrant_public_status,
    search_items as qdrant_search,
    upsert_item as qdrant_upsert,
)

_DATA_DIR = Path(__file__).resolve().parent / "data"
_VIDEOS_DIR = _DATA_DIR / "videos"
_DB_PATH = _DATA_DIR / "video_qa.db"
_lock = threading.Lock()

_ITEM_ID_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}$")
_ALLOWED_VIDEO_SUFFIXES = {".mp4", ".webm", ".mov", ".mkv"}
_MAX_VIDEO_BYTES = 200 * 1024 * 1024


def _now_ts() -> int:
    return int(time.time())


def _connect() -> sqlite3.Connection:
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    _VIDEOS_DIR.mkdir(parents=True, exist_ok=True)
    cx = sqlite3.connect(str(_DB_PATH), check_same_thread=False)
    cx.row_factory = sqlite3.Row
    return cx


def init_db() -> None:
    with _lock:
        with _connect() as cx:
            cx.execute(
                """
                CREATE TABLE IF NOT EXISTS video_qa_items (
                    id TEXT PRIMARY KEY,
                    question TEXT NOT NULL,
                    answer TEXT NOT NULL,
                    filename TEXT NOT NULL,
                    content_type TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL,
                    garage_object_key TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL,
                    processed INTEGER NOT NULL DEFAULT 0,
                    trim_start_sec REAL NOT NULL DEFAULT 0,
                    processed_at INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            _migrate_video_qa_columns(cx)
            cx.commit()


def _migrate_video_qa_columns(cx: sqlite3.Connection) -> None:
    cols = {row[1] for row in cx.execute("PRAGMA table_info(video_qa_items)").fetchall()}
    if "processed" not in cols:
        cx.execute(
            "ALTER TABLE video_qa_items ADD COLUMN processed INTEGER NOT NULL DEFAULT 0"
        )
    if "trim_start_sec" not in cols:
        cx.execute(
            "ALTER TABLE video_qa_items ADD COLUMN trim_start_sec REAL NOT NULL DEFAULT 0"
        )
    if "processed_at" not in cols:
        cx.execute(
            "ALTER TABLE video_qa_items ADD COLUMN processed_at INTEGER NOT NULL DEFAULT 0"
        )
    if "process_error" not in cols:
        cx.execute(
            "ALTER TABLE video_qa_items ADD COLUMN process_error TEXT NOT NULL DEFAULT ''"
        )


def get_status() -> dict[str, Any]:
    return {
        "storage": "qdrant" if qdrant_is_configured() else "sqlite",
        "qdrant": qdrant_public_status(),
        "garage": garage_public_status(),
        "transcribe_configured": transcribe_configured(),
        "local_video_dir": str(_VIDEOS_DIR.resolve()),
    }


def validate_item_id(item_id: str) -> str:
    item_id = (item_id or "").strip()
    if not item_id or not _ITEM_ID_RE.match(item_id):
        raise ValueError("item_id must be 1–128 chars: letters, digits, ., _, -")
    return item_id


def video_path(item_id: str) -> Path:
    return _VIDEOS_DIR / f"{item_id}.mp4"


def _row_to_item(row: sqlite3.Row) -> dict[str, Any]:
    keys = row.keys()
    return {
        "id": row["id"],
        "question": row["question"],
        "answer": row["answer"],
        "video_url": f"/api/video-qa/{row['id']}/video",
        "garage_object_key": row["garage_object_key"],
        "filename": row["filename"],
        "content_type": row["content_type"],
        "size_bytes": row["size_bytes"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "processed": bool(row["processed"]) if "processed" in keys else False,
        "trim_start_sec": float(row["trim_start_sec"]) if "trim_start_sec" in keys else 0.0,
        "processed_at": int(row["processed_at"]) if "processed_at" in keys else 0,
        "process_error": str(row["process_error"]) if "process_error" in keys else "",
    }


def _sync_qdrant(item: dict[str, Any], *, required: bool = False) -> None:
    if not qdrant_is_configured():
        return
    try:
        qdrant_upsert(item)
    except Exception:
        if required:
            raise


def _store_video(*, item_id: str, obj_key: str, data: bytes, content_type: str) -> None:
    if garage_is_configured():
        garage_upload(key=obj_key, data=data, content_type=content_type)
    video_path(item_id).write_bytes(data)


def _delete_video_files(item: dict[str, Any]) -> list[str]:
    item_id = str(item.get("id") or "")
    obj_key = str(item.get("garage_object_key") or "").strip() or None
    deleted_keys: list[str] = []
    if garage_is_configured():
        deleted_keys = garage_delete_item_videos(
            item_id=item_id,
            garage_object_key_value=obj_key,
        )
    if item_id:
        video_path(item_id).unlink(missing_ok=True)
    return deleted_keys


def parse_range_header(range_header: str | None, total: int) -> tuple[int, int]:
    if total <= 0:
        return 0, 0
    if not range_header:
        return 0, total - 1
    m = re.match(r"^bytes=(\d*)-(\d*)$", range_header.strip())
    if not m:
        return 0, total - 1
    start_s, end_s = m.group(1), m.group(2)
    if start_s:
        start = int(start_s)
        end = int(end_s) if end_s else total - 1
    elif end_s:
        suffix = int(end_s)
        start = max(0, total - suffix)
        end = total - 1
    else:
        return 0, total - 1
    start = max(0, min(start, total - 1))
    end = max(start, min(end, total - 1))
    return start, end


def open_video(
    item_id: str,
    *,
    range_header: str | None = None,
) -> dict[str, Any] | None:
    item_id = validate_item_id(item_id)
    item = get_item(item_id)
    if not item:
        return None

    obj_key = str(item.get("garage_object_key") or garage_object_key(item_id))
    content_type = str(item.get("content_type") or "video/mp4")

    if garage_is_configured():
        head = garage_head(obj_key)
        if head:
            total = int(head["size_bytes"])
            start, end = parse_range_header(range_header, total)
            stream, ct, _ = garage_iter(key=obj_key, start=start, end=end)
            return {
                "source": "garage",
                "content_type": ct or content_type,
                "size_bytes": total,
                "start": start,
                "end": end,
                "stream": stream,
                "filename": item.get("filename") or f"{item_id}.mp4",
            }

    path = video_path(item_id)
    if path.is_file():
        total = path.stat().st_size
        start, end = parse_range_header(range_header, total)
        return {
            "source": "local",
            "content_type": content_type,
            "size_bytes": total,
            "start": start,
            "end": end,
            "path": path,
            "filename": path.name,
        }
    return None


def create_item(
    *,
    item_id: str,
    question: str,
    answer: str,
    filename: str,
    content_type: str,
    data: bytes,
) -> dict[str, Any]:
    init_db()
    item_id = validate_item_id(item_id)
    question = (question or "").strip()
    answer = (answer or "").strip()
    if not question:
        raise ValueError("question is required")
    if not answer:
        raise ValueError("answer is required")
    if not data:
        raise ValueError("video file is empty")
    if len(data) > _MAX_VIDEO_BYTES:
        raise ValueError(f"video too large (max {_MAX_VIDEO_BYTES // (1024 * 1024)} MB)")

    suffix = Path(filename or "").suffix.lower()
    if suffix and suffix not in _ALLOWED_VIDEO_SUFFIXES:
        raise ValueError(f"allowed video types: {', '.join(sorted(_ALLOWED_VIDEO_SUFFIXES))}")

    if qdrant_is_configured() and qdrant_get(item_id):
        raise ValueError(f"item_id already exists: {item_id}")

    now = _now_ts()
    obj_key = garage_object_key(item_id)

    with _lock:
        with _connect() as cx:
            existing = cx.execute(
                "SELECT id FROM video_qa_items WHERE id = ?",
                (item_id,),
            ).fetchone()
            if existing:
                raise ValueError(f"item_id already exists: {item_id}")

            try:
                _store_video(item_id=item_id, obj_key=obj_key, data=data, content_type=content_type)
                cx.execute(
                    """
                    INSERT INTO video_qa_items (
                        id, question, answer, filename, content_type, size_bytes,
                        garage_object_key, created_at, updated_at,
                        processed, trim_start_sec, processed_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, 0)
                    """,
                    (
                        item_id,
                        question,
                        answer,
                        filename or f"{item_id}.mp4",
                        content_type or "video/mp4",
                        len(data),
                        obj_key,
                        now,
                        now,
                    ),
                )
                cx.commit()
            except Exception:
                try:
                    _delete_video_files({"id": item_id, "garage_object_key": obj_key})
                except Exception:
                    pass
                raise

    out = get_item(item_id)
    assert out is not None
    _sync_qdrant(out, required=True)
    return out


def list_items(*, limit: int = 50) -> list[dict[str, Any]]:
    limit = max(1, min(int(limit), 200))
    if qdrant_is_configured():
        items = qdrant_list(limit=limit)
        if items:
            init_db()
            sqlite_by_id: dict[str, dict[str, Any]] = {}
            with _lock:
                with _connect() as cx:
                    rows = cx.execute(
                        """
                        SELECT * FROM video_qa_items
                        ORDER BY updated_at DESC, id DESC
                        LIMIT ?
                        """,
                        (limit * 2,),
                    ).fetchall()
            for row in rows:
                sqlite_by_id[row["id"]] = _row_to_item(row)
            merged: list[dict[str, Any]] = []
            for q in items:
                s = sqlite_by_id.get(q["id"])
                if s and int(s.get("updated_at") or 0) >= int(q.get("updated_at") or 0):
                    merged.append({**q, **s})
                else:
                    merged.append(q)
            merged.sort(
                key=lambda x: (x.get("updated_at") or 0, x.get("id") or ""),
                reverse=True,
            )
            return merged[:limit]
    init_db()
    with _lock:
        with _connect() as cx:
            rows = cx.execute(
                """
                SELECT * FROM video_qa_items
                ORDER BY updated_at DESC, id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
    return [_row_to_item(r) for r in rows]


def _get_sqlite_item(item_id: str) -> dict[str, Any] | None:
    init_db()
    with _lock:
        with _connect() as cx:
            row = cx.execute(
                "SELECT * FROM video_qa_items WHERE id = ?",
                (item_id,),
            ).fetchone()
    return _row_to_item(row) if row else None


def get_item(item_id: str) -> dict[str, Any] | None:
    item_id = validate_item_id(item_id)
    q_item = qdrant_get(item_id) if qdrant_is_configured() else None
    s_item = _get_sqlite_item(item_id)
    if q_item and s_item:
        # SQLite is authoritative right after local writes (Qdrant may lag one read).
        if int(s_item.get("updated_at") or 0) >= int(q_item.get("updated_at") or 0):
            return {**q_item, **s_item}
        return {**s_item, **q_item}
    return q_item or s_item


def update_item(
    *,
    item_id: str,
    question: str | None = None,
    answer: str | None = None,
    filename: str | None = None,
    content_type: str | None = None,
    data: bytes | None = None,
) -> dict[str, Any]:
    init_db()
    item_id = validate_item_id(item_id)
    existing = get_item(item_id)
    if not existing:
        raise ValueError(f"item not found: {item_id}")

    new_question = (question if question is not None else existing["question"]).strip()
    new_answer = (answer if answer is not None else existing["answer"]).strip()
    if not new_question:
        raise ValueError("question is required")
    if not new_answer:
        raise ValueError("answer is required")

    new_filename = filename if filename is not None else existing["filename"]
    new_content_type = content_type if content_type is not None else existing["content_type"]
    new_size = existing["size_bytes"]
    obj_key = str(existing.get("garage_object_key") or garage_object_key(item_id))

    if data is not None:
        if not data:
            raise ValueError("video file is empty")
        if len(data) > _MAX_VIDEO_BYTES:
            raise ValueError(f"video too large (max {_MAX_VIDEO_BYTES // (1024 * 1024)} MB)")
        suffix = Path(new_filename or "").suffix.lower()
        if suffix and suffix not in _ALLOWED_VIDEO_SUFFIXES:
            raise ValueError(f"allowed video types: {', '.join(sorted(_ALLOWED_VIDEO_SUFFIXES))}")
        new_size = len(data)

    now = _now_ts()

    with _lock:
        with _connect() as cx:
            if data is not None:
                _store_video(item_id=item_id, obj_key=obj_key, data=data, content_type=new_content_type)
            row = cx.execute(
                "SELECT id FROM video_qa_items WHERE id = ?",
                (item_id,),
            ).fetchone()
            if row:
                cx.execute(
                    """
                    UPDATE video_qa_items
                    SET question = ?, answer = ?, filename = ?, content_type = ?,
                        size_bytes = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (new_question, new_answer, new_filename, new_content_type, new_size, now, item_id),
                )
            else:
                cx.execute(
                    """
                    INSERT INTO video_qa_items (
                        id, question, answer, filename, content_type, size_bytes,
                        garage_object_key, created_at, updated_at,
                        processed, trim_start_sec, processed_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, 0)
                    """,
                    (
                        item_id,
                        new_question,
                        new_answer,
                        new_filename,
                        new_content_type,
                        new_size,
                        obj_key,
                        existing.get("created_at") or now,
                        now,
                    ),
                )
            cx.commit()

    out = get_item(item_id) or {
        **existing,
        "question": new_question,
        "answer": new_answer,
        "filename": new_filename,
        "content_type": new_content_type,
        "size_bytes": new_size,
        "updated_at": now,
    }
    if out.get("answer") != new_answer:
        out = {
            **out,
            "question": new_question,
            "answer": new_answer,
            "filename": new_filename,
            "content_type": new_content_type,
            "size_bytes": new_size,
            "updated_at": now,
        }
    _sync_qdrant(out, required=True)
    return get_item(item_id) or out


def _persist_item_fields(item_id: str, *, fields: dict[str, Any]) -> dict[str, Any]:
    """Update SQLite row fields and sync Qdrant from merged item."""
    init_db()
    item_id = validate_item_id(item_id)
    existing = get_item(item_id)
    if not existing:
        raise ValueError(f"item not found: {item_id}")

    now = _now_ts()
    merged = {**existing, **fields, "updated_at": now}

    with _lock:
        with _connect() as cx:
            row = cx.execute(
                "SELECT id FROM video_qa_items WHERE id = ?",
                (item_id,),
            ).fetchone()
            if row:
                sets: list[str] = []
                vals: list[Any] = []
                for col in (
                    "question",
                    "answer",
                    "processed",
                    "trim_start_sec",
                    "processed_at",
                    "process_error",
                ):
                    if col in fields:
                        sets.append(f"{col} = ?")
                        val = fields[col]
                        if col == "processed":
                            val = 1 if val else 0
                        vals.append(val)
                sets.append("updated_at = ?")
                vals.append(now)
                vals.append(item_id)
                cx.execute(
                    f"UPDATE video_qa_items SET {', '.join(sets)} WHERE id = ?",
                    vals,
                )
            cx.commit()

    _sync_qdrant(merged, required=True)
    return get_item(item_id) or merged


def clear_process_error(item_id: str) -> None:
    _persist_item_fields(item_id, fields={"process_error": ""})


def record_process_error(item_id: str, error: str) -> dict[str, Any]:
    msg = (error or "").strip()[:2000]
    return _persist_item_fields(
        item_id,
        fields={"process_error": msg, "processed": False},
    )


def complete_item_processing(
    *,
    item_id: str,
    answer: str,
    video_data: bytes,
    content_type: str,
    trim_start_sec: float,
) -> dict[str, Any]:
    """After pipeline: replace video, set transcribed answer, mark processed, sync Qdrant."""
    init_db()
    item_id = validate_item_id(item_id)
    answer = (answer or "").strip()
    if not answer:
        raise ValueError("answer is required after processing")
    if not video_data:
        raise ValueError("processed video is empty")
    if len(video_data) > _MAX_VIDEO_BYTES:
        raise ValueError(f"video too large (max {_MAX_VIDEO_BYTES // (1024 * 1024)} MB)")

    existing = get_item(item_id)
    if not existing:
        raise ValueError(f"item not found: {item_id}")

    obj_key = str(existing.get("garage_object_key") or garage_object_key(item_id))
    now = _now_ts()
    new_size = len(video_data)
    filename = existing.get("filename") or f"{item_id}.mp4"

    with _lock:
        with _connect() as cx:
            _store_video(
                item_id=item_id,
                obj_key=obj_key,
                data=video_data,
                content_type=content_type,
            )
            row = cx.execute(
                "SELECT id FROM video_qa_items WHERE id = ?",
                (item_id,),
            ).fetchone()
            if row:
                cx.execute(
                    """
                    UPDATE video_qa_items
                    SET question = '', answer = ?, content_type = ?, size_bytes = ?,
                        updated_at = ?, processed = 1, trim_start_sec = ?, processed_at = ?,
                        process_error = ''
                    WHERE id = ?
                    """,
                    (answer, content_type, new_size, now, float(trim_start_sec), now, item_id),
                )
            else:
                cx.execute(
                    """
                    INSERT INTO video_qa_items (
                        id, question, answer, filename, content_type, size_bytes,
                        garage_object_key, created_at, updated_at,
                        processed, trim_start_sec, processed_at, process_error
                    ) VALUES (?, '', ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, '')
                    """,
                    (
                        item_id,
                        answer,
                        filename,
                        content_type,
                        new_size,
                        obj_key,
                        existing.get("created_at") or now,
                        now,
                        float(trim_start_sec),
                        now,
                    ),
                )
            cx.commit()

    out = {
        **existing,
        "question": "",
        "answer": answer,
        "content_type": content_type,
        "size_bytes": new_size,
        "updated_at": now,
        "processed": True,
        "trim_start_sec": float(trim_start_sec),
        "processed_at": now,
        "process_error": "",
        "garage_object_key": obj_key,
        "filename": filename,
    }
    _sync_qdrant(out, required=True)
    return get_item(item_id) or out


def delete_item(item_id: str) -> dict[str, Any]:
    item_id = validate_item_id(item_id)
    item = get_item(item_id)
    if not item:
        init_db()
        with _lock:
            with _connect() as cx:
                row = cx.execute(
                    "SELECT id FROM video_qa_items WHERE id = ?",
                    (item_id,),
                ).fetchone()
                if not row:
                    return {"deleted": False, "id": item_id}
        video_path(item_id).unlink(missing_ok=True)
        return {"deleted": False, "id": item_id}

    garage_keys_deleted: list[str] = []
    try:
        garage_keys_deleted = _delete_video_files(item)
    except RuntimeError:
        raise
    except Exception as e:
        raise RuntimeError(f"Failed to delete video files: {e}") from e

    init_db()
    with _lock:
        with _connect() as cx:
            cx.execute("DELETE FROM video_qa_items WHERE id = ?", (item_id,))
            cx.commit()

    if qdrant_is_configured():
        try:
            qdrant_delete(item_id)
        except Exception as e:
            raise RuntimeError(f"Deleted Garage video but Qdrant delete failed: {e}") from e

    return {
        "deleted": True,
        "id": item_id,
        "garage_deleted": bool(garage_keys_deleted) or not garage_is_configured(),
        "garage_object_keys": garage_keys_deleted,
    }


def delete_items(item_ids: list[str]) -> dict[str, Any]:
    """Delete many entries; continues on per-item failure."""
    deleted: list[str] = []
    failed: list[dict[str, str]] = []
    for raw_id in item_ids:
        item_id = str(raw_id or "").strip()
        if not item_id:
            continue
        try:
            result = delete_item(item_id)
            if result.get("deleted"):
                deleted.append(item_id)
            else:
                failed.append({"id": item_id, "error": "not found"})
        except Exception as e:
            failed.append({"id": item_id, "error": str(e)})
    return {"deleted": deleted, "failed": failed, "count": len(deleted)}


def search_items(*, query: str, limit: int = 20) -> list[dict[str, Any]]:
    q = (query or "").strip()
    if not q:
        return []
    limit = max(1, min(int(limit), 100))
    if qdrant_is_configured():
        results = qdrant_search(query=q, limit=limit)
        if results:
            return results
    init_db()
    pattern = f"%{q.lower()}%"
    with _lock:
        with _connect() as cx:
            rows = cx.execute(
                """
                SELECT * FROM video_qa_items
                WHERE lower(question) LIKE ? OR lower(answer) LIKE ? OR lower(id) LIKE ?
                    OR lower(process_error) LIKE ?
                ORDER BY updated_at DESC, id DESC
                LIMIT ?
                """,
                (pattern, pattern, pattern, pattern, limit),
            ).fetchall()
    return [_row_to_item(r) for r in rows]
