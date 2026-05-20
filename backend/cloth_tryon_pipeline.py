"""
Cloth try-on → avatar video → hologram prepare pipeline.

Requires ffmpeg on PATH. Optional Pillow for sharpest-frame selection (falls back to mid-video frame).
"""
from __future__ import annotations

import asyncio
import io
import logging
import re
import shutil
import subprocess
import tempfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from avatar_assets_store import (
    latest_prepare_run,
    list_audios as db_list_audios,
    record_prepare_run,
    upsert_video as db_upsert_video,
)
from chunk_pipeline import _run as ffmpeg_run

logger = logging.getLogger("cloth_tryon")

StatusCallback = Callable[[str, str], None | Awaitable[None]]

_ASSET_ID_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$")


@dataclass(frozen=True)
class ClothHologramContext:
    profile_id: str
    avatar_id: str
    audio_id: str
    audio_path: str
    upload_video_id: str
    ref_text: str
    force_regenerate: bool
    set_as_default: bool


def _sanitize_asset_id(raw: str, *, field: str) -> str:
    v = (raw or "").strip()
    if not _ASSET_ID_RE.fullmatch(v):
        raise ValueError(f"Invalid {field}.")
    return v


def _new_hologram_uuid() -> str:
    """Opaque id for profile_id / avatar_id (LiveTalking asset id charset)."""
    return uuid.uuid4().hex


def resolve_cloth_hologram_context(
    cloth_id: str,
    *,
    force_regenerate: bool = False,
    set_as_default: bool = True,
) -> ClothHologramContext:
    """
    Build hologram prepare context:
    - profile_id / avatar_id: new UUID per try-on
    - audio_id / audio_path: from latest Avatar Studio audio upload (SQLite)
    - upload_video_id: new UUID for POST /api/upload/video; prepare uses response id + path
    """
    profile_id = _new_hologram_uuid()
    avatar_id = _new_hologram_uuid()
    ref_text = ""

    audio_id = ""
    audio_path = ""
    latest = latest_prepare_run()
    if latest:
        audio_id = str(latest.get("audio_id") or "").strip()
        audio_path = str(latest.get("audio_path") or "").strip()
    if not audio_id or not audio_path:
        audios = db_list_audios()
        if audios:
            row = audios[0]
            audio_id = str(row.get("audio_id") or "").strip()
            audio_path = str(row.get("path") or "").strip()

    if not audio_id or not audio_path:
        raise RuntimeError(
            "No hologram audio found. Upload voice in Avatar Studio (Hologram Avatar) first."
        )

    upload_video_id = _new_hologram_uuid()

    return ClothHologramContext(
        profile_id=_sanitize_asset_id(profile_id, field="profile_id"),
        avatar_id=_sanitize_asset_id(avatar_id, field="avatar_id"),
        audio_id=_sanitize_asset_id(audio_id, field="audio_id"),
        audio_path=audio_path,
        upload_video_id=_sanitize_asset_id(upload_video_id, field="upload_video_id"),
        ref_text=ref_text,
        force_regenerate=force_regenerate,
        set_as_default=set_as_default,
    )


def hologram_ids_from_upload_response(
    upload_data: dict[str, Any],
    *,
    fallback_upload_id: str,
) -> tuple[str, str]:
    """
    Return (video_id, video_path) from POST /api/upload/video data, e.g.
    { "id": "new_test1", "path": "/home/.../new_test1.mov", ... }
    """
    video_path = str(upload_data.get("path") or "").strip()
    if not video_path:
        video_path = extract_hologram_asset_path(upload_data, asset_id=fallback_upload_id, kind="video")
    video_id = str(
        upload_data.get("id") or upload_data.get("video_id") or fallback_upload_id
    ).strip()
    if not video_id:
        video_id = fallback_upload_id
    return video_id, video_path


def parse_livetalking_response(payload: Any, *, context: str) -> dict[str, Any]:
    """Unwrap LiveTalking-style { code, data, msg } JSON."""
    if not isinstance(payload, dict):
        raise RuntimeError(f"{context}: unexpected response type.")
    code = payload.get("code")
    if code is not None:
        try:
            if int(code) != 0:
                msg = payload.get("msg") or payload.get("message") or f"{context} failed."
                raise RuntimeError(str(msg))
        except (TypeError, ValueError):
            pass
    data = payload.get("data")
    if isinstance(data, dict):
        return data
    return payload


