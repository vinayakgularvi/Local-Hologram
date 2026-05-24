"""Track Video RAG → offline-exports jobs and poll remote status."""

from __future__ import annotations

import asyncio
import logging
import re
import sqlite3
import threading
import time
from typing import Any

from offline_exports_client import (
    create_export,
    get_export_status,
    is_enabled,
    public_config,
    terminal_status,
)
from video_qa_store import validate_item_id

_log = logging.getLogger("video_qa_exports")

_DATA_DIR = __import__("pathlib").Path(__file__).resolve().parent / "data"
_DB_PATH = _DATA_DIR / "video_qa.db"
_lock = threading.Lock()

_EXPORT_ID_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}$")
_POLL_TASKS: dict[str, asyncio.Task] = {}


def _connect() -> sqlite3.Connection:
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    cx = sqlite3.connect(str(_DB_PATH), check_same_thread=False)
    cx.row_factory = sqlite3.Row
    return cx


def init_export_db() -> None:
    with _lock:
        with _connect() as cx:
            cx.execute(
                """
                CREATE TABLE IF NOT EXISTS video_qa_export_jobs (
                    job_id TEXT PRIMARY KEY,
                    export_item_id TEXT NOT NULL,
                    source_item_id TEXT,
                    user_question TEXT NOT NULL,
                    answer TEXT NOT NULL,
                    status TEXT NOT NULL,
                    stage TEXT,
                    output_path TEXT,
                    log_path TEXT,
                    error TEXT,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL
                )
                """
            )
            cx.commit()


def _now_ts() -> int:
    return int(time.time())


def make_export_item_id(source_item_id: str) -> str:
    base = re.sub(r"[^a-zA-Z0-9._-]", "_", (source_item_id or "item").strip())[:80]
    suffix = str(_now_ts())
    item_id = f"answer_{base}_{suffix}"
    if len(item_id) > 128:
        item_id = item_id[:128]
    if not _EXPORT_ID_RE.match(item_id):
        item_id = f"answer_{suffix}"
    return item_id


