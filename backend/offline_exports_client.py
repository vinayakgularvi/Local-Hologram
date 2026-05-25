"""Client for LiveTalking offline-exports API (lip-sync video → Qdrant on remote host)."""

from __future__ import annotations

import os
from typing import Any

import httpx

from feature_flags import offline_exports_enabled


def is_enabled() -> bool:
    return offline_exports_enabled() and bool(base_url())


def base_url() -> str:
    return (
        (os.environ.get("OFFLINE_EXPORTS_BASE_URL") or "").strip().rstrip("/")
        or (os.environ.get("WEBRTC_SIGNALING_BASE") or "").strip().rstrip("/")
    )


def profile_id() -> str:
    return (os.environ.get("OFFLINE_EXPORT_PROFILE_ID") or "working_profile").strip()


def tts_server() -> str:
    return (
        (os.environ.get("OFFLINE_EXPORT_TTS_SERVER") or "").strip().rstrip("/")
        or (os.environ.get("AVATAR_API_BASE") or "").strip().rstrip("/")
        or "http://10.29.145.124:9000"
    )


def gpu_id() -> int:
    raw = (os.environ.get("OFFLINE_EXPORT_GPU_ID") or "6").strip()
    try:
        return int(raw)
    except ValueError:
        return 6


def public_config() -> dict[str, Any]:
    return {
        "enabled": is_enabled(),
        "base_url": base_url() or None,
        "profile_id": profile_id(),
        "tts_server": tts_server(),
        "gpu_id": gpu_id(),
    }


def _unwrap(payload: dict[str, Any]) -> dict[str, Any]:
    if isinstance(payload.get("data"), dict):
        return payload["data"]
    return payload


def create_export(
    *,
    item_id: str,
    text: str,
    question: str,
    answer: str,
    profile_id_value: str | None = None,
    tts_server_value: str | None = None,
    gpu_id_value: int | None = None,
) -> dict[str, Any]:
    url = base_url()
    if not url:
        raise RuntimeError("OFFLINE_EXPORTS_BASE_URL / WEBRTC_SIGNALING_BASE is not set")
    body = {
        "item_id": item_id,
        "text": text,
        "question": question,
        "answer": answer,
        "profile_id": profile_id_value or profile_id(),
        "tts_server": tts_server_value or tts_server(),
        "gpu_id": gpu_id_value if gpu_id_value is not None else gpu_id(),
    }
    timeout = max(10.0, float(os.environ.get("OFFLINE_EXPORT_TIMEOUT_SEC") or "120"))
    from hologram_trace import trace

    trace(f"offline-exports: HTTP POST {url}/api/offline-exports item_id={item_id}")
    with httpx.Client(timeout=httpx.Timeout(timeout, connect=15.0)) as client:
        r = client.post(
            f"{url}/api/offline-exports",
            json=body,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
        )
    if r.status_code >= 400:
        raise RuntimeError(r.text[:500] or f"offline-exports HTTP {r.status_code}")
    payload = r.json()
    if not isinstance(payload, dict):
        raise RuntimeError("offline-exports returned non-object JSON")
    if payload.get("code") not in (None, 0):
        raise RuntimeError(str(payload.get("msg") or "offline-exports error"))
    data = _unwrap(payload)
    job_id = str(data.get("job_id") or data.get("id") or "").strip()
    if not job_id:
        raise RuntimeError("offline-exports response missing job_id")
    return data


def get_export_status(job_id: str) -> dict[str, Any]:
    url = base_url()
    if not url:
        raise RuntimeError("OFFLINE_EXPORTS_BASE_URL / WEBRTC_SIGNALING_BASE is not set")
    job_id = (job_id or "").strip()
    if not job_id:
        raise ValueError("job_id is required")
    timeout = max(5.0, float(os.environ.get("OFFLINE_EXPORT_STATUS_TIMEOUT_SEC") or "30"))
    with httpx.Client(timeout=httpx.Timeout(timeout, connect=10.0)) as client:
        r = client.get(
            f"{url}/api/offline-exports/{job_id}",
            headers={"Accept": "application/json"},
        )
    if r.status_code >= 400:
        raise RuntimeError(r.text[:500] or f"offline-exports status HTTP {r.status_code}")
    payload = r.json()
    if not isinstance(payload, dict):
        raise RuntimeError("offline-exports status returned non-object JSON")
    if payload.get("code") not in (None, 0):
        raise RuntimeError(str(payload.get("msg") or "offline-exports status error"))
    return _unwrap(payload)


def terminal_status(status: str) -> bool:
    s = (status or "").strip().lower()
    return s in (
        "done",
        "completed",
        "complete",
        "success",
        "succeeded",
        "failed",
        "error",
        "cancelled",
        "canceled",
    )
