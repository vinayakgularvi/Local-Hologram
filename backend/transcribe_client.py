"""HTTP clients for speech-to-text: NVIDIA Parakeet / NeMo (default) or legacy Whisper API."""

from __future__ import annotations

import os
import time
from typing import Any

import httpx

_PARAKEET_BACKENDS = frozenset({
    "parakeet",
    "nvidia",
    "nemo",
    "nvidia-parakeet",
    "hf-nemo",
    "v1",
    "openai",  # legacy alias (OpenAI-compatible HTTP shape, not OpenAI STT)
})
_WHISPER_BACKENDS = frozenset({"whisper", "legacy", "hf-whisper"})

_LANGUAGE_TO_PARAKEET_TAG: dict[str, str] = {
    "english": "en-US",
    "en": "en-US",
    "spanish": "es-ES",
    "es": "es-ES",
    "french": "fr-FR",
    "fr": "fr-FR",
    "german": "de-DE",
    "de": "de-DE",
    "italian": "it-IT",
    "it": "it-IT",
    "portuguese": "pt-PT",
    "pt": "pt-PT",
    "hindi": "hi-IN",
    "hi": "hi-IN",
    "japanese": "ja-JP",
    "ja": "ja-JP",
    "korean": "ko-KR",
    "ko": "ko-KR",
    "chinese": "zh-CN",
    "zh": "zh-CN",
}


def _env_int(name: str, default: int) -> int:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _parakeet_env(primary: str, legacy: str, default: str) -> str:
    """Read Parakeet STT env; fall back to deprecated TRANSCRIBE_OPENAI_* names."""
    return (os.environ.get(primary) or os.environ.get(legacy) or default).strip() or default


def get_transcribe_backend() -> str:
    raw = (os.environ.get("TRANSCRIBE_BACKEND") or "parakeet").strip().lower()
    if raw in _PARAKEET_BACKENDS:
        return "parakeet"
    if raw in _WHISPER_BACKENDS:
        return "whisper"
    return "parakeet"


def resolve_transcribe_url(*, backend: str | None = None) -> str:
    b = backend or get_transcribe_backend()
    if b == "whisper":
        return (
            (os.environ.get("TRANSCRIBE_WHISPER_API_URL") or "").strip()
            or (os.environ.get("TRANSCRIBE_API_URL") or "").strip()
        )
    return (os.environ.get("TRANSCRIBE_API_URL") or "").strip() or (
        "http://127.0.0.1:8899/v1/audio/transcriptions"
    )


def transcribe_configured() -> bool:
    return bool(resolve_transcribe_url())


def map_language_for_backend(language: str, *, backend: str | None = None) -> str:
    b = backend or get_transcribe_backend()
    raw = (language or os.environ.get("TRANSCRIBE_DEFAULT_LANGUAGE") or "").strip()
    if b == "whisper":
        return raw or "english"
    default_tag = _parakeet_env("TRANSCRIBE_PARAKEET_LANGUAGE", "TRANSCRIBE_OPENAI_LANGUAGE", "en-US")
    if not raw:
        return default_tag
    low = raw.lower().replace("_", "-")
    if low in _LANGUAGE_TO_PARAKEET_TAG:
        return _LANGUAGE_TO_PARAKEET_TAG[low]
    if len(low) >= 4 and "-" in low:
        parts = low.split("-", 1)
        if len(parts[0]) == 2:
            return f"{parts[0].lower()}-{parts[1].upper()}"
        return raw
    if len(low) == 2:
        return _LANGUAGE_TO_PARAKEET_TAG.get(low, f"{low}-{low.upper()}")
    return default_tag


def _max_upload_bytes() -> int:
    return max(256 * 1024, _env_int("TRANSCRIBE_MAX_UPLOAD_MB", 12) * 1024 * 1024)


def _slice_min_bytes() -> int:
    return max(1, _env_int("TRANSCRIBE_SLICE_MIN_BYTES", 200))


def _empty_slice_result(*, backend: str | None = None) -> dict[str, Any]:
    b = backend or get_transcribe_backend()
    return {
        "text": "",
        "metadata": None,
        "chunks": [],
        "latency_ms": 0.0,
        "skipped": True,
        "backend": b,
    }


def _timeout_sec(*, slice_request: bool) -> float:
    if slice_request:
        return max(2.0, float(os.environ.get("TRANSCRIBE_SLICE_TIMEOUT_SEC") or "8"))
    return max(5.0, float(os.environ.get("TRANSCRIBE_TIMEOUT_SEC") or "120"))


