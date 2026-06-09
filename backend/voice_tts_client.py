"""Voice-clone TTS (/v1/tts/reference) and LiveTalking /humanaudiowithpath upload."""

from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import asdict, dataclass
from typing import Any

import httpx

from hologram_trace import voice_trace

logger = logging.getLogger(__name__)


@dataclass
class VoiceDispatchTiming:
    """Per-chunk voice dispatch timings (ms)."""

    mode: str
    chars: int
    tts_ms: float | None = None
    humanaudio_ms: float | None = None
    total_ms: float | None = None
    human_dispatch_ms: float | None = None
    tts_parallel: bool | None = None

    def as_dict(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v is not None}


@dataclass
class HumanaudioWorkItem:
    seq: int
    text: str
    interrupt: bool
    chunk_index: int
    generation: int = 0
    analytics_turn_id: int | None = None
    record_analytics: bool = True
    latency_sink: list[dict[str, Any]] | None = None
    stream_start: float | None = None
    enqueued_at: float | None = None


def _env_float(name: str, default: float) -> float:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def voice_stream_parallel_enabled() -> bool:
    return os.environ.get("VOICE_STREAM_PARALLEL", "1").strip().lower() in (
        "1",
        "true",
        "yes",
    )


def tts_max_parallel() -> int:
    return max(1, min(_env_int("VOICE_TTS_MAX_PARALLEL", 4), 8))


def voice_dispatch_mode() -> str:
    raw = (os.environ.get("VOICE_DISPATCH_MODE") or "human").strip().lower()
    if raw in ("humanaudio", "tts", "voice_clone", "external_tts"):
        return "humanaudio"
    return "human"


def tts_api_url() -> str:
    explicit = (os.environ.get("VOICE_TTS_API_URL") or "").strip().rstrip("/")
    if explicit:
        return explicit
    base = (os.environ.get("AVATAR_API_BASE") or "http://127.0.0.1:9000").strip().rstrip("/")
    return f"{base}/v1/tts/reference"


def tts_reference_id() -> str:
    ref = (os.environ.get("VOICE_TTS_REFERENCE_ID") or "").strip()
    if not ref:
        raise RuntimeError(
            "VOICE_TTS_REFERENCE_ID is not set (reference voice id for /v1/tts/reference)."
        )
    return ref


def _tts_reference_form_fields(gen_text: str) -> dict[str, str]:
    return {
        "sway_sampling_coef": os.environ.get("VOICE_TTS_SWAY_SAMPLING_COEF", "-1").strip() or "-1",
        "remove_silence": os.environ.get("VOICE_TTS_REMOVE_SILENCE", "false").strip() or "false",
        "speed": os.environ.get("VOICE_TTS_SPEED", "1").strip() or "1",
        "target_rms": os.environ.get("VOICE_TTS_TARGET_RMS", "0.1").strip() or "0.1",
        "use_cache": os.environ.get("VOICE_TTS_USE_CACHE", "true").strip() or "true",
        "overwrite": os.environ.get("VOICE_TTS_OVERWRITE", "false").strip() or "false",
        "reference_id": tts_reference_id(),
        "seed": os.environ.get("VOICE_TTS_SEED", "0").strip() or "0",
        "cross_fade_duration": os.environ.get("VOICE_TTS_CROSS_FADE_DURATION", "0.15").strip() or "0.15",
        "gen_text": gen_text.strip(),
        "cfg_strength": os.environ.get("VOICE_TTS_CFG_STRENGTH", "2").strip() or "2",
        "nfe_step": os.environ.get("VOICE_TTS_NFE_STEP", "32").strip() or "32",
        "fix_duration": os.environ.get("VOICE_TTS_FIX_DURATION", "0").strip() or "0",
    }


