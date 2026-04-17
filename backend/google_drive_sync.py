"""
Google Drive → Chroma RAG via Drive API v3 (service account).

Create a Google Cloud project, enable Drive API, create a service account, download JSON key.
Share your Drive folder with the service account email (Viewer is enough). Set folder ID from the URL.

Env:
  GOOGLE_DRIVE_CREDENTIALS_PATH  Path to service account JSON (e.g. backend/secrets/gdrive-sa.json)
  GOOGLE_DRIVE_FOLDER_ID         Folder ID (from https://drive.google.com/drive/folders/FOLDER_ID)
  GOOGLE_DRIVE_MAX_FILES, GOOGLE_DRIVE_MAX_DEPTH, GOOGLE_DRIVE_MAX_BYTES (optional)
"""

from __future__ import annotations

import io
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


def _credentials_path() -> str | None:
    raw = os.environ.get("GOOGLE_DRIVE_CREDENTIALS_PATH", "").strip()
    if not raw:
        return None
    p = os.path.abspath(os.path.expanduser(raw))
    return p if os.path.isfile(p) else None


def is_configured() -> bool:
    fid = os.environ.get("GOOGLE_DRIVE_FOLDER_ID", "").strip()
    return bool(_credentials_path() and fid)


def public_config() -> dict[str, Any]:
    fid = os.environ.get("GOOGLE_DRIVE_FOLDER_ID", "").strip()
    masked = ""
    if len(fid) > 8:
        masked = f"…{fid[-6:]}"
    elif fid:
        masked = "(set)"
    live_env = os.environ.get("GOOGLE_DRIVE_LIVE_SYNC", "1").strip().lower() in (
        "1",
        "true",
        "yes",
    )
    interval = max(30, _env_int("GOOGLE_DRIVE_SYNC_INTERVAL_SEC", 90))
    return {
        "configured": is_configured(),
        "folder_id_hint": masked,
        "max_files": _env_int("GOOGLE_DRIVE_MAX_FILES", 80),
        "max_depth": _env_int("GOOGLE_DRIVE_MAX_DEPTH", 6),
        "max_bytes_per_file": _env_int("GOOGLE_DRIVE_MAX_BYTES", 25 * 1024 * 1024),
        "live_sync_enabled": live_env,
        "sync_interval_sec": interval,
        "live": dict(_live),
    }


def _allowed_suffixes() -> set[str]:
    raw = os.environ.get("GOOGLE_DRIVE_ALLOWED_SUFFIXES", ".pdf,.docx,.txt,.md").strip()
    return {s.strip().lower() for s in raw.split(",") if s.strip()}


def _build_drive_service():
    from google.oauth2 import service_account
    from googleapiclient.discovery import build

    path = _credentials_path()
    if not path:
        raise RuntimeError("GOOGLE_DRIVE_CREDENTIALS_PATH must point to a readable service account JSON file.")
    scopes = ("https://www.googleapis.com/auth/drive.readonly",)
    creds = service_account.Credentials.from_service_account_file(path, scopes=scopes)
    return build("drive", "v3", credentials=creds, cache_discovery=False)


def _list_children(service: Any, parent_id: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    page_token: str | None = None
    q = f"'{parent_id}' in parents and trashed = false"
    while True:
        resp = (
            service.files()
            .list(
                q=q,
                spaces="drive",
                fields="nextPageToken, files(id, name, mimeType, size)",
                pageToken=page_token,
                pageSize=100,
                supportsAllDrives=True,
                includeItemsFromAllDrives=True,
            )
            .execute()
        )
        out.extend(resp.get("files") or [])
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return out


def _download_file_bytes(service: Any, file_id: str) -> bytes:
    from googleapiclient.http import MediaIoBaseDownload

    request = service.files().get_media(fileId=file_id)
    fh = io.BytesIO()
    downloader = MediaIoBaseDownload(fh, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    return fh.getvalue()


def _is_google_native_doc(mime: str) -> bool:
    return mime.startswith("application/vnd.google-apps.") and mime != "application/vnd.google-apps.folder"


def sync_to_chroma(
    *,
    chunk_size: int,
    chunk_overlap: int,
) -> dict[str, Any]:
    if not is_configured():
        raise RuntimeError(
            "Google Drive is not configured. Set GOOGLE_DRIVE_CREDENTIALS_PATH and GOOGLE_DRIVE_FOLDER_ID."
        )

    folder_id = os.environ["GOOGLE_DRIVE_FOLDER_ID"].strip()
    max_files = _env_int("GOOGLE_DRIVE_MAX_FILES", 80)
    max_depth = _env_int("GOOGLE_DRIVE_MAX_DEPTH", 6)
    max_bytes = _env_int("GOOGLE_DRIVE_MAX_BYTES", 25 * 1024 * 1024)
    allowed = _allowed_suffixes()

    service = _build_drive_service()

    ingested: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    errors: list[dict[str, str]] = []
    seen = 0

    def walk(fid: str, rel: str, depth: int) -> None:
        nonlocal seen
        if seen >= max_files or depth > max_depth:
            return
        try:
            items = _list_children(service, fid)
        except Exception as e:
            errors.append({"name": rel or folder_id, "error": f"list failed: {e}"})
            return
        for it in items:
            if seen >= max_files:
                return
            name = it.get("name") or "file"
            iid = it.get("id")
            mime = (it.get("mimeType") or "").lower()
            rid = f"{rel}/{name}" if rel else name
            if not iid:
                continue
            if mime == "application/vnd.google-apps.folder":
                walk(iid, rid, depth + 1)
                continue
            if _is_google_native_doc(mime):
                skipped.append({"name": rid, "reason": "Google Docs/Sheets (export not enabled); use PDF/DOCX/TXT"})
                continue
            suf = ""
            if "." in name:
                suf = "." + name.rsplit(".", 1)[-1].lower()
            if suf not in allowed:
                skipped.append({"name": rid, "reason": "extension not allowed"})
                continue
            size = int(it.get("size") or 0)
            if size and size > max_bytes:
                skipped.append({"name": rid, "reason": f"too large ({size} bytes)"})
                continue
            try:
                data = _download_file_bytes(service, iid)
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

    walk(folder_id, "", 0)

    return {
        "folder_id": folder_id,
        "ingested": ingested,
        "skipped": skipped[:200],
        "errors": errors,
        "total_ingested": len(ingested),
    }