def _parakeet_form(language: str) -> dict[str, str]:
    """Multipart fields for NVIDIA Parakeet (NeMo) /v1/audio/transcriptions."""
    return {
        "language": map_language_for_backend(language, backend="parakeet"),
        "response_format": _parakeet_env(
            "TRANSCRIBE_PARAKEET_RESPONSE_FORMAT",
            "TRANSCRIBE_OPENAI_RESPONSE_FORMAT",
            "json",
        ),
        "return_timestamps": _parakeet_env(
            "TRANSCRIBE_PARAKEET_RETURN_TIMESTAMPS",
            "TRANSCRIBE_OPENAI_RETURN_TIMESTAMPS",
            "false",
        ),
        "enable_automatic_punctuation": _parakeet_env(
            "TRANSCRIBE_PARAKEET_ENABLE_AUTOMATIC_PUNCTUATION",
            "TRANSCRIBE_OPENAI_ENABLE_AUTOMATIC_PUNCTUATION",
            "false",
        ),
    }


def _whisper_form_from_env(
    *,
    language: str,
    slice_request: bool,
    overrides: dict[str, str] | None = None,
) -> dict[str, str]:
    mode = (os.environ.get("TRANSCRIBE_MODE") or "chunked").strip().lower() or "chunked"
    if mode not in ("sequential", "chunked"):
        mode = "chunked"
    if slice_request:
        mode = "sequential"
    chunk_len = (os.environ.get("TRANSCRIBE_CHUNK_LENGTH_S") or "10").strip() or "10"
    if slice_request:
        chunk_len = "0"
    elif mode == "chunked" and chunk_len in ("", "0"):
        chunk_len = "10"
    if overrides:
        om = (overrides.get("mode") or "").strip().lower()
        if om in ("sequential", "chunked"):
            mode = om
        if slice_request:
            mode = "sequential"
        oc = (overrides.get("chunk_length_s") or "").strip()
        if oc:
            chunk_len = oc
        elif slice_request:
            chunk_len = "0"
    model = (os.environ.get("TRANSCRIBE_MODEL_ID") or "").strip() or (
        "openai/whisper-large-v3-turbo"
    )
    if slice_request:
        model = (
            (os.environ.get("TRANSCRIBE_SLICE_MODEL_ID") or "").strip()
            or (overrides or {}).get("model_id", "").strip()
            or model
        )
    max_tokens = (
        str(max(8, _env_int("TRANSCRIBE_SLICE_MAX_NEW_TOKENS", 32)))
        if slice_request
        else str(max(16, _env_int("TRANSCRIBE_MAX_NEW_TOKENS", 128)))
    )
    batch = (
        str(max(1, _env_int("TRANSCRIBE_SLICE_BATCH_SIZE", 1)))
        if slice_request
        else str(max(1, _env_int("TRANSCRIBE_BATCH_SIZE", 8)))
    )
    form: dict[str, str] = {
        "stride_length_s": (os.environ.get("TRANSCRIBE_STRIDE_LENGTH_S") or "0").strip() or "0",
        "mode": mode,
        "task": (os.environ.get("TRANSCRIBE_TASK") or "transcribe").strip() or "transcribe",
        "batch_size": batch,
        "num_beams": str(max(1, _env_int("TRANSCRIBE_NUM_BEAMS", 1))),
        "chunk_length_s": chunk_len,
        "model_id": model,
        "temperature": (os.environ.get("TRANSCRIBE_TEMPERATURE") or "0").strip() or "0",
        "max_new_tokens": max_tokens,
        "timestamp": (os.environ.get("TRANSCRIBE_TIMESTAMP") or "none").strip() or "none",
        "language": map_language_for_backend(language, backend="whisper"),
    }
    if overrides:
        for key, val in overrides.items():
            s = (val or "").strip()
            if s:
                form[key] = s
    if slice_request:
        form["mode"] = "sequential"
        form["chunk_length_s"] = "0"
    return form


def _parse_response(payload: dict[str, Any], *, backend: str) -> dict[str, Any]:
    text = str(payload.get("text") or "").strip()
    if backend == "parakeet":
        return {
            "text": text,
            "metadata": {
                "provider": "nvidia",
                "backend": payload.get("backend"),
                "model": payload.get("model"),
                "language": payload.get("language"),
                "duration_seconds": payload.get("duration_seconds"),
                "filename": payload.get("filename"),
            },
            "chunks": None,
        }
    return {
        "text": text,
        "metadata": payload.get("metadata"),
        "chunks": payload.get("chunks"),
    }


def _post_transcribe(
    client: httpx.Client,
    *,
    url: str,
    data: bytes,
    filename: str,
    content_type: str,
    form: dict[str, str],
) -> httpx.Response:
    files = {"file": (filename, data, content_type)}
    return client.post(url, data=form, files=files)