def synthesize_speech(gen_text: str) -> tuple[str, float]:
    """POST /v1/tts/reference; return (output_path on TTS host, elapsed_ms)."""
    text = (gen_text or "").strip()
    if not text:
        raise ValueError("empty gen_text for TTS")
    url = tts_api_url()
    timeout = httpx.Timeout(max(5.0, _env_float("VOICE_TTS_TIMEOUT_SEC", 120.0)), connect=15.0)
    form = _tts_reference_form_fields(text)
    t0 = time.perf_counter()
    with httpx.Client(timeout=timeout) as client:
        r = client.post(url, data=form)
    latency_ms = (time.perf_counter() - t0) * 1000.0
    if r.status_code >= 400:
        raise RuntimeError(r.text[:500] or f"TTS HTTP {r.status_code}")
    try:
        payload = r.json()
    except Exception as e:
        raise RuntimeError(f"TTS response not JSON: {e}") from e
    if not isinstance(payload, dict):
        raise RuntimeError("TTS returned non-object JSON")
    output_path = str(payload.get("output_path") or "").strip()
    if not output_path:
        raise RuntimeError("TTS reference response missing output_path")
    elapsed = payload.get("elapsed_ms")
    if elapsed is None:
        tts_ms = latency_ms
    else:
        tts_ms = float(elapsed)
    cache_hit = bool(payload.get("cache_hit"))
    logger.debug(
        "TTS reference chars=%d path=%s latency_ms=%.0f cache_hit=%s",
        len(text),
        output_path,
        tts_ms,
        cache_hit,
    )
    voice_trace(
        f"TTS reference path={output_path} tts_ms={tts_ms:.0f} "
        f"cache_hit={cache_hit} chars={len(text)}"
    )
    return output_path, tts_ms


async def post_humanaudio(
    sessionid: str,
    output_path: str,
    *,
    webrtc_base: str,
    request_id: str = "",
) -> tuple[dict[str, Any] | None, float]:
    """POST /humanaudiowithpath with a server-side WAV path from TTS."""
    sid = (sessionid or "").strip()
    if not sid:
        raise ValueError("empty sessionid for humanaudio")
    path = (output_path or "").strip()
    if not path:
        raise ValueError("empty output_path for humanaudio")
    base = (webrtc_base or "").strip().rstrip("/")
    if not base:
        raise RuntimeError("WEBRTC_SIGNALING_BASE is not set")
    url = f"{base}/humanaudiowithpath"
    data = {
        "sessionid": sid,
        "path": path,
        "request_id": (request_id or "").strip(),
    }
    timeout = httpx.Timeout(max(5.0, _env_float("VOICE_TTS_HUMANAUDIO_TIMEOUT_SEC", 60.0)), connect=15.0)
    t0 = time.perf_counter()
    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.post(url, data=data)
    latency_ms = (time.perf_counter() - t0) * 1000.0
    if r.status_code >= 400:
        raise RuntimeError(r.text[:500] or f"humanaudiowithpath HTTP {r.status_code}")
    try:
        body = r.json()
    except Exception:
        body = {"raw": r.text[:500]}
    return body if isinstance(body, dict) else None, latency_ms


def record_chunk_latency(
    sink: list[dict[str, Any]],
    item: HumanaudioWorkItem,
    timing: VoiceDispatchTiming,
) -> None:
    now = time.perf_counter()
    entry: dict[str, Any] = {
        "chunk": item.chunk_index,
        "seq": item.seq,
        **timing.as_dict(),
    }
    if item.stream_start is not None:
        entry["enqueued_ms"] = round(
            ((item.enqueued_at or item.stream_start) - item.stream_start) * 1000.0,
            1,
        )
        entry["completed_ms"] = round((now - item.stream_start) * 1000.0, 1)
    if item.enqueued_at is not None:
        entry["queue_wait_ms"] = round((now - item.enqueued_at) * 1000.0, 1)
    sink.append(entry)


