"""SQLite persistence for voice / Ollama analytics (local kiosk use)."""

from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_DB_PATH = Path(__file__).resolve().parent / "data" / "analytics.db"
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
            CREATE TABLE IF NOT EXISTS voice_turns (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                heard_chars INTEGER NOT NULL DEFAULT 0,
                answer_chars INTEGER NOT NULL DEFAULT 0,
                total_request_ms REAL NOT NULL,
                ollama_wall_ms REAL NOT NULL,
                prompt_tokens INTEGER,
                completion_tokens INTEGER,
                ollama_total_duration_ns INTEGER,
                ollama_load_duration_ns INTEGER
            )
            """
        )
        cx.execute("CREATE INDEX IF NOT EXISTS idx_voice_turns_ts ON voice_turns(ts)")
        cx.commit()


def record_voice_turn(
    *,
    heard_chars: int,
    answer_chars: int,
    total_request_ms: float,
    ollama_wall_ms: float,
    prompt_tokens: int | None,
    completion_tokens: int | None,
    ollama_total_duration_ns: int | None,
    ollama_load_duration_ns: int | None,
) -> None:
    init_db()
    with _lock:
        with _connect() as cx:
            cx.execute(
                """
                INSERT INTO voice_turns (
                    ts, heard_chars, answer_chars, total_request_ms, ollama_wall_ms,
                    prompt_tokens, completion_tokens, ollama_total_duration_ns, ollama_load_duration_ns
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    _utc_now_iso(),
                    heard_chars,
                    answer_chars,
                    total_request_ms,
                    ollama_wall_ms,
                    prompt_tokens,
                    completion_tokens,
                    ollama_total_duration_ns,
                    ollama_load_duration_ns,
                ),
            )
            cx.commit()


def get_summary() -> dict[str, Any]:
    init_db()
    with _connect() as cx:
        row = cx.execute(
            """
            SELECT
                COUNT(*) AS total_questions,
                AVG(total_request_ms) AS avg_total_ms,
                AVG(ollama_wall_ms) AS avg_ollama_wall_ms,
                MIN(total_request_ms) AS min_total_ms,
                MAX(total_request_ms) AS max_total_ms,
                SUM(COALESCE(prompt_tokens, 0)) AS sum_prompt_tokens,
                SUM(COALESCE(completion_tokens, 0)) AS sum_completion_tokens,
                SUM(COALESCE(prompt_tokens, 0) + COALESCE(completion_tokens, 0)) AS sum_total_tokens,
                AVG(heard_chars) AS avg_heard_chars,
                AVG(answer_chars) AS avg_answer_chars
            FROM voice_turns
            """
        ).fetchone()
        first = cx.execute("SELECT MIN(ts) AS first_ts FROM voice_turns").fetchone()
        last = cx.execute("SELECT MAX(ts) AS last_ts FROM voice_turns").fetchone()
    if not row:
        return _empty_summary()
    d = dict(row)
    d["first_event_ts"] = first["first_ts"] if first else None
    d["last_event_ts"] = last["last_ts"] if last else None
    return d


def _empty_summary() -> dict[str, Any]:
    return {
        "total_questions": 0,
        "avg_total_ms": None,
        "avg_ollama_wall_ms": None,
        "min_total_ms": None,
        "max_total_ms": None,
        "sum_prompt_tokens": 0,
        "sum_completion_tokens": 0,
        "sum_total_tokens": 0,
        "avg_heard_chars": None,
        "avg_answer_chars": None,
        "first_event_ts": None,
        "last_event_ts": None,
    }


def get_recent_voice_turns(limit: int = 50) -> list[dict[str, Any]]:
    init_db()
    limit = max(1, min(limit, 500))
    with _connect() as cx:
        cur = cx.execute(
            """
            SELECT id, ts, heard_chars, answer_chars, total_request_ms, ollama_wall_ms,
                   prompt_tokens, completion_tokens, ollama_total_duration_ns
            FROM voice_turns
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        )
        return [dict(r) for r in cur.fetchall()]


def clear_all() -> int:
    """Remove all rows. Returns deleted count."""
    init_db()
    with _lock:
        with _connect() as cx:
            n = cx.execute("SELECT COUNT(*) FROM voice_turns").fetchone()[0]
            cx.execute("DELETE FROM voice_turns")
            cx.commit()
            return int(n)
