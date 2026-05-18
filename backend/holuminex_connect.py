"""Persisted Holuminex RAG connect JSON (Avatar Studio) — voice-turn and RAG query POST to chat_post_url."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, urlunparse

import httpx

logger = logging.getLogger(__name__)

_BACKEND_DIR = Path(__file__).resolve().parent
CONNECT_PATH = _BACKEND_DIR / "data" / "holuminex_connect.json"
MAX_FILE_BYTES = 512_000

_STUDIO_INTEGRATIONS_PATH = _BACKEND_DIR / "data" / "studio_integrations.json"
_EMBED_KEY = "holuminex_connect"


def _atomic_write_text(path: Path, text: str, *, encoding: str = "utf-8") -> None:
    """Write then rename into place so readers never see a half-written file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    data = text.encode(encoding)
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _load_embedded_from_studio_integrations() -> dict[str, Any] | None:
    if not _STUDIO_INTEGRATIONS_PATH.is_file():
        return None
    try:
        raw = _STUDIO_INTEGRATIONS_PATH.read_text(encoding="utf-8")
        root = json.loads(raw)
        if not isinstance(root, dict):
            return None
        emb = root.get(_EMBED_KEY)
        return emb if isinstance(emb, dict) else None
    except Exception as e:
        logger.warning("Could not read embedded Holuminex block from studio_integrations.json: %s", e)
        return None


def _embed_into_studio_integrations(data: dict[str, Any]) -> None:
    merged: dict[str, Any] = {}
    if _STUDIO_INTEGRATIONS_PATH.is_file():
        try:
            prev = json.loads(_STUDIO_INTEGRATIONS_PATH.read_text(encoding="utf-8"))
            if isinstance(prev, dict):
                merged = prev
        except Exception as e:
            logger.warning("Could not merge Holuminex into studio_integrations.json: %s", e)
            merged = {}
    merged[_EMBED_KEY] = dict(data)
    blob = json.dumps(merged, indent=2)
    if len(blob.encode("utf-8")) > 2_000_000:
        logger.warning("studio_integrations.json would exceed 2MB after Holuminex embed; skipping embed")
        return
    _atomic_write_text(_STUDIO_INTEGRATIONS_PATH, blob)


def _remove_embedded_from_studio_integrations() -> None:
    if not _STUDIO_INTEGRATIONS_PATH.is_file():
        return
    try:
        prev = json.loads(_STUDIO_INTEGRATIONS_PATH.read_text(encoding="utf-8"))
        if not isinstance(prev, dict) or _EMBED_KEY not in prev:
            return
        del prev[_EMBED_KEY]
        if prev:
            _atomic_write_text(_STUDIO_INTEGRATIONS_PATH, json.dumps(prev, indent=2))
        else:
            _STUDIO_INTEGRATIONS_PATH.unlink()
    except OSError as e:
        logger.warning("Could not update studio_integrations.json after Holuminex delete: %s", e)
    except Exception as e:
        logger.warning("Could not strip embedded Holuminex from studio_integrations.json: %s", e)


def _valid_http_url(u: Any) -> bool:
    if not isinstance(u, str):
        return False
    s = u.strip()
    if not s:
        return False
    try:
        p = urlparse(s)
        return p.scheme in ("http", "https") and bool(p.netloc)
    except Exception:
        return False


def load_raw() -> dict[str, Any] | None:
    """Prefer dedicated file; otherwise use copy embedded in studio_integrations.json."""
    if CONNECT_PATH.is_file():
        try:
            raw = CONNECT_PATH.read_bytes()
            if len(raw) > MAX_FILE_BYTES:
                logger.warning("holuminex_connect.json exceeds max size; ignoring")
                return None
            data = json.loads(raw.decode("utf-8"))
            return data if isinstance(data, dict) else None
        except Exception as e:
            logger.warning("Failed to read holuminex_connect.json: %s", e)
            return None
    emb = _load_embedded_from_studio_integrations()
    if emb and len(json.dumps(emb).encode("utf-8")) <= MAX_FILE_BYTES:
        return emb
    return None


def is_configured() -> bool:
    d = load_raw()
    return bool(d and _valid_http_url(d.get("chat_post_url")))


def _derive_playground_url(chat_post_url: str) -> str | None:
    try:
        p = urlparse(chat_post_url.strip())
        if p.scheme not in ("http", "https") or not p.netloc:
            return None
        path = p.path or ""
        if path.endswith("/chat"):
            new_path = path[: -len("/chat")] + "/"
        elif "/chat" in path:
            new_path = path.split("/chat", 1)[0].rstrip("/") + "/"
        else:
            new_path = (path.rstrip("/") or "/") + "/"
        if not new_path.endswith("/"):
            new_path += "/"
        return urlunparse((p.scheme, p.netloc, new_path, "", "", ""))
    except Exception:
        return None


