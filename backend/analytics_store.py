"""SQLite persistence for voice / Ollama analytics (local kiosk use)."""

from __future__ import annotations

import json
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
    if "stream_chunk_count" not in cols:
        cx.execute("ALTER TABLE voice_turns ADD COLUMN stream_chunk_count INTEGER")
    if "rag_first_chunk_enqueued_ms" not in cols:
        cx.execute("ALTER TABLE voice_turns ADD COLUMN rag_first_chunk_enqueued_ms REAL")
    if "stream_first_tts_ms" not in cols:
        cx.execute("ALTER TABLE voice_turns ADD COLUMN stream_first_tts_ms REAL")
    if "stream_first_humanaudio_ms" not in cols:
        cx.execute("ALTER TABLE voice_turns ADD COLUMN stream_first_humanaudio_ms REAL")
    if "stream_first_chunk_total_ms" not in cols:
        cx.execute("ALTER TABLE voice_turns ADD COLUMN stream_first_chunk_total_ms REAL")
    if "stream_first_chunk_completed_ms" not in cols:
        cx.execute("ALTER TABLE voice_turns ADD COLUMN stream_first_chunk_completed_ms REAL")
    if "stream_avg_tts_ms" not in cols:
        cx.execute("ALTER TABLE voice_turns ADD COLUMN stream_avg_tts_ms REAL")
    if "stream_avg_humanaudio_ms" not in cols:
        cx.execute("ALTER TABLE voice_turns ADD COLUMN stream_avg_humanaudio_ms REAL")
    if "stream_sum_tts_ms" not in cols:
        cx.execute("ALTER TABLE voice_turns ADD COLUMN stream_sum_tts_ms REAL")
    if "stream_sum_humanaudio_ms" not in cols:
        cx.execute("ALTER TABLE voice_turns ADD COLUMN stream_sum_humanaudio_ms REAL")
    if "stream_avg_rag_sentence_ms" not in cols:
        cx.execute("ALTER TABLE voice_turns ADD COLUMN stream_avg_rag_sentence_ms REAL")
    if "stream_sentence_chunks" not in cols:
        cx.execute("ALTER TABLE voice_turns ADD COLUMN stream_sentence_chunks TEXT")
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


def _encode_sentence_chunks(chunks: list[dict[str, Any]] | None) -> str | None:
    if not chunks:
        return None
    try:
        return json.dumps(chunks, separators=(",", ":"))
    except (TypeError, ValueError):
        return None


def _decode_sentence_chunks(raw: Any) -> list[dict[str, Any]]:
    if raw is None:
        return []
    if isinstance(raw, list):
        return raw
    if not isinstance(raw, str) or not raw.strip():
        return []
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return []
    return parsed if isinstance(parsed, list) else []


