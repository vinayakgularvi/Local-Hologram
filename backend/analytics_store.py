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


def _ensure_voice_turn_columns(cx: sqlite3.Connection) -> None:
    cols = {row[1] for row in cx.execute("PRAGMA table_info(voice_turns)")}
    if "webrtc_first_voice_ms" not in cols:
        cx.execute("ALTER TABLE voice_turns ADD COLUMN webrtc_first_voice_ms REAL")
    if "rag_latency_ms" not in cols:
        cx.execute("ALTER TABLE voice_turns ADD COLUMN rag_latency_ms REAL")
    if "stt_latency_ms" not in cols:
        cx.execute("ALTER TABLE voice_turns ADD COLUMN stt_latency_ms REAL")
    if "time_to_first_voice_ms" not in cols:
        cx.execute("ALTER TABLE voice_turns ADD COLUMN time_to_first_voice_ms REAL")
    if "mic_to_speaker_voice_ms" not in cols:
        cx.execute("ALTER TABLE voice_turns ADD COLUMN mic_to_speaker_voice_ms REAL")


def compute_time_to_first_voice_ms(
    *,
    stt_latency_ms: float | None,
    rag_latency_ms: float | None,
    webrtc_first_voice_ms: float | None,
    total_request_ms: float | None = None,
) -> float | None:
    """
    Mic → first avatar audio: STT + RAG stream (or server voice-turn when no RAG) + WebRTC leg.
    """
    if webrtc_first_voice_ms is None:
        return None
    stt = float(stt_latency_ms or 0)
    webrtc = float(webrtc_first_voice_ms)
    if rag_latency_ms is not None:
        middle = float(rag_latency_ms)
    elif total_request_ms is not None:
        middle = float(total_request_ms)
    else:
        middle = 0.0
    total = stt + middle + webrtc
    return total if total > 0 else None


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
                ollama_load_duration_ns INTEGER,
                webrtc_first_voice_ms REAL,
                rag_latency_ms REAL,
                stt_latency_ms REAL,
                time_to_first_voice_ms REAL,
                mic_to_speaker_voice_ms REAL
            )
            """
        )
        _ensure_voice_turn_columns(cx)
        cx.execute("CREATE INDEX IF NOT EXISTS idx_voice_turns_ts ON voice_turns(ts)")
        cx.commit()


def record_voice_turn(
    *,
    heard_chars: int,
    answer_chars: int,
    total_request_ms: float,
    rag_latency_ms: float | None = None,
    stt_latency_ms: float | None = None,
    prompt_tokens: int | None = None,
    completion_tokens: int | None = None,
) -> int:
    init_db()
    with _lock:
        with _connect() as cx:
            cur = cx.execute(
                """
                INSERT INTO voice_turns (
                    ts, heard_chars, answer_chars, total_request_ms, ollama_wall_ms,
                    prompt_tokens, completion_tokens, ollama_total_duration_ns, ollama_load_duration_ns,
                    webrtc_first_voice_ms, rag_latency_ms, stt_latency_ms
                ) VALUES (?, ?, ?, ?, 0, ?, ?, NULL, NULL, NULL, ?, ?)
                """,
                (
                    _utc_now_iso(),
                    heard_chars,
                    answer_chars,
                    total_request_ms,
                    prompt_tokens,
                    completion_tokens,
                    rag_latency_ms,
                    stt_latency_ms,
                ),
            )
            cx.commit()
            return int(cur.lastrowid)


def update_voice_turn_webrtc_first_voice(
    turn_id: int,
    webrtc_first_voice_ms: float,
    *,
    time_to_first_voice_ms: float | None = None,
    mic_to_speaker_voice_ms: float | None = None,
) -> bool:
    """Client-reported WebRTC audio, combined latency, and mic tap → speaker audio."""
    init_db()
    webrtc = float(webrtc_first_voice_ms)
    if webrtc < 0 or webrtc > 600_000:
        return False
    mic_speaker = None
    if mic_to_speaker_voice_ms is not None:
        mic_speaker = float(mic_to_speaker_voice_ms)
        if mic_speaker < 0 or mic_speaker > 600_000:
            return False
    with _lock:
        with _connect() as cx:
            row = cx.execute(
                """
                SELECT stt_latency_ms, rag_latency_ms, total_request_ms, webrtc_first_voice_ms
                FROM voice_turns WHERE id = ?
                """,
                (int(turn_id),),
            ).fetchone()
            if not row or row["webrtc_first_voice_ms"] is not None:
                return False
            ttfv = time_to_first_voice_ms
            if ttfv is None:
                ttfv = compute_time_to_first_voice_ms(
                    stt_latency_ms=row["stt_latency_ms"],
                    rag_latency_ms=row["rag_latency_ms"],
                    webrtc_first_voice_ms=webrtc,
                    total_request_ms=row["total_request_ms"],
                )
            cur = cx.execute(
                """
                UPDATE voice_turns
                SET webrtc_first_voice_ms = ?, time_to_first_voice_ms = ?, mic_to_speaker_voice_ms = ?
                WHERE id = ? AND webrtc_first_voice_ms IS NULL
                """,
                (webrtc, ttfv, mic_speaker, int(turn_id)),
            )
            cx.commit()
            return cur.rowcount > 0


def get_summary() -> dict[str, Any]:
    init_db()
    with _connect() as cx:
        row = cx.execute(
            """
            SELECT
                COUNT(*) AS total_questions,
                AVG(total_request_ms) AS avg_total_ms,
                AVG(rag_latency_ms) AS avg_rag_latency_ms,
                MIN(rag_latency_ms) AS min_rag_latency_ms,
                MAX(rag_latency_ms) AS max_rag_latency_ms,
                MIN(total_request_ms) AS min_total_ms,
                MAX(total_request_ms) AS max_total_ms,
                SUM(COALESCE(prompt_tokens, 0)) AS sum_prompt_tokens,
                SUM(COALESCE(completion_tokens, 0)) AS sum_completion_tokens,
                SUM(COALESCE(prompt_tokens, 0) + COALESCE(completion_tokens, 0)) AS sum_total_tokens,
                AVG(heard_chars) AS avg_heard_chars,
                AVG(answer_chars) AS avg_answer_chars,
                AVG(webrtc_first_voice_ms) AS avg_webrtc_first_voice_ms,
                MIN(webrtc_first_voice_ms) AS min_webrtc_first_voice_ms,
                MAX(webrtc_first_voice_ms) AS max_webrtc_first_voice_ms,
                AVG(stt_latency_ms) AS avg_stt_latency_ms,
                MIN(stt_latency_ms) AS min_stt_latency_ms,
                MAX(stt_latency_ms) AS max_stt_latency_ms,
                AVG(time_to_first_voice_ms) AS avg_time_to_first_voice_ms,
                MIN(time_to_first_voice_ms) AS min_time_to_first_voice_ms,
                MAX(time_to_first_voice_ms) AS max_time_to_first_voice_ms,
                AVG(mic_to_speaker_voice_ms) AS avg_mic_to_speaker_voice_ms,
                MIN(mic_to_speaker_voice_ms) AS min_mic_to_speaker_voice_ms,
                MAX(mic_to_speaker_voice_ms) AS max_mic_to_speaker_voice_ms
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
        "avg_rag_latency_ms": None,
        "min_rag_latency_ms": None,
        "max_rag_latency_ms": None,
        "min_total_ms": None,
        "max_total_ms": None,
        "sum_prompt_tokens": 0,
        "sum_completion_tokens": 0,
        "sum_total_tokens": 0,
        "avg_heard_chars": None,
        "avg_answer_chars": None,
        "avg_webrtc_first_voice_ms": None,
        "min_webrtc_first_voice_ms": None,
        "max_webrtc_first_voice_ms": None,
        "avg_stt_latency_ms": None,
        "min_stt_latency_ms": None,
        "max_stt_latency_ms": None,
        "avg_time_to_first_voice_ms": None,
        "min_time_to_first_voice_ms": None,
        "max_time_to_first_voice_ms": None,
        "avg_mic_to_speaker_voice_ms": None,
        "min_mic_to_speaker_voice_ms": None,
        "max_mic_to_speaker_voice_ms": None,
        "first_event_ts": None,
        "last_event_ts": None,
    }


def get_recent_voice_turns(limit: int = 50) -> list[dict[str, Any]]:
    init_db()
    limit = max(1, min(limit, 500))
    with _connect() as cx:
        cur = cx.execute(
            """
            SELECT id, ts, heard_chars, answer_chars, total_request_ms,
                   prompt_tokens, completion_tokens, webrtc_first_voice_ms, rag_latency_ms,
                   stt_latency_ms, time_to_first_voice_ms, mic_to_speaker_voice_ms
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