async def _apost_transcribe(
    client: httpx.AsyncClient,
    *,
    url: str,
    data: bytes,
    filename: str,
    content_type: str,
    form: dict[str, str],
) -> httpx.Response:
    files = {"file": (filename, data, content_type)}
    return await client.post(url, data=form, files=files)


def transcribe_bytes(
    data: bytes,
    *,
    filename: str = "audio.wav",
    content_type: str = "audio/wav",
    language: str | None = None,
    slice_request: bool = False,
    whisper_overrides: dict[str, str] | None = None,
) -> dict[str, Any]:
    url = resolve_transcribe_url()
    if not url:
        raise RuntimeError("TRANSCRIBE_API_URL is not set")
    if not data:
        if slice_request:
            return _empty_slice_result()
        raise ValueError("empty audio for transcribe")
    if slice_request and len(data) < _slice_min_bytes():
        return _empty_slice_result()
    max_bytes = _max_upload_bytes()
    if len(data) > max_bytes:
        raise ValueError(f"audio too large for transcribe (max {max_bytes // (1024 * 1024)} MB)")

    backend = get_transcribe_backend()
    lang = language or os.environ.get("TRANSCRIBE_DEFAULT_LANGUAGE") or ""
    form = (
        _parakeet_form(lang)
        if backend == "parakeet"
        else _whisper_form_from_env(
            language=lang, slice_request=slice_request, overrides=whisper_overrides
        )
    )
    timeout = httpx.Timeout(_timeout_sec(slice_request=slice_request), connect=15.0)
    t0 = time.perf_counter()
    with httpx.Client(timeout=timeout) as client:
        r = _post_transcribe(
            client,
            url=url,
            data=data,
            filename=filename,
            content_type=content_type,
            form=form,
        )
    latency_ms = round((time.perf_counter() - t0) * 1000.0, 1)
    if r.status_code >= 400:
        if slice_request and r.status_code in (400, 413, 422):
            return {
                "text": "",
                "metadata": None,
                "chunks": [],
                "latency_ms": latency_ms,
                "skipped": True,
            }
        raise RuntimeError(r.text[:500] or f"transcribe HTTP {r.status_code}")
    payload = r.json()
    if not isinstance(payload, dict):
        raise RuntimeError("transcribe returned non-object JSON")
    out = _parse_response(payload, backend=backend)
    out["latency_ms"] = latency_ms
    out["backend"] = backend
    return out


async def transcribe_upload_async(
    data: bytes,
    *,
    filename: str = "mic.webm",
    content_type: str = "application/octet-stream",
    language: str = "",
    slice_request: bool = False,
    whisper_overrides: dict[str, str] | None = None,
) -> dict[str, Any]:
    url = resolve_transcribe_url()
    if not url:
        raise RuntimeError("TRANSCRIBE_API_URL is not set")
    if not data:
        if slice_request:
            return _empty_slice_result()
        raise ValueError("empty audio upload")
    if slice_request and len(data) < _slice_min_bytes():
        return _empty_slice_result()
    max_bytes = _max_upload_bytes()
    if len(data) > max_bytes:
        raise ValueError(f"audio too large (max {max_bytes // (1024 * 1024)} MB)")

    backend = get_transcribe_backend()
    form = (
        _parakeet_form(language)
        if backend == "parakeet"
        else _whisper_form_from_env(
            language=language,
            slice_request=slice_request,
            overrides=whisper_overrides,
        )
    )
    timeout = httpx.Timeout(_timeout_sec(slice_request=slice_request), connect=15.0)
    t0 = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await _apost_transcribe(
                client,
                url=url,
                data=data,
                filename=filename,
                content_type=content_type,
                form=form,
            )
    except httpx.TimeoutException as e:
        raise TimeoutError("Transcribe service timed out.") from e
    except httpx.RequestError as e:
        raise ConnectionError(f"Transcribe service unreachable: {e}") from e

    latency_ms = round((time.perf_counter() - t0) * 1000.0, 1)
    if r.status_code >= 400:
        if slice_request and r.status_code in (400, 413, 422):
            return {
                "text": "",
                "metadata": None,
                "chunks": [],
                "latency_ms": latency_ms,
                "skipped": True,
                "backend": backend,
            }
        raise RuntimeError(r.text[:500] or f"Transcribe service error ({r.status_code})")

    try:
        payload = r.json()
    except Exception as e:
        raise ValueError("Transcribe returned non-JSON.") from e
    if not isinstance(payload, dict):
        raise ValueError("Transcribe returned unexpected JSON.")
    out = _parse_response(payload, backend=backend)
    out["latency_ms"] = latency_ms
    out["backend"] = backend
    return out
