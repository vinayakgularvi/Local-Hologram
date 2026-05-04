"""
Amazon S3 → Chroma RAG (list + get_object under a prefix).

Env:
  S3_BUCKET                    Required bucket name
  S3_PREFIX                    Optional key prefix (e.g. docs/handbook/)
  AWS_REGION / AWS_DEFAULT_REGION   Default us-east-1
  AWS_ACCESS_KEY_ID + AWS_SECRET_ACCESS_KEY   Or set S3_USE_DEFAULT_CREDENTIAL_CHAIN=1 for IAM / profile
  S3_MAX_FILES, S3_MAX_DEPTH, S3_MAX_BYTES, S3_ALLOWED_SUFFIXES
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
    bucket = os.environ.get("S3_BUCKET", "").strip()
    if not bucket:
        return False
    if os.environ.get("S3_USE_DEFAULT_CREDENTIAL_CHAIN", "").strip().lower() in ("1", "true", "yes"):
        return True
    return bool(
        os.environ.get("AWS_ACCESS_KEY_ID", "").strip() and os.environ.get("AWS_SECRET_ACCESS_KEY", "").strip()
    )


def public_config() -> dict[str, Any]:
    bucket = os.environ.get("S3_BUCKET", "").strip()
    hint = ""
    if len(bucket) > 16:
        hint = f"{bucket[:8]}…"
    elif bucket:
        hint = bucket[:12] + ("…" if len(bucket) > 12 else "")
    live_env = os.environ.get("S3_LIVE_SYNC", "1").strip().lower() in ("1", "true", "yes")
    try:
        from studio_live_sync import live_sync_flags_for_connector

        ls = live_sync_flags_for_connector(live_env)
    except Exception:
        ls = {"live_sync_env_enabled": live_env, "live_sync_master_enabled": True, "live_sync_enabled": live_env}
    interval = max(30, _env_int("S3_SYNC_INTERVAL_SEC", 90))
    auth = "default_chain" if os.environ.get("S3_USE_DEFAULT_CREDENTIAL_CHAIN", "").strip().lower() in (
        "1",
        "true",
        "yes",
    ) else ("access_key" if os.environ.get("AWS_ACCESS_KEY_ID", "").strip() else "none")
    return {
        "configured": is_configured(),
        "bucket_hint": hint or "—",
        "prefix": _normalize_prefix(os.environ.get("S3_PREFIX", "")),
        "max_files": _env_int("S3_MAX_FILES", 80),
        "max_depth": _env_int("S3_MAX_DEPTH", 8),
        "max_bytes_per_file": _env_int("S3_MAX_BYTES", 25 * 1024 * 1024),
        **ls,
        "sync_interval_sec": interval,
        "auth_mode": auth if is_configured() else "none",
        "live": dict(_live),
    }


def _allowed_suffixes() -> set[str]:
    raw = os.environ.get("S3_ALLOWED_SUFFIXES", ".pdf,.docx,.txt,.md").strip()
    return {s.strip().lower() for s in raw.split(",") if s.strip()}


def _s3_client():
    import boto3

    region = (
        os.environ.get("AWS_REGION", "").strip()
        or os.environ.get("AWS_DEFAULT_REGION", "").strip()
        or "us-east-1"
    )
    if os.environ.get("S3_USE_DEFAULT_CREDENTIAL_CHAIN", "").strip().lower() in ("1", "true", "yes"):
        return boto3.client("s3", region_name=region)
    return boto3.client(
        "s3",
        region_name=region,
        aws_access_key_id=os.environ.get("AWS_ACCESS_KEY_ID", "").strip() or None,
        aws_secret_access_key=os.environ.get("AWS_SECRET_ACCESS_KEY", "").strip() or None,
        aws_session_token=os.environ.get("AWS_SESSION_TOKEN", "").strip() or None,
    )


def sync_to_chroma(
    *,
    chunk_size: int,
    chunk_overlap: int,
) -> dict[str, Any]:
    if not is_configured():
        raise RuntimeError(
            "S3 is not configured. Set S3_BUCKET and AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY, "
            "or S3_USE_DEFAULT_CREDENTIAL_CHAIN=1 with ambient credentials."
        )
    bucket = os.environ["S3_BUCKET"].strip()
    base_prefix = _normalize_prefix(os.environ.get("S3_PREFIX", ""))
    max_files = _env_int("S3_MAX_FILES", 80)
    max_depth = _env_int("S3_MAX_DEPTH", 8)
    max_bytes = _env_int("S3_MAX_BYTES", 25 * 1024 * 1024)
    allowed = _allowed_suffixes()

    client = _s3_client()
    ingested: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    errors: list[dict[str, str]] = []
    seen = 0

    def walk(prefix: str, rel: str, depth: int) -> None:
        nonlocal seen
        if seen >= max_files or depth > max_depth:
            return
        paginator = client.get_paginator("list_objects_v2")
        try:
            pages = paginator.paginate(Bucket=bucket, Prefix=prefix, Delimiter="/", PaginationConfig={"PageSize": 500})
            for page in pages:
                if seen >= max_files:
                    return
                for cp in page.get("CommonPrefixes") or ():
                    np = cp.get("Prefix") or ""
                    if not np:
                        continue
                    name = np.rstrip("/").split("/")[-1] or np
                    walk(np, f"{rel}/{name}" if rel else name, depth + 1)
                for obj in page.get("Contents") or ():
                    if seen >= max_files:
                        return
                    key = obj.get("Key") or ""
                    if not key or key.endswith("/"):
                        continue
                    name = key.split("/")[-1]
                    rid = f"{rel}/{name}" if rel else key
                    suf = ""
                    if "." in name:
                        suf = "." + name.rsplit(".", 1)[-1].lower()
                    if suf not in allowed:
                        skipped.append({"name": rid, "reason": "extension not allowed"})
                        continue
                    sz = int(obj.get("Size") or 0)
                    if sz and sz > max_bytes:
                        skipped.append({"name": rid, "reason": f"too large ({sz} bytes)"})
                        continue
                    try:
                        body = client.get_object(Bucket=bucket, Key=key)["Body"].read()
                        if len(body) > max_bytes:
                            skipped.append({"name": rid, "reason": "downloaded size over limit"})
                            continue
                        info = ingest_file(
                            filename=name or key,
                            data=body,
                            chunk_size=chunk_size,
                            chunk_overlap=chunk_overlap,
                        )
                        ingested.append({"path": rid, **info})
                        seen += 1
                    except Exception as e:
                        errors.append({"name": rid, "error": str(e)})
        except Exception as e:
            errors.append({"name": rel or prefix, "error": f"list failed: {e}"})

    walk(base_prefix, "", 0)

    return {
        "bucket": bucket,
        "prefix": base_prefix,
        "ingested": ingested,
        "skipped": skipped[:200],
        "errors": errors,
        "total_ingested": len(ingested),
    }
