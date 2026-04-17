"""
Microsoft SharePoint → Chroma RAG via Microsoft Graph (application permissions).

Requires Azure AD app registration with admin-consented application permissions, e.g.:
  Sites.Read.All  (or Sites.Selected with configured access)

Env:
  AZURE_TENANT_ID, AZURE_CLIENT_ID, AZURE_CLIENT_SECRET
  SHAREPOINT_SITE_URL  (e.g. https://contoso.sharepoint.com/sites/MySite)
  SHAREPOINT_FOLDER_PATH  (optional, e.g. "Shared Documents/Handbook" — relative to library root)
  SHAREPOINT_MAX_FILES, SHAREPOINT_MAX_DEPTH, SHAREPOINT_MAX_BYTES (optional)
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote, urlparse

import httpx

from rag_store import ingest_file

GRAPH = "https://graph.microsoft.com/v1.0"

# Last live / manual sync (for GET /api/sharepoint/config)
_live: dict[str, Any] = {
    "sync_running": False,
    "last_sync_started": None,
    "last_sync_finished": None,
    "last_ok": None,
    "last_error": None,
    "last_total_ingested": None,
    "last_errors_count": None,
}


def live_sync_mark_start() -> None:
    _live["sync_running"] = True
    _live["last_sync_started"] = datetime.now(timezone.utc).isoformat()


def live_sync_mark_done(result: dict[str, Any] | None, error: str | None) -> None:
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


def _env_int(key: str, default: int) -> int:
    v = os.environ.get(key, "").strip()
    if not v:
        return default
    try:
        return int(float(v))
    except ValueError:
        return default


def is_configured() -> bool:
    return bool(
        os.environ.get("AZURE_TENANT_ID", "").strip()
        and os.environ.get("AZURE_CLIENT_ID", "").strip()
        and os.environ.get("AZURE_CLIENT_SECRET", "").strip()
        and os.environ.get("SHAREPOINT_SITE_URL", "").strip()
    )


def public_config() -> dict[str, Any]:
    url = os.environ.get("SHAREPOINT_SITE_URL", "").strip()
    host = ""
    if url:
        try:
            host = urlparse(url).netloc or url
        except Exception:
            host = url
    live_env = os.environ.get("SHAREPOINT_LIVE_SYNC", "1").strip().lower() in (
        "1",
        "true",
        "yes",
    )
    interval = max(30, _env_int("SHAREPOINT_SYNC_INTERVAL_SEC", 90))
    return {
        "configured": is_configured(),
        "site_host": host,
        "folder_path": os.environ.get("SHAREPOINT_FOLDER_PATH", "").strip(),
        "max_files": _env_int("SHAREPOINT_MAX_FILES", 80),
        "max_depth": _env_int("SHAREPOINT_MAX_DEPTH", 6),
        "max_bytes_per_file": _env_int("SHAREPOINT_MAX_BYTES", 25 * 1024 * 1024),
        "live_sync_enabled": live_env,
        "sync_interval_sec": interval,
        "live": dict(_live),
    }


def _acquire_token() -> str:
    import msal

    tenant = os.environ["AZURE_TENANT_ID"].strip()
    client_id = os.environ["AZURE_CLIENT_ID"].strip()
    secret = os.environ["AZURE_CLIENT_SECRET"].strip()
    app = msal.ConfidentialClientApplication(
        client_id,
        authority=f"https://login.microsoftonline.com/{tenant}",
        client_credential=secret,
    )
    result = app.acquire_token_for_client(scopes=["https://graph.microsoft.com/.default"])
    if "access_token" not in result:
        err = result.get("error_description") or result.get("error") or str(result)
        raise RuntimeError(f"Azure AD token failed: {err}")
    return result["access_token"]


def _site_id_from_url(site_url: str) -> str:
    u = urlparse(site_url.strip())
    if not u.scheme or not u.netloc:
        raise ValueError("SHAREPOINT_SITE_URL must be a full URL (https://tenant.sharepoint.com/sites/...)")
    host = u.netloc
    raw = (u.path or "/").strip()
    if not raw or raw == "/":
        path = "/"
    else:
        segs = [quote(s, safe="") for s in raw.strip("/").split("/")]
        path = "/" + "/".join(segs)
    # Graph: {hostname}:/{server-relative-path}
    resource = f"{host}:{path}"
    url = f"{GRAPH}/sites/{resource}"
    token = _acquire_token()
    r = httpx.get(
        url,
        headers={"Authorization": f"Bearer {token}"},
        timeout=60.0,
    )
    if r.status_code >= 400:
        raise RuntimeError(f"Graph sites resolve failed ({r.status_code}): {r.text[:500]}")
    data = r.json()
    sid = data.get("id")
    if not sid:
        raise RuntimeError("Graph returned no site id")
    return str(sid)


def _drive_id(site_id: str, token: str, preferred: str | None) -> str:
    if preferred and preferred.strip():
        return preferred.strip()
    r = httpx.get(
        f"{GRAPH}/sites/{site_id}/drive",
        headers={"Authorization": f"Bearer {token}"},
        timeout=60.0,
    )
    if r.status_code >= 400:
        raise RuntimeError(f"Graph default drive failed ({r.status_code}): {r.text[:500]}")
    data = r.json()
    did = data.get("id")
    if not did:
        raise RuntimeError("Graph returned no drive id")
    return str(did)


def _allowed_suffixes() -> set[str]:
    raw = os.environ.get("SHAREPOINT_ALLOWED_SUFFIXES", ".pdf,.docx,.txt,.md").strip()
    return {s.strip().lower() for s in raw.split(",") if s.strip()}


def _download_item(token: str, drive_id: str, item_id: str) -> bytes:
    url = f"{GRAPH}/drives/{drive_id}/items/{item_id}/content"
    r = httpx.get(
        url,
        headers={"Authorization": f"Bearer {token}"},
        follow_redirects=True,
        timeout=120.0,
    )
    if r.status_code >= 400:
        raise RuntimeError(f"Download failed ({r.status_code}): {r.text[:300]}")
    return r.content


def _list_children(
    token: str,
    drive_id: str,
    *,
    item_id: str | None,
    next_url: str | None = None,
) -> tuple[list[dict[str, Any]], str | None]:
    if next_url:
        url = next_url
    elif item_id is None:
        url = f"{GRAPH}/drives/{drive_id}/root/children?$top=200"
    else:
        url = f"{GRAPH}/drives/{drive_id}/items/{item_id}/children?$top=200"
    r = httpx.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=120.0)
    if r.status_code >= 400:
        raise RuntimeError(f"List children failed ({r.status_code}): {r.text[:400]}")
    data = r.json()
    items = data.get("value") or []
    nxt = data.get("@odata.nextLink")
    return items, nxt


def _resolve_folder_start_item(
    token: str,
    drive_id: str,
    folder_path: str,
) -> str | None:
    """Return drive item id for folder path, or None for library root. Path is relative to default library root."""
    fp = folder_path.strip().strip("/").strip()
    if not fp:
        return None
    parts = [p for p in fp.replace("\\", "/").split("/") if p]
    enc_path = "/".join(quote(p, safe="") for p in parts)
    url = f"{GRAPH}/drives/{drive_id}/root:/{enc_path}"
    r = httpx.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=60.0)
    if r.status_code >= 400:
        raise RuntimeError(f"Folder not found: {fp} ({r.status_code}): {r.text[:400]}")
    data = r.json() or {}
    if "folder" not in data:
        raise RuntimeError(f"Path is not a folder: {fp}")
    fid = data.get("id")
    if not fid:
        raise RuntimeError("Graph returned no folder id")
    return str(fid)


def sync_to_chroma(
    *,
    chunk_size: int,
    chunk_overlap: int,
) -> dict[str, Any]:
    """
    Walk SharePoint folder (recursive), download supported files, ingest into Chroma.
    """
    if not is_configured():
        raise RuntimeError("SharePoint / Azure is not configured (see SHAREPOINT_SITE_URL and AZURE_* env).")

    max_files = _env_int("SHAREPOINT_MAX_FILES", 80)
    max_depth = _env_int("SHAREPOINT_MAX_DEPTH", 6)
    max_bytes = _env_int("SHAREPOINT_MAX_BYTES", 25 * 1024 * 1024)
    allowed = _allowed_suffixes()
    site_url = os.environ["SHAREPOINT_SITE_URL"].strip()
    folder_path = os.environ.get("SHAREPOINT_FOLDER_PATH", "").strip()
    drive_override = os.environ.get("SHAREPOINT_DRIVE_ID", "").strip() or None

    token = _acquire_token()
    site_id = _site_id_from_url(site_url)
    drive_id = _drive_id(site_id, token, drive_override)

    start_folder_id = _resolve_folder_start_item(token, drive_id, folder_path)
    start_rel = folder_path.strip().strip("/") if folder_path else ""

    ingested: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    errors: list[dict[str, str]] = []
    seen = 0

    def walk(item_id: str | None, rel: str, depth: int) -> None:
        nonlocal seen
        if seen >= max_files:
            return
        if depth > max_depth:
            return

        next_link: str | None = None
        while True:
            items, next_link = _list_children(token, drive_id, item_id=item_id, next_url=next_link)
            for it in items:
                if seen >= max_files:
                    return
                name = it.get("name") or "file"
                rid = rel + "/" + name if rel else name
                if "folder" in it:
                    walk(it.get("id"), rid, depth + 1)
                    continue
                if "file" not in it:
                    continue
                suf = ""
                if "." in name:
                    suf = "." + name.rsplit(".", 1)[-1].lower()
                if suf not in allowed:
                    skipped.append({"name": rid, "reason": "extension not allowed"})
                    continue
                size = int(it.get("size") or 0)
                if size > max_bytes:
                    skipped.append({"name": rid, "reason": f"too large ({size} bytes)"})
                    continue
                iid = it.get("id")
                if not iid:
                    continue
                try:
                    data = _download_item(token, drive_id, str(iid))
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

            if not next_link:
                break

    if start_folder_id is None:
        walk(None, "", 0)
    else:
        walk(start_folder_id, start_rel, 0)

    return {
        "site_id": site_id,
        "drive_id": drive_id,
        "start_folder": folder_path or "(library root)",
        "ingested": ingested,
        "skipped": skipped[:200],
        "errors": errors,
        "total_ingested": len(ingested),
    }
