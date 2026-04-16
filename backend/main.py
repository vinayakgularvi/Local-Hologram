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

import edge_tts
import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from analytics_store import clear_all, get_recent_voice_turns, get_summary, init_db, record_voice_turn
from chunk_pipeline import ffmpeg_concat_videos, ffprobe_duration_seconds, run_chunked_lipsync
from realtime_lipsync import process_segment_sync, split_next_segment

_BACKEND_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _BACKEND_DIR.parent
load_dotenv(_REPO_ROOT / ".env")
load_dotenv(_BACKEND_DIR / ".env")


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


OLLAMA_BASE = os.environ.get("OLLAMA_BASE", "http://127.0.0.1:11434").rstrip("/")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "llama3.1:8b")
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

OUTPUT_DIR = Path(__file__).resolve().parent / "outputs"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="Lip-Sync Agent")


@app.on_event("startup")
def _analytics_startup() -> None:
    init_db()


app.add_middleware(
    CORSMiddleware,
    allow_origins=os.environ.get("CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173").split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/outputs", StaticFiles(directory=str(OUTPUT_DIR)), name="outputs")


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


@app.post("/offer")
async def webrtc_offer_proxy(request: Request) -> Response:
    return await _forward_webrtc_post("/offer", request)


@app.post("/human")
async def webrtc_human_proxy(request: Request) -> Response:
    return await _forward_webrtc_post("/human", request)


@app.post("/record")
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


async def ollama_stream_tokens(
    prompt: str,
    metrics: dict[str, Any] | None = None,
) -> AsyncIterator[str]:
    """Yields incremental text tokens from Ollama's streaming /api/generate.

    If ``metrics`` is provided, the final ``done`` chunk fills token counts and durations.
    """
    payload = {
        "model": OLLAMA_MODEL,
        "prompt": prompt,
        "stream": True,
        "options": {"temperature": 0.7},
    }
    async with httpx.AsyncClient(timeout=300.0) as client:
        async with client.stream(
            "POST",
            f"{OLLAMA_BASE}/api/generate",
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


async def ollama_generate(prompt: str, metrics: dict[str, Any] | None = None) -> str:
    parts: list[str] = []
    async for t in ollama_stream_tokens(prompt, metrics):
        parts.append(t)
    text = "".join(parts).strip()
    if not text:
        raise HTTPException(status_code=502, detail="Ollama returned an empty response.")
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


@app.get("/api/health")
async def health():
    return {
        "ok": True,
        "ollama": OLLAMA_BASE,
        "model": OLLAMA_MODEL,
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
    }


@app.get("/api/webrtc")
async def webrtc_proxy_status():
    """Whether POST /offer, /human, /record are forwarded to WEBRTC_SIGNALING_BASE."""
    return {"signaling_proxy_configured": bool(WEBRTC_SIGNALING_BASE)}


class VoiceTurnBody(BaseModel):
    text: str = Field(..., min_length=1, max_length=8000)


@app.post("/api/voice-turn")
async def voice_turn(body: VoiceTurnBody):
    """
    Browser STT text → Ollama (short avatar-friendly reply) → client sends answer to LiveTalking /human.
    """
    user_text = body.text.strip()
    if not user_text:
        raise HTTPException(status_code=400, detail="text is empty.")
    prompt = (
        f"{VOICE_OLLAMA_INSTRUCTION}\n\nUser said:\n{user_text}\n\n"
        "Assistant (spoken reply only, no markdown):"
    )
    t0 = time.perf_counter()
    ollama_metrics: dict[str, Any] = {}
    answer = await ollama_generate(prompt, ollama_metrics)
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
    return {"answer": answer, "heard": user_text}


@app.get("/api/analytics/summary")
async def analytics_summary():
    """Aggregates for dashboard (voice /mic → Ollama turns)."""
    s = get_summary()
    return {"kind": "voice_turns", **s}


@app.get("/api/analytics/voice-turns")
async def analytics_voice_turns(limit: int = 50):
    return {"items": get_recent_voice_turns(limit)}


class AnalyticsResetBody(BaseModel):
    secret: str = Field(..., min_length=1, max_length=256)


@app.post("/api/analytics/reset")
async def analytics_reset(body: AnalyticsResetBody):
    """Clears stored analytics when ``ANALYTICS_RESET_SECRET`` matches (kiosk / admin)."""
    expected = os.environ.get("ANALYTICS_RESET_SECRET", "").strip()
    if not expected or body.secret != expected:
        raise HTTPException(status_code=403, detail="Invalid or unset ANALYTICS_RESET_SECRET.")
    n = clear_all()
    return {"cleared": n}


@app.post("/api/lipsync")
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
            "video_url": f"/outputs/{out_name}",
        }
    finally:
        shutil.rmtree(work, ignore_errors=True)


@app.post("/api/lipsync/stream")
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
                    "model": OLLAMA_MODEL,
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
                                f"/outputs/{pub.name}",
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
                            "video_url": f"/outputs/{pub.name}",
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
                        "video_url": f"/outputs/{out_name}",
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
                    "video_url": f"/outputs/{out_name}",
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
    app.mount("/", StaticFiles(directory=str(_spa_dist), html=True), name="spa")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=int(os.environ.get("PORT", "8000")), reload=True)
