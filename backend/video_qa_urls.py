"""Public playback URLs for Video Q&A entries (CDN; Garage stream API fallback only)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import httpx

_CDN_FETCH_TIMEOUT_SEC = 120.0


def cdn_base() -> str:
    """e.g. http://10.29.145.124:5080/live/streams/video-qa"""
    for key in ("VIDEO_QA_CDN_BASE", "VIDEO_QA_STREAM_CDN_BASE"):
        v = (os.environ.get(key) or "").strip().rstrip("/")
        if v:
            return v
    return ""


def cdn_configured() -> bool:
    return bool(cdn_base())


def public_video_url(item: dict[str, Any]) -> str:
    """
    When VIDEO_QA_CDN_BASE is set, return {base}/{filename} (CDN playback).
    Otherwise /api/video-qa/{id}/video (Garage stream proxy; no local disk).
    """
    item_id = str(item.get("id") or "").strip()
    filename = str(item.get("filename") or "").strip()
    if not filename and item_id:
        filename = f"{item_id}.mp4"
    else:
        filename = Path(filename).name

    base = cdn_base()
    if base and filename:
        return f"{base}/{filename}"

    if item_id:
        return f"/api/video-qa/{item_id}/video"
    return ""


def with_public_video_url(item: dict[str, Any]) -> dict[str, Any]:
    out = dict(item)
    out["video_url"] = public_video_url(out)
    return out


def fetch_video_bytes(item: dict[str, Any], *, timeout: float = _CDN_FETCH_TIMEOUT_SEC) -> tuple[bytes, str]:
    """Download video bytes from CDN URL (for processing pipeline)."""
    url = public_video_url(item)
    if not url.startswith(("http://", "https://")):
        raise FileNotFoundError(
            "CDN URL not configured (set VIDEO_QA_CDN_BASE) or missing filename on item"
        )
    item_id = str(item.get("id") or "").strip()
    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        res = client.get(url)
        res.raise_for_status()
        ct = (res.headers.get("content-type") or item.get("content_type") or "video/mp4").split(";")[0]
        data = res.content
    if not data:
        raise FileNotFoundError(f"empty video from CDN for item {item_id or '?'}")
    return data, ct
