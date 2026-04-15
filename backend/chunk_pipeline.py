"""
Chunk-based lip-sync: small audio segments + matching video subclips → Gradio → stitch.

Requires ffmpeg and ffprobe on PATH (no pydub — compatible with Python 3.13+).
"""
from __future__ import annotations

import os
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Callable


def _run(cmd: list[str]) -> None:
    subprocess.run(cmd, check=True, capture_output=True, text=True)


def ffprobe_duration_seconds(path: str) -> float:
    out = subprocess.check_output(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            path,
        ],
        text=True,
    ).strip()
    return float(out or 0.0)


def ffmpeg_extract_wav_segment(
    src_wav: str, start_sec: float, duration_sec: float, out: str
) -> None:
    start_sec = max(0.0, start_sec)
    duration_sec = max(0.05, duration_sec)
    _run(
        [
            "ffmpeg",
            "-y",
            "-ss",
            f"{start_sec:.4f}",
            "-i",
            src_wav,
            "-t",
            f"{duration_sec:.4f}",
            "-acodec",
            "pcm_s16le",
            out,
        ]
    )


def ffmpeg_extract_video_segment(src: str, start: float, duration: float, out: str) -> None:
    start = max(0.0, start)
    duration = max(0.05, duration)
    cmd_copy = [
        "ffmpeg",
        "-y",
        "-ss",
        f"{start:.4f}",
        "-i",
        src,
        "-t",
        f"{duration:.4f}",
        "-c",
        "copy",
        "-avoid_negative_ts",
        "make_zero",
        out,
    ]
    try:
        _run(cmd_copy)
    except subprocess.CalledProcessError:
        _run(
            [
                "ffmpeg",
                "-y",
                "-ss",
                f"{start:.4f}",
                "-i",
                src,
                "-t",
                f"{duration:.4f}",
                "-c:v",
                "libx264",
                "-c:a",
                "aac",
                "-pix_fmt",
                "yuv420p",
                out,
            ]
        )


def ffmpeg_trim_start(src: str, trim_sec: float, out: str) -> None:
    if trim_sec <= 0:
        raise ValueError("trim_sec must be positive")
    try:
        _run(
            [
                "ffmpeg",
                "-y",
                "-ss",
                f"{trim_sec:.4f}",
                "-i",
                src,
                "-c",
                "copy",
                "-avoid_negative_ts",
                "make_zero",
                out,
            ]
        )
    except subprocess.CalledProcessError:
        _run(
            [
                "ffmpeg",
                "-y",
                "-ss",
                f"{trim_sec:.4f}",
                "-i",
                src,
                "-c:v",
                "libx264",
                "-c:a",
                "aac",
                "-pix_fmt",
                "yuv420p",
                out,
            ]
        )


def ffmpeg_concat_videos(paths: list[str], out: str) -> None:
    if not paths:
        raise ValueError("No segments to concatenate.")
    if len(paths) == 1:
        import shutil

        shutil.copy2(paths[0], out)
        return

    def esc(p: str) -> str:
        return p.replace("'", "'\\''")

    lines = [f"file '{esc(os.path.abspath(p))}'" for p in paths]
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".txt", delete=False, encoding="utf-8"
    ) as f:
        f.write("\n".join(lines) + "\n")
        list_path = f.name
    try:
        try:
            _run(
                [
                    "ffmpeg",
                    "-y",
                    "-f",
                    "concat",
                    "-safe",
                    "0",
                    "-i",
                    list_path,
                    "-c",
                    "copy",
                    "-movflags",
                    "+faststart",
                    out,
                ]
            )
        except subprocess.CalledProcessError:
            _run(
                [
                    "ffmpeg",
                    "-y",
                    "-f",
                    "concat",
                    "-safe",
                    "0",
                    "-i",
                    list_path,
                    "-c:v",
                    "libx264",
                    "-c:a",
                    "aac",
                    "-pix_fmt",
                    "yuv420p",
                    "-movflags",
                    "+faststart",
                    out,
                ]
            )
    finally:
        try:
            os.unlink(list_path)
        except OSError:
            pass


