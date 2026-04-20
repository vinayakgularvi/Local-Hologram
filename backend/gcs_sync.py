"""
Google Cloud Storage → Chroma RAG (list_blobs + download_as_bytes).

Env:
  GCS_BUCKET                         Required bucket name
  GCS_PREFIX                         Optional object prefix (folder)
  GCS_CREDENTIALS_PATH               Optional path to service account JSON (preferred for GCS-only)
  Or GOOGLE_APPLICATION_CREDENTIALS   Standard ADC file path
  Or GCS_USE_ADC=1                    Use application-default / workload credentials
  GCS_MAX_FILES, GCS_MAX_DEPTH, GCS_MAX_BYTES, GCS_ALLOWED_SUFFIXES
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


def _resolved_credentials_path() -> str | None:
    for key in ("GCS_CREDENTIALS_PATH", "GOOGLE_APPLICATION_CREDENTIALS"):
        raw = os.environ.get(key, "").strip()
        if not raw:
            continue
        p = os.path.abspath(os.path.expanduser(raw))
        if os.path.isfile(p):
            return p
    return None


def is_configured() -> bool:
    if not os.environ.get("GCS_BUCKET", "").strip():
        return False
    if os.environ.get("GCS_USE_ADC", "").strip().lower() in ("1", "true", "yes"):
        return True
    return _resolved_credentials_path() is not None


def public_config() -> dict[str, Any]:
    b = os.environ.get("GCS_BUCKET", "").strip()
    hint = b[:10] + "…" if len(b) > 12 else (b or "—")
    live_env = os.environ.get("GCS_LIVE_SYNC", "1").strip().lower() in ("1", "true", "yes")
    try:
        from studio_live_sync import live_sync_flags_for_connector

        ls = live_sync_flags_for_connector(live_env)
    except Exception:
        ls = {"live_sync_env_enabled": live_env, "live_sync_master_enabled": True, "live_sync_enabled": live_env}
    interval = max(30, _env_int("GCS_SYNC_INTERVAL_SEC", 90))
    auth = "adc" if os.environ.get("GCS_USE_ADC", "").strip().lower() in ("1", "true", "yes") else (
        "service_account" if _resolved_credentials_path() else "none"
    )
    return {
        "configured": is_configured(),
        "bucket_hint": hint,
        "prefix": _normalize_prefix(os.environ.get("GCS_PREFIX", "")),
        "max_files": _env_int("GCS_MAX_FILES", 80),
        "max_depth": _env_int("GCS_MAX_DEPTH", 8),
        "max_bytes_per_file": _env_int("GCS_MAX_BYTES", 25 * 1024 * 1024),
        **ls,
        "sync_interval_sec": interval,
        "auth_mode": auth if is_configured() else "none",
        "live": dict(_live),
    }


def _allowed_suffixes() -> set[str]:
    raw = os.environ.get("GCS_ALLOWED_SUFFIXES", ".pdf,.docx,.txt,.md").strip()
    return {s.strip().lower() for s in raw.split(",") if s.strip()}


def _storage_client():
    from google.cloud import storage
    from google.oauth2 import service_account

    if os.environ.get("GCS_USE_ADC", "").strip().lower() in ("1", "true", "yes"):
        return storage.Client()
    path = _resolved_credentials_path()
    if not path:
        raise RuntimeError("GCS credentials path missing.")
    creds = service_account.Credentials.from_service_account_file(path)
    return storage.Client(credentials=creds, project=creds.project_id)


def sync_to_chroma(
    *,
    chunk_size: int,
    chunk_overlap: int,
) -> dict[str, Any]:
    if not is_configured():
        raise RuntimeError(
            "GCS is not configured. Set GCS_BUCKET and GCS_CREDENTIALS_PATH (or "
            "GOOGLE_APPLICATION_CREDENTIALS), or GCS_USE_ADC=1."
        )
    bucket_name = os.environ["GCS_BUCKET"].strip()
    base_prefix = _normalize_prefix(os.environ.get("GCS_PREFIX", ""))
    max_files = _env_int("GCS_MAX_FILES", 80)
    max_depth = _env_int("GCS_MAX_DEPTH", 8)
    max_bytes = _env_int("GCS_MAX_BYTES", 25 * 1024 * 1024)
    allowed = _allowed_suffixes()

    client = _storage_client()

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
        for blob in client.list_blobs(bucket_name, prefix=base_prefix or None):
            if seen >= max_files:
                break
            name = blob.name
            if name.endswith("/"):
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
            sz = int(blob.size or 0)
            if sz and sz > max_bytes:
                skipped.append({"name": rid, "reason": f"too large ({sz} bytes)"})
                continue
            try:
                data = blob.download_as_bytes()
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
        errors.append({"name": bucket_name, "error": f"list failed: {e}"})

    return {
        "bucket": bucket_name,
        "prefix": base_prefix,
        "ingested": ingested,
        "skipped": skipped[:200],
        "errors": errors,
        "total_ingested": len(ingested),
    }
