"""
Global on/off for all cloud → Chroma background live sync loops (SharePoint, Drive, etc.).

Persists to backend/data/studio_live_sync.json. When master is off, loops idle but manual
POST /api/*/sync endpoints still work. Per-source env flags (e.g. SHAREPOINT_LIVE_SYNC) still apply.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

_BACKEND_DIR = Path(__file__).resolve().parent
STATE_PATH = _BACKEND_DIR / "data" / "studio_live_sync.json"
_lock = threading.Lock()


def is_master_enabled() -> bool:
    """When False, all studio integration background sync loops pause."""
    with _lock:
        if not STATE_PATH.is_file():
            return True
        try:
            raw = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except Exception:
            return True
        if not isinstance(raw, dict):
            return True
        v = raw.get("master_enabled", True)
        if isinstance(v, bool):
            return v
        return str(v).strip().lower() in ("1", "true", "yes", "on")


def set_master_enabled(enabled: bool) -> None:
    with _lock:
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        STATE_PATH.write_text(
            json.dumps({"master_enabled": bool(enabled)}, indent=2),
            encoding="utf-8",
        )


def live_sync_flags_for_connector(env_enabled: bool) -> dict[str, Any]:
    """UI + API: env gate, master gate, and effective live_sync_enabled."""
    m = is_master_enabled()
    e = bool(env_enabled)
    return {
        "live_sync_env_enabled": e,
        "live_sync_master_enabled": m,
        "live_sync_enabled": e and m,
    }
