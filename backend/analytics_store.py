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
    if "stt_first_chunk_latency_ms" not in cols:
        cx.execute("ALTER TABLE voice_turns ADD COLUMN stt_first_chunk_latency_ms REAL")
    if "stt_chunk_count" not in cols:
        cx.execute("ALTER TABLE voice_turns ADD COLUMN stt_chunk_count INTEGER")
    if "stt_to_lip_sync_ms" not in cols:
        cx.execute("ALTER TABLE voice_turns ADD COLUMN stt_to_lip_sync_ms REAL")
    if "time_to_first_voice_ms" not in cols:
        cx.execute("ALTER TABLE voice_turns ADD COLUMN time_to_first_voice_ms REAL")
    if "mic_to_speaker_voice_ms" not in cols:
        cx.execute("ALTER TABLE voice_turns ADD COLUMN mic_to_speaker_voice_ms REAL")
    if "tts_latency_ms" not in cols:
        cx.execute("ALTER TABLE voice_turns ADD COLUMN tts_latency_ms REAL")
    if "lip_sync_latency_ms" not in cols:
        cx.execute("ALTER TABLE voice_turns ADD COLUMN lip_sync_latency_ms REAL")
    if "mic_to_lip_sync_ms" not in cols:
        cx.execute("ALTER TABLE voice_turns ADD COLUMN mic_to_lip_sync_ms REAL")
    if "lip_sync_to_video_stream_ms" not in cols:
        cx.execute("ALTER TABLE voice_turns ADD COLUMN lip_sync_to_video_stream_ms REAL")
    if "mic_to_first_audio_ms" not in cols:
        cx.execute("ALTER TABLE voice_turns ADD COLUMN mic_to_first_audio_ms REAL")
    if "client_voice_turn_ms" not in cols:
        cx.execute("ALTER TABLE voice_turns ADD COLUMN client_voice_turn_ms REAL")
    if "human_dispatch_ms" not in cols:
        cx.execute("ALTER TABLE voice_turns ADD COLUMN human_dispatch_ms REAL")
    if "human_dispatched" not in cols:
        cx.execute("ALTER TABLE voice_turns ADD COLUMN human_dispatched INTEGER")
    if "time_to_audio_playback_ms" not in cols:
        cx.execute("ALTER TABLE voice_turns ADD COLUMN time_to_audio_playback_ms REAL")
    if "time_to_video_playback_ms" not in cols:
        cx.execute("ALTER TABLE voice_turns ADD COLUMN time_to_video_playback_ms REAL")
    if "mic_to_video_playback_ms" not in cols:
        cx.execute("ALTER TABLE voice_turns ADD COLUMN mic_to_video_playback_ms REAL")
    if "lip_sync_avatar_play_ms" not in cols:
        cx.execute("ALTER TABLE voice_turns ADD COLUMN lip_sync_avatar_play_ms REAL")
    if "stream_start_to_avatar_ms" not in cols:
        cx.execute("ALTER TABLE voice_turns ADD COLUMN stream_start_to_avatar_ms REAL")
    if "video_stream_first_ms" not in cols:
        cx.execute("ALTER TABLE voice_turns ADD COLUMN video_stream_first_ms REAL")
    if "webrtc_real_playback_ms" not in cols:
        cx.execute("ALTER TABLE voice_turns ADD COLUMN webrtc_real_playback_ms REAL")
    cx.execute(
        """
        UPDATE voice_turns
        SET tts_latency_ms = webrtc_first_voice_ms
        WHERE tts_latency_ms IS NULL AND webrtc_first_voice_ms IS NOT NULL
        """
    )


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
                mic_to_speaker_voice_ms REAL,
                tts_latency_ms REAL,
                lip_sync_latency_ms REAL,
                mic_to_lip_sync_ms REAL,
                lip_sync_to_video_stream_ms REAL,
                mic_to_first_audio_ms REAL,
                client_voice_turn_ms REAL,
                human_dispatch_ms REAL,
                human_dispatched INTEGER,
                time_to_audio_playback_ms REAL,
                time_to_video_playback_ms REAL,
                mic_to_video_playback_ms REAL,
                lip_sync_avatar_play_ms REAL,
                stream_start_to_avatar_ms REAL,
                video_stream_first_ms REAL,
                webrtc_real_playback_ms REAL
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
    stt_first_chunk_latency_ms: float | None = None,
    stt_chunk_count: int | None = None,
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
                    webrtc_first_voice_ms, rag_latency_ms, stt_latency_ms,
                    stt_first_chunk_latency_ms, stt_chunk_count
                ) VALUES (?, ?, ?, ?, 0, ?, ?, NULL, NULL, NULL, ?, ?, ?, ?)
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
                    stt_first_chunk_latency_ms,
                    stt_chunk_count,
                ),
            )
            cx.commit()
            return int(cur.lastrowid)


