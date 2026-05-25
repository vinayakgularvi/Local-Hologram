"""Process Video Q&A entries: trim leading silence, transcribe, update answer."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from chunk_pipeline import (
    ffmpeg_detect_leading_silence_trim_sec,
    ffmpeg_extract_audio_wav,
    ffmpeg_trim_start,
    ffprobe_duration_seconds,
)
from transcribe_client import transcribe_bytes, transcribe_configured
from video_qa_garage import garage_object_key, is_configured as garage_is_configured, read_object_bytes
from video_qa_urls import cdn_configured, fetch_video_bytes
from video_qa_store import (
    clear_process_error,
    complete_item_processing,
    get_item,
    record_process_error,
    validate_item_id,
)


def _silence_noise_db() -> float:
    raw = (os.environ.get("VIDEO_QA_SILENCE_NOISE_DB") or "-45").strip()
    try:
        return float(raw)
    except ValueError:
        return -35.0


def _silence_min_sec() -> float:
    raw = (os.environ.get("VIDEO_QA_SILENCE_MIN_SEC") or "0.25").strip()
    try:
        return float(raw)
    except ValueError:
        return 0.25


def _max_trim_sec() -> float:
    raw = (os.environ.get("VIDEO_QA_MAX_TRIM_SEC") or "120").strip()
    try:
        return float(raw)
    except ValueError:
        return 120.0


def _download_video_bytes(item: dict[str, Any]) -> tuple[bytes, str]:
    item_id = str(item["id"])
    obj_key = str(item.get("garage_object_key") or garage_object_key(item_id))
    if garage_is_configured():
        try:
            return read_object_bytes(obj_key)
        except Exception:
            pass
    if cdn_configured():
        try:
            return fetch_video_bytes(item)
        except Exception:
            pass
    raise FileNotFoundError(
        f"video not found for item {item_id} (Garage object missing and CDN fetch failed)"
    )


def _ffmpeg_error_message(exc: BaseException) -> str:
    if isinstance(exc, subprocess.CalledProcessError):
        detail = (exc.stderr or exc.stdout or "").strip()
        if isinstance(detail, bytes):
            detail = detail.decode("utf-8", errors="replace")
        tail = detail.splitlines()[-1] if detail else str(exc)
        return f"ffmpeg failed: {tail}"
    if isinstance(exc, FileNotFoundError):
        return "ffmpeg/ffprobe not found on PATH — install ffmpeg"
    return str(exc)


def process_item(item_id: str, *, force: bool = False) -> dict[str, Any]:
    """
    Trim leading silence, transcribe audio, set answer, mark processed, re-upload video, sync Qdrant.
    """
    if not transcribe_configured():
        raise RuntimeError("TRANSCRIBE_API_URL is not configured")

    item_id = validate_item_id(item_id)
    item = get_item(item_id)
    if not item:
        raise ValueError(f"item not found: {item_id}")

    if item.get("processed") and not force:
        return {
            "id": item_id,
            "skipped": True,
            "reason": "already processed",
            "processed": True,
            "answer": item.get("answer"),
        }

    try:
        clear_process_error(item_id)
    except Exception:
        pass

    content_type = str(item.get("content_type") or "video/mp4")
    work = Path(tempfile.mkdtemp(prefix="video_qa_proc_"))
    try:
        raw_path = work / "source.mp4"
        trimmed_path = work / "trimmed.mp4"
        audio_path = work / "audio.wav"

        video_bytes, content_type = _download_video_bytes(item)
        raw_path.write_bytes(video_bytes)

        trim_sec = ffmpeg_detect_leading_silence_trim_sec(
            str(raw_path),
            noise_db=_silence_noise_db(),
            min_silence_sec=_silence_min_sec(),
            max_trim_sec=_max_trim_sec(),
        )
        ffmpeg_trim_start(str(raw_path), trim_sec, str(trimmed_path), accurate=True)
        duration_after = ffprobe_duration_seconds(str(trimmed_path))
        if duration_after < 0.25:
            raise RuntimeError("video too short after trimming leading silence")

        ffmpeg_extract_audio_wav(str(trimmed_path), str(audio_path))
        audio_bytes = audio_path.read_bytes()
        if len(audio_bytes) < 1000:
            raise RuntimeError("extracted audio is too short to transcribe")

        tx = transcribe_bytes(audio_bytes, filename="speech.wav", content_type="audio/wav")
        transcript = str(tx.get("text") or "").strip()
        if not transcript:
            raise RuntimeError("transcribe returned empty text")

        trimmed_bytes = trimmed_path.read_bytes()
        updated = complete_item_processing(
            item_id=item_id,
            answer=transcript,
            video_data=trimmed_bytes,
            content_type=content_type,
            trim_start_sec=trim_sec,
        )
        return {
            "id": item_id,
            "skipped": False,
            "processed": True,
            "trim_start_sec": trim_sec,
            "answer": transcript,
            "transcribe_latency_ms": tx.get("latency_ms"),
            "size_bytes": updated.get("size_bytes"),
            "item": updated,
        }
    except Exception as e:
        wrapped = RuntimeError(_ffmpeg_error_message(e)) if isinstance(
            e, (subprocess.CalledProcessError, FileNotFoundError)
        ) else e
        try:
            record_process_error(item_id, str(wrapped))
        except Exception:
            pass
        raise wrapped from e
    finally:
        shutil.rmtree(work, ignore_errors=True)


def process_items(
    *,
    item_ids: list[str] | None = None,
    skip_processed: bool = True,
    limit: int = 50,
    force: bool = False,
) -> dict[str, Any]:
    from video_qa_store import list_items

    limit = max(1, min(int(limit), 100))
    if item_ids:
        targets = [validate_item_id(i) for i in item_ids]
    else:
        all_items = list_items(limit=200)
        targets = []
        for it in all_items:
            if skip_processed and it.get("processed") and not force:
                continue
            targets.append(str(it["id"]))
            if len(targets) >= limit:
                break

    results: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    for iid in targets:
        try:
            results.append(process_item(iid, force=force))
        except Exception as e:
            errors.append({"id": iid, "error": str(e)})

    ok = sum(1 for r in results if not r.get("skipped"))
    skipped = sum(1 for r in results if r.get("skipped"))
    return {
        "processed_count": ok,
        "skipped_count": skipped,
        "error_count": len(errors),
        "results": results,
        "errors": errors,
    }
