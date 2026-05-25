"""VOD / CDN availability checks for Video Q&A (playback on stream CDN)."""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

_DEFAULT_VOD_STATUS_BASE = "http://10.29.145.124:8090/vod/status"
_FETCH_TIMEOUT_SEC = 8.0
_BATCH_MAX = 100
_BATCH_WORKERS = 8


def vod_status_base() -> str:
    for key in ("VIDEO_QA_VOD_STATUS_BASE", "VIDEO_QA_VOD_STATUS_URL"):
        v = (os.environ.get(key) or "").strip().rstrip("/")
        if v:
            return v
    return _DEFAULT_VOD_STATUS_BASE


def vod_status_configured() -> bool:
    return bool(vod_status_base())


def _normalize_video_name(video_name: str) -> str:
    return Path((video_name or "").strip()).name


def vod_status_url(video_name: str) -> str:
    name = _normalize_video_name(video_name)
    if not name:
        return ""
    base = vod_status_base()
    return f"{base}/{quote(name, safe='')}"


def fetch_vod_status(video_name: str, *, timeout: float = _FETCH_TIMEOUT_SEC) -> dict[str, Any]:
    """
    GET {VIDEO_QA_VOD_STATUS_BASE}/{filename}
    e.g. http://10.29.145.124:8090/vod/status/answer_<id>_<ts>.mp4
    """
    name = _normalize_video_name(video_name)
    if not name:
        return {
            "video_name": "",
            "configured": vod_status_configured(),
            "available": False,
            "error": "empty video_name",
        }

    url = vod_status_url(name)
    if not url:
        return {
            "video_name": name,
            "configured": False,
            "available": False,
            "error": "VIDEO_QA_VOD_STATUS_BASE not set",
        }

    try:
        with httpx.Client(timeout=timeout) as client:
            res = client.get(url)
            res.raise_for_status()
            data = res.json()
    except httpx.HTTPStatusError as e:
        return {
            "video_name": name,
            "configured": True,
            "status_url": url,
            "available": False,
            "http_status": e.response.status_code,
            "error": str(e),
        }
    except Exception as e:
        return {
            "video_name": name,
            "configured": True,
            "status_url": url,
            "available": False,
            "error": str(e),
        }

    if not isinstance(data, dict):
        return {
            "video_name": name,
            "configured": True,
            "status_url": url,
            "available": False,
            "error": "invalid JSON response",
        }

    out: dict[str, Any] = {
        "configured": True,
        "status_url": url,
        "video_name": str(data.get("video_name") or name),
        "status": str(data.get("status") or ""),
        "available": bool(data.get("available")),
        "file_exists": bool(data.get("file_exists")),
        "http_available": bool(data.get("http_available")),
        "file_size_bytes": int(data.get("file_size_bytes") or 0),
        "vod_url": str(data.get("vod_url") or "").strip(),
        "error": str(data.get("error") or "").strip(),
    }
    if not out["vod_url"] and out["available"]:
        from video_qa_urls import public_video_url

        out["vod_url"] = public_video_url({"id": "", "filename": name})
    return out


def batch_vod_status(
    filenames: list[str],
    *,
    max_items: int = _BATCH_MAX,
) -> dict[str, dict[str, Any]]:
    unique: list[str] = []
    seen: set[str] = set()
    for raw in filenames:
        name = _normalize_video_name(str(raw))
        if not name or name in seen:
            continue
        seen.add(name)
        unique.append(name)
        if len(unique) >= max_items:
            break

    if not unique:
        return {}

    results: dict[str, dict[str, Any]] = {}
    workers = min(_BATCH_WORKERS, len(unique))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(fetch_vod_status, name): name for name in unique}
        for fut in as_completed(futures):
            name = futures[fut]
            try:
                results[name] = fut.result()
            except Exception as e:
                results[name] = {
                    "video_name": name,
                    "configured": vod_status_configured(),
                    "available": False,
                    "error": str(e),
                }
    return results


def public_status() -> dict[str, Any]:
    base = vod_status_base()
    return {
        "configured": bool(base),
        "status_base": base,
        "example_status_url": f"{base}/answer_example.mp4" if base else "",
    }
