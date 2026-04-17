"""
Persist Avatar Studio connector settings to disk (local dev) and apply to os.environ.

Values override .env when present. File: backend/data/studio_integrations.json
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

_BACKEND_DIR = Path(__file__).resolve().parent
INTEGRATIONS_PATH = _BACKEND_DIR / "data" / "studio_integrations.json"
GDRIVE_CREDENTIALS_SAVED = _BACKEND_DIR / "data" / "studio_gdrive_credentials.json"
GCS_CREDENTIALS_SAVED = _BACKEND_DIR / "data" / "studio_gcs_credentials.json"


def _reload_dotenv() -> None:
    """Reload .env so cleared integration keys can fall back to file-based config."""
    from dotenv import load_dotenv

    root = _BACKEND_DIR.parent
    load_dotenv(root / ".env", override=True)
    load_dotenv(_BACKEND_DIR / ".env", override=True)


INTEGRATION_KEYS: dict[str, tuple[str, ...]] = {
    "sharepoint": (
        "AZURE_TENANT_ID",
        "AZURE_CLIENT_ID",
        "AZURE_CLIENT_SECRET",
        "SHAREPOINT_SITE_URL",
        "SHAREPOINT_FOLDER_PATH",
    ),
    "google_drive": (
        "GOOGLE_DRIVE_CREDENTIALS_PATH",
        "GOOGLE_DRIVE_FOLDER_ID",
    ),
    "dropbox": (
        "DROPBOX_ACCESS_TOKEN",
        "DROPBOX_REFRESH_TOKEN",
        "DROPBOX_APP_KEY",
        "DROPBOX_APP_SECRET",
        "DROPBOX_FOLDER_PATH",
    ),
    "s3": (
        "S3_BUCKET",
        "S3_PREFIX",
        "AWS_REGION",
        "AWS_DEFAULT_REGION",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_SESSION_TOKEN",
        "S3_USE_DEFAULT_CREDENTIAL_CHAIN",
    ),
    "azure_blob": (
        "AZURE_STORAGE_CONNECTION_STRING",
        "AZURE_STORAGE_ACCOUNT_NAME",
        "AZURE_STORAGE_ACCOUNT_KEY",
        "AZURE_BLOB_CONTAINER",
        "AZURE_BLOB_PREFIX",
    ),
    "gcs": (
        "GCS_BUCKET",
        "GCS_PREFIX",
        "GCS_CREDENTIALS_PATH",
        "GOOGLE_APPLICATION_CREDENTIALS",
        "GCS_USE_ADC",
    ),
    "llm": (
        "LLM_PROVIDER",
        "OLLAMA_BASE",
        "OLLAMA_MODEL",
        "MODEL_API_STYLE",
        "OPENAI_API_KEY",
        "OPENAI_BASE_URL",
        "OPENAI_MODEL",
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_BASE_URL",
        "ANTHROPIC_MODEL",
        "GOOGLE_API_KEY",
        "GOOGLE_GEMINI_BASE_URL",
        "GOOGLE_GEMINI_MODEL",
    ),
}


def _load_raw() -> dict[str, Any]:
    if not INTEGRATIONS_PATH.is_file():
        return {}
    try:
        data = json.loads(INTEGRATIONS_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def read_section_strings(section: str) -> dict[str, str]:
    """Return non-empty string values currently stored for a section (no secrets logged elsewhere)."""
    if section not in INTEGRATION_KEYS:
        raise ValueError(f"Unknown integration section: {section}")
    block = _load_raw().get(section)
    if not isinstance(block, dict):
        return {}
    out: dict[str, str] = {}
    for k in INTEGRATION_KEYS[section]:
        v = block.get(k)
        if v is None:
            continue
        s = str(v).strip()
        if s:
            out[k] = s
    return out


def apply_studio_integrations_to_environ() -> None:
    """Apply saved integration env vars (override .env)."""
    data = _load_raw()
    for section, keys in INTEGRATION_KEYS.items():
        block = data.get(section)
        if not isinstance(block, dict):
            continue
        for k in keys:
            v = block.get(k)
            if v is None:
                continue
            s = str(v).strip()
            if s:
                os.environ[k] = s


def save_section(section: str, values: dict[str, str | None]) -> None:
    """Merge keys into section and persist; empty strings remove a key."""
    if section not in INTEGRATION_KEYS:
        raise ValueError(f"Unknown integration section: {section}")
    data = _load_raw()
    if section not in data or not isinstance(data.get(section), dict):
        data[section] = {}
    block: dict[str, Any] = dict(data[section])
    allowed = set(INTEGRATION_KEYS[section])
    for k, v in values.items():
        if k not in allowed:
            continue
        if v is None:
            block.pop(k, None)
            continue
        s = str(v).strip()
        if s:
            block[k] = s
        else:
            block.pop(k, None)
    data[section] = block
    INTEGRATIONS_PATH.parent.mkdir(parents=True, exist_ok=True)
    INTEGRATIONS_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
    _reload_dotenv()
    apply_studio_integrations_to_environ()


def delete_section(section: str) -> None:
    if section not in INTEGRATION_KEYS:
        raise ValueError(f"Unknown integration section: {section}")
    data = _load_raw()
    if section in data:
        del data[section]
    if data:
        INTEGRATIONS_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
    elif INTEGRATIONS_PATH.is_file():
        INTEGRATIONS_PATH.unlink()
    if section == "google_drive" and GDRIVE_CREDENTIALS_SAVED.is_file():
        try:
            GDRIVE_CREDENTIALS_SAVED.unlink()
        except OSError:
            pass
    if section == "gcs" and GCS_CREDENTIALS_SAVED.is_file():
        try:
            GCS_CREDENTIALS_SAVED.unlink()
        except OSError:
            pass
    _reload_dotenv()
    apply_studio_integrations_to_environ()


def public_summary() -> dict[str, Any]:
    """Which sections have saved keys (masked)."""
    data = _load_raw()
    sections: dict[str, Any] = {}
    for name, keys in INTEGRATION_KEYS.items():
        block = data.get(name)
        if not isinstance(block, dict) or not block:
            sections[name] = {"saved_keys": [], "has_saved": False}
            continue
        saved = [k for k in keys if str(block.get(k, "")).strip()]
        sections[name] = {
            "has_saved": bool(saved),
            "saved_keys": saved,
            "masked": {k: "set" for k in saved},
        }
    return {
        "integrations_file": str(INTEGRATIONS_PATH.name),
        "file_exists": INTEGRATIONS_PATH.is_file(),
        "sections": sections,
    }
