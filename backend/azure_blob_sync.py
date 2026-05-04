"""
Azure Blob Storage → Chroma RAG (ContainerClient list + download_blob).

Env (pick one auth style):
  AZURE_STORAGE_CONNECTION_STRING   Full connection string
  OR
  AZURE_STORAGE_ACCOUNT_NAME + AZURE_STORAGE_ACCOUNT_KEY

  AZURE_BLOB_CONTAINER               Required container name
  AZURE_BLOB_PREFIX                  Optional virtual folder prefix (e.g. handbook/)
  AZURE_BLOB_MAX_FILES, AZURE_BLOB_MAX_DEPTH, AZURE_BLOB_MAX_BYTES, AZURE_BLOB_ALLOWED_SUFFIXES
"""

from __future__ import annotations

import os
from typing import Any

from rag_store import ingest_file

_live: dict[str, Any] = {
    "sync_running": False,
    "last_sync_started": None,
    "last_sync_finished": None,
    "last_ok": None,
    "last_error": None,
    "last_total_ingested": None,
    "last_errors_count": None,
}


def _env_int(key: str, default: int) -> int:
    v = os.environ.get(key, "").strip()
    if not v:
        return default
    try:
        return int(float(v))
    except ValueError:
        return default


def live_sync_mark_start() -> None:
    from datetime import datetime, timezone

    _live["sync_running"] = True
    _live["last_sync_started"] = datetime.now(timezone.utc).isoformat()


def live_sync_mark_done(result: dict[str, Any] | None, error: str | None) -> None:
    from datetime import datetime, timezone

    _live["sync_running"] = False
    _live["last_sync_finished"] = datetime.now(timezone.utc).isoformat()
    _live["last_ok"] = error is None
    _live["last_error"] = error
    if result is not None:
        _live["last_total_ingested"] = result.get("total_ingested")
        _live["last_errors_count"] = len(result.get("errors") or [])
    else:
        _live["last_total_ingested"] = None
        if error:
            _live["last_errors_count"] = None


def _normalize_prefix(raw: str) -> str:
    p = raw.strip().lstrip("/")
    if p and not p.endswith("/"):
        p += "/"
    return p


def is_configured() -> bool:
    ctr = os.environ.get("AZURE_BLOB_CONTAINER", "").strip()
    if not ctr:
        return False
    if os.environ.get("AZURE_STORAGE_CONNECTION_STRING", "").strip():
        return True
    return bool(
        os.environ.get("AZURE_STORAGE_ACCOUNT_NAME", "").strip()
        and os.environ.get("AZURE_STORAGE_ACCOUNT_KEY", "").strip()
    )


def public_config() -> dict[str, Any]:
    ctr = os.environ.get("AZURE_BLOB_CONTAINER", "").strip()
    hint = ctr[:10] + "…" if len(ctr) > 12 else (ctr or "—")
    live_env = os.environ.get("AZURE_BLOB_LIVE_SYNC", "1").strip().lower() in ("1", "true", "yes")
    try:
        from studio_live_sync import live_sync_flags_for_connector

        ls = live_sync_flags_for_connector(live_env)
    except Exception:
        ls = {"live_sync_env_enabled": live_env, "live_sync_master_enabled": True, "live_sync_enabled": live_env}
    interval = max(30, _env_int("AZURE_BLOB_SYNC_INTERVAL_SEC", 90))
    auth = "connection_string" if os.environ.get("AZURE_STORAGE_CONNECTION_STRING", "").strip() else (
        "account_key" if is_configured() else "none"
    )
    return {
        "configured": is_configured(),
        "container_hint": hint,
        "prefix": _normalize_prefix(os.environ.get("AZURE_BLOB_PREFIX", "")),
        "max_files": _env_int("AZURE_BLOB_MAX_FILES", 80),
        "max_depth": _env_int("AZURE_BLOB_MAX_DEPTH", 8),
        "max_bytes_per_file": _env_int("AZURE_BLOB_MAX_BYTES", 25 * 1024 * 1024),
        **ls,
        "sync_interval_sec": interval,
        "auth_mode": auth if is_configured() else "none",
        "live": dict(_live),
    }