def rag_playground_url() -> str | None:
    d = load_raw()
    if not d:
        return None
    for key in ("rag_playground_url", "rag_ui_url", "external_ui_url", "playground_url"):
        v = d.get(key)
        if isinstance(v, str) and _valid_http_url(v):
            u = v.strip().rstrip("/")
            return u + "/"
    cup = d.get("chat_post_url")
    if isinstance(cup, str):
        return _derive_playground_url(cup)
    return None


def public_info() -> dict[str, Any]:
    d = load_raw()
    if not d or not _valid_http_url(d.get("chat_post_url")):
        return {"configured": False}
    return {
        "configured": True,
        "chat_post_url": str(d.get("chat_post_url", "")).strip(),
        "rag_playground_url": rag_playground_url(),
        "chat_model_hint": d.get("chat_model_hint"),
        "connect_version": d.get("holuminex_connect_version"),
    }


def save_config(data: dict[str, Any]) -> None:
    if not isinstance(data, dict):
        raise ValueError("Root JSON value must be an object.")
    if not _valid_http_url(data.get("chat_post_url")):
        raise ValueError("chat_post_url is required and must be an http(s) URL with a host.")
    blob = json.dumps(data, indent=2)
    if len(blob.encode("utf-8")) > MAX_FILE_BYTES:
        raise ValueError(f"JSON must be at most {MAX_FILE_BYTES} bytes.")
    _atomic_write_text(CONNECT_PATH, blob)
    try:
        _embed_into_studio_integrations(data)
    except Exception as e:
        logger.warning("Holuminex saved to %s but studio_integrations embed failed: %s", CONNECT_PATH.name, e)


def delete_config() -> None:
    if CONNECT_PATH.is_file():
        try:
            CONNECT_PATH.unlink()
        except OSError as e:
            logger.warning("Could not remove holuminex_connect.json: %s", e)
    _remove_embedded_from_studio_integrations()


def _extract_answer_text(obj: Any) -> str:
    if isinstance(obj, str) and obj.strip():
        return obj.strip()
    if not isinstance(obj, dict):
        return ""
    for key in ("content", "text", "message", "answer", "response", "reply"):
        v = obj.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()
    choices = obj.get("choices")
    if isinstance(choices, list) and choices:
        c0 = choices[0] if isinstance(choices[0], dict) else {}
        delta = c0.get("delta") if isinstance(c0.get("delta"), dict) else {}
        if isinstance(delta.get("content"), str) and delta["content"].strip():
            return delta["content"].strip()
        msg = c0.get("message")
        if isinstance(msg, dict) and isinstance(msg.get("content"), str) and msg["content"].strip():
            return msg["content"].strip()
    for key in ("data", "result", "delta"):
        inner = obj.get(key)
        if isinstance(inner, dict):
            t = _extract_answer_text(inner)
            if t:
                return t
        if isinstance(inner, str) and inner.strip():
            return inner.strip()
    return ""


def build_chat_payload(user_text: str) -> tuple[str, dict[str, Any]]:
    d = load_raw()
    if not d or not _valid_http_url(d.get("chat_post_url")):
        raise ValueError("Holuminex connect is not configured.")
    url = str(d["chat_post_url"]).strip()
    defaults = d.get("defaults")
    if not isinstance(defaults, dict):
        defaults = {}
    body: dict[str, Any] = dict(defaults)
    for key in ("chat_model_hint", "holuminex_connect_version"):
        if key in d and d[key] is not None:
            body[key] = d[key]
    hint = d.get("chat_model_hint")
    if isinstance(hint, str) and hint.strip():
        body.setdefault("model", hint.strip())
    body["messages"] = [{"role": "user", "content": user_text.strip()}]
    return url, body


async def chat_complete(user_text: str, *, timeout_sec: float = 120.0) -> str:
    url, payload = build_chat_payload(user_text)
    headers = {"Content-Type": "application/json", "Accept": "application/json, text/plain, */*"}
    timeout = httpx.Timeout(timeout_sec, connect=20.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(url, json=payload, headers=headers)
        if resp.status_code >= 400:
            detail = resp.text[:1200] if resp.text else resp.reason_phrase
            raise RuntimeError(f"HTTP {resp.status_code}: {detail}")
        ctype = (resp.headers.get("content-type") or "").lower()
        if "application/json" in ctype:
            try:
                obj = resp.json()
            except json.JSONDecodeError as e:
                raise RuntimeError(f"Invalid JSON in response: {e}") from e
            text = _extract_answer_text(obj)
            if text:
                return text
            raise RuntimeError("Chat response JSON contained no extractable text.")
        t = (resp.text or "").strip()
        if t:
            return t
        raise RuntimeError("Empty chat response.")