def run_chunked_lipsync(
    *,
    source_video: str,
    speech_wav: Path,
    work: Path,
    chunk_sec: float,
    overlap_sec: float,
    predict: Callable[[str, str], Any],
    resolve_output: Callable[[Any], str],
    parallel_workers: int = 1,
) -> str:
    """
    Split speech_wav into overlapping windows (step = chunk_sec - overlap_sec).
    Map each window's start onto the video timeline linearly (speech time → video time).
    For each window: extract matching video slice + audio slice → Gradio → collect mp4.
    Optionally trim overlap from the start of clips 2..N, then ffmpeg concat.
    """
    if chunk_sec <= 0:
        raise ValueError("chunk_sec must be positive")
    if overlap_sec < 0 or overlap_sec >= chunk_sec:
        raise ValueError("overlap_sec must be in [0, chunk_sec)")

    total_audio_sec = ffprobe_duration_seconds(str(speech_wav))
    if total_audio_sec < 0.05:
        raise ValueError("Speech audio is too short.")

    video_duration = ffprobe_duration_seconds(source_video)
    if video_duration <= 0:
        raise ValueError("Could not read source video duration (ffprobe).")

    step_sec = chunk_sec - overlap_sec
    if step_sec <= 0:
        raise ValueError("chunk_sec - overlap_sec must be positive")

    tasks: list[tuple[int, str, str]] = []
    t_audio = 0.0
    idx = 0

    while t_audio < total_audio_sec - 1e-6:
        dur = min(chunk_sec, total_audio_sec - t_audio)
        if dur < 0.25:
            break

        frac_start = t_audio / total_audio_sec
        video_start = frac_start * video_duration
        video_start = min(video_start, max(0.0, video_duration - dur))

        ap = work / f"chunk_{idx:04d}.wav"
        ffmpeg_extract_wav_segment(str(speech_wav), t_audio, dur, str(ap))

        vp = work / f"chunk_{idx:04d}_src.mp4"
        ffmpeg_extract_video_segment(source_video, video_start, dur, str(vp))

        tasks.append((idx, str(vp), str(ap)))

        idx += 1
        if t_audio + chunk_sec >= total_audio_sec - 1e-6:
            break
        t_audio += step_sec

    if not tasks:
        raise RuntimeError("No chunks produced from speech audio.")

    n = len(tasks)
    chunk_outputs: list[str | None] = [None] * n

    def _run_one(item: tuple[int, str, str]) -> tuple[int, str]:
        i, vp, ap = item
        result = predict(vp, ap)
        return i, resolve_output(result)

    if parallel_workers <= 1:
        for item in tasks:
            i, out_path = _run_one(item)
            chunk_outputs[i] = out_path
    else:
        workers = max(1, min(parallel_workers, n))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(_run_one, item): item[0] for item in tasks}
            for fut in as_completed(futures):
                i, out_path = fut.result()
                chunk_outputs[i] = out_path

    chunk_outputs_str: list[str] = [p for p in chunk_outputs if p is not None]
    if len(chunk_outputs_str) != n:
        raise RuntimeError("Chunk lip-sync produced incomplete results.")
    chunk_outputs = chunk_outputs_str

    if overlap_sec > 0 and len(chunk_outputs) > 1:
        trimmed: list[str] = []
        for i, p in enumerate(chunk_outputs):
            if i == 0:
                trimmed.append(p)
                continue
            dur = ffprobe_duration_seconds(p)
            if dur <= overlap_sec + 0.08:
                trimmed.append(p)
                continue
            tout = work / f"chunk_{i:04d}_trim.mp4"
            try:
                ffmpeg_trim_start(p, overlap_sec, str(tout))
                trimmed.append(str(tout))
            except subprocess.CalledProcessError:
                trimmed.append(p)
        chunk_outputs = trimmed

    final_out = work / "stitched.mp4"
    ffmpeg_concat_videos(chunk_outputs, str(final_out))
    return str(final_out)
