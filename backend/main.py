"""
Lip-Sync Agent API: question -> Ollama -> TTS -> Wav2Lip HTTP lip-sync pipeline.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, AsyncIterator
from urllib.parse import urlparse

import edge_tts
import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from gradio_client import Client, handle_file
from pydantic import BaseModel, Field

from analytics_store import clear_all, get_recent_voice_turns, get_summary, init_db, record_voice_turn
from rag_store import (
    delete_source,
    get_status as rag_get_status,
    ingest_file,
    list_sources as rag_list_sources,
    query_documents,
    reset_collection as rag_reset_collection,
)
from google_drive_sync import is_configured as gdrive_is_configured
from google_drive_sync import live_sync_mark_done as gdrive_live_sync_mark_done
from google_drive_sync import live_sync_mark_start as gdrive_live_sync_mark_start
from google_drive_sync import public_config as gdrive_public_config
from google_drive_sync import sync_to_chroma as gdrive_sync_to_chroma
from dropbox_sync import is_configured as dropbox_is_configured
from dropbox_sync import live_sync_mark_done as dropbox_live_sync_mark_done
from dropbox_sync import live_sync_mark_start as dropbox_live_sync_mark_start
from dropbox_sync import public_config as dropbox_public_config
from dropbox_sync import sync_to_chroma as dropbox_sync_to_chroma
from s3_sync import is_configured as s3_is_configured
from s3_sync import live_sync_mark_done as s3_live_sync_mark_done
from s3_sync import live_sync_mark_start as s3_live_sync_mark_start
from s3_sync import public_config as s3_public_config
from s3_sync import sync_to_chroma as s3_sync_to_chroma
from azure_blob_sync import is_configured as azure_blob_is_configured
from azure_blob_sync import live_sync_mark_done as azure_blob_live_sync_mark_done
from azure_blob_sync import live_sync_mark_start as azure_blob_live_sync_mark_start
from azure_blob_sync import public_config as azure_blob_public_config
from azure_blob_sync import sync_to_chroma as azure_blob_sync_to_chroma
from gcs_sync import is_configured as gcs_is_configured
from gcs_sync import live_sync_mark_done as gcs_live_sync_mark_done
from gcs_sync import live_sync_mark_start as gcs_live_sync_mark_start
from gcs_sync import public_config as gcs_public_config
from gcs_sync import sync_to_chroma as gcs_sync_to_chroma
from sharepoint_sync import is_configured as sharepoint_is_configured
from sharepoint_sync import live_sync_mark_done, live_sync_mark_start
from sharepoint_sync import public_config as sharepoint_public_config
from sharepoint_sync import sync_to_chroma as sharepoint_sync_to_chroma
from chunk_pipeline import ffmpeg_concat_videos, ffprobe_duration_seconds, run_chunked_lipsync
from realtime_lipsync import process_segment_sync, split_next_segment
from studio_integrations import (
    apply_studio_integrations_to_environ,
    delete_section as studio_delete_section,
    public_summary as studio_integrations_summary,
    read_section_strings as studio_read_section_strings,
    save_section as studio_save_section,
)
from studio_integrations import GDRIVE_CREDENTIALS_SAVED as STUDIO_GDRIVE_CREDENTIALS_PATH
from studio_integrations import GCS_CREDENTIALS_SAVED as STUDIO_GCS_CREDENTIALS_PATH
from studio_live_sync import is_master_enabled, set_master_enabled

_BACKEND_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _BACKEND_DIR.parent
load_dotenv(_REPO_ROOT / ".env")
load_dotenv(_BACKEND_DIR / ".env")
apply_studio_integrations_to_environ()

# URL prefix for all HTTP routes, static mounts, and browser-facing paths (match Vite `base`).
_raw_hx = (os.environ.get("HOLUMINEX_PREFIX", "/holuminex") or "/holuminex").strip()
HOLUMINEX_PREFIX = (_raw_hx if _raw_hx.startswith("/") else f"/{_raw_hx}").rstrip("/") or "/holuminex"


def _hx(path: str) -> str:
    if not path.startswith("/"):
        path = "/" + path
    return f"{HOLUMINEX_PREFIX}{path}"



def _env_first_float(*keys: str, default: float) -> float:
    for k in keys:
        v = os.environ.get(k)
        if v is not None and str(v).strip() != "":
            return float(v)
    return default


def _env_first_int(*keys: str, default: int) -> int:
    for k in keys:
        v = os.environ.get(k)
        if v is not None and str(v).strip() != "":
            return int(float(v))
    return default


def _ollama_base() -> str:
    v = os.environ.get("OLLAMA_BASE", "").strip()
    return (v or "http://10.29.145.124:8000").rstrip("/")


def _ollama_model() -> str:
    v = os.environ.get("OLLAMA_MODEL", "").strip()
    return v or "AFM-4.5B-Q4_K_M.gguf"


def _model_api_style() -> str:
    return os.environ.get("MODEL_API_STYLE", "auto").strip().lower()


def _llm_provider() -> str:
    v = os.environ.get("LLM_PROVIDER", "").strip().lower()
    if v in ("openai", "anthropic", "google"):
        return v
    return "local"


def _google_gemini_api_key() -> str:
    return (os.environ.get("GOOGLE_API_KEY", "").strip() or os.environ.get("GEMINI_API_KEY", "").strip())


VOICE_MAX_TOKENS = max(16, _env_first_int("VOICE_MAX_TOKENS", default=96))
VOICE_OLLAMA_INSTRUCTION = os.environ.get(
    "VOICE_OLLAMA_INSTRUCTION",
    "You are a hologram voice assistant. Reply in at most 2 short natural sentences "
    "suitable for a talking avatar. Be concise and conversational.",
).strip()
LIPSYNC_FILE_API_URL = os.environ.get(
    "LIPSYNC_FILE_API_URL",
    "http://10.29.145.124:8000/lipsync/file",
).rstrip("/")
TTS_VOICE = os.environ.get("TTS_VOICE", "en-US-AriaNeural")
LIPSYNC_CHECKPOINT_PATH = os.environ.get(
    "LIPSYNC_CHECKPOINT_PATH",
    "checkpoints/checkpoints/Wav2Lip-SD-GAN.pt",
)
LIPSYNC_FPS = _env_first_int("LIPSYNC_FPS", default=25)
LIPSYNC_FACE_DET_BATCH_SIZE = _env_first_int("LIPSYNC_FACE_DET_BATCH_SIZE", default=16)
LIPSYNC_WAV2LIP_BATCH_SIZE = _env_first_int("LIPSYNC_WAV2LIP_BATCH_SIZE", default=128)
LIPSYNC_PAD_TOP = _env_first_int("LIPSYNC_PAD_TOP", default=0)
LIPSYNC_PAD_BOTTOM = _env_first_int("LIPSYNC_PAD_BOTTOM", default=10)
LIPSYNC_PAD_LEFT = _env_first_int("LIPSYNC_PAD_LEFT", default=0)
LIPSYNC_PAD_RIGHT = _env_first_int("LIPSYNC_PAD_RIGHT", default=0)
LIPSYNC_CROP_TOP = _env_first_int("LIPSYNC_CROP_TOP", default=0)
LIPSYNC_CROP_BOTTOM = _env_first_int("LIPSYNC_CROP_BOTTOM", default=-1)
LIPSYNC_CROP_LEFT = _env_first_int("LIPSYNC_CROP_LEFT", default=0)
LIPSYNC_CROP_RIGHT = _env_first_int("LIPSYNC_CROP_RIGHT", default=-1)
LIPSYNC_BOX_TOP = _env_first_int("LIPSYNC_BOX_TOP", default=-1)
LIPSYNC_BOX_BOTTOM = _env_first_int("LIPSYNC_BOX_BOTTOM", default=-1)
LIPSYNC_BOX_LEFT = _env_first_int("LIPSYNC_BOX_LEFT", default=-1)
LIPSYNC_BOX_RIGHT = _env_first_int("LIPSYNC_BOX_RIGHT", default=-1)
LIPSYNC_RESIZE_FACTOR = _env_first_int("LIPSYNC_RESIZE_FACTOR", default=1)
LIPSYNC_ROTATE = os.environ.get("LIPSYNC_ROTATE", "false").strip().lower() in ("1", "true", "yes")
LIPSYNC_NOSMOOTH = os.environ.get("LIPSYNC_NOSMOOTH", "false").strip().lower() in ("1", "true", "yes")
# Chunk mode (batch): many lip-sync API calls over full TTS wav. Default off.
_UC_RAW = os.environ.get("LIPSYNC_USE_CHUNKS", "0").strip()
try:
    _UC_NUM = float(_UC_RAW)
    LIPSYNC_RT_TARGET_FROM_UC = _UC_NUM if _UC_NUM > 1 else None
except ValueError:
    LIPSYNC_RT_TARGET_FROM_UC = None
LIPSYNC_USE_CHUNKS = _UC_RAW.lower() in (
    "1",
    "true",
    "yes",
)
# Stream API: start lip-sync before Ollama finishes (token queue + segment flushes)
LIPSYNC_REALTIME = os.environ.get("LIPSYNC_REALTIME", "1").strip().lower() in (
    "1",
    "true",
    "yes",
)
LIPSYNC_RT_TARGET_SEC = (
    LIPSYNC_RT_TARGET_FROM_UC
    if LIPSYNC_RT_TARGET_FROM_UC is not None
    else _env_first_float("LIPSYNC_RT_TARGET_SEC", "LIPSYNC_STREAM_CHUNK_SEC", default=4.0)
)
# Lower = first lip-sync segment starts sooner (more “live” video; may cut mid-phrase)
LIPSYNC_RT_MIN_CHARS = max(4, _env_first_int("LIPSYNC_RT_MIN_CHARS", default=8))
# Larger chunks = fewer lip-sync API round-trips when chunk mode is on
LIPSYNC_CHUNK_SEC = float(os.environ.get("LIPSYNC_CHUNK_SEC", "6"))
LIPSYNC_CHUNK_OVERLAP_SEC = float(os.environ.get("LIPSYNC_CHUNK_OVERLAP_SEC", "0"))
# If speech is shorter than this (seconds), use a single lip-sync call even when LIPSYNC_USE_CHUNKS=1
LIPSYNC_AUTO_SINGLE_BELOW_SEC = float(os.environ.get("LIPSYNC_AUTO_SINGLE_BELOW_SEC", "60"))
# Concurrent chunk jobs (each uses one HTTP request). Try 2 if the server can queue/batch.
LIPSYNC_CHUNK_PARALLEL = max(1, _env_first_int("LIPSYNC_CHUNK_PARALLEL", default=1))

# LiveTalking-style WebRTC signaling (POST /offer, /human, /record) — proxied to this origin when set
WEBRTC_SIGNALING_BASE = os.environ.get("WEBRTC_SIGNALING_BASE", "").strip().rstrip("/")
AVATAR_API_BASE = os.environ.get("AVATAR_API_BASE", "http://10.29.145.124:9000").strip().rstrip("/")
AVATAR_SAMPLE_TEXT = os.environ.get(
    "AVATAR_SAMPLE_TEXT",
    (
        "What used to take weeks of casting, recording, and editing now takes minutes. "
        "Generate character voices in over 70 languages, adjust the delivery in real time, "
        "and keep your production moving. This is text-to-speech built for creators."
    ),
).strip()

RAG_MAX_UPLOAD_BYTES = max(256_000, _env_first_int("RAG_MAX_UPLOAD_MB", default=25) * 1024 * 1024)
RAG_CHUNK_SIZE = max(200, _env_first_int("RAG_CHUNK_SIZE", default=900))
RAG_CHUNK_OVERLAP = max(0, _env_first_int("RAG_CHUNK_OVERLAP", default=120))
RAG_ALLOWED_SUFFIXES = frozenset({".pdf", ".docx", ".txt", ".md"})

# RAG → /api/voice-turn (mic STT → Ollama): retrieve chunks from Chroma, inject into prompt.
VOICE_RAG_ENABLED = os.environ.get("VOICE_RAG_ENABLED", "1").strip().lower() in (
    "1",
    "true",
    "yes",
)
VOICE_RAG_N_RESULTS = max(1, min(20, _env_first_int("VOICE_RAG_N_RESULTS", default=6)))
VOICE_RAG_MAX_CONTEXT_CHARS = max(400, _env_first_int("VOICE_RAG_MAX_CONTEXT_CHARS", default=3500))

# SharePoint → Chroma: background sync (see _sharepoint_live_loop)
SHAREPOINT_LIVE_SYNC = os.environ.get("SHAREPOINT_LIVE_SYNC", "1").strip().lower() in (
    "1",
    "true",
    "yes",
)
SHAREPOINT_SYNC_INTERVAL_SEC = max(30, _env_first_int("SHAREPOINT_SYNC_INTERVAL_SEC", default=90))

# Google Drive → Chroma (service account + shared folder)
GOOGLE_DRIVE_LIVE_SYNC = os.environ.get("GOOGLE_DRIVE_LIVE_SYNC", "1").strip().lower() in (
    "1",
    "true",
    "yes",
)
GOOGLE_DRIVE_SYNC_INTERVAL_SEC = max(30, _env_first_int("GOOGLE_DRIVE_SYNC_INTERVAL_SEC", default=90))

# Dropbox → Chroma (Dropbox API: access token or refresh token + app key/secret)
DROPBOX_LIVE_SYNC = os.environ.get("DROPBOX_LIVE_SYNC", "1").strip().lower() in (
    "1",
    "true",
    "yes",
)
DROPBOX_SYNC_INTERVAL_SEC = max(30, _env_first_int("DROPBOX_SYNC_INTERVAL_SEC", default=90))

# Amazon S3 → Chroma
S3_LIVE_SYNC = os.environ.get("S3_LIVE_SYNC", "1").strip().lower() in ("1", "true", "yes")
S3_SYNC_INTERVAL_SEC = max(30, _env_first_int("S3_SYNC_INTERVAL_SEC", default=90))

# Azure Blob → Chroma
AZURE_BLOB_LIVE_SYNC = os.environ.get("AZURE_BLOB_LIVE_SYNC", "1").strip().lower() in ("1", "true", "yes")
AZURE_BLOB_SYNC_INTERVAL_SEC = max(30, _env_first_int("AZURE_BLOB_SYNC_INTERVAL_SEC", default=90))

# Google Cloud Storage → Chroma
GCS_LIVE_SYNC = os.environ.get("GCS_LIVE_SYNC", "1").strip().lower() in ("1", "true", "yes")
GCS_SYNC_INTERVAL_SEC = max(30, _env_first_int("GCS_SYNC_INTERVAL_SEC", default=90))

OUTPUT_DIR = Path(__file__).resolve().parent / "outputs"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(
    title="Lip-Sync Agent",
    docs_url=_hx("/docs"),
    openapi_url=_hx("/openapi.json"),
    redoc_url=_hx("/redoc"),
)

_sharepoint_sync_lock = asyncio.Lock()
_google_drive_sync_lock = asyncio.Lock()
_dropbox_sync_lock = asyncio.Lock()
_s3_sync_lock = asyncio.Lock()
_azure_blob_sync_lock = asyncio.Lock()
_gcs_sync_lock = asyncio.Lock()
_background_tasks: list[asyncio.Task] = []


async def _sharepoint_sync_execute() -> dict[str, Any]:
    """Microsoft Graph → Chroma; updates SharePoint live-sync status markers."""
    live_sync_mark_start()
    try:
        out = await asyncio.to_thread(
            sharepoint_sync_to_chroma,
            chunk_size=RAG_CHUNK_SIZE,
            chunk_overlap=RAG_CHUNK_OVERLAP,
        )
        live_sync_mark_done(out, None)
        return out
    except Exception as e:
        live_sync_mark_done(None, str(e))
        raise


async def _sharepoint_live_loop() -> None:
    """Background: re-sync SharePoint documents into Chroma on an interval."""
    await asyncio.sleep(2.0)
    log = logging.getLogger("lipsync")
    while True:
        try:
            if not (SHAREPOINT_LIVE_SYNC and is_master_enabled()):
                await asyncio.sleep(15.0)
                continue
            if not sharepoint_is_configured():
                await asyncio.sleep(60.0)
                continue
            try:
                async with _sharepoint_sync_lock:
                    await _sharepoint_sync_execute()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.warning("SharePoint live sync failed: %s", e)
            await asyncio.sleep(float(SHAREPOINT_SYNC_INTERVAL_SEC))
        except asyncio.CancelledError:
            break


async def _google_drive_sync_execute() -> dict[str, Any]:
    """Google Drive → Chroma; updates live-sync status markers."""
    gdrive_live_sync_mark_start()
    try:
        out = await asyncio.to_thread(
            gdrive_sync_to_chroma,
            chunk_size=RAG_CHUNK_SIZE,
            chunk_overlap=RAG_CHUNK_OVERLAP,
        )
        gdrive_live_sync_mark_done(out, None)
        return out
    except Exception as e:
        gdrive_live_sync_mark_done(None, str(e))
        raise


async def _google_drive_live_loop() -> None:
    """Background: re-sync Google Drive folder into Chroma on an interval."""
    await asyncio.sleep(3.5)
    log = logging.getLogger("lipsync")
    while True:
        try:
            if not (GOOGLE_DRIVE_LIVE_SYNC and is_master_enabled()):
                await asyncio.sleep(15.0)
                continue
            if not gdrive_is_configured():
                await asyncio.sleep(60.0)
                continue
            try:
                async with _google_drive_sync_lock:
                    await _google_drive_sync_execute()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.warning("Google Drive live sync failed: %s", e)
            await asyncio.sleep(float(GOOGLE_DRIVE_SYNC_INTERVAL_SEC))
        except asyncio.CancelledError:
            break


async def _dropbox_sync_execute() -> dict[str, Any]:
    """Dropbox → Chroma; updates live-sync status markers."""
    dropbox_live_sync_mark_start()
    try:
        out = await asyncio.to_thread(
            dropbox_sync_to_chroma,
            chunk_size=RAG_CHUNK_SIZE,
            chunk_overlap=RAG_CHUNK_OVERLAP,
        )
        dropbox_live_sync_mark_done(out, None)
        return out
    except Exception as e:
        dropbox_live_sync_mark_done(None, str(e))
        raise


async def _dropbox_live_loop() -> None:
    """Background: re-sync Dropbox folder into Chroma on an interval."""
    await asyncio.sleep(4.0)
    log = logging.getLogger("lipsync")
    while True:
        try:
            if not (DROPBOX_LIVE_SYNC and is_master_enabled()):
                await asyncio.sleep(15.0)
                continue
            if not dropbox_is_configured():
                await asyncio.sleep(60.0)
                continue
            try:
                async with _dropbox_sync_lock:
                    await _dropbox_sync_execute()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.warning("Dropbox live sync failed: %s", e)
            await asyncio.sleep(float(DROPBOX_SYNC_INTERVAL_SEC))
        except asyncio.CancelledError:
            break


async def _s3_sync_execute() -> dict[str, Any]:
    s3_live_sync_mark_start()
    try:
        out = await asyncio.to_thread(
            s3_sync_to_chroma,
            chunk_size=RAG_CHUNK_SIZE,
            chunk_overlap=RAG_CHUNK_OVERLAP,
        )
        s3_live_sync_mark_done(out, None)
        return out
    except Exception as e:
        s3_live_sync_mark_done(None, str(e))
        raise


async def _s3_live_loop() -> None:
    await asyncio.sleep(4.5)
    log = logging.getLogger("lipsync")
    while True:
        try:
            if not (S3_LIVE_SYNC and is_master_enabled()):
                await asyncio.sleep(15.0)
                continue
            if not s3_is_configured():
                await asyncio.sleep(60.0)
                continue
            try:
                async with _s3_sync_lock:
                    await _s3_sync_execute()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.warning("S3 live sync failed: %s", e)
            await asyncio.sleep(float(S3_SYNC_INTERVAL_SEC))
        except asyncio.CancelledError:
            break


async def _azure_blob_sync_execute() -> dict[str, Any]:
    azure_blob_live_sync_mark_start()
    try:
        out = await asyncio.to_thread(
            azure_blob_sync_to_chroma,
            chunk_size=RAG_CHUNK_SIZE,
            chunk_overlap=RAG_CHUNK_OVERLAP,
        )
        azure_blob_live_sync_mark_done(out, None)
        return out
    except Exception as e:
        azure_blob_live_sync_mark_done(None, str(e))
        raise


async def _azure_blob_live_loop() -> None:
    await asyncio.sleep(5.0)
    log = logging.getLogger("lipsync")
    while True:
        try:
            if not (AZURE_BLOB_LIVE_SYNC and is_master_enabled()):
                await asyncio.sleep(15.0)
                continue
            if not azure_blob_is_configured():
                await asyncio.sleep(60.0)
                continue
            try:
                async with _azure_blob_sync_lock:
                    await _azure_blob_sync_execute()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.warning("Azure Blob live sync failed: %s", e)
            await asyncio.sleep(float(AZURE_BLOB_SYNC_INTERVAL_SEC))
        except asyncio.CancelledError:
            break


async def _gcs_sync_execute() -> dict[str, Any]:
    gcs_live_sync_mark_start()
    try:
        out = await asyncio.to_thread(
            gcs_sync_to_chroma,
            chunk_size=RAG_CHUNK_SIZE,
            chunk_overlap=RAG_CHUNK_OVERLAP,
        )
        gcs_live_sync_mark_done(out, None)
        return out
    except Exception as e:
        gcs_live_sync_mark_done(None, str(e))
        raise


async def _gcs_live_loop() -> None:
    await asyncio.sleep(5.5)
    log = logging.getLogger("lipsync")
    while True:
        try:
            if not (GCS_LIVE_SYNC and is_master_enabled()):
                await asyncio.sleep(15.0)
                continue
            if not gcs_is_configured():
                await asyncio.sleep(60.0)
                continue
            try:
                async with _gcs_sync_lock:
                    await _gcs_sync_execute()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.warning("GCS live sync failed: %s", e)
            await asyncio.sleep(float(GCS_SYNC_INTERVAL_SEC))
        except asyncio.CancelledError:
            break


@app.on_event("startup")
async def _on_startup() -> None:
    init_db()
    global _background_tasks
    _background_tasks = []
    if SHAREPOINT_LIVE_SYNC:
        _background_tasks.append(asyncio.create_task(_sharepoint_live_loop()))
    if GOOGLE_DRIVE_LIVE_SYNC:
        _background_tasks.append(asyncio.create_task(_google_drive_live_loop()))
    if DROPBOX_LIVE_SYNC:
        _background_tasks.append(asyncio.create_task(_dropbox_live_loop()))
    if S3_LIVE_SYNC:
        _background_tasks.append(asyncio.create_task(_s3_live_loop()))
    if AZURE_BLOB_LIVE_SYNC:
        _background_tasks.append(asyncio.create_task(_azure_blob_live_loop()))
    if GCS_LIVE_SYNC:
        _background_tasks.append(asyncio.create_task(_gcs_live_loop()))


@app.on_event("shutdown")
async def _on_shutdown() -> None:
    global _background_tasks
    for t in _background_tasks:
        t.cancel()
    for t in _background_tasks:
        try:
            await t
        except asyncio.CancelledError:
            pass
    _background_tasks = []


app.add_middleware(
    CORSMiddleware,
    allow_origins=os.environ.get(
        "CORS_ORIGINS",
        "http://localhost:6064,http://127.0.0.1:6064,"
        "http://localhost:6066,http://127.0.0.1:6066,"
        "http://localhost:6067,http://127.0.0.1:6067",
    ).split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount(_hx("/outputs"), StaticFiles(directory=str(OUTPUT_DIR)), name="outputs")
_analytics_subscribers: set[asyncio.Queue[dict[str, Any]]] = set()


def _analytics_snapshot(limit: int = 40) -> dict[str, Any]:
    return {
        "summary": {"kind": "voice_turns", **get_summary()},
        "recent": get_recent_voice_turns(limit),
    }


async def _publish_analytics_snapshot() -> None:
    if not _analytics_subscribers:
        return
    payload = _analytics_snapshot(limit=40)
    stale: list[asyncio.Queue[dict[str, Any]]] = []
    for q in list(_analytics_subscribers):
        try:
            q.put_nowait(payload)
        except asyncio.QueueFull:
            try:
                _ = q.get_nowait()
            except asyncio.QueueEmpty:
                pass
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:
                stale.append(q)
    for q in stale:
        _analytics_subscribers.discard(q)


def _avatar_predict(api_name: str, **kwargs: Any) -> Any:
    client = Client(AVATAR_API_BASE)
    return client.predict(api_name=api_name, **kwargs)


def _result_items(result: Any) -> list[Any]:
    if isinstance(result, (list, tuple)):
        return list(result)
    return [result]


def _result_status(items: list[Any]) -> str:
    if len(items) >= 2 and items[1] is not None:
        return str(items[1])
    return "OK"


async def _store_gradio_file(candidate: Any, *, prefix: str, fallback_ext: str) -> str:
    path: str | None = None
    if isinstance(candidate, dict):
        path = candidate.get("path") or candidate.get("name") or candidate.get("url")
    elif isinstance(candidate, str):
        path = candidate
    if not path:
        raise HTTPException(status_code=502, detail="Gradio returned no file path.")

    suffix = Path(urlparse(path).path).suffix or fallback_ext
    out_name = f"{prefix}_{uuid.uuid4().hex}{suffix}"
    out_path = OUTPUT_DIR / out_name

    if path.startswith("http://") or path.startswith("https://"):
        async with httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=15.0)) as client:
            r = await client.get(path)
            if r.status_code >= 400:
                raise HTTPException(
                    status_code=502,
                    detail=f"Unable to fetch gradio output file: {r.status_code}",
                )
            out_path.write_bytes(r.content)
    else:
        src = Path(path)
        if not src.is_file():
            raise HTTPException(status_code=502, detail=f"Output file not found: {path}")
        shutil.copy2(src, out_path)

    return _hx(f"/outputs/{out_name}")


async def _forward_webrtc_post(subpath: str, request: Request) -> Response:
    """Forward JSON POST body to LiveTalking (or compatible) signaling server."""
    if not WEBRTC_SIGNALING_BASE:
        raise HTTPException(
            status_code=503,
            detail=(
                "WEBRTC_SIGNALING_BASE is not set. "
                "Set it in .env to your LiveTalking server origin (e.g. http://127.0.0.1:8010)."
            ),
        )
    url = f"{WEBRTC_SIGNALING_BASE}{subpath}"
    body = await request.body()
    ct = request.headers.get("content-type") or "application/json"
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=30.0)) as client:
            r = await client.post(url, content=body, headers={"Content-Type": ct})
    except httpx.HTTPError as e:
        raise HTTPException(
            status_code=502,
            detail=f"Upstream WebRTC signaling server unreachable: {e}",
        ) from e
    out_headers: dict[str, str] = {}
    if v := r.headers.get("content-type"):
        out_headers["content-type"] = v
    return Response(content=r.content, status_code=r.status_code, headers=out_headers)


@app.post(f"{HOLUMINEX_PREFIX}/offer")
async def webrtc_offer_proxy(request: Request) -> Response:
    return await _forward_webrtc_post("/offer", request)


@app.post(f"{HOLUMINEX_PREFIX}/human")
async def webrtc_human_proxy(request: Request) -> Response:
    return await _forward_webrtc_post("/human", request)


@app.post(f"{HOLUMINEX_PREFIX}/record")
async def webrtc_record_proxy(request: Request) -> Response:
    return await _forward_webrtc_post("/record", request)


logger = logging.getLogger("lipsync")


def _timing_payload(
    event: str,
    job_id: str,
    *,
    duration_ms: float | None = None,
    **extra: Any,
) -> str:
    d: dict[str, Any] = {"event": event, "job_id": job_id}
    if duration_ms is not None:
        d["duration_ms"] = round(duration_ms, 2)
    for k, v in extra.items():
        if v is not None:
            d[k] = v
    return json.dumps(d, ensure_ascii=False, default=str)


def _is_openai_compatible_style() -> bool:
    style = _model_api_style()
    if style in ("openai", "v1", "chat"):
        return True
    if style in ("ollama", "native"):
        return False
    # auto mode: bases that already point to /v1 are treated as OpenAI-compatible.
    return _ollama_base().endswith("/v1")


def _chat_completions_url(api_base: str) -> str:
    b = api_base.rstrip("/")
    if b.endswith("/v1"):
        return f"{b}/chat/completions"
    return f"{b}/v1/chat/completions"


async def _stream_chat_completions(
    *,
    api_base: str,
    model: str,
    prompt: str,
    metrics: dict[str, Any] | None = None,
    max_tokens: int | None = None,
    extra_headers: dict[str, str] | None = None,
) -> AsyncIterator[str]:
    url = _chat_completions_url(api_base)
    payload: dict[str, Any] = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.7,
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    if max_tokens is not None:
        payload["max_tokens"] = int(max_tokens)
    headers = {"Accept": "text/event-stream", **(extra_headers or {})}
    async with httpx.AsyncClient(timeout=300.0) as client:
        async with client.stream("POST", url, json=payload, headers=headers) as response:
            if response.status_code >= 400:
                body = await response.aread()
                raise HTTPException(
                    status_code=502,
                    detail=f"Model API error {response.status_code}: {body.decode()[:500]}",
                )
            async for raw in response.aiter_lines():
                line = raw.strip()
                if not line or not line.startswith("data:"):
                    continue
                chunk = line[5:].strip()
                if chunk == "[DONE]":
                    break
                try:
                    data = json.loads(chunk)
                except json.JSONDecodeError:
                    continue
                if data.get("error"):
                    raise HTTPException(status_code=502, detail=str(data["error"]))
                if metrics is not None and isinstance(data.get("usage"), dict):
                    usage = data["usage"]
                    metrics["prompt_eval_count"] = usage.get("prompt_tokens")
                    metrics["eval_count"] = usage.get("completion_tokens")
                choices = data.get("choices") or []
                if choices:
                    delta = choices[0].get("delta") or {}
                    piece = delta.get("content") or ""
                    if piece:
                        yield piece


async def _stream_openai_compatible(
    prompt: str,
    metrics: dict[str, Any] | None = None,
    *,
    max_tokens: int | None = None,
) -> AsyncIterator[str]:
    async for piece in _stream_chat_completions(
        api_base=_ollama_base(),
        model=_ollama_model(),
        prompt=prompt,
        metrics=metrics,
        max_tokens=max_tokens,
        extra_headers=None,
    ):
        yield piece


async def _stream_remote_openai(
    prompt: str,
    metrics: dict[str, Any] | None = None,
    *,
    max_tokens: int | None = None,
) -> AsyncIterator[str]:
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not key:
        raise HTTPException(status_code=503, detail="OPENAI_API_KEY is not set.")
    base = os.environ.get("OPENAI_BASE_URL", "").strip() or "https://api.openai.com"
    model = os.environ.get("OPENAI_MODEL", "").strip() or "gpt-4o-mini"
    async for piece in _stream_chat_completions(
        api_base=base,
        model=model,
        prompt=prompt,
        metrics=metrics,
        max_tokens=max_tokens,
        extra_headers={"Authorization": f"Bearer {key}"},
    ):
        yield piece


async def _stream_remote_anthropic(
    prompt: str,
    metrics: dict[str, Any] | None = None,
    *,
    max_tokens: int | None = None,
) -> AsyncIterator[str]:
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not key:
        raise HTTPException(status_code=503, detail="ANTHROPIC_API_KEY is not set.")
    base = os.environ.get("ANTHROPIC_BASE_URL", "").strip() or "https://api.anthropic.com"
    base = base.rstrip("/")
    url = f"{base}/v1/messages"
    model = os.environ.get("ANTHROPIC_MODEL", "").strip() or "claude-3-5-haiku-20241022"
    mt = max_tokens if max_tokens is not None else VOICE_MAX_TOKENS
    mt = max(256, min(int(mt), 8192))
    body: dict[str, Any] = {
        "model": model,
        "max_tokens": mt,
        "messages": [{"role": "user", "content": prompt}],
        "stream": True,
    }
    headers = {
        "x-api-key": key,
        "anthropic-version": "2023-06-01",
        "Accept": "text/event-stream",
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(timeout=300.0) as client:
        async with client.stream("POST", url, json=body, headers=headers) as response:
            if response.status_code >= 400:
                err_body = await response.aread()
                raise HTTPException(
                    status_code=502,
                    detail=f"Anthropic API error {response.status_code}: {err_body.decode()[:500]}",
                )
            async for raw in response.aiter_lines():
                line = raw.strip()
                if not line:
                    continue
                chunk = line[5:].strip() if line.startswith("data:") else line
                if not chunk or chunk == "[DONE]":
                    continue
                if chunk.startswith(":"):
                    continue
                try:
                    data = json.loads(chunk)
                except json.JSONDecodeError:
                    continue
                if data.get("type") == "error":
                    raise HTTPException(status_code=502, detail=str(data.get("error") or data))
                if data.get("type") == "content_block_delta":
                    d = data.get("delta") or {}
                    if d.get("type") == "text_delta":
                        piece = d.get("text") or ""
                        if piece:
                            yield piece
                if data.get("type") == "message_delta" and metrics is not None:
                    u = data.get("usage") or {}
                    if u.get("input_tokens") is not None:
                        metrics["prompt_eval_count"] = u.get("input_tokens")
                    if u.get("output_tokens") is not None:
                        metrics["eval_count"] = u.get("output_tokens")


def _gemini_stream_text_piece(obj: dict[str, Any]) -> str:
    parts_out: list[str] = []
    for c in obj.get("candidates") or []:
        content = c.get("content") or {}
        for p in content.get("parts") or []:
            t = p.get("text")
            if isinstance(t, str) and t:
                parts_out.append(t)
    return "".join(parts_out)


async def _stream_remote_google_gemini(
    prompt: str,
    metrics: dict[str, Any] | None = None,
    *,
    max_tokens: int | None = None,
) -> AsyncIterator[str]:
    key = _google_gemini_api_key()
    if not key:
        raise HTTPException(
            status_code=503,
            detail="GOOGLE_API_KEY (or GEMINI_API_KEY) is not set.",
        )
    base = os.environ.get("GOOGLE_GEMINI_BASE_URL", "").strip() or "https://generativelanguage.googleapis.com"
    base = base.rstrip("/")
    mid_raw = os.environ.get("GOOGLE_GEMINI_MODEL", "").strip() or "gemini-2.0-flash"
    _gem_allowed = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-._")
    if mid_raw.startswith("models/"):
        suffix = "".join(c for c in mid_raw[9:] if c in _gem_allowed) or "gemini-2.0-flash"
        model_path = f"models/{suffix}"
    else:
        slug = "".join(c for c in mid_raw if c in _gem_allowed) or "gemini-2.0-flash"
        model_path = f"models/{slug}"
    url = f"{base}/v1beta/{model_path}:streamGenerateContent?alt=sse"
    mt = max_tokens if max_tokens is not None else VOICE_MAX_TOKENS
    mt = max(16, min(int(mt), 8192))
    body: dict[str, Any] = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"maxOutputTokens": mt, "temperature": 0.7},
    }
    headers = {
        "Content-Type": "application/json",
        "x-goog-api-key": key,
    }
    cumulative = ""
    async with httpx.AsyncClient(timeout=300.0) as client:
        async with client.stream("POST", url, json=body, headers=headers) as response:
            if response.status_code >= 400:
                err_body = await response.aread()
                raise HTTPException(
                    status_code=502,
                    detail=f"Google Gemini API error {response.status_code}: {err_body.decode()[:600]}",
                )
            async for raw in response.aiter_lines():
                line = raw.strip()
                if not line:
                    continue
                chunk = line[5:].strip() if line.startswith("data:") else line
                if not chunk or chunk == "[DONE]":
                    continue
                if chunk.startswith(":"):
                    continue
                try:
                    data = json.loads(chunk)
                except json.JSONDecodeError:
                    continue
                err = data.get("error")
                if isinstance(err, dict):
                    raise HTTPException(status_code=502, detail=str(err.get("message") or err)[:500])
                if err:
                    raise HTTPException(status_code=502, detail=str(err)[:500])
                piece = _gemini_stream_text_piece(data)
                if not piece:
                    um = data.get("usageMetadata")
                    if isinstance(um, dict) and metrics is not None:
                        if um.get("promptTokenCount") is not None:
                            metrics["prompt_eval_count"] = um.get("promptTokenCount")
                        if um.get("candidatesTokenCount") is not None:
                            metrics["eval_count"] = um.get("candidatesTokenCount")
                    continue
                if piece.startswith(cumulative):
                    delta = piece[len(cumulative) :]
                    cumulative = piece
                else:
                    delta = piece
                    cumulative += piece
                if delta:
                    yield delta


async def _stream_ollama_native(
    prompt: str,
    metrics: dict[str, Any] | None = None,
    *,
    max_tokens: int | None = None,
) -> AsyncIterator[str]:
    payload = {
        "model": _ollama_model(),
        "prompt": prompt,
        "stream": True,
        "options": {"temperature": 0.7},
    }
    if max_tokens is not None:
        payload["options"]["num_predict"] = int(max_tokens)
    async with httpx.AsyncClient(timeout=300.0) as client:
        async with client.stream(
            "POST",
            f"{_ollama_base()}/api/generate",
            json=payload,
        ) as response:
            if response.status_code >= 400:
                body = await response.aread()
                raise HTTPException(
                    status_code=502,
                    detail=f"Ollama error {response.status_code}: {body.decode()[:500]}",
                )
            async for line in response.aiter_lines():
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if data.get("error"):
                    raise HTTPException(
                        status_code=502,
                        detail=str(data["error"]),
                    )
                piece = data.get("response") or ""
                if piece:
                    yield piece
                if data.get("done"):
                    if metrics is not None:
                        metrics["prompt_eval_count"] = data.get("prompt_eval_count")
                        metrics["eval_count"] = data.get("eval_count")
                        metrics["total_duration_ns"] = data.get("total_duration")
                        metrics["load_duration_ns"] = data.get("load_duration")
                        metrics["prompt_eval_duration_ns"] = data.get("prompt_eval_duration")
                        metrics["eval_duration_ns"] = data.get("eval_duration")
                    break


async def ollama_stream_tokens(
    prompt: str,
    metrics: dict[str, Any] | None = None,
    *,
    max_tokens: int | None = None,
) -> AsyncIterator[str]:
    """Yields incremental text tokens from configured LLM (local, OpenAI, Anthropic, or Google Gemini)."""
    prov = _llm_provider()
    if prov == "openai":
        async for tok in _stream_remote_openai(prompt, metrics, max_tokens=max_tokens):
            yield tok
        return
    if prov == "anthropic":
        async for tok in _stream_remote_anthropic(prompt, metrics, max_tokens=max_tokens):
            yield tok
        return
    if prov == "google":
        async for tok in _stream_remote_google_gemini(prompt, metrics, max_tokens=max_tokens):
            yield tok
        return
    if _is_openai_compatible_style():
        async for tok in _stream_openai_compatible(prompt, metrics, max_tokens=max_tokens):
            yield tok
    else:
        async for tok in _stream_ollama_native(prompt, metrics, max_tokens=max_tokens):
            yield tok


async def ollama_generate(
    prompt: str,
    metrics: dict[str, Any] | None = None,
    *,
    max_tokens: int | None = None,
) -> str:
    parts: list[str] = []
    async for t in ollama_stream_tokens(prompt, metrics, max_tokens=max_tokens):
        parts.append(t)
    text = "".join(parts).strip()
    if not text:
        raise HTTPException(status_code=502, detail="The model returned an empty response.")
    return text


def sse_event(obj: dict) -> str:
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"


async def flush_sse_stream(agen: AsyncIterator[str]) -> AsyncIterator[str]:
    """Force an event-loop turn after each chunk so bytes reach the client before more CPU work."""
    async for chunk in agen:
        yield chunk
        await asyncio.sleep(0)


SSE_STREAM_HEADERS = {
    "Cache-Control": "no-cache, no-store, must-revalidate",
    "Pragma": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
    "X-Content-Type-Options": "nosniff",
}


async def text_to_wav(text: str, out_path: Path) -> None:
    communicate = edge_tts.Communicate(text, TTS_VOICE)
    await communicate.save(str(out_path))


def _ensure_ffmpeg() -> None:
    import subprocess

    try:
        subprocess.run(
            ["ffprobe", "-version"],
            capture_output=True,
            check=True,
            timeout=10,
        )
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        raise HTTPException(
            status_code=500,
            detail="ffprobe/ffmpeg not found on PATH. Install ffmpeg for chunk stitching.",
        ) from e


def _bool_form_value(v: bool) -> str:
    return "true" if v else "false"


def lipsync_file_predict(
    video_path: str,
    audio_path: str,
    *,
    job_id: str | None = None,
    segment_index: int | None = None,
) -> str:
    t0 = time.perf_counter()
    form_data = {
        "box_top": str(LIPSYNC_BOX_TOP),
        "crop_top": str(LIPSYNC_CROP_TOP),
        "box_right": str(LIPSYNC_BOX_RIGHT),
        "fps": str(LIPSYNC_FPS),
        "pad_top": str(LIPSYNC_PAD_TOP),
        "crop_right": str(LIPSYNC_CROP_RIGHT),
        "nosmooth": _bool_form_value(LIPSYNC_NOSMOOTH),
        "pad_right": str(LIPSYNC_PAD_RIGHT),
        "pad_left": str(LIPSYNC_PAD_LEFT),
        "face_det_batch_size": str(LIPSYNC_FACE_DET_BATCH_SIZE),
        "crop_left": str(LIPSYNC_CROP_LEFT),
        "pad_bottom": str(LIPSYNC_PAD_BOTTOM),
        "wav2lip_batch_size": str(LIPSYNC_WAV2LIP_BATCH_SIZE),
        "crop_bottom": str(LIPSYNC_CROP_BOTTOM),
        "resize_factor": str(LIPSYNC_RESIZE_FACTOR),
        "rotate": _bool_form_value(LIPSYNC_ROTATE),
        "box_left": str(LIPSYNC_BOX_LEFT),
        "checkpoint_path": LIPSYNC_CHECKPOINT_PATH,
        "box_bottom": str(LIPSYNC_BOX_BOTTOM),
    }

    headers = {"accept": "application/json"}
    with open(video_path, "rb") as face_fp, open(audio_path, "rb") as audio_fp:
        files = {
            "face": (Path(video_path).name, face_fp, "video/mp4"),
            "audio": (Path(audio_path).name, audio_fp, "audio/wav"),
        }
        try:
            with httpx.Client(timeout=600.0) as client:
                resp = client.post(
                    LIPSYNC_FILE_API_URL,
                    data=form_data,
                    files=files,
                    headers=headers,
                )
        except httpx.HTTPError as e:
            raise HTTPException(status_code=502, detail=f"Lip-sync request failed: {e}") from e
    http_ms = (time.perf_counter() - t0) * 1000.0

    if resp.status_code >= 400:
        detail = resp.text[:800]
        raise HTTPException(
            status_code=502,
            detail=f"Lip-sync server error {resp.status_code}: {detail}",
        )

    ctype = resp.headers.get("content-type", "")
    if "video" not in ctype and "application/octet-stream" not in ctype:
        detail = resp.text[:800]
        raise HTTPException(
            status_code=502,
            detail=f"Lip-sync server returned non-video response ({ctype}): {detail}",
        )

    out_path = Path(video_path).with_name(f"lipsync_{uuid.uuid4().hex}.mp4")
    t1 = time.perf_counter()
    out_path.write_bytes(resp.content)
    write_ms = (time.perf_counter() - t1) * 1000.0
    jid = job_id or "unknown"
    logger.info(
        _timing_payload(
            "lipsync_file_api",
            jid,
            duration_ms=http_ms + write_ms,
            http_ms=round(http_ms, 2),
            write_bytes_ms=round(write_ms, 2),
            response_bytes=len(resp.content),
            segment_index=segment_index,
            status_code=resp.status_code,
        )
    )
    return str(out_path)


def run_lipsync_job(
    local_video: Path,
    audio_path: Path,
    work: Path,
    *,
    job_id: str | None = None,
) -> str:
    """Returns path to final mp4 (single call or stitched chunks)."""
    jid = job_id or "unknown"
    t_job = time.perf_counter()
    speech_sec = ffprobe_duration_seconds(str(audio_path))
    use_chunks = LIPSYNC_USE_CHUNKS
    if (
        use_chunks
        and LIPSYNC_AUTO_SINGLE_BELOW_SEC > 0
        and speech_sec < LIPSYNC_AUTO_SINGLE_BELOW_SEC
    ):
        use_chunks = False

    if use_chunks:
        _ensure_ffmpeg()
        parallel = LIPSYNC_CHUNK_PARALLEL

        def predict(vp: str, ap: str) -> Any:
            return lipsync_file_predict(vp, ap, job_id=jid)

        out = run_chunked_lipsync(
            source_video=str(local_video),
            speech_wav=audio_path,
            work=work,
            chunk_sec=LIPSYNC_CHUNK_SEC,
            overlap_sec=LIPSYNC_CHUNK_OVERLAP_SEC,
            predict=predict,
            resolve_output=resolve_output_video,
            parallel_workers=parallel,
        )
        logger.info(
            _timing_payload(
                "lipsync_job_chunked",
                jid,
                duration_ms=(time.perf_counter() - t_job) * 1000.0,
                speech_sec=round(speech_sec, 3),
                chunk_sec=LIPSYNC_CHUNK_SEC,
                parallel=parallel,
            )
        )
        return out

    result = lipsync_file_predict(str(local_video), str(audio_path), job_id=jid)
    out_path = resolve_output_video(result)
    logger.info(
        _timing_payload(
            "lipsync_job_single",
            jid,
            duration_ms=(time.perf_counter() - t_job) * 1000.0,
            speech_sec=round(speech_sec, 3),
        )
    )
    return out_path


def resolve_output_video(result: Any) -> str:
    if result is None:
        raise HTTPException(status_code=502, detail="Lip-sync server returned no result.")
    if isinstance(result, (list, tuple)) and result:
        candidate = result[0]
    else:
        candidate = result
    if isinstance(candidate, dict) and "video" in candidate:
        path = candidate["video"]
    elif isinstance(candidate, dict) and "name" in candidate:
        path = candidate["name"]
    else:
        path = candidate
    if not path or not isinstance(path, str):
        raise HTTPException(
            status_code=502,
            detail=f"Unexpected lip-sync output shape: {type(result)!r}",
        )
    if not os.path.isfile(path):
        raise HTTPException(status_code=502, detail=f"Output file missing: {path}")
    return path


def _active_llm_model_label() -> str:
    p = _llm_provider()
    if p == "openai":
        return os.environ.get("OPENAI_MODEL", "").strip() or "gpt-4o-mini"
    if p == "anthropic":
        return os.environ.get("ANTHROPIC_MODEL", "").strip() or "claude-3-5-haiku-20241022"
    if p == "google":
        return os.environ.get("GOOGLE_GEMINI_MODEL", "").strip() or "gemini-2.0-flash"
    return _ollama_model()


def _llm_public_config() -> dict[str, Any]:
    """Non-secret LLM / provider status for Studio and health (reads os.environ each call)."""
    ob = _ollama_base()
    om = _ollama_model()
    oa_url = os.environ.get("OPENAI_BASE_URL", "").strip() or "https://api.openai.com"
    oa_model = os.environ.get("OPENAI_MODEL", "").strip() or "gpt-4o-mini"
    an_url = os.environ.get("ANTHROPIC_BASE_URL", "").strip() or "https://api.anthropic.com"
    an_model = os.environ.get("ANTHROPIC_MODEL", "").strip() or "claude-3-5-haiku-20241022"
    gg_url = os.environ.get("GOOGLE_GEMINI_BASE_URL", "").strip() or "https://generativelanguage.googleapis.com"
    gg_model = os.environ.get("GOOGLE_GEMINI_MODEL", "").strip() or "gemini-2.0-flash"
    return {
        "provider": _llm_provider(),
        "local": {
            "ollama_base": ob,
            "ollama_model": om,
            "model_api_style": _model_api_style(),
            "openai_compatible": _is_openai_compatible_style(),
        },
        "openai": {
            "configured": bool(os.environ.get("OPENAI_API_KEY", "").strip()),
            "base_url": oa_url,
            "model": oa_model,
        },
        "anthropic": {
            "configured": bool(os.environ.get("ANTHROPIC_API_KEY", "").strip()),
            "base_url": an_url,
            "model": an_model,
        },
        "google": {
            "configured": bool(_google_gemini_api_key()),
            "base_url": gg_url,
            "model": gg_model,
        },
    }


@app.get(f"{HOLUMINEX_PREFIX}/api/health")
async def health():
    rag_info: dict[str, Any] = {
        "chunk_size": RAG_CHUNK_SIZE,
        "chunk_overlap": RAG_CHUNK_OVERLAP,
        "max_upload_mb": RAG_MAX_UPLOAD_BYTES // (1024 * 1024),
        "allowed_suffixes": sorted(RAG_ALLOWED_SUFFIXES),
        "voice_rag_enabled": VOICE_RAG_ENABLED,
        "voice_rag_n_results": VOICE_RAG_N_RESULTS,
        "voice_rag_max_context_chars": VOICE_RAG_MAX_CONTEXT_CHARS,
    }
    try:
        rag_info.update(rag_get_status())
    except Exception as e:
        rag_info["error"] = str(e)
    return {
        "ok": True,
        "ollama": _ollama_base(),
        "model": _ollama_model(),
        "model_api_style": _model_api_style(),
        "llm_provider": _llm_provider(),
        "llm": _llm_public_config(),
        "voice_max_tokens": VOICE_MAX_TOKENS,
        "lipsync_file_api": LIPSYNC_FILE_API_URL,
        "checkpoint_path": LIPSYNC_CHECKPOINT_PATH,
        "fps": LIPSYNC_FPS,
        "face_det_batch_size": LIPSYNC_FACE_DET_BATCH_SIZE,
        "wav2lip_batch_size": LIPSYNC_WAV2LIP_BATCH_SIZE,
        "chunk_streaming": LIPSYNC_USE_CHUNKS,
        "chunk_sec": LIPSYNC_CHUNK_SEC,
        "chunk_overlap_sec": LIPSYNC_CHUNK_OVERLAP_SEC,
        "auto_single_below_sec": LIPSYNC_AUTO_SINGLE_BELOW_SEC,
        "chunk_parallel": LIPSYNC_CHUNK_PARALLEL,
        "ollama_streaming": True,
        "realtime_lipsync": LIPSYNC_REALTIME,
        "rt_target_sec": LIPSYNC_RT_TARGET_SEC,
        "rt_min_chars": LIPSYNC_RT_MIN_CHARS,
        "webrtc_signaling_proxy": bool(WEBRTC_SIGNALING_BASE),
        "avatar_api_base": AVATAR_API_BASE,
        "rag": rag_info,
        "sharepoint": {
            **sharepoint_public_config(),
        },
        "google_drive": {
            **gdrive_public_config(),
        },
        "dropbox": {
            **dropbox_public_config(),
        },
        "s3": {
            **s3_public_config(),
        },
        "azure_blob": {
            **azure_blob_public_config(),
        },
        "gcs": {
            **gcs_public_config(),
        },
    }


@app.get(f"{HOLUMINEX_PREFIX}/api/webrtc")
async def webrtc_proxy_status():
    """Whether POST /offer, /human, /record are forwarded to WEBRTC_SIGNALING_BASE."""
    return {"signaling_proxy_configured": bool(WEBRTC_SIGNALING_BASE)}


def _build_voice_llm_prompt(user_text: str) -> tuple[str, dict[str, Any]]:
    """
    Build Ollama prompt for the hologram voice turn. Optionally prepends ChromaDB retrieval
    when VOICE_RAG_ENABLED and the knowledge base has chunks.
    """
    rag_meta: dict[str, Any] = {
        "enabled": VOICE_RAG_ENABLED,
        "used": False,
        "chunks": 0,
    }
    user_block = (
        f"User said:\n{user_text}\n\n"
        "Assistant (spoken reply only, no markdown):"
    )
    if not VOICE_RAG_ENABLED:
        return f"{VOICE_OLLAMA_INSTRUCTION}\n\n{user_block}", rag_meta

    try:
        st = rag_get_status()
        if int(st.get("chunk_count") or 0) == 0:
            return f"{VOICE_OLLAMA_INSTRUCTION}\n\n{user_block}", rag_meta
    except Exception as e:
        logger.warning("RAG status unavailable for voice-turn: %s", e)
        rag_meta["error"] = "status_unavailable"
        return f"{VOICE_OLLAMA_INSTRUCTION}\n\n{user_block}", rag_meta

    try:
        retrieved = query_documents(user_text, VOICE_RAG_N_RESULTS)
        results = retrieved.get("results") or []
    except Exception as e:
        logger.warning("RAG query failed for voice-turn: %s", e)
        rag_meta["error"] = str(e)
        return f"{VOICE_OLLAMA_INSTRUCTION}\n\n{user_block}", rag_meta

    if not results:
        return f"{VOICE_OLLAMA_INSTRUCTION}\n\n{user_block}", rag_meta

    parts: list[str] = []
    for i, hit in enumerate(results, 1):
        chunk_text = (hit.get("text") or "").strip()
        if not chunk_text:
            continue
        fn = (hit.get("metadata") or {}).get("filename", "document")
        parts.append(f"[{i}] ({fn})\n{chunk_text}")

    if not parts:
        return f"{VOICE_OLLAMA_INSTRUCTION}\n\n{user_block}", rag_meta

    context = "\n\n".join(parts)
    if len(context) > VOICE_RAG_MAX_CONTEXT_CHARS:
        context = context[: VOICE_RAG_MAX_CONTEXT_CHARS] + "…"

    rag_meta["used"] = True
    rag_meta["chunks"] = len(parts)
    prompt = (
        f"{VOICE_OLLAMA_INSTRUCTION}\n\n"
        "Relevant passages from uploaded documents "
        "(use when they help answer the user; if they do not apply, answer normally):\n"
        f"{context}\n\n"
        f"{user_block}"
    )
    return prompt, rag_meta


class VoiceTurnBody(BaseModel):
    text: str = Field(..., min_length=1, max_length=8000)


@app.post(f"{HOLUMINEX_PREFIX}/api/voice-turn")
async def voice_turn(body: VoiceTurnBody):
    """
    Browser STT text → optional ChromaDB RAG → Ollama (short avatar-friendly reply) → client sends answer to LiveTalking /human.
    """
    user_text = body.text.strip()
    if not user_text:
        raise HTTPException(status_code=400, detail="text is empty.")
    prompt, rag_meta = await asyncio.to_thread(_build_voice_llm_prompt, user_text)
    t0 = time.perf_counter()
    ollama_metrics: dict[str, Any] = {}
    answer = await ollama_generate(prompt, ollama_metrics, max_tokens=VOICE_MAX_TOKENS)
    total_ms = (time.perf_counter() - t0) * 1000.0
    if os.environ.get("ANALYTICS_DISABLE", "").strip().lower() not in (
        "1",
        "true",
        "yes",
    ):
        record_voice_turn(
            heard_chars=len(user_text),
            answer_chars=len(answer),
            total_request_ms=total_ms,
            ollama_wall_ms=total_ms,
            prompt_tokens=ollama_metrics.get("prompt_eval_count"),
            completion_tokens=ollama_metrics.get("eval_count"),
            ollama_total_duration_ns=ollama_metrics.get("total_duration_ns"),
            ollama_load_duration_ns=ollama_metrics.get("load_duration_ns"),
        )
        await _publish_analytics_snapshot()
    return {"answer": answer, "heard": user_text, "rag": rag_meta}


@app.get(f"{HOLUMINEX_PREFIX}/api/analytics/summary")
async def analytics_summary():
    """Aggregates for dashboard (voice /mic → Ollama turns)."""
    s = get_summary()
    return {"kind": "voice_turns", **s}


@app.get(f"{HOLUMINEX_PREFIX}/api/analytics/voice-turns")
async def analytics_voice_turns(limit: int = 50):
    return {"items": get_recent_voice_turns(limit)}


@app.get(f"{HOLUMINEX_PREFIX}/api/analytics/stream")
async def analytics_stream():
    """SSE stream of analytics snapshots (push updates, no client polling)."""
    q: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=1)
    _analytics_subscribers.add(q)

    async def gen() -> AsyncIterator[str]:
        try:
            # Send initial state immediately.
            yield sse_event({"type": "snapshot", **_analytics_snapshot(limit=40)})
            while True:
                try:
                    payload = await asyncio.wait_for(q.get(), timeout=20.0)
                    yield sse_event({"type": "snapshot", **payload})
                except asyncio.TimeoutError:
                    # Keep-alive comment for proxies and idle connections.
                    yield ": keepalive\n\n"
        finally:
            _analytics_subscribers.discard(q)

    return StreamingResponse(
        flush_sse_stream(gen()),
        media_type="text/event-stream",
        headers=dict(SSE_STREAM_HEADERS),
    )


class AnalyticsResetBody(BaseModel):
    secret: str = Field(..., min_length=1, max_length=256)


@app.post(f"{HOLUMINEX_PREFIX}/api/analytics/reset")
async def analytics_reset(body: AnalyticsResetBody):
    """Clears stored analytics when ``ANALYTICS_RESET_SECRET`` matches (kiosk / admin)."""
    expected = os.environ.get("ANALYTICS_RESET_SECRET", "").strip()
    if not expected or body.secret != expected:
        raise HTTPException(status_code=403, detail="Invalid or unset ANALYTICS_RESET_SECRET.")
    n = clear_all()
    await _publish_analytics_snapshot()
    return {"cleared": n}


class RagQueryBody(BaseModel):
    query: str = Field(..., min_length=1, max_length=4000)
    n_results: int = Field(6, ge=1, le=30)


@app.get(f"{HOLUMINEX_PREFIX}/api/rag/status")
async def rag_status():
    """ChromaDB persistent store: chunk counts and indexed sources."""
    try:
        base = rag_get_status()
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"RAG store unavailable: {e}") from e
    try:
        base["sources"] = rag_list_sources()
    except Exception:
        base["sources"] = []
    return base


@app.post(f"{HOLUMINEX_PREFIX}/api/rag/ingest")
async def rag_ingest(files: list[UploadFile] = File(...)):
    """Upload PDF, DOCX, or TXT — text is chunked and embedded into ChromaDB (on-disk)."""
    if not files:
        raise HTTPException(status_code=400, detail="No files uploaded.")
    results: list[dict[str, Any]] = []
    for uf in files:
        name = uf.filename or "document"
        suffix = Path(name).suffix.lower()
        if suffix not in RAG_ALLOWED_SUFFIXES:
            results.append(
                {
                    "filename": name,
                    "ok": False,
                    "error": f"Allowed types: {', '.join(sorted(RAG_ALLOWED_SUFFIXES))}",
                }
            )
            continue
        try:
            data = await uf.read()
            if len(data) > RAG_MAX_UPLOAD_BYTES:
                raise ValueError(f"File too large (max {RAG_MAX_UPLOAD_BYTES // (1024 * 1024)} MB).")
            info = await asyncio.to_thread(
                ingest_file,
                filename=name,
                data=data,
                chunk_size=RAG_CHUNK_SIZE,
                chunk_overlap=RAG_CHUNK_OVERLAP,
            )
            results.append({"filename": name, "ok": True, **info})
        except Exception as e:
            results.append({"filename": name, "ok": False, "error": str(e)})
    return {"results": results}


@app.post(f"{HOLUMINEX_PREFIX}/api/rag/query")
async def rag_query(body: RagQueryBody):
    """Semantic search over ingested documents (for RAG prompts or debugging)."""
    try:
        return query_documents(body.query.strip(), body.n_results)
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"RAG query failed: {e}") from e


@app.delete(f"{HOLUMINEX_PREFIX}/api/rag/sources/{{source_id}}")
async def rag_delete_source(source_id: str):
    if not source_id.strip() or len(source_id) > 64:
        raise HTTPException(status_code=400, detail="Invalid source_id.")
    try:
        n = delete_source(source_id.strip())
    except Exception as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    return {"deleted_chunks": n}


class RagResetBody(BaseModel):
    secret: str = Field(..., min_length=1, max_length=256)


class StudioLiveSyncBody(BaseModel):
    master_enabled: bool


class StudioSharePointConnect(BaseModel):
    azure_tenant_id: str = Field("", max_length=512)
    azure_client_id: str = Field("", max_length=512)
    azure_client_secret: str = Field("", max_length=512)
    sharepoint_site_url: str = Field("", max_length=2048)
    sharepoint_folder_path: str = Field("", max_length=1024)


class StudioDropboxConnect(BaseModel):
    dropbox_access_token: str = Field("", max_length=8192)
    dropbox_refresh_token: str = Field("", max_length=8192)
    dropbox_app_key: str = Field("", max_length=512)
    dropbox_app_secret: str = Field("", max_length=512)
    dropbox_folder_path: str = Field("", max_length=2048)


class StudioS3Connect(BaseModel):
    s3_bucket: str = Field("", max_length=256)
    s3_prefix: str = Field("", max_length=2048)
    aws_region: str = Field("", max_length=64)
    aws_access_key_id: str = Field("", max_length=256)
    aws_secret_access_key: str = Field("", max_length=256)
    aws_session_token: str = Field("", max_length=4096)
    s3_use_default_credential_chain: str = Field("", max_length=8)


class StudioAzureBlobConnect(BaseModel):
    azure_storage_connection_string: str = Field("", max_length=4096)
    azure_storage_account_name: str = Field("", max_length=256)
    azure_storage_account_key: str = Field("", max_length=512)
    azure_blob_container: str = Field("", max_length=256)
    azure_blob_prefix: str = Field("", max_length=2048)


class StudioPineconeConnect(BaseModel):
    pinecone_api_key: str = Field("", max_length=512)
    pinecone_index_name: str = Field("", max_length=256)
    pinecone_host: str = Field("", max_length=512)


class StudioMilvusConnect(BaseModel):
    milvus_uri: str = Field("", max_length=2048)
    milvus_token: str = Field("", max_length=8192)
    milvus_db_name: str = Field("", max_length=256)
    milvus_collection_name: str = Field("", max_length=256)


class StudioWeaviateConnect(BaseModel):
    weaviate_url: str = Field("", max_length=2048)
    weaviate_api_key: str = Field("", max_length=8192)
    weaviate_class_name: str = Field("", max_length=256)


class StudioQdrantConnect(BaseModel):
    qdrant_url: str = Field("", max_length=2048)
    qdrant_api_key: str = Field("", max_length=8192)
    qdrant_collection_name: str = Field("", max_length=256)


class StudioElasticsearchConnect(BaseModel):
    elasticsearch_url: str = Field("", max_length=2048)
    elasticsearch_api_key: str = Field("", max_length=8192)
    elasticsearch_index_name: str = Field("", max_length=256)


class StudioAzureAISearchConnect(BaseModel):
    azure_ai_search_endpoint: str = Field("", max_length=2048)
    azure_ai_search_api_key: str = Field("", max_length=8192)
    azure_ai_search_index_name: str = Field("", max_length=256)


class StudioLLMConnect(BaseModel):
    llm_provider: str = Field("", max_length=32)
    ollama_base: str = Field("", max_length=2048)
    ollama_model: str = Field("", max_length=512)
    model_api_style: str = Field("", max_length=32)
    openai_api_key: str = Field("", max_length=512)
    openai_base_url: str = Field("", max_length=2048)
    openai_model: str = Field("", max_length=256)
    anthropic_api_key: str = Field("", max_length=512)
    anthropic_base_url: str = Field("", max_length=2048)
    anthropic_model: str = Field("", max_length=256)
    google_api_key: str = Field("", max_length=512)
    google_gemini_base_url: str = Field("", max_length=2048)
    google_gemini_model: str = Field("", max_length=256)


@app.post(f"{HOLUMINEX_PREFIX}/api/rag/reset")
async def rag_reset(body: RagResetBody):
    """Wipes the Chroma collection when ``RAG_RESET_SECRET`` matches."""
    expected = os.environ.get("RAG_RESET_SECRET", "").strip()
    if not expected or body.secret != expected:
        raise HTTPException(status_code=403, detail="Invalid or unset RAG_RESET_SECRET.")
    try:
        await asyncio.to_thread(rag_reset_collection)
    except Exception as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    return {"ok": True}


@app.get(f"{HOLUMINEX_PREFIX}/api/sharepoint/config")
async def sharepoint_config():
    """Whether Azure / SharePoint env is set (no secrets returned)."""
    return sharepoint_public_config()


@app.post(f"{HOLUMINEX_PREFIX}/api/sharepoint/sync")
async def sharepoint_sync():
    """
    On-demand sync (same pipeline as live sync). Uses a lock so concurrent runs with the
    background loop do not overlap.
    """
    if not sharepoint_is_configured():
        raise HTTPException(
            status_code=503,
            detail="SharePoint is not configured. Set AZURE_TENANT_ID, AZURE_CLIENT_ID, "
            "AZURE_CLIENT_SECRET, and SHAREPOINT_SITE_URL.",
        )
    async with _sharepoint_sync_lock:
        try:
            return await _sharepoint_sync_execute()
        except RuntimeError as e:
            raise HTTPException(status_code=502, detail=str(e)) from e
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"SharePoint sync failed: {e}") from e


@app.get(f"{HOLUMINEX_PREFIX}/api/google-drive/config")
async def google_drive_config():
    """Whether Google Drive env is set (no secrets returned)."""
    return gdrive_public_config()


@app.post(f"{HOLUMINEX_PREFIX}/api/google-drive/sync")
async def google_drive_sync():
    """On-demand Google Drive → Chroma sync (same pipeline as live; lock prevents overlap)."""
    if not gdrive_is_configured():
        raise HTTPException(
            status_code=503,
            detail="Google Drive is not configured. Set GOOGLE_DRIVE_CREDENTIALS_PATH and "
            "GOOGLE_DRIVE_FOLDER_ID (share the folder with the service account email).",
        )
    async with _google_drive_sync_lock:
        try:
            return await _google_drive_sync_execute()
        except RuntimeError as e:
            raise HTTPException(status_code=502, detail=str(e)) from e
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"Google Drive sync failed: {e}") from e


@app.get(f"{HOLUMINEX_PREFIX}/api/dropbox/config")
async def dropbox_config():
    """Whether Dropbox env is set (no secrets returned)."""
    return dropbox_public_config()


@app.post(f"{HOLUMINEX_PREFIX}/api/dropbox/sync")
async def dropbox_sync():
    """On-demand Dropbox → Chroma sync (same pipeline as live; lock prevents overlap)."""
    if not dropbox_is_configured():
        raise HTTPException(
            status_code=503,
            detail="Dropbox is not configured. Set DROPBOX_ACCESS_TOKEN, or "
            "DROPBOX_REFRESH_TOKEN with DROPBOX_APP_KEY and DROPBOX_APP_SECRET. "
            "Optional: DROPBOX_FOLDER_PATH (default root).",
        )
    async with _dropbox_sync_lock:
        try:
            return await _dropbox_sync_execute()
        except RuntimeError as e:
            raise HTTPException(status_code=502, detail=str(e)) from e
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"Dropbox sync failed: {e}") from e


@app.get(f"{HOLUMINEX_PREFIX}/api/s3/config")
async def s3_config():
    return s3_public_config()


@app.post(f"{HOLUMINEX_PREFIX}/api/s3/sync")
async def s3_sync():
    if not s3_is_configured():
        raise HTTPException(
            status_code=503,
            detail="S3 is not configured. Set S3_BUCKET and AWS keys, or S3_USE_DEFAULT_CREDENTIAL_CHAIN=1.",
        )
    async with _s3_sync_lock:
        try:
            return await _s3_sync_execute()
        except RuntimeError as e:
            raise HTTPException(status_code=502, detail=str(e)) from e
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"S3 sync failed: {e}") from e


@app.get(f"{HOLUMINEX_PREFIX}/api/azure-blob/config")
async def azure_blob_config():
    return azure_blob_public_config()


@app.post(f"{HOLUMINEX_PREFIX}/api/azure-blob/sync")
async def azure_blob_sync():
    if not azure_blob_is_configured():
        raise HTTPException(
            status_code=503,
            detail="Azure Blob is not configured. Set AZURE_STORAGE_CONNECTION_STRING or "
            "AZURE_STORAGE_ACCOUNT_NAME + AZURE_STORAGE_ACCOUNT_KEY, and AZURE_BLOB_CONTAINER.",
        )
    async with _azure_blob_sync_lock:
        try:
            return await _azure_blob_sync_execute()
        except RuntimeError as e:
            raise HTTPException(status_code=502, detail=str(e)) from e
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"Azure Blob sync failed: {e}") from e


@app.get(f"{HOLUMINEX_PREFIX}/api/gcs/config")
async def gcs_config():
    return gcs_public_config()


@app.post(f"{HOLUMINEX_PREFIX}/api/gcs/sync")
async def gcs_sync():
    if not gcs_is_configured():
        raise HTTPException(
            status_code=503,
            detail="GCS is not configured. Set GCS_BUCKET and GCS_CREDENTIALS_PATH (or "
            "GOOGLE_APPLICATION_CREDENTIALS), or GCS_USE_ADC=1.",
        )
    async with _gcs_sync_lock:
        try:
            return await _gcs_sync_execute()
        except RuntimeError as e:
            raise HTTPException(status_code=502, detail=str(e)) from e
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"GCS sync failed: {e}") from e


@app.get(f"{HOLUMINEX_PREFIX}/api/llm/config")
async def llm_config():
    """Active LLM provider and non-secret connection hints (OpenAI / Anthropic keys never returned)."""
    return _llm_public_config()


@app.get(f"{HOLUMINEX_PREFIX}/api/studio/integrations")
async def studio_integrations_status():
    """Which connector settings are saved locally (no secret values)."""
    return studio_integrations_summary()


@app.get(f"{HOLUMINEX_PREFIX}/api/studio/live-sync")
async def studio_live_sync_get():
    """Global master switch: when false, all cloud → Chroma background loops pause."""
    return {"master_enabled": is_master_enabled()}


@app.post(f"{HOLUMINEX_PREFIX}/api/studio/live-sync")
async def studio_live_sync_set(body: StudioLiveSyncBody):
    set_master_enabled(body.master_enabled)
    return {
        "ok": True,
        "master_enabled": body.master_enabled,
        "summary": studio_integrations_summary(),
    }


@app.post(f"{HOLUMINEX_PREFIX}/api/studio/integrations/sharepoint")
async def studio_integrations_connect_sharepoint(body: StudioSharePointConnect):
    """Save SharePoint / Azure app credentials to studio_integrations.json and apply env."""
    studio_save_section(
        "sharepoint",
        {
            "AZURE_TENANT_ID": body.azure_tenant_id,
            "AZURE_CLIENT_ID": body.azure_client_id,
            "AZURE_CLIENT_SECRET": body.azure_client_secret,
            "SHAREPOINT_SITE_URL": body.sharepoint_site_url,
            "SHAREPOINT_FOLDER_PATH": body.sharepoint_folder_path,
        },
    )
    return {
        "ok": True,
        "summary": studio_integrations_summary(),
        "config": sharepoint_public_config(),
    }


@app.post(f"{HOLUMINEX_PREFIX}/api/studio/integrations/dropbox")
async def studio_integrations_connect_dropbox(body: StudioDropboxConnect):
    """Save Dropbox tokens / app keys to studio_integrations.json and apply env."""
    studio_save_section(
        "dropbox",
        {
            "DROPBOX_ACCESS_TOKEN": body.dropbox_access_token,
            "DROPBOX_REFRESH_TOKEN": body.dropbox_refresh_token,
            "DROPBOX_APP_KEY": body.dropbox_app_key,
            "DROPBOX_APP_SECRET": body.dropbox_app_secret,
            "DROPBOX_FOLDER_PATH": body.dropbox_folder_path,
        },
    )
    return {
        "ok": True,
        "summary": studio_integrations_summary(),
        "config": dropbox_public_config(),
    }


@app.post(f"{HOLUMINEX_PREFIX}/api/studio/integrations/s3")
async def studio_integrations_connect_s3(body: StudioS3Connect):
    studio_save_section(
        "s3",
        {
            "S3_BUCKET": body.s3_bucket,
            "S3_PREFIX": body.s3_prefix,
            "AWS_REGION": body.aws_region,
            "AWS_DEFAULT_REGION": body.aws_region,
            "AWS_ACCESS_KEY_ID": body.aws_access_key_id,
            "AWS_SECRET_ACCESS_KEY": body.aws_secret_access_key,
            "AWS_SESSION_TOKEN": body.aws_session_token,
            "S3_USE_DEFAULT_CREDENTIAL_CHAIN": body.s3_use_default_credential_chain,
        },
    )
    return {"ok": True, "summary": studio_integrations_summary(), "config": s3_public_config()}


@app.post(f"{HOLUMINEX_PREFIX}/api/studio/integrations/azure-blob")
async def studio_integrations_connect_azure_blob(body: StudioAzureBlobConnect):
    studio_save_section(
        "azure_blob",
        {
            "AZURE_STORAGE_CONNECTION_STRING": body.azure_storage_connection_string,
            "AZURE_STORAGE_ACCOUNT_NAME": body.azure_storage_account_name,
            "AZURE_STORAGE_ACCOUNT_KEY": body.azure_storage_account_key,
            "AZURE_BLOB_CONTAINER": body.azure_blob_container,
            "AZURE_BLOB_PREFIX": body.azure_blob_prefix,
        },
    )
    return {"ok": True, "summary": studio_integrations_summary(), "config": azure_blob_public_config()}


@app.post(f"{HOLUMINEX_PREFIX}/api/studio/integrations/gcs")
async def studio_integrations_connect_gcs(
    bucket: str = Form(""),
    prefix: str = Form(""),
    credentials_path: str = Form(""),
    gcs_use_adc: str = Form(""),
    credentials: UploadFile | None = File(None),
):
    vals: dict[str, str | None] = {}
    if bucket.strip():
        vals["GCS_BUCKET"] = bucket.strip()
    if prefix.strip():
        vals["GCS_PREFIX"] = prefix.strip()
    if gcs_use_adc.strip().lower() in ("1", "true", "yes"):
        vals["GCS_USE_ADC"] = "1"
    path_val = ""
    if credentials is not None and (credentials.filename or "").strip():
        raw = await credentials.read()
        if len(raw) > 2_000_000:
            raise HTTPException(status_code=400, detail="Credentials JSON is too large (max 2 MB).")
        STUDIO_GCS_CREDENTIALS_PATH.parent.mkdir(parents=True, exist_ok=True)
        STUDIO_GCS_CREDENTIALS_PATH.write_bytes(raw)
        path_val = str(STUDIO_GCS_CREDENTIALS_PATH.resolve())
    elif credentials_path.strip():
        path_val = credentials_path.strip()
    if path_val:
        vals["GCS_CREDENTIALS_PATH"] = path_val
    if not vals:
        raise HTTPException(
            status_code=400,
            detail="Provide bucket name, GCS_USE_ADC, and/or credentials JSON or path.",
        )
    studio_save_section("gcs", vals)
    return {"ok": True, "summary": studio_integrations_summary(), "config": gcs_public_config()}


@app.post(f"{HOLUMINEX_PREFIX}/api/studio/integrations/llm")
async def studio_integrations_connect_llm(body: StudioLLMConnect):
    """Save LLM provider (local / OpenAI / Anthropic / Google Gemini) and related settings; applies immediately."""
    prov = body.llm_provider.strip().lower()
    if prov not in ("local", "openai", "anthropic", "google"):
        raise HTTPException(
            status_code=400,
            detail="llm_provider must be local, openai, anthropic, or google.",
        )
    prev = studio_read_section_strings("llm")

    def set_or_clear(key: str, raw: str) -> str | None:
        s = raw.strip()
        return s if s else ""

    vals: dict[str, str | None] = {"LLM_PROVIDER": prov}
    vals["OLLAMA_BASE"] = set_or_clear("OLLAMA_BASE", body.ollama_base)
    vals["OLLAMA_MODEL"] = set_or_clear("OLLAMA_MODEL", body.ollama_model)
    vals["MODEL_API_STYLE"] = set_or_clear("MODEL_API_STYLE", body.model_api_style)
    vals["OPENAI_BASE_URL"] = set_or_clear("OPENAI_BASE_URL", body.openai_base_url)
    vals["OPENAI_MODEL"] = set_or_clear("OPENAI_MODEL", body.openai_model)
    vals["ANTHROPIC_BASE_URL"] = set_or_clear("ANTHROPIC_BASE_URL", body.anthropic_base_url)
    vals["ANTHROPIC_MODEL"] = set_or_clear("ANTHROPIC_MODEL", body.anthropic_model)
    vals["GOOGLE_GEMINI_BASE_URL"] = set_or_clear("GOOGLE_GEMINI_BASE_URL", body.google_gemini_base_url)
    vals["GOOGLE_GEMINI_MODEL"] = set_or_clear("GOOGLE_GEMINI_MODEL", body.google_gemini_model)
    if body.openai_api_key.strip():
        vals["OPENAI_API_KEY"] = body.openai_api_key.strip()
    if body.anthropic_api_key.strip():
        vals["ANTHROPIC_API_KEY"] = body.anthropic_api_key.strip()
    if body.google_api_key.strip():
        vals["GOOGLE_API_KEY"] = body.google_api_key.strip()

    openai_key = (
        body.openai_api_key.strip()
        or prev.get("OPENAI_API_KEY", "")
        or os.environ.get("OPENAI_API_KEY", "").strip()
    )
    anth_key = (
        body.anthropic_api_key.strip()
        or prev.get("ANTHROPIC_API_KEY", "")
        or os.environ.get("ANTHROPIC_API_KEY", "").strip()
    )
    google_key = (
        body.google_api_key.strip()
        or prev.get("GOOGLE_API_KEY", "")
        or _google_gemini_api_key()
    )
    if prov == "openai" and not openai_key:
        raise HTTPException(
            status_code=400,
            detail="OpenAI provider requires an API key (paste a key or keep one already saved in Studio).",
        )
    if prov == "anthropic" and not anth_key:
        raise HTTPException(
            status_code=400,
            detail="Anthropic provider requires an API key (paste a key or keep one already saved in Studio).",
        )
    if prov == "google" and not google_key:
        raise HTTPException(
            status_code=400,
            detail="Google Gemini requires GOOGLE_API_KEY (paste a key or keep one already saved in Studio).",
        )
    studio_save_section("llm", vals)
    return {"ok": True, "summary": studio_integrations_summary(), "config": _llm_public_config()}


@app.post(f"{HOLUMINEX_PREFIX}/api/studio/integrations/pinecone")
async def studio_integrations_connect_pinecone(body: StudioPineconeConnect):
    """Save Pinecone API settings to studio_integrations.json (ingest/query wiring is separate)."""
    prev = studio_read_section_strings("pinecone")
    api_key = body.pinecone_api_key.strip() or prev.get("PINECONE_API_KEY", "")
    index_name = body.pinecone_index_name.strip() or prev.get("PINECONE_INDEX_NAME", "")
    if not api_key or not index_name:
        raise HTTPException(
            status_code=400,
            detail="PINECONE_API_KEY and PINECONE_INDEX_NAME are required (paste a new API key or keep one already saved).",
        )
    host = body.pinecone_host.strip()
    vals: dict[str, str | None] = {
        "PINECONE_API_KEY": api_key,
        "PINECONE_INDEX_NAME": index_name,
        "PINECONE_HOST": host if host else None,
    }
    studio_save_section("pinecone", vals)
    return {"ok": True, "summary": studio_integrations_summary()}


@app.post(f"{HOLUMINEX_PREFIX}/api/studio/integrations/milvus")
async def studio_integrations_connect_milvus(body: StudioMilvusConnect):
    """Save Milvus / Zilliz connection settings to studio_integrations.json."""
    prev = studio_read_section_strings("milvus")
    uri = body.milvus_uri.strip() or prev.get("MILVUS_URI", "")
    collection = body.milvus_collection_name.strip() or prev.get("MILVUS_COLLECTION_NAME", "")
    if not uri or not collection:
        raise HTTPException(
            status_code=400,
            detail="MILVUS_URI and MILVUS_COLLECTION_NAME are required.",
        )
    token = body.milvus_token.strip() or prev.get("MILVUS_TOKEN", "")
    db = body.milvus_db_name.strip() or prev.get("MILVUS_DB_NAME", "")
    vals: dict[str, str | None] = {
        "MILVUS_URI": uri,
        "MILVUS_COLLECTION_NAME": collection,
        "MILVUS_TOKEN": token if token else None,
        "MILVUS_DB_NAME": db if db else None,
    }
    studio_save_section("milvus", vals)
    return {"ok": True, "summary": studio_integrations_summary()}


@app.post(f"{HOLUMINEX_PREFIX}/api/studio/integrations/weaviate")
async def studio_integrations_connect_weaviate(body: StudioWeaviateConnect):
    """Save Weaviate connection settings to studio_integrations.json."""
    prev = studio_read_section_strings("weaviate")
    url = body.weaviate_url.strip() or prev.get("WEAVIATE_URL", "")
    class_name = body.weaviate_class_name.strip() or prev.get("WEAVIATE_CLASS_NAME", "")
    if not url or not class_name:
        raise HTTPException(
            status_code=400,
            detail="WEAVIATE_URL and WEAVIATE_CLASS_NAME are required.",
        )
    api_key = body.weaviate_api_key.strip() or prev.get("WEAVIATE_API_KEY", "")
    vals: dict[str, str | None] = {
        "WEAVIATE_URL": url,
        "WEAVIATE_CLASS_NAME": class_name,
        "WEAVIATE_API_KEY": api_key if api_key else None,
    }
    studio_save_section("weaviate", vals)
    return {"ok": True, "summary": studio_integrations_summary()}


@app.post(f"{HOLUMINEX_PREFIX}/api/studio/integrations/qdrant")
async def studio_integrations_connect_qdrant(body: StudioQdrantConnect):
    """Save Qdrant connection settings to studio_integrations.json."""
    prev = studio_read_section_strings("qdrant")
    url = body.qdrant_url.strip() or prev.get("QDRANT_URL", "")
    collection = body.qdrant_collection_name.strip() or prev.get("QDRANT_COLLECTION_NAME", "")
    if not url or not collection:
        raise HTTPException(
            status_code=400,
            detail="QDRANT_URL and QDRANT_COLLECTION_NAME are required.",
        )
    api_key = body.qdrant_api_key.strip() or prev.get("QDRANT_API_KEY", "")
    vals: dict[str, str | None] = {
        "QDRANT_URL": url,
        "QDRANT_COLLECTION_NAME": collection,
        "QDRANT_API_KEY": api_key if api_key else None,
    }
    studio_save_section("qdrant", vals)
    return {"ok": True, "summary": studio_integrations_summary()}


@app.post(f"{HOLUMINEX_PREFIX}/api/studio/integrations/elasticsearch")
async def studio_integrations_connect_elasticsearch(body: StudioElasticsearchConnect):
    """Save Elasticsearch connection settings to studio_integrations.json."""
    prev = studio_read_section_strings("elasticsearch")
    url = body.elasticsearch_url.strip() or prev.get("ELASTICSEARCH_URL", "")
    index_name = body.elasticsearch_index_name.strip() or prev.get("ELASTICSEARCH_INDEX_NAME", "")
    if not url or not index_name:
        raise HTTPException(
            status_code=400,
            detail="ELASTICSEARCH_URL and ELASTICSEARCH_INDEX_NAME are required.",
        )
    api_key = body.elasticsearch_api_key.strip() or prev.get("ELASTICSEARCH_API_KEY", "")
    vals: dict[str, str | None] = {
        "ELASTICSEARCH_URL": url,
        "ELASTICSEARCH_INDEX_NAME": index_name,
        "ELASTICSEARCH_API_KEY": api_key if api_key else None,
    }
    studio_save_section("elasticsearch", vals)
    return {"ok": True, "summary": studio_integrations_summary()}


@app.post(f"{HOLUMINEX_PREFIX}/api/studio/integrations/azure-ai-search")
async def studio_integrations_connect_azure_ai_search(body: StudioAzureAISearchConnect):
    """Save Azure AI Search (formerly Cognitive Search) settings to studio_integrations.json."""
    prev = studio_read_section_strings("azure_ai_search")
    endpoint = body.azure_ai_search_endpoint.strip() or prev.get("AZURE_AI_SEARCH_ENDPOINT", "")
    index_name = body.azure_ai_search_index_name.strip() or prev.get("AZURE_AI_SEARCH_INDEX_NAME", "")
    if not endpoint or not index_name:
        raise HTTPException(
            status_code=400,
            detail="AZURE_AI_SEARCH_ENDPOINT and AZURE_AI_SEARCH_INDEX_NAME are required.",
        )
    api_key = body.azure_ai_search_api_key.strip() or prev.get("AZURE_AI_SEARCH_API_KEY", "")
    if not api_key:
        raise HTTPException(
            status_code=400,
            detail="AZURE_AI_SEARCH_API_KEY is required (paste an admin or query key, or keep one already saved).",
        )
    vals: dict[str, str | None] = {
        "AZURE_AI_SEARCH_ENDPOINT": endpoint.rstrip("/"),
        "AZURE_AI_SEARCH_INDEX_NAME": index_name,
        "AZURE_AI_SEARCH_API_KEY": api_key,
    }
    studio_save_section("azure_ai_search", vals)
    return {"ok": True, "summary": studio_integrations_summary()}


@app.post(f"{HOLUMINEX_PREFIX}/api/studio/integrations/google-drive")
async def studio_integrations_connect_google_drive(
    folder_id: str = Form(""),
    credentials_path: str = Form(""),
    credentials: UploadFile | None = File(None),
):
    """Save Google Drive folder ID and optional service-account JSON (upload or server path)."""
    vals: dict[str, str | None] = {}
    if folder_id.strip():
        vals["GOOGLE_DRIVE_FOLDER_ID"] = folder_id.strip()
    path_val = ""
    if credentials is not None and (credentials.filename or "").strip():
        raw = await credentials.read()
        if len(raw) > 2_000_000:
            raise HTTPException(status_code=400, detail="Credentials JSON is too large (max 2 MB).")
        STUDIO_GDRIVE_CREDENTIALS_PATH.parent.mkdir(parents=True, exist_ok=True)
        STUDIO_GDRIVE_CREDENTIALS_PATH.write_bytes(raw)
        path_val = str(STUDIO_GDRIVE_CREDENTIALS_PATH.resolve())
    elif credentials_path.strip():
        path_val = credentials_path.strip()
    if path_val:
        vals["GOOGLE_DRIVE_CREDENTIALS_PATH"] = path_val
    if not vals:
        raise HTTPException(
            status_code=400,
            detail="Provide a folder ID, upload a service account JSON, and/or a credentials file path.",
        )
    studio_save_section("google_drive", vals)
    return {
        "ok": True,
        "summary": studio_integrations_summary(),
        "config": gdrive_public_config(),
    }


@app.delete(f"{HOLUMINEX_PREFIX}/api/studio/integrations/{{section}}")
async def studio_integrations_clear(section: str):
    """Remove saved settings for a connector section."""
    if section not in (
        "sharepoint",
        "google_drive",
        "dropbox",
        "s3",
        "azure_blob",
        "gcs",
        "llm",
        "pinecone",
        "milvus",
        "weaviate",
        "qdrant",
        "elasticsearch",
        "azure_ai_search",
    ):
        raise HTTPException(status_code=404, detail="Unknown section.")
    studio_delete_section(section)
    return {"ok": True, "summary": studio_integrations_summary()}


@app.post(f"{HOLUMINEX_PREFIX}/api/avatar/save-voice")
async def avatar_save_voice(
    ref_aud: UploadFile = File(...),
    ref_txt: str = Form(...),
    use_xvec: bool = Form(False),
    voice_name: str = Form("avatar_voice"),
):
    if not ref_txt.strip():
        raise HTTPException(status_code=400, detail="ref_txt is required.")
    work = Path(tempfile.mkdtemp(prefix="avatar_save_"))
    try:
        ext = Path(ref_aud.filename or "ref.wav").suffix or ".wav"
        ref_path = work / f"ref{ext}"
        with ref_path.open("wb") as f:
            shutil.copyfileobj(ref_aud.file, f)

        result = await asyncio.to_thread(
            _avatar_predict,
            "/save_prompt",
            ref_aud=handle_file(str(ref_path)),
            ref_txt=ref_txt.strip(),
            use_xvec=bool(use_xvec),
        )
        items = _result_items(result)
        safe = "".join(ch if (ch.isalnum() or ch in ("-", "_")) else "_" for ch in voice_name.strip())
        safe = (safe or "avatar_voice")[:40]
        voice_file_url = await _store_gradio_file(
            items[0] if items else None,
            prefix=f"voice_prompt_{safe}",
            fallback_ext=".pt",
        )
        return {"voice_file_url": voice_file_url, "status": _result_status(items)}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Avatar save_prompt failed: {e}") from e
    finally:
        shutil.rmtree(work, ignore_errors=True)


@app.post(f"{HOLUMINEX_PREFIX}/api/avatar/load-and-generate")
async def avatar_load_and_generate(
    file_obj: UploadFile = File(...),
    text: str = Form(...),
    lang_disp: str = Form("Auto"),
):
    if not text.strip():
        raise HTTPException(status_code=400, detail="text is required.")
    work = Path(tempfile.mkdtemp(prefix="avatar_load_"))
    try:
        ext = Path(file_obj.filename or "voice_prompt.pt").suffix or ".pt"
        prompt_path = work / f"prompt{ext}"
        with prompt_path.open("wb") as f:
            shutil.copyfileobj(file_obj.file, f)

        result = await asyncio.to_thread(
            _avatar_predict,
            "/load_prompt_and_gen",
            file_obj=handle_file(str(prompt_path)),
            text=text.strip(),
            lang_disp=(lang_disp or "Auto"),
        )
        items = _result_items(result)
        audio_url = await _store_gradio_file(
            items[0] if items else None,
            prefix="avatar_tts",
            fallback_ext=".wav",
        )
        return {"audio_url": audio_url, "status": _result_status(items)}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Avatar load_prompt_and_gen failed: {e}") from e
    finally:
        shutil.rmtree(work, ignore_errors=True)


@app.post(f"{HOLUMINEX_PREFIX}/api/avatar/run-voice-clone")
async def avatar_run_voice_clone(
    ref_aud: UploadFile = File(...),
    ref_txt: str = Form(...),
    use_xvec: bool = Form(False),
    text: str = Form(...),
    lang_disp: str = Form("Auto"),
):
    if not ref_txt.strip():
        raise HTTPException(status_code=400, detail="ref_txt is required.")
    if not text.strip():
        raise HTTPException(status_code=400, detail="text is required.")
    work = Path(tempfile.mkdtemp(prefix="avatar_clone_"))
    try:
        ext = Path(ref_aud.filename or "ref.wav").suffix or ".wav"
        ref_path = work / f"ref{ext}"
        with ref_path.open("wb") as f:
            shutil.copyfileobj(ref_aud.file, f)

        result = await asyncio.to_thread(
            _avatar_predict,
            "/run_voice_clone",
            ref_aud=handle_file(str(ref_path)),
            ref_txt=ref_txt.strip(),
            use_xvec=bool(use_xvec),
            text=text.strip(),
            lang_disp=(lang_disp or "Auto"),
        )
        items = _result_items(result)
        audio_url = await _store_gradio_file(
            items[0] if items else None,
            prefix="avatar_clone",
            fallback_ext=".wav",
        )
        return {"audio_url": audio_url, "status": _result_status(items)}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Avatar run_voice_clone failed: {e}") from e
    finally:
        shutil.rmtree(work, ignore_errors=True)


@app.get(f"{HOLUMINEX_PREFIX}/api/avatar/config")
async def avatar_config():
    return {"sample_text": AVATAR_SAMPLE_TEXT}


@app.get(f"{HOLUMINEX_PREFIX}/api/avatar/voices")
async def avatar_voices():
    items: list[dict[str, str]] = []
    for p in sorted(OUTPUT_DIR.glob("*.pt"), key=lambda x: x.stat().st_mtime, reverse=True):
        stem = p.stem
        display = stem
        if stem.startswith("voice_prompt_"):
            rest = stem[len("voice_prompt_") :]
            parts = rest.rsplit("_", 1)
            display = parts[0] if len(parts) == 2 else rest
        display = display.replace("_", " ").strip() or "Avatar Voice"
        items.append({"name": display, "url": _hx(f"/outputs/{p.name}")})
    return {"items": items}


@app.post(f"{HOLUMINEX_PREFIX}/api/avatar/save-customer-recording")
async def avatar_save_customer_recording(
    audio: UploadFile = File(...),
    customer_name: str = Form("customer"),
    prompt_text: str = Form(""),
):
    ext = Path(audio.filename or "recording.webm").suffix or ".webm"
    safe = "".join(ch if (ch.isalnum() or ch in ("-", "_")) else "_" for ch in customer_name.strip())
    safe = (safe or "customer")[:40]
    out_name = f"customer_recording_{safe}_{uuid.uuid4().hex}{ext}"
    out_path = OUTPUT_DIR / out_name
    with out_path.open("wb") as f:
        shutil.copyfileobj(audio.file, f)
    status = "Recording saved."
    if prompt_text.strip():
        status = "Recording saved with prompt text."
    return {"audio_url": _hx(f"/outputs/{out_name}"), "status": status}


@app.post(f"{HOLUMINEX_PREFIX}/api/lipsync")
async def lipsync_qa(
    video: UploadFile = File(...),
    question: str = Form(...),
):
    if not question.strip():
        raise HTTPException(status_code=400, detail="Question is required.")

    job_id = str(uuid.uuid4())
    work = Path(tempfile.mkdtemp(prefix=f"lipsync_{job_id}_"))

    try:
        t_req = time.perf_counter()
        video_suffix = Path(video.filename or "input.mp4").suffix or ".mp4"
        local_video = work / f"input{video_suffix}"
        with local_video.open("wb") as f:
            shutil.copyfileobj(video.file, f)

        t_ollama = time.perf_counter()
        answer = await ollama_generate(question.strip())
        logger.info(
            _timing_payload(
                "ollama_generate",
                job_id,
                duration_ms=(time.perf_counter() - t_ollama) * 1000.0,
                answer_chars=len(answer),
            )
        )

        audio_path = work / "speech.wav"
        t_tts = time.perf_counter()
        await text_to_wav(answer, audio_path)
        logger.info(
            _timing_payload(
                "tts_edge",
                job_id,
                duration_ms=(time.perf_counter() - t_tts) * 1000.0,
            )
        )

        loop = asyncio.get_event_loop()
        try:
            t_ls = time.perf_counter()
            src = await loop.run_in_executor(
                None,
                lambda: run_lipsync_job(
                    local_video,
                    audio_path,
                    work,
                    job_id=job_id,
                ),
            )
            logger.info(
                _timing_payload(
                    "lipsync_job_wall",
                    job_id,
                    duration_ms=(time.perf_counter() - t_ls) * 1000.0,
                )
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

        out_name = f"{job_id}.mp4"
        dest = OUTPUT_DIR / out_name
        shutil.copy2(src, dest)

        logger.info(
            _timing_payload(
                "api_lipsync_total",
                job_id,
                duration_ms=(time.perf_counter() - t_req) * 1000.0,
            )
        )
        return {
            "answer": answer,
            "video_url": _hx(f"/outputs/{out_name}"),
        }
    finally:
        shutil.rmtree(work, ignore_errors=True)


@app.post(f"{HOLUMINEX_PREFIX}/api/lipsync/stream")
async def lipsync_qa_stream(
    video: UploadFile = File(...),
    question: str = Form(...),
):
    """SSE: Ollama tokens + realtime lip-sync segments (optional) or legacy wait-then-lipsync."""
    if not question.strip():
        raise HTTPException(status_code=400, detail="Question is required.")

    job_id = str(uuid.uuid4())
    work = Path(tempfile.mkdtemp(prefix=f"lipsync_{job_id}_"))
    video_suffix = Path(video.filename or "input.mp4").suffix or ".mp4"
    local_video = work / f"input{video_suffix}"
    content = await video.read()
    local_video.write_bytes(content)

    prompt = question.strip()
    max_chars = int(LIPSYNC_RT_TARGET_SEC * 12) + 50

    async def gen():
        try:
            t_stream = time.perf_counter()
            yield sse_event(
                {
                    "type": "start",
                    "model": _active_llm_model_label(),
                    "llm_provider": _llm_provider(),
                    "realtime": LIPSYNC_REALTIME,
                    "rt_target_sec": LIPSYNC_RT_TARGET_SEC,
                }
            )

            if LIPSYNC_REALTIME:
                # Decouple SSE from lip-sync HTTP calls: tokens forward on one task;
                # lip-sync runs sequentially on another.
                # Otherwise await(run_segment) blocks the generator and the browser sees nothing until each GPU step ends.
                _ensure_ffmpeg()
                out_q: asyncio.Queue = asyncio.Queue()
                char_q: asyncio.Queue[str | None] = asyncio.Queue()
                lip_q: asyncio.Queue[tuple[int, str] | None] = asyncio.Queue()
                pipeline_error: list[BaseException | None] = [None]
                full_text: list[str] = []
                segment_by_idx: dict[int, str] = {}
                video_cursor = [0.0]

                loop = asyncio.get_event_loop()

                def run_segment_sync(seg: str, idx: int) -> str:
                    def predict_for_seg(vp: str, ap: str) -> Any:
                        return lipsync_file_predict(
                            vp,
                            ap,
                            job_id=job_id,
                            segment_index=idx,
                        )

                    return process_segment_sync(
                        idx,
                        seg,
                        work=work,
                        source_video=local_video,
                        video_cursor=video_cursor,
                        voice=TTS_VOICE,
                        predict=predict_for_seg,
                        resolve_output=resolve_output_video,
                        job_id=job_id,
                    )

                async def forward_ollama() -> None:
                    t_o = time.perf_counter()
                    first = True
                    n_chunks = 0
                    try:
                        async for tok in ollama_stream_tokens(prompt):
                            if first:
                                logger.info(
                                    _timing_payload(
                                        "ollama_first_token",
                                        job_id,
                                        duration_ms=(time.perf_counter() - t_o) * 1000.0,
                                    )
                                )
                                first = False
                            n_chunks += 1
                            full_text.append(tok)
                            await out_q.put(("token", tok))
                            await char_q.put(tok)
                        await char_q.put(None)
                        logger.info(
                            _timing_payload(
                                "ollama_stream_complete",
                                job_id,
                                duration_ms=(time.perf_counter() - t_o) * 1000.0,
                                token_chunks=n_chunks,
                            )
                        )
                    except BaseException as e:
                        pipeline_error[0] = e
                        await char_q.put(None)

                async def buffer_to_segments() -> None:
                    buffer = ""
                    idx = 0
                    # No TTS/lipsync until a full first sentence; avoid max-length flush before that.
                    first_sentence_sent = False
                    try:
                        while True:
                            ch = await char_q.get()
                            if ch is None:
                                break
                            buffer += ch
                            while True:
                                seg, buffer = split_next_segment(
                                    buffer,
                                    min_chars=LIPSYNC_RT_MIN_CHARS,
                                    max_chars=max_chars,
                                    force=False,
                                    allow_size_flush=first_sentence_sent,
                                    require_sentence_punct=not first_sentence_sent,
                                )
                                if not seg:
                                    break
                                await lip_q.put((idx, seg))
                                idx += 1
                                first_sentence_sent = True
                        while buffer.strip():
                            seg, buffer = split_next_segment(
                                buffer,
                                min_chars=max(1, min(4, LIPSYNC_RT_MIN_CHARS)),
                                max_chars=max_chars,
                                force=True,
                            )
                            if not seg:
                                break
                            await lip_q.put((idx, seg))
                            idx += 1
                    finally:
                        await lip_q.put(None)

                async def lip_worker() -> None:
                    while True:
                        job = await lip_q.get()
                        if job is None:
                            break
                        idx, seg = job
                        t_seg_wall = time.perf_counter()
                        await out_q.put(
                            (
                                "status",
                                f"Lip-sync segment {idx + 1}…",
                            )
                        )
                        try:
                            out = await loop.run_in_executor(
                                None,
                                lambda s=seg, i=idx: run_segment_sync(s, i),
                            )
                            logger.info(
                                _timing_payload(
                                    "lip_worker_segment_wall",
                                    job_id,
                                    duration_ms=(time.perf_counter() - t_seg_wall) * 1000.0,
                                    segment_index=idx,
                                )
                            )
                        except Exception as e:
                            if isinstance(e, HTTPException):
                                detail = e.detail
                                msg = (
                                    detail
                                    if isinstance(detail, str)
                                    else str(detail)
                                )
                            else:
                                msg = str(e)
                            await out_q.put(("error", msg))
                            return
                        segment_by_idx[idx] = out
                        pub = OUTPUT_DIR / f"{job_id}_seg_{idx:04d}.mp4"
                        shutil.copy2(out, pub)
                        await out_q.put(
                            (
                                "segment_ready",
                                idx,
                                _hx(f"/outputs/{pub.name}"),
                            )
                        )
                    await out_q.put(("lip_done", None))

                async def closer() -> None:
                    await asyncio.gather(
                        forward_ollama(),
                        buffer_to_segments(),
                        lip_worker(),
                    )
                    await out_q.put(("pipeline_done", None))

                closer_task = asyncio.create_task(closer())

                while True:
                    ev = await out_q.get()
                    if ev[0] == "token":
                        _, content = ev
                        yield sse_event({"type": "token", "content": content})
                    elif ev[0] == "status":
                        yield sse_event(
                            {"type": "status", "stage": "lipsync", "message": ev[1]}
                        )
                    elif ev[0] == "segment_ready":
                        _, idx, url = ev
                        yield sse_event(
                            {
                                "type": "segment_ready",
                                "index": idx,
                                "video_url": url,
                            }
                        )
                    elif ev[0] == "error":
                        yield sse_event({"type": "error", "message": ev[1]})
                        closer_task.cancel()
                        return
                    elif ev[0] == "lip_done":
                        continue
                    elif ev[0] == "pipeline_done":
                        break

                if not closer_task.done():
                    try:
                        await closer_task
                    except asyncio.CancelledError:
                        pass

                err = pipeline_error[0]
                if err is not None:
                    if isinstance(err, HTTPException):
                        msg = err.detail if isinstance(err.detail, str) else str(err.detail)
                    else:
                        msg = str(err)
                    yield sse_event({"type": "error", "message": msg})
                    return

                answer = "".join(full_text).strip()
                if not answer:
                    yield sse_event({"type": "error", "message": "Ollama returned an empty response."})
                    return

                if not segment_by_idx:
                    yield sse_event(
                        {"type": "status", "stage": "lipsync", "message": "Lip-sync (full reply)…"}
                    )
                    try:
                        t_fb = time.perf_counter()
                        out = await loop.run_in_executor(
                            None,
                            lambda: run_segment_sync(answer, 0),
                        )
                        logger.info(
                            _timing_payload(
                                "realtime_fallback_full_reply_segment",
                                job_id,
                                duration_ms=(time.perf_counter() - t_fb) * 1000.0,
                            )
                        )
                    except HTTPException as e:
                        detail = e.detail
                        msg = detail if isinstance(detail, str) else str(detail)
                        yield sse_event({"type": "error", "message": msg})
                        return
                    segment_by_idx[0] = out
                    pub = OUTPUT_DIR / f"{job_id}_seg_0000.mp4"
                    shutil.copy2(out, pub)
                    yield sse_event(
                        {
                            "type": "segment_ready",
                            "index": 0,
                            "video_url": _hx(f"/outputs/{pub.name}"),
                        }
                    )

                ordered = [segment_by_idx[i] for i in sorted(segment_by_idx)]
                stitched = work / "rt_stitched.mp4"
                t_cat = time.perf_counter()
                ffmpeg_concat_videos(ordered, str(stitched))
                logger.info(
                    _timing_payload(
                        "ffmpeg_concat_stitch",
                        job_id,
                        duration_ms=(time.perf_counter() - t_cat) * 1000.0,
                        segment_count=len(ordered),
                    )
                )
                out_name = f"{job_id}.mp4"
                dest = OUTPUT_DIR / out_name
                shutil.copy2(stitched, dest)

                yield sse_event(
                    {
                        "type": "done",
                        "answer": answer,
                        "video_url": _hx(f"/outputs/{out_name}"),
                        "realtime": True,
                    }
                )
                logger.info(
                    _timing_payload(
                        "sse_stream_realtime_total",
                        job_id,
                        duration_ms=(time.perf_counter() - t_stream) * 1000.0,
                        segments=len(segment_by_idx),
                    )
                )
                return

            # --- legacy: wait for full Ollama reply, then TTS + lip-sync ---
            parts: list[str] = []
            t_legacy = time.perf_counter()
            try:
                t_o = time.perf_counter()
                first = True
                ntok = 0
                async for token in ollama_stream_tokens(prompt):
                    if first:
                        logger.info(
                            _timing_payload(
                                "ollama_first_token",
                                job_id,
                                duration_ms=(time.perf_counter() - t_o) * 1000.0,
                            )
                        )
                        first = False
                    ntok += 1
                    parts.append(token)
                    yield sse_event({"type": "token", "content": token})
                logger.info(
                    _timing_payload(
                        "ollama_stream_complete",
                        job_id,
                        duration_ms=(time.perf_counter() - t_o) * 1000.0,
                        token_chunks=ntok,
                    )
                )
            except HTTPException as e:
                detail = e.detail
                msg = detail if isinstance(detail, str) else str(detail)
                yield sse_event({"type": "error", "message": msg})
                return

            answer = "".join(parts).strip()
            if not answer:
                yield sse_event({"type": "error", "message": "Ollama returned an empty response."})
                return

            yield sse_event({"type": "status", "stage": "tts", "message": "Synthesizing speech…"})

            audio_path = work / "speech.wav"
            t_tts = time.perf_counter()
            await text_to_wav(answer, audio_path)
            logger.info(
                _timing_payload(
                    "tts_edge",
                    job_id,
                    duration_ms=(time.perf_counter() - t_tts) * 1000.0,
                )
            )

            yield sse_event({"type": "status", "stage": "lipsync", "message": "Running lip-sync…"})

            loop = asyncio.get_event_loop()
            try:
                t_ls = time.perf_counter()
                src = await loop.run_in_executor(
                    None,
                    lambda: run_lipsync_job(
                        local_video,
                        audio_path,
                        work,
                        job_id=job_id,
                    ),
                )
                logger.info(
                    _timing_payload(
                        "lipsync_job_wall",
                        job_id,
                        duration_ms=(time.perf_counter() - t_ls) * 1000.0,
                    )
                )
            except ValueError as e:
                yield sse_event({"type": "error", "message": str(e)})
                return
            except HTTPException as e:
                detail = e.detail
                msg = detail if isinstance(detail, str) else str(detail)
                yield sse_event({"type": "error", "message": msg})
                return

            out_name = f"{job_id}.mp4"
            dest = OUTPUT_DIR / out_name
            shutil.copy2(src, dest)

            yield sse_event(
                {
                    "type": "done",
                    "answer": answer,
                    "video_url": _hx(f"/outputs/{out_name}"),
                }
            )
            logger.info(
                _timing_payload(
                    "sse_stream_legacy_total",
                    job_id,
                    duration_ms=(time.perf_counter() - t_legacy) * 1000.0,
                )
            )
        except Exception as e:
            yield sse_event({"type": "error", "message": str(e)})
        finally:
            shutil.rmtree(work, ignore_errors=True)

    return StreamingResponse(
        flush_sse_stream(gen()),
        media_type="text/event-stream",
        headers=dict(SSE_STREAM_HEADERS),
    )


# Built frontend from `frontend` → `backend/static/dist` (e.g. Docker image)
_spa_dist = _BACKEND_DIR / "static" / "dist"
if _spa_dist.is_dir():

    @app.get("/")
    async def _redirect_root_to_holuminex() -> RedirectResponse:
        return RedirectResponse(url=f"{HOLUMINEX_PREFIX}/")

    app.mount(HOLUMINEX_PREFIX, StaticFiles(directory=str(_spa_dist), html=True), name="spa")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=int(os.environ.get("PORT", "6064")), reload=True)
