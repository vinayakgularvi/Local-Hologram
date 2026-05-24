"""Terminal trace lines for debugging voice / Video Q&A / export pipelines."""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone


def trace_enabled() -> bool:
    raw = (os.environ.get("HOLOGRAM_TRACE") or "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def trace(msg: str) -> None:
    """Print a timestamped line to stderr (visible in uvicorn terminal)."""
    if not trace_enabled():
        return
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    print(f"[hologram {ts}] {msg}", file=sys.stderr, flush=True)