def _row_to_job(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "job_id": row["job_id"],
        "export_item_id": row["export_item_id"],
        "source_item_id": row["source_item_id"] or "",
        "user_question": row["user_question"],
        "answer": row["answer"],
        "status": row["status"],
        "stage": row["stage"] or "",
        "output_path": row["output_path"] or "",
        "log_path": row["log_path"] or "",
        "error": row["error"] or "",
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def save_job(
    *,
    job_id: str,
    export_item_id: str,
    source_item_id: str | None,
    user_question: str,
    answer: str,
    status: str,
    stage: str = "",
    output_path: str = "",
    log_path: str = "",
    error: str = "",
) -> dict[str, Any]:
    init_export_db()
    now = _now_ts()
    with _lock:
        with _connect() as cx:
            cx.execute(
                """
                INSERT INTO video_qa_export_jobs (
                    job_id, export_item_id, source_item_id, user_question, answer,
                    status, stage, output_path, log_path, error, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(job_id) DO UPDATE SET
                    status = excluded.status,
                    stage = excluded.stage,
                    output_path = excluded.output_path,
                    log_path = excluded.log_path,
                    error = excluded.error,
                    updated_at = excluded.updated_at
                """,
                (
                    job_id,
                    export_item_id,
                    source_item_id or "",
                    user_question,
                    answer,
                    status,
                    stage,
                    output_path,
                    log_path,
                    error,
                    now,
                    now,
                ),
            )
            cx.commit()
            row = cx.execute(
                "SELECT * FROM video_qa_export_jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
    return _row_to_job(row) if row else {}


def list_jobs(*, limit: int = 50) -> list[dict[str, Any]]:
    init_export_db()
    limit = max(1, min(int(limit), 200))
    with _lock:
        with _connect() as cx:
            rows = cx.execute(
                """
                SELECT * FROM video_qa_export_jobs
                ORDER BY updated_at DESC, created_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
    return [_row_to_job(r) for r in rows]


def get_job(job_id: str) -> dict[str, Any] | None:
    init_export_db()
    job_id = (job_id or "").strip()
    if not job_id:
        return None
    with _lock:
        with _connect() as cx:
            row = cx.execute(
                "SELECT * FROM video_qa_export_jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
    return _row_to_job(row) if row else None


def update_job_from_remote(remote: dict[str, Any], *, job_id: str) -> dict[str, Any]:
    job = get_job(job_id)
    if not job:
        raise ValueError(f"export job not found: {job_id}")
    now = _now_ts()
    status = str(remote.get("status") or job["status"])
    stage = str(remote.get("stage") or job.get("stage") or "")
    output_path = str(
        remote.get("output_path") or remote.get("rendered_video_path") or job.get("output_path") or ""
    )
    log_path = str(remote.get("log_path") or job.get("log_path") or "")
    err = str(remote.get("error") or remote.get("detail") or job.get("error") or "")
    export_item_id = str(remote.get("item_id") or job["export_item_id"])
    with _lock:
        with _connect() as cx:
            cx.execute(
                """
                UPDATE video_qa_export_jobs
                SET export_item_id = ?, status = ?, stage = ?, output_path = ?, log_path = ?,
                    error = ?, updated_at = ?
                WHERE job_id = ?
                """,
                (export_item_id, status, stage, output_path, log_path, err, now, job_id),
            )
            cx.commit()
            row = cx.execute(
                "SELECT * FROM video_qa_export_jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
    return _row_to_job(row) if row else job


def refresh_job_status(job_id: str) -> dict[str, Any]:
    job = get_job(job_id)
    if not job:
        raise ValueError(f"export job not found: {job_id}")
    remote = get_export_status(job_id)
    merged = update_job_from_remote(remote, job_id=job_id)
    merged["remote"] = remote
    return merged


def submit_export_async(
    *,
    source_item_id: str,
    user_question: str,
    answer: str,
) -> dict[str, Any] | None:
    """Create remote offline-export job and return local job record (non-blocking poll scheduled)."""
    if not is_enabled():
        return None
    answer = (answer or "").strip()
    user_question = (user_question or "").strip()
    if not answer or not user_question:
        raise ValueError("question and answer are required for offline export")
    source_item_id = validate_item_id(source_item_id) if source_item_id else ""
    export_item_id = make_export_item_id(source_item_id or "rag")
    from hologram_trace import trace

    trace(
        f"offline-exports: create job item_id={export_item_id} "
        f"source={source_item_id or 'rag'}"
    )
    remote = create_export(
        item_id=export_item_id,
        text=answer,
        question=user_question,
        answer=answer,
    )
    job_id = str(remote.get("job_id") or "").strip()
    if not job_id:
        raise RuntimeError("offline-exports did not return job_id")
    job = save_job(
        job_id=job_id,
        export_item_id=str(remote.get("item_id") or export_item_id),
        source_item_id=source_item_id,
        user_question=user_question,
        answer=answer,
        status=str(remote.get("status") or "queued"),
        stage=str(remote.get("stage") or ""),
        output_path=str(remote.get("output_path") or ""),
        log_path=str(remote.get("log_path") or ""),
    )
    job["status_url"] = remote.get("status_url") or f"/api/offline-exports/{job_id}"
    job["remote"] = remote
    return job


def schedule_poll(job_id: str) -> None:
    """Schedule asyncio poll loop for a job (call from async context)."""
    job_id = (job_id or "").strip()
    if not job_id:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    existing = _POLL_TASKS.get(job_id)
    if existing and not existing.done():
        return
    _POLL_TASKS[job_id] = loop.create_task(_poll_job_loop(job_id))


async def _poll_job_loop(job_id: str) -> None:
    interval = max(2.0, float(__import__("os").environ.get("OFFLINE_EXPORT_POLL_SEC") or "4"))
    max_sec = max(60.0, float(__import__("os").environ.get("OFFLINE_EXPORT_POLL_MAX_SEC") or "3600"))
    deadline = time.monotonic() + max_sec
    while time.monotonic() < deadline:
        try:
            job = await asyncio.to_thread(refresh_job_status, job_id)
            if terminal_status(str(job.get("status") or "")):
                return
        except asyncio.CancelledError:
            raise
        except Exception as e:
            _log.warning("offline export poll %s: %s", job_id, e)
            job = get_job(job_id)
            if job:
                save_job(
                    job_id=job_id,
                    export_item_id=job["export_item_id"],
                    source_item_id=job.get("source_item_id") or None,
                    user_question=job["user_question"],
                    answer=job["answer"],
                    status="failed",
                    error=str(e)[:500],
                )
            return
        await asyncio.sleep(interval)


async def poll_active_jobs_once() -> int:
    """Refresh all non-terminal jobs once (startup / periodic)."""
    if not is_enabled():
        return 0
    init_export_db()
    updated = 0
    with _lock:
        with _connect() as cx:
            rows = cx.execute(
                """
                SELECT job_id, status FROM video_qa_export_jobs
                ORDER BY updated_at DESC
                LIMIT 100
                """
            ).fetchall()
    for row in rows:
        if terminal_status(str(row["status"] or "")):
            continue
        try:
            await asyncio.to_thread(refresh_job_status, str(row["job_id"]))
            updated += 1
        except Exception as e:
            _log.debug("export refresh %s: %s", row["job_id"], e)
    return updated


async def offline_exports_poll_loop() -> None:
    interval = max(3.0, float(__import__("os").environ.get("OFFLINE_EXPORT_BG_POLL_SEC") or "8"))
    while True:
        try:
            await poll_active_jobs_once()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            _log.warning("offline exports background poll: %s", e)
        await asyncio.sleep(interval)


def get_status() -> dict[str, Any]:
    return public_config()
