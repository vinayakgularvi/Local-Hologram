"""
Dropbox → Chroma RAG via Dropbox API (files.list_folder + files.download).

Auth (pick one):
  DROPBOX_ACCESS_TOKEN — short-lived user token from OAuth, or
  DROPBOX_REFRESH_TOKEN + DROPBOX_APP_KEY + DROPBOX_APP_SECRET — recommended for servers
    (generate refresh token in the Dropbox App Console with scopes: files.content.read).

Env:
  DROPBOX_FOLDER_PATH  Optional. Dropbox path to sync (default: root). Example: /Handbook
  DROPBOX_MAX_FILES, DROPBOX_MAX_DEPTH, DROPBOX_MAX_BYTES, DROPBOX_ALLOWED_SUFFIXES
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


def _folder_path() -> str:
    """Normalized Dropbox folder path; '' = root."""
    raw = os.environ.get("DROPBOX_FOLDER_PATH", "").strip()
    if not raw or raw == "/":
        return ""
    if not raw.startswith("/"):
        return "/" + raw
    return raw


def _auth_mode() -> str:
    if os.environ.get("DROPBOX_ACCESS_TOKEN", "").strip():
        return "access_token"
    if (
        os.environ.get("DROPBOX_REFRESH_TOKEN", "").strip()
        and os.environ.get("DROPBOX_APP_KEY", "").strip()
        and os.environ.get("DROPBOX_APP_SECRET", "").strip()
    ):
        return "refresh_token"
    return "none"


def is_configured() -> bool:
    return _auth_mode() != "none"


def public_config() -> dict[str, Any]:
    fp = _folder_path()
    if not fp:
        folder_hint = "(root)"
    elif len(fp) > 24:
        folder_hint = f"…{fp[-20:]}"
    else:
        folder_hint = fp
    live_env = os.environ.get("DROPBOX_LIVE_SYNC", "1").strip().lower() in (
        "1",
        "true",
        "yes",
    )
    try:
        from studio_live_sync import live_sync_flags_for_connector

        ls = live_sync_flags_for_connector(live_env)
    except Exception:
        ls = {"live_sync_env_enabled": live_env, "live_sync_master_enabled": True, "live_sync_enabled": live_env}
    interval = max(30, _env_int("DROPBOX_SYNC_INTERVAL_SEC", 90))
    return {
        "configured": is_configured(),
        "auth_mode": _auth_mode(),
        "folder_path_hint": folder_hint,
        "max_files": _env_int("DROPBOX_MAX_FILES", 80),
        "max_depth": _env_int("DROPBOX_MAX_DEPTH", 6),
        "max_bytes_per_file": _env_int("DROPBOX_MAX_BYTES", 25 * 1024 * 1024),
        **ls,
        "sync_interval_sec": interval,
        "live": dict(_live),
    }


def _allowed_suffixes() -> set[str]:
    raw = os.environ.get("DROPBOX_ALLOWED_SUFFIXES", ".pdf,.docx,.txt,.md").strip()
    return {s.strip().lower() for s in raw.split(",") if s.strip()}


def _build_dbx():
    import dropbox

    token = os.environ.get("DROPBOX_ACCESS_TOKEN", "").strip()
    if token:
        return dropbox.Dropbox(token)
    rt = os.environ.get("DROPBOX_REFRESH_TOKEN", "").strip()
    key = os.environ.get("DROPBOX_APP_KEY", "").strip()
    secret = os.environ.get("DROPBOX_APP_SECRET", "").strip()
    if rt and key and secret:
        return dropbox.Dropbox(
            oauth2_refresh_token=rt,
            app_key=key,
            app_secret=secret,
        )
    raise RuntimeError(
        "Dropbox is not configured. Set DROPBOX_ACCESS_TOKEN or "
        "DROPBOX_REFRESH_TOKEN with DROPBOX_APP_KEY and DROPBOX_APP_SECRET."
    )


def _list_folder_entries(dbx: Any, path: str) -> list[Any]:
    import dropbox
    from dropbox.files import ListFolderError

    entries: list[Any] = []
    try:
        res = dbx.files_list_folder(path, recursive=False)
    except dropbox.exceptions.ApiError as e:
        if isinstance(e.error, ListFolderError) and e.error.is_path():
            pe = e.error.get_path()
            if pe.is_not_found() or pe.is_malformed_path() or pe.is_not_folder():
                raise RuntimeError(f"Dropbox path not found or not a folder: {path or '/'}") from e
        raise
    entries.extend(res.entries)
    while res.has_more:
        res = dbx.files_list_folder_continue(res.cursor)
        entries.extend(res.entries)
    return entries


def sync_to_chroma(
    *,
    chunk_size: int,
    chunk_overlap: int,
) -> dict[str, Any]:
    if not is_configured():
        raise RuntimeError(
            "Dropbox is not configured. Set DROPBOX_ACCESS_TOKEN or "
            "DROPBOX_REFRESH_TOKEN with DROPBOX_APP_KEY and DROPBOX_APP_SECRET."
        )

    max_files = _env_int("DROPBOX_MAX_FILES", 80)
    max_depth = _env_int("DROPBOX_MAX_DEPTH", 6)
    max_bytes = _env_int("DROPBOX_MAX_BYTES", 25 * 1024 * 1024)
    allowed = _allowed_suffixes()
    root = _folder_path()

    dbx = _build_dbx()

    ingested: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    errors: list[dict[str, str]] = []
    seen = 0

    from dropbox.files import FileMetadata, FolderMetadata

    def walk(folder_path: str, rel: str, depth: int) -> None:
        nonlocal seen
        if seen >= max_files or depth > max_depth:
            return
        try:
            entries = _list_folder_entries(dbx, folder_path)
        except RuntimeError as e:
            if folder_path == root:
                errors.append({"name": rel or "(root)", "error": str(e)})
            else:
                errors.append({"name": rel, "error": str(e)})
            return
        except Exception as e:
            errors.append({"name": rel or folder_path, "error": f"list failed: {e}"})
            return

        for entry in entries:
            if seen >= max_files:
                return
            name = entry.name or "item"
            rid = f"{rel}/{name}" if rel else name

            if isinstance(entry, FolderMetadata):
                walk(entry.path_lower, rid, depth + 1)
                continue
            if not isinstance(entry, FileMetadata):
                continue

            suf = ""
            if "." in name:
                suf = "." + name.rsplit(".", 1)[-1].lower()
            if suf not in allowed:
                skipped.append({"name": rid, "reason": "extension not allowed"})
                continue

            size = int(entry.size or 0)
            if size and size > max_bytes:
                skipped.append({"name": rid, "reason": f"too large ({size} bytes)"})
                continue

            try:
                _, resp = dbx.files_download(entry.path_lower)
                data = resp.content
                if len(data) > max_bytes:
                    skipped.append({"name": rid, "reason": "downloaded size over limit"})
                    continue
                info = ingest_file(
                    filename=name,
                    data=data,
                    chunk_size=chunk_size,
                    chunk_overlap=chunk_overlap,
                )
                ingested.append({"path": rid, **info})
                seen += 1
            except Exception as e:
                errors.append({"name": rid, "error": str(e)})

    walk(root, "", 0)

    return {
        "folder_path": root or "/",
        "ingested": ingested,
        "skipped": skipped[:200],
        "errors": errors,
        "total_ingested": len(ingested),
    }
