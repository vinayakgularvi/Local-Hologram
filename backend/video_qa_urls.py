"""Public playback URLs for Video Q&A entries (CDN or API fallback)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any


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
    When VIDEO_QA_CDN_BASE is set, return {base}/{filename}.
    Otherwise /api/video-qa/{id}/video (proxied Garage/local stream).
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