class HumanaudioPipelineQueue:
    """
    Parallel TTS (up to N workers) while RAG streams; humanaudio uploads stay ordered.
    Chunk 2 TTS can run while chunk 1 uploads to LiveTalking.
    """

    _by_session: dict[str, HumanaudioPipelineQueue] = {}

    @classmethod
    def for_session(cls, sessionid: str, webrtc_base: str) -> HumanaudioPipelineQueue:
        sid = str(sessionid or "").strip()
        if not sid:
            raise ValueError("empty sessionid")
        if sid not in cls._by_session:
            cls._by_session[sid] = cls(sid, webrtc_base)
        return cls._by_session[sid]

    def __init__(self, sessionid: str, webrtc_base: str) -> None:
        self.sessionid = sessionid
        self.webrtc_base = webrtc_base
        self._work: asyncio.Queue[HumanaudioWorkItem | None] = asyncio.Queue()
        self._ready: dict[int, tuple[str | None, float, HumanaudioWorkItem]] = {}
        self._next_upload = 1
        self._last_seq = 0
        self._pending = 0
        self._cond = asyncio.Condition()
        self._workers: list[asyncio.Task] = []
        self._uploader: asyncio.Task | None = None
        self._started = False
        self._generation = 0

    def _ensure_started(self) -> None:
        if self._started:
            return
        self._started = True
        n = tts_max_parallel()
        for _ in range(n):
            self._workers.append(asyncio.create_task(self._tts_worker()))
        self._uploader = asyncio.create_task(self._upload_worker())
        logger.info(
            "humanaudio pipeline started sessionid=%s tts_workers=%d",
            self.sessionid,
            n,
        )
        voice_trace(
            f"pipeline START session={self.sessionid[:8]}… workers={n} "
            f"webrtc={self.webrtc_base}"
        )

    async def reset_pending(self) -> int:
        """Drop queued work after interrupt; in-flight TTS may still finish."""
        dropped = 0
        while True:
            try:
                self._work.get_nowait()
                dropped += 1
                self._work.task_done()
            except asyncio.QueueEmpty:
                break
        async with self._cond:
            self._generation += 1
            self._ready.clear()
            self._last_seq = 0
            self._next_upload = 1
            self._pending = 0
            self._cond.notify_all()
        if dropped:
            logger.info(
                "humanaudio pipeline sessionid=%s cleared %d queued chunk(s)",
                self.sessionid,
                dropped,
            )
            voice_trace(f"pipeline INTERRUPT session={self.sessionid[:8]}… cleared={dropped}")
        return dropped

    async def submit(self, item: HumanaudioWorkItem) -> None:
        self._ensure_started()
        item.generation = self._generation
        self._last_seq = max(self._last_seq, item.seq)
        self._pending += 1
        preview = (item.text[:48] + "…") if len(item.text) > 48 else item.text
        voice_trace(
            f"enqueue seq={item.seq} chunk=#{item.chunk_index} chars={len(item.text)} "
            f"interrupt={item.interrupt} pending={self._pending} text={preview!r}"
        )
        await self._work.put(item)

    async def drain(self) -> None:
        if not self._started:
            return
        voice_trace(
            f"pipeline DRAIN wait session={self.sessionid[:8]}… "
            f"last_seq={self._last_seq} pending={self._pending}"
        )
        t0 = time.perf_counter()
        await self._work.join()
        async with self._cond:
            await self._cond.wait_for(
                lambda: self._pending <= 0
                and self._next_upload > self._last_seq
                and not self._ready
            )
        voice_trace(
            f"pipeline DRAIN done session={self.sessionid[:8]}… "
            f"chunks={self._last_seq} ms={(time.perf_counter() - t0) * 1000:.0f}"
        )

    async def _tts_worker(self) -> None:
        while True:
            item = await self._work.get()
            try:
                if item is None:
                    continue
                if item.generation != self._generation:
                    async with self._cond:
                        self._pending = max(0, self._pending - 1)
                        self._cond.notify_all()
                    continue
                tts_started = time.perf_counter()
                output_path: str | None = None
                tts_ms = 0.0
                try:
                    output_path, tts_ms = await asyncio.to_thread(synthesize_speech, item.text)
                except Exception as e:
                    voice_trace(f"TTS FAILED seq={item.seq} error={e!s}")
                    logger.warning(
                        "parallel TTS failed sessionid=%s seq=%d: %s",
                        self.sessionid,
                        item.seq,
                        e,
                    )
                async with self._cond:
                    self._ready[item.seq] = (output_path, tts_ms, item)
                    self._cond.notify_all()
                if output_path:
                    wait_ms = (time.perf_counter() - tts_started) * 1000.0
                    logger.info(
                        "parallel TTS done sessionid=%s seq=%d tts_ms=%.0f wait_ms=%.0f path=%s",
                        self.sessionid,
                        item.seq,
                        tts_ms,
                        wait_ms,
                        output_path,
                    )
                    voice_trace(
                        f"TTS done seq={item.seq} tts_ms={tts_ms:.0f} path={output_path}"
                    )
            finally:
                self._work.task_done()

    async def _upload_worker(self) -> None:
        while True:
            upload_item: HumanaudioWorkItem | None = None
            output_path: str | None = None
            tts_ms = 0.0
            async with self._cond:
                await self._cond.wait_for(lambda: self._next_upload in self._ready)
                output_path, tts_ms, upload_item = self._ready.pop(self._next_upload)
                self._next_upload += 1
            if upload_item is None:
                continue
            timing: VoiceDispatchTiming | None = None
            try:
                if output_path:
                    body, upload_ms = await post_humanaudio(
                        self.sessionid,
                        output_path,
                        webrtc_base=self.webrtc_base,
                    )
                    # Wall time for upload only; analytics uses tts_ms + humanaudio_ms.
                    total_ms = round(float(tts_ms) + float(upload_ms), 1)
                    req_id = ""
                    if isinstance(body, dict):
                        data = body.get("data")
                        if isinstance(data, dict):
                            req_id = str(data.get("request_id") or "")
                    timing = VoiceDispatchTiming(
                        mode="humanaudio",
                        chars=len(upload_item.text),
                        tts_ms=round(tts_ms, 1),
                        humanaudio_ms=round(upload_ms, 1),
                        total_ms=round(total_ms, 1),
                        tts_parallel=True,
                    )
                    logger.info(
                        "parallel humanaudio ok sessionid=%s seq=%d tts_ms=%.0f "
                        "humanaudio_ms=%.0f request_id=%s",
                        self.sessionid,
                        upload_item.seq,
                        tts_ms,
                        upload_ms,
                        req_id or "-",
                    )
                    voice_trace(
                        f"humanaudio OK seq={upload_item.seq} tts_ms={tts_ms:.0f} "
                        f"upload_ms={upload_ms:.0f} total_ms={total_ms:.0f} "
                        f"request_id={req_id or '-'}"
                    )
                else:
                    timing = VoiceDispatchTiming(
                        mode="humanaudio",
                        chars=len(upload_item.text),
                        tts_parallel=True,
                    )
            except Exception as e:
                voice_trace(f"humanaudio FAILED seq={upload_item.seq} error={e!s}")
                logger.warning(
                    "parallel humanaudio failed sessionid=%s seq=%d: %s",
                    self.sessionid,
                    upload_item.seq,
                    e,
                )
            if timing and upload_item.latency_sink is not None:
                record_chunk_latency(upload_item.latency_sink, upload_item, timing)
            async with self._cond:
                self._pending = max(0, self._pending - 1)
                self._cond.notify_all()