def extract_hologram_asset_path(payload: dict[str, Any], *, asset_id: str, kind: str) -> str:
    """Read server path from upload response (several upstream field names)."""
    candidates: list[Any] = []
    for key in ("path", f"{kind}_path", "file_path", "saved_path", "remote_path", "storage_path"):
        candidates.append(payload.get(key))
    nested = payload.get(kind)
    if isinstance(nested, dict):
        for key in ("path", f"{kind}_path", "file_path"):
            candidates.append(nested.get(key))
    for value in candidates:
        if isinstance(value, str) and value.strip():
            return value.strip()
    logger.warning(
        "Hologram %s upload for %s had no path field; keys=%s",
        kind,
        asset_id,
        list(payload.keys()),
    )
    return ""


async def _emit_status(
    on_status: StatusCallback | None,
    stage: str,
    message: str,
) -> None:
    if not on_status:
        return
    out = on_status(stage, message)
    if asyncio.iscoroutine(out):
        await out


_CLOTH_CAPTURE_IMAGE_SUFFIXES = frozenset({".jpg", ".jpeg", ".png", ".webp"})


def prepare_human_image(capture_path: Path, dest: Path) -> Path:
    """Use a still photo directly, or pick the best frame from a legacy video capture."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    if capture_path.suffix.lower() in _CLOTH_CAPTURE_IMAGE_SUFFIXES:
        _, jpeg_bytes, _ = encode_tryon_jpeg(capture_path)
        dest.write_bytes(jpeg_bytes)
        return dest
    return extract_best_frame(capture_path, dest)


def extract_best_frame(video_path: Path, dest: Path) -> Path:
    """Pick sharpest frame from capture video; write to dest."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="cloth_frames_"))
    try:
        pattern = str(work / "frame_%04d.jpg")
        ffmpeg_run(
            [
                "ffmpeg",
                "-y",
                "-i",
                str(video_path),
                "-vf",
                "fps=2",
                "-q:v",
                "2",
                pattern,
            ]
        )
        frames = sorted(work.glob("frame_*.jpg"))
        if not frames:
            # fallback: single frame at 1s
            ffmpeg_run(
                [
                    "ffmpeg",
                    "-y",
                    "-ss",
                    "1",
                    "-i",
                    str(video_path),
                    "-frames:v",
                    "1",
                    "-q:v",
                    "2",
                    str(dest),
                ]
            )
            return dest

        try:
            from PIL import Image, ImageFilter

            best_path = frames[0]
            best_score = -1.0
            for fp in frames:
                with Image.open(fp) as im:
                    gray = im.convert("L")
                    edges = gray.filter(ImageFilter.FIND_EDGES)
                    score = sum(i * c for i, c in enumerate(edges.histogram()))
                if score > best_score:
                    best_score = score
                    best_path = fp
            shutil.copyfile(best_path, dest)
        except ImportError:
            mid = frames[len(frames) // 2]
            shutil.copyfile(mid, dest)
        return dest
    finally:
        shutil.rmtree(work, ignore_errors=True)


def encode_tryon_jpeg(path: Path) -> tuple[str, bytes, str]:
    """
    Build try-on multipart file tuple (filename, bytes, content_type).
    Converts WebP/PNG/AVIF/mislabeled .jpg files to real JPEG — the try-on API
    validates image content strictly.
    """
    raw = path.read_bytes()
    if not raw:
        raise RuntimeError(f"Image file is empty: {path.name}")
    if raw[:3] == b"\xff\xd8\xff":
        name = path.name if path.suffix.lower() in (".jpg", ".jpeg") else f"{path.stem}.jpg"
        return name, raw, "image/jpeg"
    try:
        from PIL import Image

        img = Image.open(io.BytesIO(raw))
        img = img.convert("RGB")
    except Exception as e:
        raise RuntimeError(f"Invalid or unsupported image: {path.name}") from e
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=92)
    return f"{path.stem}.jpg", buf.getvalue(), "image/jpeg"


