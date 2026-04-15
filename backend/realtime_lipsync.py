"""
Real-time lip-sync: flush text while the LLM streams → TTS → Wav2Lip per segment → concat.

The first TTS / lip-sync job starts only after a full first sentence (no length-based flush
before that). Later segments still flush on sentence end or max length.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from pathlib import Path
from typing import Any, Callable

import edge_tts

from chunk_pipeline import ffmpeg_extract_video_segment, ffprobe_duration_seconds

logger = logging.getLogger("lipsync")

# Sentence end for streaming: .?! then whitespace (not end-of-string, to avoid splitting early).
_STREAM_SENTENCE = re.compile(r"[.!?]+\s")


def split_next_segment(
    buffer: str,
    *,
    min_chars: int,
    max_chars: int,
    force: bool,
    allow_size_flush: bool = True,
    require_sentence_punct: bool = False,
) -> tuple[str | None, str]:
    """
    Returns (segment_to_process, remaining_buffer).
    Flushes on sentence end (.?! followed by whitespace) or when buffer reaches max_chars
    (only if allow_size_flush is True — disabled until the first sentence is emitted).
    If require_sentence_punct is True, bare newlines alone do not end a segment.
    """
    if not buffer:
        return None, buffer
    if force:
        s = buffer.strip()
        return (s if s else None), ""

    b = buffer
    if allow_size_flush and len(b) >= max_chars:
        sp = b.rfind(" ", 0, max_chars)
        cut = sp if sp >= min_chars else max_chars
        seg = b[:cut].strip()
        rest = b[cut:].strip()
        if seg:
            return seg, rest

    m = _STREAM_SENTENCE.search(b)
    if m:
        end = m.end()
        seg = b[:end].strip()
        rest = b[end:].strip()
        if len(seg) >= min_chars:
            return seg, rest

    puncts: tuple[str, ...] = (". ", "? ", "! ", ".\n", "?\n", "!\n", "\n")
    if require_sentence_punct:
        puncts = (". ", "? ", "! ", ".\n", "?\n", "!\n")
    for punct in puncts:
        idx = b.find(punct)
        if idx != -1:
            end = idx + len(punct)
            seg = b[:end].strip()
            rest = b[end:].strip()
            if len(seg) >= min_chars:
                return seg, rest

    return None, buffer


def text_to_wav_sync(text: str, out_path: Path, voice: str) -> None:
    async def _run() -> None:
        com = edge_tts.Communicate(text.strip(), voice)
        await com.save(str(out_path))

    asyncio.run(_run())


def process_segment_sync(
    idx: int,
    text: str,
    *,
    work: Path,
    source_video: Path,
    video_cursor: list[float],
    voice: str,
    predict: Callable[[str, str], Any],
    resolve_output: Callable[[Any], str],
    job_id: str | None = None,
) -> str:
    """TTS → slice source video → lip-sync API; advances video_cursor. Returns output mp4 path."""
    text = text.strip()
    if not text:
        raise ValueError("Empty segment")

    jid = job_id or "unknown"
    t0 = time.perf_counter()

    wav = work / f"rt_seg_{idx:04d}.wav"
    text_to_wav_sync(text, wav, voice)
    t1 = time.perf_counter()

    dur = ffprobe_duration_seconds(str(wav))
    vid_len = ffprobe_duration_seconds(str(source_video))
    start = min(video_cursor[0], max(0.0, vid_len - 0.1))
    take = min(dur, max(0.05, vid_len - start))

    vp = work / f"rt_seg_{idx:04d}_in.mp4"
    ffmpeg_extract_video_segment(str(source_video), start, take, str(vp))
    video_cursor[0] = min(vid_len, start + take)
    t2 = time.perf_counter()

    result = predict(str(vp), str(wav))
    t3 = time.perf_counter()
    logger.info(
        json.dumps(
            {
                "event": "realtime_segment",
                "job_id": jid,
                "segment_index": idx,
                "tts_ms": round((t1 - t0) * 1000.0, 2),
                "ffprobe_and_ffmpeg_extract_ms": round((t2 - t1) * 1000.0, 2),
                "lipsync_predict_ms": round((t3 - t2) * 1000.0, 2),
                "total_ms": round((t3 - t0) * 1000.0, 2),
                "text_chars": len(text),
            },
            ensure_ascii=False,
        )
    )
    return resolve_output(result)
