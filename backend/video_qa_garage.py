"""Garage (S3-compatible) storage for Video Q&A files."""

from __future__ import annotations

import os
from typing import Any, Iterator

_GARAGE_ENV_KEYS = (
    "GARAGE_S3_ENDPOINT",
    "VIDEO_QA_GARAGE_ENDPOINT",
    "S3_ENDPOINT_URL",
)


def garage_object_key(item_id: str) -> str:
    return f"videos/{item_id}.mp4"


def _endpoint() -> str:
    for key in _GARAGE_ENV_KEYS:
        v = (os.environ.get(key) or "").strip()
        if v:
            return v.rstrip("/")
    return ""


def _bucket() -> str:
    for env_key in ("VIDEO_QA_S3_BUCKET", "GARAGE_S3_BUCKET", "S3_BUCKET"):
        v = (os.environ.get(env_key) or "").strip()
        if v:
            return v
    return "video-qa"


def _region() -> str:
    return (
        (os.environ.get("AWS_REGION") or "").strip()
        or (os.environ.get("AWS_DEFAULT_REGION") or "").strip()
        or "garage"
    )


def is_configured() -> bool:
    if not _endpoint() or not _bucket():
        return False
    if os.environ.get("S3_USE_DEFAULT_CREDENTIAL_CHAIN", "").strip().lower() in ("1", "true", "yes"):
        return True
    return bool(
        (os.environ.get("AWS_ACCESS_KEY_ID") or "").strip()
        and (os.environ.get("AWS_SECRET_ACCESS_KEY") or "").strip()
    )


def public_status() -> dict[str, Any]:
    out: dict[str, Any] = {
        "configured": is_configured(),
        "endpoint": _endpoint(),
        "bucket": _bucket(),
        "region": _region(),
        "reachable": False,
        "error": None,
    }
    if not is_configured():
        return out
    try:
        client = _client()
        client.head_bucket(Bucket=_bucket())
        out["reachable"] = True
    except Exception as e:
        out["error"] = str(e)
    return out


def _client():
    import boto3
    from botocore.config import Config

    kwargs: dict[str, Any] = {
        "service_name": "s3",
        "region_name": _region(),
        "endpoint_url": _endpoint(),
        "config": Config(signature_version="s3v4", s3={"addressing_style": "path"}),
    }
    if os.environ.get("S3_USE_DEFAULT_CREDENTIAL_CHAIN", "").strip().lower() not in ("1", "true", "yes"):
        kwargs["aws_access_key_id"] = (os.environ.get("AWS_ACCESS_KEY_ID") or "").strip() or None
        kwargs["aws_secret_access_key"] = (os.environ.get("AWS_SECRET_ACCESS_KEY") or "").strip() or None
        token = (os.environ.get("AWS_SESSION_TOKEN") or "").strip()
        if token:
            kwargs["aws_session_token"] = token
    return boto3.client(**kwargs)


def upload_object(*, key: str, data: bytes, content_type: str) -> None:
    if not is_configured():
        raise RuntimeError("Garage S3 is not configured")
    key = key.lstrip("/")
    client = _client()
    client.put_object(
        Bucket=_bucket(),
        Key=key,
        Body=data,
        ContentType=content_type or "video/mp4",
    )


def delete_object(key: str) -> None:
    if not is_configured():
        raise RuntimeError("Garage S3 is not configured")
    key = key.lstrip("/")
    if not key:
        raise ValueError("object key is required")
    client = _client()
    client.delete_object(Bucket=_bucket(), Key=key)


def delete_item_videos(*, item_id: str, garage_object_key_value: str | None = None) -> list[str]:
    """Delete all Garage objects associated with a video Q&A item."""
    if not is_configured():
        return []
    keys: list[str] = []
    if garage_object_key_value:
        k = str(garage_object_key_value).strip().lstrip("/")
        if k:
            keys.append(k)
    if item_id:
        default = garage_object_key(item_id)
        if default not in keys:
            keys.append(default)
    deleted: list[str] = []
    errors: list[str] = []
    for key in keys:
        try:
            delete_object(key)
            deleted.append(key)
        except Exception as e:
            errors.append(f"{key}: {e}")
    if errors and not deleted:
        raise RuntimeError("Garage delete failed: " + "; ".join(errors))
    return deleted


def object_exists(key: str) -> bool:
    if not is_configured():
        return False
    key = key.lstrip("/")
    try:
        _client().head_object(Bucket=_bucket(), Key=key)
        return True
    except Exception:
        return False


def head_object(key: str) -> dict[str, Any] | None:
    if not is_configured():
        return None
    key = key.lstrip("/")
    try:
        meta = _client().head_object(Bucket=_bucket(), Key=key)
        return {
            "content_type": str(meta.get("ContentType") or "video/mp4"),
            "size_bytes": int(meta.get("ContentLength") or 0),
        }
    except Exception:
        return None


def iter_object_bytes(*, key: str, start: int = 0, end: int | None = None) -> tuple[Iterator[bytes], str, int]:
    if not is_configured():
        raise RuntimeError("Garage S3 is not configured")
    key = key.lstrip("/")
    client = _client()
    head = head_object(key)
    if not head:
        raise FileNotFoundError(key)
    total = head["size_bytes"]
    content_type = head["content_type"]
    if end is None or end >= total:
        end = total - 1
    if total <= 0:
        raise FileNotFoundError(key)
    start = max(0, min(start, total - 1))
    end = max(start, min(end, total - 1))
    range_header = f"bytes={start}-{end}"
    resp = client.get_object(Bucket=_bucket(), Key=key, Range=range_header)
    body = resp["Body"]

    def _gen() -> Iterator[bytes]:
        try:
            while True:
                chunk = body.read(1024 * 1024)
                if not chunk:
                    break
                yield chunk
        finally:
            body.close()

    return _gen(), content_type, total


def read_object_bytes(key: str) -> tuple[bytes, str]:
    chunks, content_type, _ = iter_object_bytes(key=key)
    return b"".join(chunks), content_type