async def call_tryon(
    *,
    tryon_base: str,
    human_image: Path,
    garment_image: Path,
    garment_description: str,
    timeout_sec: float,
) -> bytes:
    url = f"{tryon_base.rstrip('/')}/tryon"
    data = {
        "garment_description": garment_description,
        "auto_mask": "true",
        "auto_crop": "false",
        "denoise_steps": "30",
        "seed": "42",
    }
    if not garment_image.is_file():
        raise RuntimeError(f"Garment image not found: {garment_image}")
    if not human_image.is_file():
        raise RuntimeError(f"Human image not found: {human_image}")

    human_part = encode_tryon_jpeg(human_image)
    garment_part = encode_tryon_jpeg(garment_image)
    logger.info(
        "try-on upload human=%s garment=%s (%d bytes)",
        human_part[0],
        garment_part[0],
        len(garment_part[1]),
    )

    timeout = httpx.Timeout(timeout_sec, connect=30.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        files = {
            "human_image": human_part,
            "garment_image": garment_part,
        }
        r = await client.post(url, data=data, files=files)
    if r.status_code >= 400:
        body = (r.text or "")[:800]
        raise RuntimeError(f"Try-on failed ({r.status_code}): {body}")
    ct = (r.headers.get("content-type") or "").lower()
    if "image" not in ct:
        raise RuntimeError(f"Try-on returned unexpected content-type: {ct or 'unknown'}")
    return r.content


async def avatar_video_submit(
    *,
    video_api_base: str,
    image_path: Path,
    reference_video_path: Path,
    fixed_form: dict[str, str],
    timeout_sec: float,
) -> str:
    img_ct = "image/png" if image_path.suffix.lower() == ".png" else "image/jpeg"
    vid_ct = "video/quicktime" if reference_video_path.suffix.lower() == ".mov" else "video/mp4"
    timeout = httpx.Timeout(timeout_sec, connect=30.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        with image_path.open("rb") as img_f, reference_video_path.open("rb") as vid_f:
            files = [
                ("image", (image_path.name, img_f, img_ct)),
                ("reference_video", (reference_video_path.name, vid_f, vid_ct)),
            ]
            r = await client.post(
                f"{video_api_base.rstrip('/')}/generate-controlled",
                data=fixed_form,
                files=files,
            )
    if r.status_code >= 400:
        raise RuntimeError(f"Avatar video submit failed ({r.status_code}): {(r.text or '')[:800]}")
    data = r.json()
    job_id = str(data.get("id") or "").strip()
    if not job_id:
        raise RuntimeError("Avatar video service returned no job id.")
    return job_id


async def avatar_video_wait(
    *,
    video_api_base: str,
    job_id: str,
    poll_interval_sec: float,
    job_timeout_sec: float,
    on_status: StatusCallback | None,
) -> None:
    base = video_api_base.rstrip("/")
    deadline = time.monotonic() + job_timeout_sec
    timeout = httpx.Timeout(120.0, connect=15.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        while time.monotonic() < deadline:
            r = await client.get(f"{base}/jobs/{job_id}")
            if r.status_code >= 400:
                raise RuntimeError(f"Avatar video status failed ({r.status_code}): {(r.text or '')[:400]}")
            data = r.json()
            status = str(data.get("status") or "")
            await _emit_status(on_status, "avatar_video", f"Rendering avatar video ({status or '…'})")
            if status == "succeeded":
                return
            if status == "failed" or data.get("error"):
                raise RuntimeError(str(data.get("error") or "Avatar video job failed."))
            await asyncio.sleep(poll_interval_sec)
    raise RuntimeError("Avatar video job timed out.")


async def avatar_video_download(
    *,
    video_api_base: str,
    job_id: str,
    dest: Path,
    timeout_sec: float,
) -> Path:
    url = f"{video_api_base.rstrip('/')}/jobs/{job_id}/video"
    timeout = httpx.Timeout(timeout_sec, connect=30.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.get(url)
    if r.status_code >= 400:
        raise RuntimeError(f"Avatar video download failed ({r.status_code}): {(r.text or '')[:400]}")
    dest.write_bytes(r.content)
    return dest


async def hologram_upload_video_file(
    *,
    webrtc_base: str,
    video_id: str,
    local_video: Path,
    upload_filename: str,
    content_type: str,
    upload_timeout_sec: float,
) -> dict[str, Any]:
    """POST /api/upload/video — returns unwrapped data { id, path, filename, stored_at }."""
    base = webrtc_base.rstrip("/")
    timeout = httpx.Timeout(upload_timeout_sec, connect=30.0)
    fname = upload_filename or local_video.name
    ctype = content_type or "application/octet-stream"
    async with httpx.AsyncClient(timeout=timeout) as client:
        with local_video.open("rb") as fh:
            files = [("file", (fname, fh, ctype))]
            data = {"video_id": video_id}
            r = await client.post(f"{base}/api/upload/video", data=data, files=files)
    if r.status_code >= 400:
        raise RuntimeError(f"Hologram video upload failed ({r.status_code}): {(r.text or '')[:800]}")
    try:
        raw = r.json()
    except Exception as e:
        raise RuntimeError("Hologram video upload returned invalid JSON.") from e
    return parse_livetalking_response(raw, context="video upload")


async def hologram_prepare(
    *,
    webrtc_base: str,
    profile_id: str,
    avatar_id: str,
    video_id: str,
    audio_id: str,
    video_path: str,
    audio_path: str,
    ref_text: str,
    force_regenerate: bool,
    set_as_default: bool,
    prepare_timeout_sec: float,
) -> dict[str, Any]:
    base = webrtc_base.rstrip("/")
    payload = {
        "profile_id": profile_id,
        "avatar_id": avatar_id,
        "video_id": video_id,
        "video_path": video_path,
        "audio_id": audio_id,
        "audio_path": audio_path,
        "force_regenerate": bool(force_regenerate),
        "set_as_default": bool(set_as_default),
        "ref_text": ref_text,
    }
    timeout = httpx.Timeout(prepare_timeout_sec, connect=30.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.post(
            f"{base}/api/avatar/prepare",
            json=payload,
            headers={"Accept": "application/json", "Content-Type": "application/json"},
        )
    if r.status_code >= 400:
        raise RuntimeError(f"Hologram prepare failed ({r.status_code}): {(r.text or '')[:800]}")
    try:
        raw = r.json()
    except Exception as e:
        raise RuntimeError("Hologram prepare returned invalid JSON.") from e
    return parse_livetalking_response(raw, context="avatar prepare")


async def run_cloth_tryon_pipeline(
    *,
    cloth_id: str,
    garment_path: Path,
    capture_path: Path,
    reference_video_path: Path,
    tryon_base: str,
    video_api_base: str,
    webrtc_base: str,
    avatar_video_fixed_form: dict[str, str],
    garment_description: str,
    hologram: ClothHologramContext,
    tryon_timeout_sec: float,
    avatar_video_submit_timeout_sec: float,
    avatar_video_poll_interval_sec: float,
    avatar_video_job_timeout_sec: float,
    hologram_upload_timeout_sec: float,
    hologram_prepare_timeout_sec: float,
    on_status: StatusCallback | None = None,
) -> dict[str, Any]:
    work = Path(tempfile.mkdtemp(prefix="cloth_pipeline_"))
    try:
        is_photo = capture_path.suffix.lower() in _CLOTH_CAPTURE_IMAGE_SUFFIXES
        await _emit_status(
            on_status,
            "extracting_frame",
            "Using your photo…" if is_photo else "Choosing best photo from your capture…",
        )
        human_frame = work / "human_frame.jpg"
        await asyncio.to_thread(prepare_human_image, capture_path, human_frame)

        await _emit_status(on_status, "tryon", "Applying garment (try-on)…")
        tryon_png = work / "tryon_result.png"
        png_bytes = await call_tryon(
            tryon_base=tryon_base,
            human_image=human_frame,
            garment_image=garment_path,
            garment_description=garment_description,
            timeout_sec=tryon_timeout_sec,
        )
        tryon_png.write_bytes(png_bytes)

        await _emit_status(on_status, "avatar_video", "Starting avatar video render…")
        job_id = await avatar_video_submit(
            video_api_base=video_api_base,
            image_path=tryon_png,
            reference_video_path=reference_video_path,
            fixed_form=avatar_video_fixed_form,
            timeout_sec=avatar_video_submit_timeout_sec,
        )
        await avatar_video_wait(
            video_api_base=video_api_base,
            job_id=job_id,
            poll_interval_sec=avatar_video_poll_interval_sec,
            job_timeout_sec=avatar_video_job_timeout_sec,
            on_status=on_status,
        )
        rendered_mp4 = work / "avatar_render.mp4"
        await avatar_video_download(
            video_api_base=video_api_base,
            job_id=job_id,
            dest=rendered_mp4,
            timeout_sec=avatar_video_submit_timeout_sec,
        )

        await _emit_status(on_status, "hologram_upload", "Uploading video to hologram server…")
        upload_name = rendered_mp4.name
        upload_ct = "video/mp4"
        upload_data = await hologram_upload_video_file(
            webrtc_base=webrtc_base,
            video_id=hologram.upload_video_id,
            local_video=rendered_mp4,
            upload_filename=upload_name,
            content_type=upload_ct,
            upload_timeout_sec=hologram_upload_timeout_sec,
        )
        prepare_video_id, remote_path = hologram_ids_from_upload_response(
            upload_data,
            fallback_upload_id=hologram.upload_video_id,
        )
        if not remote_path:
            raise RuntimeError(
                "Hologram video upload returned no path. "
                f"Response keys: {list(upload_data.keys())}."
            )
        logger.info(
            "Hologram video uploaded id=%s path=%s",
            prepare_video_id,
            remote_path,
        )
        db_upsert_video(
            video_id=prepare_video_id,
            path=remote_path,
            filename=str(upload_data.get("filename") or upload_name),
            remote_stored_at=upload_data.get("stored_at")
            if isinstance(upload_data.get("stored_at"), int)
            else None,
        )

        await _emit_status(on_status, "hologram_prepare", "Preparing hologram avatar…")
        prepared = await hologram_prepare(
            webrtc_base=webrtc_base,
            profile_id=hologram.profile_id,
            avatar_id=hologram.avatar_id,
            video_id=prepare_video_id,
            audio_id=hologram.audio_id,
            video_path=remote_path,
            audio_path=hologram.audio_path,
            ref_text=hologram.ref_text,
            force_regenerate=hologram.force_regenerate,
            set_as_default=hologram.set_as_default,
            prepare_timeout_sec=hologram_prepare_timeout_sec,
        )
        final_profile_id = str(prepared.get("profile_id") or hologram.profile_id).strip()
        final_avatar_id = str(prepared.get("avatar_id") or hologram.avatar_id).strip()
        final_video_path = str(prepared.get("video_path") or remote_path).strip()
        final_audio_path = str(prepared.get("audio_path") or hologram.audio_path).strip()
        record_prepare_run(
            profile_id=final_profile_id,
            avatar_id=final_avatar_id,
            video_id=prepare_video_id,
            audio_id=hologram.audio_id,
            video_path=final_video_path,
            audio_path=final_audio_path,
            ref_text=hologram.ref_text,
            set_as_default=hologram.set_as_default,
            force_regenerate=hologram.force_regenerate,
            result=prepared,
        )

        await _emit_status(on_status, "succeeded", "Your hologram avatar is ready.")
        return {
            "ok": True,
            "cloth_id": cloth_id,
            "avatar_video_job_id": job_id,
            "upload_video_id": hologram.upload_video_id,
            "video_id": prepare_video_id,
            "video_path": final_video_path,
            "audio_id": hologram.audio_id,
            "audio_path": final_audio_path,
            "profile_id": final_profile_id,
            "avatar_id": final_avatar_id,
            "prepare": prepared,
            "default_profile_updated": prepared.get("default_profile_updated"),
            "generated_now": prepared.get("generated_now"),
        }
    finally:
        shutil.rmtree(work, ignore_errors=True)