async def speak_after_tts(
    speak_text: str,
    sessionid: str,
    *,
    webrtc_base: str,
    interrupt: bool = False,
) -> tuple[float | None, VoiceDispatchTiming | None]:
    """Synthesize with /v1/tts/reference; call /humanaudiowithpath with output_path."""
    text = (speak_text or "").strip()
    sid = (sessionid or "").strip()
    if not text or not sid:
        return None, None
    t0 = time.perf_counter()
    try:
        output_path, tts_ms = await asyncio.to_thread(synthesize_speech, text)
        if not output_path:
            raise RuntimeError("TTS returned no output_path")
        body, upload_ms = await post_humanaudio(
            sid, output_path, webrtc_base=webrtc_base
        )
        total_ms = (time.perf_counter() - t0) * 1000.0
        req_id = ""
        if isinstance(body, dict):
            data = body.get("data")
            if isinstance(data, dict):
                req_id = str(data.get("request_id") or "")
        timing = VoiceDispatchTiming(
            mode="humanaudio",
            chars=len(text),
            tts_ms=round(tts_ms, 1),
            humanaudio_ms=round(upload_ms, 1),
            total_ms=round(total_ms, 1),
        )
        logger.info(
            "voice-tts LATENCY sessionid=%s interrupt=%s chars=%d tts_ms=%.0f humanaudio_ms=%.0f total_ms=%.0f request_id=%s",
            sid,
            interrupt,
            len(text),
            tts_ms,
            upload_ms,
            total_ms,
            req_id or "-",
        )
        return total_ms, timing
    except Exception as e:
        logger.warning("voice-tts speak_after_tts failed sessionid=%s: %s", sid, e)
        return None, None


async def dispatch_voice_to_livetalking(
    speak_text: str,
    sessionid: str,
    *,
    webrtc_base: str,
    interrupt: bool = False,
) -> tuple[float | None, VoiceDispatchTiming | None]:
    """humanaudio mode only: TTS reference completes before /humanaudiowithpath."""
    if voice_dispatch_mode() != "humanaudio":
        return None, None
    return await speak_after_tts(
        speak_text,
        sessionid,
        webrtc_base=webrtc_base,
        interrupt=interrupt,
    )