def _valid_ms(v: float | None) -> float | None:
    if v is None:
        return None
    x = float(v)
    if x < 0 or x > 600_000:
        return None
    return x


def update_voice_turn_webrtc_first_voice(
    turn_id: int,
    webrtc_first_voice_ms: float,
    *,
    time_to_first_voice_ms: float | None = None,
    mic_to_speaker_voice_ms: float | None = None,
    tts_latency_ms: float | None = None,
    mic_to_first_audio_ms: float | None = None,
    client_voice_turn_ms: float | None = None,
    time_to_audio_playback_ms: float | None = None,
) -> bool:
    """Client-reported first audio (TTS/WebRTC leg) and related client timings."""
    init_db()
    webrtc = _valid_ms(webrtc_first_voice_ms)
    if webrtc is None:
        return False
    tts = _valid_ms(tts_latency_ms)
    if tts is None:
        tts = webrtc
    mic_speaker = _valid_ms(mic_to_speaker_voice_ms)
    mic_audio = _valid_ms(mic_to_first_audio_ms)
    client_vt = _valid_ms(client_voice_turn_ms)
    audio_playback = _valid_ms(time_to_audio_playback_ms)
    if audio_playback is None:
        audio_playback = tts
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
            ttfv = _valid_ms(time_to_first_voice_ms)
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
                SET webrtc_first_voice_ms = ?, tts_latency_ms = ?,
                    time_to_first_voice_ms = ?, mic_to_speaker_voice_ms = ?,
                    mic_to_first_audio_ms = ?, client_voice_turn_ms = ?,
                    time_to_audio_playback_ms = ?
                WHERE id = ? AND webrtc_first_voice_ms IS NULL
                """,
                (webrtc, tts, ttfv, mic_speaker, mic_audio, client_vt, audio_playback, int(turn_id)),
            )
            cx.commit()
            return cur.rowcount > 0


def mark_voice_turn_human_dispatched(turn_id: int) -> bool:
    init_db()
    with _lock:
        with _connect() as cx:
            cur = cx.execute(
                """
                UPDATE voice_turns SET human_dispatched = 1
                WHERE id = ? AND (human_dispatched IS NULL OR human_dispatched = 0)
                """,
                (int(turn_id),),
            )
            cx.commit()
            return cur.rowcount > 0


def update_voice_turn_human_dispatch_ms(turn_id: int, human_dispatch_ms: float) -> bool:
    """Server-reported LiveTalking /human POST duration."""
    init_db()
    ms = _valid_ms(human_dispatch_ms)
    if ms is None:
        return False
    with _lock:
        with _connect() as cx:
            cur = cx.execute(
                """
                UPDATE voice_turns SET human_dispatch_ms = ?
                WHERE id = ? AND human_dispatch_ms IS NULL
                """,
                (ms, int(turn_id)),
            )
            cx.commit()
            return cur.rowcount > 0


def update_voice_turn_lip_sync_latency(
    turn_id: int,
    lip_sync_latency_ms: float,
    *,
    mic_to_lip_sync_ms: float | None = None,
    lip_sync_to_video_stream_ms: float | None = None,
    time_to_video_playback_ms: float | None = None,
    mic_to_video_playback_ms: float | None = None,
    lip_sync_avatar_play_ms: float | None = None,
    stream_start_to_avatar_ms: float | None = None,
    video_stream_first_ms: float | None = None,
    webrtc_real_playback_ms: float | None = None,
    stt_to_lip_sync_ms: float | None = None,
) -> bool:
    """Playback: early stream tick, lip-sync avatar play, and gap between them."""
    init_db()
    lip = _valid_ms(lip_sync_latency_ms)
    if lip is None:
        return False
    mic_lip = _valid_ms(mic_to_lip_sync_ms)
    stream_start = _valid_ms(lip_sync_to_video_stream_ms)
    video_playback = _valid_ms(time_to_video_playback_ms)
    mic_video = _valid_ms(mic_to_video_playback_ms)
    avatar_play = _valid_ms(lip_sync_avatar_play_ms)
    stream_gap = _valid_ms(stream_start_to_avatar_ms)
    stream_first = _valid_ms(video_stream_first_ms)
    real_playback = _valid_ms(webrtc_real_playback_ms)
    stt_lip = _valid_ms(stt_to_lip_sync_ms)
    with _lock:
        with _connect() as cx:
            row = cx.execute(
                """
                SELECT webrtc_first_voice_ms, lip_sync_latency_ms
                FROM voice_turns WHERE id = ?
                """,
                (int(turn_id),),
            ).fetchone()
            if not row or row["webrtc_first_voice_ms"] is None or row["lip_sync_latency_ms"] is not None:
                return False
            cur = cx.execute(
                """
                UPDATE voice_turns
                SET lip_sync_latency_ms = ?, mic_to_lip_sync_ms = ?,
                    lip_sync_to_video_stream_ms = ?, time_to_video_playback_ms = ?,
                    mic_to_video_playback_ms = ?, lip_sync_avatar_play_ms = ?,
                    stream_start_to_avatar_ms = ?, video_stream_first_ms = ?,
                    webrtc_real_playback_ms = ?, stt_to_lip_sync_ms = ?
                WHERE id = ? AND lip_sync_latency_ms IS NULL
                """,
                (
                    lip,
                    mic_lip,
                    stream_start,
                    video_playback,
                    mic_video,
                    avatar_play,
                    stream_gap,
                    stream_first,
                    real_playback,
                    stt_lip,
                    int(turn_id),
                ),
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
                AVG(stt_first_chunk_latency_ms) AS avg_stt_first_chunk_latency_ms,
                MIN(stt_first_chunk_latency_ms) AS min_stt_first_chunk_latency_ms,
                MAX(stt_first_chunk_latency_ms) AS max_stt_first_chunk_latency_ms,
                AVG(stt_to_lip_sync_ms) AS avg_stt_to_lip_sync_ms,
                MIN(stt_to_lip_sync_ms) AS min_stt_to_lip_sync_ms,
                MAX(stt_to_lip_sync_ms) AS max_stt_to_lip_sync_ms,
                AVG(time_to_first_voice_ms) AS avg_time_to_first_voice_ms,
                MIN(time_to_first_voice_ms) AS min_time_to_first_voice_ms,
                MAX(time_to_first_voice_ms) AS max_time_to_first_voice_ms,
                AVG(mic_to_lip_sync_ms) AS avg_mic_to_lip_sync_ms,
                MIN(mic_to_lip_sync_ms) AS min_mic_to_lip_sync_ms,
                MAX(mic_to_lip_sync_ms) AS max_mic_to_lip_sync_ms,
                AVG(mic_to_speaker_voice_ms) AS avg_mic_to_speaker_voice_ms,
                MIN(mic_to_speaker_voice_ms) AS min_mic_to_speaker_voice_ms,
                MAX(mic_to_speaker_voice_ms) AS max_mic_to_speaker_voice_ms,
                AVG(tts_latency_ms) AS avg_tts_latency_ms,
                MIN(tts_latency_ms) AS min_tts_latency_ms,
                MAX(tts_latency_ms) AS max_tts_latency_ms,
                AVG(lip_sync_latency_ms) AS avg_lip_sync_latency_ms,
                MIN(lip_sync_latency_ms) AS min_lip_sync_latency_ms,
                MAX(lip_sync_latency_ms) AS max_lip_sync_latency_ms,
                AVG(lip_sync_to_video_stream_ms) AS avg_lip_sync_to_video_stream_ms,
                MIN(lip_sync_to_video_stream_ms) AS min_lip_sync_to_video_stream_ms,
                MAX(lip_sync_to_video_stream_ms) AS max_lip_sync_to_video_stream_ms,
                AVG(mic_to_first_audio_ms) AS avg_mic_to_first_audio_ms,
                MIN(mic_to_first_audio_ms) AS min_mic_to_first_audio_ms,
                MAX(mic_to_first_audio_ms) AS max_mic_to_first_audio_ms,
                AVG(client_voice_turn_ms) AS avg_client_voice_turn_ms,
                MIN(client_voice_turn_ms) AS min_client_voice_turn_ms,
                MAX(client_voice_turn_ms) AS max_client_voice_turn_ms,
                AVG(human_dispatch_ms) AS avg_human_dispatch_ms,
                MIN(human_dispatch_ms) AS min_human_dispatch_ms,
                MAX(human_dispatch_ms) AS max_human_dispatch_ms,
                SUM(CASE WHEN human_dispatched = 1 THEN 1 ELSE 0 END) AS human_dispatched_count,
                AVG(time_to_audio_playback_ms) AS avg_time_to_audio_playback_ms,
                MIN(time_to_audio_playback_ms) AS min_time_to_audio_playback_ms,
                MAX(time_to_audio_playback_ms) AS max_time_to_audio_playback_ms,
                AVG(time_to_video_playback_ms) AS avg_time_to_video_playback_ms,
                MIN(time_to_video_playback_ms) AS min_time_to_video_playback_ms,
                MAX(time_to_video_playback_ms) AS max_time_to_video_playback_ms,
                AVG(mic_to_video_playback_ms) AS avg_mic_to_video_playback_ms,
                MIN(mic_to_video_playback_ms) AS min_mic_to_video_playback_ms,
                MAX(mic_to_video_playback_ms) AS max_mic_to_video_playback_ms,
                AVG(lip_sync_avatar_play_ms) AS avg_lip_sync_avatar_play_ms,
                MIN(lip_sync_avatar_play_ms) AS min_lip_sync_avatar_play_ms,
                MAX(lip_sync_avatar_play_ms) AS max_lip_sync_avatar_play_ms,
                AVG(stream_start_to_avatar_ms) AS avg_stream_start_to_avatar_ms,
                MIN(stream_start_to_avatar_ms) AS min_stream_start_to_avatar_ms,
                MAX(stream_start_to_avatar_ms) AS max_stream_start_to_avatar_ms,
                AVG(video_stream_first_ms) AS avg_video_stream_first_ms,
                MIN(video_stream_first_ms) AS min_video_stream_first_ms,
                MAX(video_stream_first_ms) AS max_video_stream_first_ms,
                AVG(webrtc_real_playback_ms) AS avg_webrtc_real_playback_ms,
                MIN(webrtc_real_playback_ms) AS min_webrtc_real_playback_ms,
                MAX(webrtc_real_playback_ms) AS max_webrtc_real_playback_ms
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
        "avg_stt_first_chunk_latency_ms": None,
        "min_stt_first_chunk_latency_ms": None,
        "max_stt_first_chunk_latency_ms": None,
        "avg_stt_to_lip_sync_ms": None,
        "min_stt_to_lip_sync_ms": None,
        "max_stt_to_lip_sync_ms": None,
        "avg_time_to_first_voice_ms": None,
        "min_time_to_first_voice_ms": None,
        "max_time_to_first_voice_ms": None,
        "avg_mic_to_lip_sync_ms": None,
        "min_mic_to_lip_sync_ms": None,
        "max_mic_to_lip_sync_ms": None,
        "avg_mic_to_speaker_voice_ms": None,
        "min_mic_to_speaker_voice_ms": None,
        "max_mic_to_speaker_voice_ms": None,
        "avg_tts_latency_ms": None,
        "min_tts_latency_ms": None,
        "max_tts_latency_ms": None,
        "avg_lip_sync_latency_ms": None,
        "min_lip_sync_latency_ms": None,
        "max_lip_sync_latency_ms": None,
        "avg_lip_sync_to_video_stream_ms": None,
        "min_lip_sync_to_video_stream_ms": None,
        "max_lip_sync_to_video_stream_ms": None,
        "avg_mic_to_first_audio_ms": None,
        "min_mic_to_first_audio_ms": None,
        "max_mic_to_first_audio_ms": None,
        "avg_client_voice_turn_ms": None,
        "min_client_voice_turn_ms": None,
        "max_client_voice_turn_ms": None,
        "avg_human_dispatch_ms": None,
        "min_human_dispatch_ms": None,
        "max_human_dispatch_ms": None,
        "human_dispatched_count": 0,
        "avg_time_to_audio_playback_ms": None,
        "min_time_to_audio_playback_ms": None,
        "max_time_to_audio_playback_ms": None,
        "avg_time_to_video_playback_ms": None,
        "min_time_to_video_playback_ms": None,
        "max_time_to_video_playback_ms": None,
        "avg_mic_to_video_playback_ms": None,
        "min_mic_to_video_playback_ms": None,
        "max_mic_to_video_playback_ms": None,
        "avg_lip_sync_avatar_play_ms": None,
        "min_lip_sync_avatar_play_ms": None,
        "max_lip_sync_avatar_play_ms": None,
        "avg_stream_start_to_avatar_ms": None,
        "min_stream_start_to_avatar_ms": None,
        "max_stream_start_to_avatar_ms": None,
        "avg_video_stream_first_ms": None,
        "min_video_stream_first_ms": None,
        "max_video_stream_first_ms": None,
        "avg_webrtc_real_playback_ms": None,
        "min_webrtc_real_playback_ms": None,
        "max_webrtc_real_playback_ms": None,
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
                   stt_latency_ms, stt_first_chunk_latency_ms, stt_chunk_count, stt_to_lip_sync_ms,
                   time_to_first_voice_ms, mic_to_speaker_voice_ms,
                   tts_latency_ms, lip_sync_latency_ms, mic_to_lip_sync_ms,
                   lip_sync_to_video_stream_ms, mic_to_first_audio_ms,
                   client_voice_turn_ms, human_dispatch_ms, human_dispatched,
                   time_to_audio_playback_ms, time_to_video_playback_ms, mic_to_video_playback_ms,
                   lip_sync_avatar_play_ms, stream_start_to_avatar_ms, video_stream_first_ms,
                   webrtc_real_playback_ms
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
