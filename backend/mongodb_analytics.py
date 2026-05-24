"""Async MongoDB persistence for avatar Q&A and voice-turn analytics."""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Any

_log = logging.getLogger("mongodb_analytics")

_client: Any = None
_db: Any = None


def _env_bool(name: str, default: bool = False) -> bool:
    raw = (os.environ.get(name) or "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


def is_enabled() -> bool:
    if _env_bool("MONGODB_DISABLE", default=False):
        return False
    if _env_bool("MONGODB_ENABLED", default=False):
        return bool(_connection_uri())
    return bool(_connection_uri())


def _connection_uri() -> str:
    return (os.environ.get("MONGODB_URI") or "").strip()


def _database_name() -> str:
    return (os.environ.get("MONGODB_DATABASE") or "local_hologram").strip() or "local_hologram"


def _collection_name() -> str:
    return (os.environ.get("MONGODB_AVATAR_TURNS_COLLECTION") or "avatar_turns").strip() or "avatar_turns"


def public_config() -> dict[str, Any]:
    uri = _connection_uri()
    out: dict[str, Any] = {
        "enabled": is_enabled(),
        "database": _database_name() if is_enabled() else None,
        "collection": _collection_name() if is_enabled() else None,
        "uri_configured": bool(uri),
    }
    if is_enabled():
        try:
            from mongodb_export_trigger import public_config as export_trigger_config

            out["export_trigger"] = export_trigger_config()
        except Exception:
            out["export_trigger"] = None
    return out


def _default_uri() -> str:
    host = (os.environ.get("MONGODB_HOST") or "localhost").strip()
    port = (os.environ.get("MONGODB_PORT") or "27017").strip()
    user = (os.environ.get("MONGODB_USERNAME") or "admin").strip()
    password = (os.environ.get("MONGODB_PASSWORD") or "admin").strip()
    auth_source = (os.environ.get("MONGODB_AUTH_SOURCE") or "admin").strip()
    if user and password:
        from urllib.parse import quote_plus

        return (
            f"mongodb://{quote_plus(user)}:{quote_plus(password)}@{host}:{port}/"
            f"?authSource={quote_plus(auth_source)}"
        )
    return f"mongodb://{host}:{port}/"


def _resolve_uri() -> str:
    return _connection_uri() or _default_uri()


def _ensure_client() -> Any:
    global _client, _db
    if _client is not None and _db is not None:
        return _db
    try:
        from pymongo import MongoClient
    except ImportError as e:
        raise RuntimeError("pymongo is not installed") from e
    uri = _resolve_uri()
    timeout_ms = max(1000, int(float(os.environ.get("MONGODB_CONNECT_TIMEOUT_MS") or "5000")))
    _client = MongoClient(uri, serverSelectionTimeoutMS=timeout_ms)
    _db = _client[_database_name()]
    return _db


def get_collection(name: str | None = None) -> Any:
    """Collection in MONGODB_DATABASE (default: avatar_turns)."""
    db = _ensure_client()
    return db[name or _collection_name()]


def _get_collection():
    return get_collection()


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _analytics_from_sqlite_row(row: dict[str, Any]) -> dict[str, Any]:
    skip = {"id", "ts"}
    out: dict[str, Any] = {"ts": row.get("ts")}
    for k, v in row.items():
        if k in skip:
            continue
        if v is not None:
            out[k] = v
    if row.get("human_dispatched") is not None:
        out["human_dispatched"] = bool(row.get("human_dispatched"))
    return out


def _sanitize_rag_meta(rag: dict[str, Any] | None) -> dict[str, Any] | None:
    if not rag or not isinstance(rag, dict):
        return None
    allowed = (
        "used",
        "chunks",
        "llm",
        "rag_latency_ms",
        "rag_first_sentence_ms",
        "stream_human",
        "human_sentence_count",
    )
    slim = {k: rag[k] for k in allowed if k in rag}
    return slim or None


def upsert_avatar_turn(
    *,
    analytics_turn_id: int | None = None,
    turn_uuid: str | None = None,
    sessionid: str,
    question: str,
    answer: str,
    speak_text: str = "",
    analytics: dict[str, Any] | None = None,
    rag: dict[str, Any] | None = None,
    human_dispatched: bool | None = None,
    total_request_ms: float | None = None,
) -> Any | None:
    """Upsert avatar turn; returns MongoDB _id when a new document was inserted."""
    if not is_enabled():
        return None
    tid = int(analytics_turn_id) if analytics_turn_id is not None else 0
    key_uuid = (turn_uuid or "").strip()
    if tid < 1 and not key_uuid:
        import uuid

        key_uuid = str(uuid.uuid4())
    now = _utc_now()
    analytics_payload = dict(analytics or {})
    if total_request_ms is not None:
        analytics_payload.setdefault("total_request_ms", total_request_ms)
    doc: dict[str, Any] = {
        "sessionid": (sessionid or "").strip(),
        "question": (question or "").strip(),
        "answer": (answer or "").strip(),
        "speak_text": (speak_text or "").strip(),
        "heard": (question or "").strip(),
        "analytics": analytics_payload,
        "rag": _sanitize_rag_meta(rag),
        "human_dispatched": bool(human_dispatched) if human_dispatched is not None else None,
        "updated_at": now,
    }
    if tid >= 1:
        doc["analytics_turn_id"] = tid
        filt: dict[str, Any] = {"analytics_turn_id": tid}
    else:
        doc["turn_uuid"] = key_uuid
        filt = {"turn_uuid": key_uuid}
    coll = _get_collection()
    result = coll.update_one(
        filt,
        {
            "$set": doc,
            "$setOnInsert": {"created_at": now},
        },
        upsert=True,
    )
    try:
        if tid >= 1:
            coll.create_index("analytics_turn_id", unique=True, sparse=True)
        coll.create_index("sessionid")
        coll.create_index([("created_at", -1)])
        coll.create_index("offline_export_job_id", sparse=True)
    except Exception:
        pass

    return result.upserted_id


def sync_avatar_turn_from_sqlite(analytics_turn_id: int) -> bool:
    """Merge latest SQLite voice_turn metrics into MongoDB."""
    if not is_enabled():
        return False
    from analytics_store import get_voice_turn

    row = get_voice_turn(int(analytics_turn_id))
    if not row:
        return False
    tid = int(row["id"])
    coll = _get_collection()
    coll.update_one(
        {"analytics_turn_id": tid},
        {
            "$set": {
                "analytics": _analytics_from_sqlite_row(row),
                "updated_at": _utc_now(),
            },
        },
        upsert=False,
    )
    return True


async def record_avatar_turn_async(**kwargs: Any) -> None:
    try:
        inserted_id = await asyncio.to_thread(upsert_avatar_turn, **kwargs)
        if inserted_id is not None:
            from hologram_trace import trace
            from mongodb_export_trigger import trigger_export_for_new_document

            trace(f"MongoDB: new avatar_turns doc _id={inserted_id} → offline-export trigger")
            await trigger_export_for_new_document(inserted_id)
    except Exception as e:
        _log.warning("MongoDB record avatar turn failed: %s", e)


async def sync_avatar_turn_async(analytics_turn_id: int) -> None:
    try:
        await asyncio.to_thread(sync_avatar_turn_from_sqlite, analytics_turn_id)
    except Exception as e:
        _log.debug("MongoDB sync turn %s failed: %s", analytics_turn_id, e)


def schedule_record_avatar_turn(**kwargs: Any) -> None:
    """Fire-and-forget from async request handlers."""
    if not is_enabled():
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    loop.create_task(record_avatar_turn_async(**kwargs))


def schedule_sync_avatar_turn(analytics_turn_id: int | None) -> None:
    if not is_enabled() or analytics_turn_id is None or int(analytics_turn_id) < 1:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    loop.create_task(sync_avatar_turn_async(int(analytics_turn_id)))
