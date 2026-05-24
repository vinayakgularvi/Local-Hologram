"""Trigger OFFLINE_EXPORTS when new documents appear in MongoDB (local_hologram DB)."""

from __future__ import annotations

import asyncio
import logging
import os
import re
from datetime import datetime, timezone
from typing import Any

from hologram_trace import trace
from mongodb_analytics import get_collection as mongo_get_collection, is_enabled as mongo_enabled
from offline_exports_client import is_enabled as offline_exports_enabled
from video_qa_exports import schedule_poll, submit_export_async

_log = logging.getLogger("mongodb_export_trigger")


def _env_bool(name: str, default: bool = False) -> bool:
    raw = (os.environ.get(name) or "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


def is_trigger_enabled() -> bool:
    return (
        mongo_enabled()
        and offline_exports_enabled()
        and _env_bool("MONGODB_EXPORT_TRIGGER", default=False)
    )


def change_stream_enabled() -> bool:
    return is_trigger_enabled() and _env_bool("MONGODB_EXPORT_CHANGE_STREAM", default=False)


def trigger_collection_name() -> str:
    return (
        (os.environ.get("MONGODB_EXPORT_TRIGGER_COLLECTION") or "").strip()
        or (os.environ.get("MONGODB_AVATAR_TURNS_COLLECTION") or "").strip()
        or "avatar_turns"
    )


def public_config() -> dict[str, Any]:
    return {
        "enabled": is_trigger_enabled(),
        "collection": trigger_collection_name() if is_trigger_enabled() else None,
        "change_stream": change_stream_enabled(),
    }


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _source_item_id_from_doc(doc: dict[str, Any]) -> str:
    for key in ("source_item_id", "item_id", "export_item_id"):
        v = doc.get(key)
        if v:
            return re.sub(r"[^a-zA-Z0-9._-]", "_", str(v).strip())[:80]
    sid = str(doc.get("sessionid") or "").strip()
    if sid and sid != "0":
        safe = re.sub(r"[^a-zA-Z0-9._-]", "_", sid)[:80]
        if safe:
            return safe
    tid = doc.get("analytics_turn_id")
    if tid is not None:
        return f"turn_{tid}"
    return "mongo"


def _export_text_from_doc(doc: dict[str, Any]) -> tuple[str, str]:
    question = str(doc.get("question") or doc.get("heard") or "").strip()
    answer = str(doc.get("answer") or "").strip()
    text = str(doc.get("speak_text") or doc.get("text") or answer).strip()
    return question, text or answer


def _claim_document(coll: Any, doc_id: Any) -> bool:
    """Atomically mark document so export is only queued once."""
    result = coll.find_one_and_update(
        {
            "_id": doc_id,
            "offline_export_job_id": {"$exists": False},
            "$or": [
                {"offline_export_claimed": {"$exists": False}},
                {"offline_export_claimed": False},
            ],
        },
        {
            "$set": {
                "offline_export_claimed": True,
                "offline_export_claimed_at": _utc_now(),
            }
        },
    )
    return result is not None


def _mark_export_job(coll: Any, doc_id: Any, job: dict[str, Any]) -> None:
    coll.update_one(
        {"_id": doc_id},
        {
            "$set": {
                "offline_export_job_id": job.get("job_id"),
                "offline_export_status": job.get("status") or "queued",
                "offline_export_item_id": job.get("export_item_id"),
                "offline_export_updated_at": _utc_now(),
            }
        },
    )


def _mark_export_failed(coll: Any, doc_id: Any, error: str) -> None:
    coll.update_one(
        {"_id": doc_id},
        {
            "$set": {
                "offline_export_status": "failed",
                "offline_export_error": error[:500],
                "offline_export_updated_at": _utc_now(),
            },
            "$unset": {"offline_export_claimed": ""},
        },
    )


def process_document_export(doc: dict[str, Any], *, collection_name: str | None = None) -> dict[str, Any] | None:
    """Queue offline-export for one MongoDB document (sync)."""
    if not is_trigger_enabled():
        trace("offline-export trigger: SKIPPED (MONGODB_EXPORT_TRIGGER off)")
        return None
    if not doc or not doc.get("_id"):
        return None
    if doc.get("offline_export_job_id"):
        trace(f"offline-export trigger: SKIP doc={doc.get('_id')} (already has job)")
        return None

    question, text = _export_text_from_doc(doc)
    if not question or not text:
        trace(f"offline-export trigger: SKIP doc={doc.get('_id')} (missing Q/A)")
        return None

    coll = mongo_get_collection(collection_name or trigger_collection_name())
    doc_id = doc["_id"]
    if not _claim_document(coll, doc_id):
        trace(f"offline-export trigger: SKIP doc={doc_id} (already claimed)")
        return None

    trace(
        f"offline-export trigger: POST /api/offline-exports doc={doc_id} "
        f"q={question[:60]!r}…"
    )
    try:
        job = submit_export_async(
            source_item_id=_source_item_id_from_doc(doc),
            user_question=question,
            answer=text,
        )
    except Exception as e:
        _mark_export_failed(coll, doc_id, str(e))
        _log.warning("mongo export trigger failed for %s: %s", doc_id, e)
        raise

    if job:
        _mark_export_job(coll, doc_id, job)
        schedule_poll(str(job["job_id"]))
        trace(
            f"offline-export trigger: queued job_id={job.get('job_id')} "
            f"item={job.get('export_item_id')} status={job.get('status')}"
        )
    return job


async def process_document_export_async(doc: dict[str, Any], *, collection_name: str | None = None) -> None:
    try:
        await asyncio.to_thread(process_document_export, doc, collection_name=collection_name)
    except Exception as e:
        _log.warning("async mongo export trigger: %s", e)


def schedule_export_for_document(
    doc: dict[str, Any] | None,
    *,
    collection_name: str | None = None,
) -> None:
    """Fire-and-forget export when a new MongoDB document is inserted."""
    if not is_trigger_enabled() or not doc:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    loop.create_task(process_document_export_async(doc, collection_name=collection_name))


async def trigger_export_for_new_document(doc_id: Any) -> None:
    """Load inserted document and queue OFFLINE_EXPORTS (async-safe)."""
    if not is_trigger_enabled() or doc_id is None:
        if doc_id is not None and not is_trigger_enabled():
            trace("offline-export trigger: SKIPPED (set MONGODB_EXPORT_TRIGGER=true)")
        return
    cname = trigger_collection_name()

    def _fetch() -> dict[str, Any] | None:
        return mongo_get_collection(cname).find_one({"_id": doc_id})

    doc = await asyncio.to_thread(_fetch)
    if doc:
        await process_document_export_async(doc, collection_name=cname)


def _change_stream_worker() -> None:
    coll = mongo_get_collection(trigger_collection_name())
    pipeline = [{"$match": {"operationType": "insert"}}]
    _log.info(
        "MongoDB export change stream watching db=%s collection=%s",
        coll.database.name,
        coll.name,
    )
    with coll.watch(pipeline, full_document="required") as stream:
        for change in stream:
            doc = change.get("fullDocument")
            if isinstance(doc, dict):
                try:
                    process_document_export(doc, collection_name=coll.name)
                except Exception as e:
                    _log.warning("change stream export: %s", e)


async def mongodb_export_change_stream_loop() -> None:
    """Background watcher for inserts by external writers (requires replica set)."""
    if not change_stream_enabled():
        return
    while True:
        try:
            await asyncio.to_thread(_change_stream_worker)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            _log.warning("MongoDB change stream unavailable (%s); retry in 15s", e)
            await asyncio.sleep(15.0)
