"""Synchronous client for the Whisper transcribe HTTP API."""

from __future__ import annotations

import os
import time
from typing import Any

import httpx


def _env_int(name: str, default: int) -> int:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def transcribe_configured() -> bool:
    return bool((os.environ.get("TRANSCRIBE_API_URL") or "").strip())


def transcribe_bytes(
    data: bytes,
    *,
    filename: str = "audio.wav",
    content_type: str = "audio/wav",
    language: str | None = None,
) -> dict[str, Any]:
    url = (os.environ.get("TRANSCRIBE_API_URL") or "").strip()
    if not url:
        raise RuntimeError("TRANSCRIBE_API_URL is not set")

    max_bytes = max(1, _env_int("TRANSCRIBE_MAX_UPLOAD_MB", 12)) * 1024 * 1024
    if not data:
        raise ValueError("empty audio for transcribe")
    if len(data) > max_bytes:
        raise ValueError(f"audio too large for transcribe (max {max_bytes // (1024 * 1024)} MB)")

    mode = (os.environ.get("TRANSCRIBE_MODE") or "chunked").strip().lower() or "chunked"
    if mode not in ("sequential", "chunked"):
        mode = "chunked"
    chunk_len = (os.environ.get("TRANSCRIBE_CHUNK_LENGTH_S") or "10").strip() or "10"
    if mode == "chunked" and chunk_len in ("", "0"):
        chunk_len = "10"

    lang = (language or os.environ.get("TRANSCRIBE_DEFAULT_LANGUAGE") or "english").strip() or "english"
    form: dict[str, str] = {
        "stride_length_s": (os.environ.get("TRANSCRIBE_STRIDE_LENGTH_S") or "0").strip() or "0",
        "mode": mode,
        "task": (os.environ.get("TRANSCRIBE_TASK") or "transcribe").strip() or "transcribe",
        "batch_size": str(max(1, _env_int("TRANSCRIBE_BATCH_SIZE", 8))),
        "num_beams": str(max(1, _env_int("TRANSCRIBE_NUM_BEAMS", 1))),
        "chunk_length_s": chunk_len,
        "model_id": (os.environ.get("TRANSCRIBE_MODEL_ID") or "").strip()
        or "openai/whisper-large-v3-turbo",
        "temperature": (os.environ.get("TRANSCRIBE_TEMPERATURE") or "0").strip() or "0",
        "max_new_tokens": str(max(16, _env_int("TRANSCRIBE_MAX_NEW_TOKENS", 128))),
        "timestamp": (os.environ.get("TRANSCRIBE_TIMESTAMP") or "none").strip() or "none",
        "language": lang,
    }
    timeout = max(5.0, float(os.environ.get("TRANSCRIBE_TIMEOUT_SEC") or "120"))
    files = {"file": (filename, data, content_type)}
    t0 = time.perf_counter()
    with httpx.Client(timeout=httpx.Timeout(timeout, connect=15.0)) as client:
        r = client.post(url, data=form, files=files)
    latency_ms = round((time.perf_counter() - t0) * 1000.0, 1)
    if r.status_code >= 400:
        raise RuntimeError(r.text[:500] or f"transcribe HTTP {r.status_code}")
    payload = r.json()
    if not isinstance(payload, dict):
        raise RuntimeError("transcribe returned non-object JSON")
    text = str(payload.get("text") or "").strip()
    return {
        "text": text,
        "metadata": payload.get("metadata"),
        "chunks": payload.get("chunks"),
        "latency_ms": latency_ms,
    }