def _allowed_suffixes() -> set[str]:
    raw = os.environ.get("AZURE_BLOB_ALLOWED_SUFFIXES", ".pdf,.docx,.txt,.md").strip()
    return {s.strip().lower() for s in raw.split(",") if s.strip()}


def _blob_service():
    from azure.storage.blob import BlobServiceClient

    conn = os.environ.get("AZURE_STORAGE_CONNECTION_STRING", "").strip()
    if conn:
        return BlobServiceClient.from_connection_string(conn)
    name = os.environ.get("AZURE_STORAGE_ACCOUNT_NAME", "").strip()
    key = os.environ.get("AZURE_STORAGE_ACCOUNT_KEY", "").strip()
    url = f"https://{name}.blob.core.windows.net"
    return BlobServiceClient(account_url=url, credential=key)


def sync_to_chroma(
    *,
    chunk_size: int,
    chunk_overlap: int,
) -> dict[str, Any]:
    if not is_configured():
        raise RuntimeError(
            "Azure Blob is not configured. Set AZURE_STORAGE_CONNECTION_STRING "
            "or AZURE_STORAGE_ACCOUNT_NAME + AZURE_STORAGE_ACCOUNT_KEY, and AZURE_BLOB_CONTAINER."
        )
    container_name = os.environ["AZURE_BLOB_CONTAINER"].strip()
    base_prefix = _normalize_prefix(os.environ.get("AZURE_BLOB_PREFIX", ""))
    max_files = _env_int("AZURE_BLOB_MAX_FILES", 80)
    max_depth = _env_int("AZURE_BLOB_MAX_DEPTH", 8)
    max_bytes = _env_int("AZURE_BLOB_MAX_BYTES", 25 * 1024 * 1024)
    allowed = _allowed_suffixes()

    service = _blob_service()
    cc = service.get_container_client(container_name)

    ingested: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    errors: list[dict[str, str]] = []
    seen = 0

    def _depth_for_key(key: str) -> int:
        rest = key[len(base_prefix) :] if key.startswith(base_prefix) else key
        rest = rest.strip("/")
        if not rest:
            return 0
        return rest.count("/")

    try:
        for blob in cc.list_blobs(name_starts_with=base_prefix):
            if seen >= max_files:
                break
            name = getattr(blob, "name", "") or ""
            if not name or name.endswith("/"):
                continue
            if _depth_for_key(name) > max_depth:
                skipped.append({"name": name, "reason": "over max depth"})
                continue
            base = name.split("/")[-1]
            rid = name
            suf = ""
            if "." in base:
                suf = "." + base.rsplit(".", 1)[-1].lower()
            if suf not in allowed:
                skipped.append({"name": rid, "reason": "extension not allowed"})
                continue
            sz = int(getattr(blob, "size", None) or 0)
            if sz and sz > max_bytes:
                skipped.append({"name": rid, "reason": f"too large ({sz} bytes)"})
                continue
            try:
                bc = cc.get_blob_client(name)
                data = bc.download_blob().readall()
                if len(data) > max_bytes:
                    skipped.append({"name": rid, "reason": "downloaded size over limit"})
                    continue
                info = ingest_file(
                    filename=base or name,
                    data=data,
                    chunk_size=chunk_size,
                    chunk_overlap=chunk_overlap,
                )
                ingested.append({"path": rid, **info})
                seen += 1
            except Exception as e:
                errors.append({"name": rid, "error": str(e)})
    except Exception as e:
        errors.append({"name": base_prefix or container_name, "error": f"list failed: {e}"})

    return {
        "container": container_name,
        "prefix": base_prefix,
        "ingested": ingested,
        "skipped": skipped[:200],
        "errors": errors,
        "total_ingested": len(ingested),
    }
