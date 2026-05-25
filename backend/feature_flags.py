"""Feature toggles — set true in .env to re-enable Video RAG and offline exports."""

from __future__ import annotations

import os


def _env_bool(name: str, *, default: bool = False) -> bool:
    raw = (os.environ.get(name) or "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


def video_rag_enabled() -> bool:
    """Live hologram Video RAG (post-answer Qdrant match + cached CDN playback)."""
    return _env_bool("VIDEO_RAG_ENABLED", default=False)


def offline_exports_enabled() -> bool:
    """LiveTalking offline lip-sync export → Qdrant ingest."""
    return _env_bool("OFFLINE_EXPORTS", default=False)


def mongodb_export_trigger_enabled() -> bool:
    """MongoDB insert → queue offline export (requires OFFLINE_EXPORTS=true)."""
    return _env_bool("MONGODB_EXPORT_TRIGGER", default=False)


def public_feature_flags() -> dict[str, bool]:
    return {
        "video_rag_enabled": video_rag_enabled(),
        "offline_exports_enabled": offline_exports_enabled(),
        "mongodb_export_trigger_enabled": mongodb_export_trigger_enabled(),
    }
