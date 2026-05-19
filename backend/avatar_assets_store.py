"""SQLite persistence for Hologram Avatar video/audio uploads (Avatar Studio)."""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_DB_PATH = Path(__file__).resolve().parent / "data" / "avatar_studio.db"
_lock = threading.Lock()


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _connect() -> sqlite3.Connection:
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    cx = sqlite3.connect(str(_DB_PATH), check_same_thread=False)
    cx.row_factory = sqlite3.Row
    return cx


def init_db() -> None:
    with _connect() as cx:
        cx.execute(
            """
            CREATE TABLE IF NOT EXISTS livetalking_videos (
                video_id TEXT PRIMARY KEY,
                path TEXT NOT NULL,
                filename TEXT,
                remote_stored_at INTEGER,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        cx.execute(
            """
            CREATE TABLE IF NOT EXISTS livetalking_audios (
                audio_id TEXT PRIMARY KEY,
                path TEXT NOT NULL,
                filename TEXT,
                remote_stored_at INTEGER,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        cx.execute(
            """
            CREATE TABLE IF NOT EXISTS livetalking_prepare_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                profile_id TEXT NOT NULL,
                avatar_id TEXT NOT NULL,
                video_id TEXT NOT NULL,
                audio_id TEXT NOT NULL,
                video_path TEXT NOT NULL,
                audio_path TEXT NOT NULL,
                ref_text TEXT NOT NULL DEFAULT '',
                set_as_default INTEGER NOT NULL DEFAULT 1,
                force_regenerate INTEGER NOT NULL DEFAULT 0,
                result_json TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        cx.execute(
            "CREATE INDEX IF NOT EXISTS idx_livetalking_prepare_ts ON livetalking_prepare_runs(created_at)"
        )
        cx.commit()


def _row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {k: row[k] for k in row.keys()}


def upsert_video(
    *,
    video_id: str,
    path: str,
    filename: str | None = None,
    remote_stored_at: int | None = None,
) -> dict[str, Any]:
    init_db()
    now = _utc_now_iso()
    with _lock:
        with _connect() as cx:
            existing = cx.execute(
                "SELECT video_id FROM livetalking_videos WHERE video_id = ?",
                (video_id,),
            ).fetchone()
            if existing:
                cx.execute(
                    """
                    UPDATE livetalking_videos
                    SET path = ?, filename = ?, remote_stored_at = ?, updated_at = ?
                    WHERE video_id = ?
                    """,
                    (path, filename, remote_stored_at, now, video_id),
                )
            else:
                cx.execute(
                    """
                    INSERT INTO livetalking_videos (
                        video_id, path, filename, remote_stored_at, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (video_id, path, filename, remote_stored_at, now, now),
                )
            cx.commit()
    out = get_video(video_id)
    assert out is not None
    return out


def upsert_audio(
    *,
    audio_id: str,
    path: str,
    filename: str | None = None,
    remote_stored_at: int | None = None,
) -> dict[str, Any]:
    init_db()
    now = _utc_now_iso()
    with _lock:
        with _connect() as cx:
            existing = cx.execute(
                "SELECT audio_id FROM livetalking_audios WHERE audio_id = ?",
                (audio_id,),
            ).fetchone()
            if existing:
                cx.execute(
                    """
                    UPDATE livetalking_audios
                    SET path = ?, filename = ?, remote_stored_at = ?, updated_at = ?
                    WHERE audio_id = ?
                    """,
                    (path, filename, remote_stored_at, now, audio_id),
                )
            else:
                cx.execute(
                    """
                    INSERT INTO livetalking_audios (
                        audio_id, path, filename, remote_stored_at, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (audio_id, path, filename, remote_stored_at, now, now),
                )
            cx.commit()
    out = get_audio(audio_id)
    assert out is not None
    return out


def list_videos() -> list[dict[str, Any]]:
    init_db()
    with _connect() as cx:
        rows = cx.execute(
            """
            SELECT video_id, path, filename, remote_stored_at, created_at, updated_at
            FROM livetalking_videos
            ORDER BY updated_at DESC
            """
        ).fetchall()
    return [_row_to_dict(r) for r in rows if r is not None]  # type: ignore[misc]


def list_audios() -> list[dict[str, Any]]:
    init_db()
    with _connect() as cx:
        rows = cx.execute(
            """
            SELECT audio_id, path, filename, remote_stored_at, created_at, updated_at
            FROM livetalking_audios
            ORDER BY updated_at DESC
            """
        ).fetchall()
    return [_row_to_dict(r) for r in rows if r is not None]  # type: ignore[misc]


def get_video(video_id: str) -> dict[str, Any] | None:
    init_db()
    with _connect() as cx:
        row = cx.execute(
            """
            SELECT video_id, path, filename, remote_stored_at, created_at, updated_at
            FROM livetalking_videos WHERE video_id = ?
            """,
            (video_id,),
        ).fetchone()
    return _row_to_dict(row)


def get_audio(audio_id: str) -> dict[str, Any] | None:
    init_db()
    with _connect() as cx:
        row = cx.execute(
            """
            SELECT audio_id, path, filename, remote_stored_at, created_at, updated_at
            FROM livetalking_audios WHERE audio_id = ?
            """,
            (audio_id,),
        ).fetchone()
    return _row_to_dict(row)


def record_prepare_run(
    *,
    profile_id: str,
    avatar_id: str,
    video_id: str,
    audio_id: str,
    video_path: str,
    audio_path: str,
    ref_text: str,
    set_as_default: bool,
    force_regenerate: bool,
    result: dict[str, Any],
) -> None:
    init_db()
    with _lock:
        with _connect() as cx:
            cx.execute(
                """
                INSERT INTO livetalking_prepare_runs (
                    profile_id, avatar_id, video_id, audio_id, video_path, audio_path,
                    ref_text, set_as_default, force_regenerate, result_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    profile_id,
                    avatar_id,
                    video_id,
                    audio_id,
                    video_path,
                    audio_path,
                    ref_text,
                    1 if set_as_default else 0,
                    1 if force_regenerate else 0,
                    json.dumps(result, ensure_ascii=False, default=str),
                    _utc_now_iso(),
                ),
            )
            cx.commit()


def latest_prepare_run() -> dict[str, Any] | None:
    init_db()
    with _connect() as cx:
        row = cx.execute(
            """
            SELECT id, profile_id, avatar_id, video_id, audio_id, video_path, audio_path,
                   ref_text, set_as_default, force_regenerate, result_json, created_at
            FROM livetalking_prepare_runs
            ORDER BY id DESC
            LIMIT 1
            """
        ).fetchone()
    if row is None:
        return None
    d = _row_to_dict(row)
    if d and d.get("result_json"):
        try:
            d["result"] = json.loads(str(d["result_json"]))
        except json.JSONDecodeError:
            d["result"] = None
    return d