def _voice_turn_row(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    d = dict(row)
    d["stream_sentence_chunks"] = _decode_sentence_chunks(d.get("stream_sentence_chunks"))
    return d


def update_voice_turn_stream_latency(
    turn_id: int,
    *,
    stream_chunk_count: int | None = None,
    stream_sentence_chunks: list[dict[str, Any]] | None = None,
    stream_avg_rag_sentence_ms: float | None = None,
    stream_avg_tts_ms: float | None = None,
    stream_avg_humanaudio_ms: float | None = None,
    # Legacy kwargs ignored (older callers / migrations)
    rag_first_chunk_enqueued_ms: float | None = None,
    stream_first_tts_ms: float | None = None,
    stream_first_humanaudio_ms: float | None = None,
    stream_first_chunk_total_ms: float | None = None,
    stream_first_chunk_completed_ms: float | None = None,
    stream_sum_tts_ms: float | None = None,
    stream_sum_humanaudio_ms: float | None = None,
) -> bool:
    """Per-sentence RAG / TTS (elapsed_ms) / humanaudiowithpath timings for one voice turn."""
    del (
        rag_first_chunk_enqueued_ms,
        stream_first_tts_ms,
        stream_first_humanaudio_ms,
        stream_first_chunk_total_ms,
        stream_first_chunk_completed_ms,
        stream_sum_tts_ms,
        stream_sum_humanaudio_ms,
    )
    init_db()
    chunks = stream_chunk_count
    if chunks is not None and (chunks < 0 or chunks > 10_000):
        chunks = None
    encoded = _encode_sentence_chunks(stream_sentence_chunks)
    fields = {
        "stream_avg_rag_sentence_ms": _valid_ms(stream_avg_rag_sentence_ms),
        "stream_avg_tts_ms": _valid_ms(stream_avg_tts_ms),
        "stream_avg_humanaudio_ms": _valid_ms(stream_avg_humanaudio_ms),
    }
    if not any(v is not None for v in fields.values()) and chunks is None and not encoded:
        return False
    with _lock:
        with _connect() as cx:
            row = cx.execute(
                "SELECT id FROM voice_turns WHERE id = ?",
                (int(turn_id),),
            ).fetchone()
            if not row:
                return False
            sets: list[str] = []
            vals: list[Any] = []
            if chunks is not None:
                sets.append("stream_chunk_count = ?")
                vals.append(int(chunks))
            if encoded is not None:
                sets.append("stream_sentence_chunks = ?")
                vals.append(encoded)
            for col, val in fields.items():
                if val is not None:
                    sets.append(f"{col} = ?")
                    vals.append(val)
            if not sets:
                return False
            vals.append(int(turn_id))
            cur = cx.execute(
                f"UPDATE voice_turns SET {', '.join(sets)} WHERE id = ?",
                vals,
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
    """Aggregate averages for transcribe + per-sentence RAG / TTS / humanaudio."""
    init_db()
    with _connect() as cx:
        row = cx.execute(
            """
            SELECT
                COUNT(*) AS total_questions,
                AVG(stt_latency_ms) AS avg_stt_latency_ms,
                AVG(stream_avg_rag_sentence_ms) AS avg_stream_avg_rag_sentence_ms,
                AVG(stream_avg_tts_ms) AS avg_stream_avg_tts_ms,
                AVG(stream_avg_humanaudio_ms) AS avg_stream_avg_humanaudio_ms,
                AVG(stream_chunk_count) AS avg_stream_chunk_count
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
        "avg_stt_latency_ms": None,
        "avg_stream_avg_rag_sentence_ms": None,
        "avg_stream_avg_tts_ms": None,
        "avg_stream_avg_humanaudio_ms": None,
        "avg_stream_chunk_count": None,
        "first_event_ts": None,
        "last_event_ts": None,
    }


def get_voice_turn(turn_id: int) -> dict[str, Any] | None:
    """Single voice-turn row by SQLite id (for MongoDB sync)."""
    init_db()
    tid = int(turn_id)
    if tid < 1:
        return None
    with _connect() as cx:
        row = cx.execute(
            """
            SELECT id, ts, stt_latency_ms, stream_chunk_count,
                   stream_avg_rag_sentence_ms, stream_avg_tts_ms, stream_avg_humanaudio_ms,
                   stream_sentence_chunks
            FROM voice_turns WHERE id = ?
            """,
            (tid,),
        ).fetchone()
    return _voice_turn_row(row) if row else None


def get_recent_voice_turns(limit: int = 50) -> list[dict[str, Any]]:
    init_db()
    limit = max(1, min(limit, 500))
    with _connect() as cx:
        cur = cx.execute(
            """
            SELECT id, ts, stt_latency_ms, stream_chunk_count,
                   stream_avg_rag_sentence_ms, stream_avg_tts_ms, stream_avg_humanaudio_ms,
                   stream_sentence_chunks
            FROM voice_turns
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        )
        return [_voice_turn_row(r) for r in cur.fetchall()]


def clear_all() -> int:
    """Remove all rows. Returns deleted count."""
    init_db()
    with _lock:
        with _connect() as cx:
            n = cx.execute("SELECT COUNT(*) FROM voice_turns").fetchone()[0]
            cx.execute("DELETE FROM voice_turns")
            cx.commit()
            return int(n)
