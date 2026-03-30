"""Chat API proxy: Gemini API key stays server-side."""

import os
from typing import Any, Literal

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field, field_validator

load_dotenv()

MAX_MESSAGE_CHARS = 8000
MAX_MESSAGES = 40

GEMINI_GENERATE_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
)

app = FastAPI(title="Local Hologram API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class Message(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str = Field(..., max_length=MAX_MESSAGE_CHARS)


class ChatRequest(BaseModel):
    messages: list[Message]
    model: str | None = "gemini-2.5-flash"

    @field_validator("messages")
    @classmethod
    def limit_message_count(cls, v: list[Message]) -> list[Message]:
        if len(v) > MAX_MESSAGES:
            raise ValueError("Too many messages")
        return v


MAX_TTS_CHARS = 8000


class TtsRequest(BaseModel):
    text: str = Field(..., max_length=MAX_TTS_CHARS)


def _gemini_api_key() -> str | None:
    key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if key and isinstance(key, str) and key.strip():
        return key.strip()
    return None


def _build_gemini_body(messages: list[Message]) -> dict[str, Any]:
    """Map OpenAI-style messages to Gemini generateContent JSON."""
    system_chunks: list[str] = []
    contents: list[dict[str, Any]] = []

    for m in messages:
        if m.role == "system":
            system_chunks.append(m.content)
            continue
        gemini_role = "model" if m.role == "assistant" else "user"
        entry = {"role": gemini_role, "parts": [{"text": m.content}]}
        if contents and contents[-1]["role"] == gemini_role:
            prev = contents[-1]["parts"][0]["text"]
            contents[-1]["parts"][0]["text"] = f"{prev}\n\n{m.content}"
        else:
            contents.append(entry)

    body: dict[str, Any] = {
        "contents": contents,
        "generationConfig": {"temperature": 0.7},
    }
    if system_chunks:
        body["systemInstruction"] = {"parts": [{"text": "\n\n".join(system_chunks)}]}
    return body


def _extract_reply_text(data: dict[str, Any]) -> str | None:
    candidates = data.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        return None
    first = candidates[0]
    if not isinstance(first, dict):
        return None
    content = first.get("content")
    if not isinstance(content, dict):
        return None
    parts = content.get("parts")
    if not isinstance(parts, list) or not parts:
        return None
    texts: list[str] = []
    for p in parts:
        if isinstance(p, dict) and isinstance(p.get("text"), str):
            texts.append(p["text"])
    if not texts:
        return None
    return "".join(texts)


def _gemini_error_message(data: dict[str, Any]) -> str:
    err = data.get("error")
    if isinstance(err, dict) and isinstance(err.get("message"), str):
        return err["message"]
    if isinstance(err, str):
        return err
    return "Gemini request failed"


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(_request: Request, exc: RequestValidationError) -> JSONResponse:
    errors = exc.errors()
    first = errors[0] if errors else {}
    msg = str(first.get("msg", "Invalid request"))
    if msg.startswith("Value error, "):
        msg = msg.removeprefix("Value error, ")
    return JSONResponse(status_code=400, content={"error": msg})


@app.post("/api/chat")
async def chat(body: ChatRequest) -> JSONResponse:
    key = _gemini_api_key()
    if not key:
        return JSONResponse(
            status_code=500,
            content={
                "error": "Server missing GEMINI_API_KEY (or GOOGLE_API_KEY). Copy .env.example to .env."
            },
        )

    model = (body.model or "gemini-2.5-flash").strip()
    if not model:
        model = "gemini-2.5-flash"

    payload = _build_gemini_body(body.messages)
    if not payload.get("contents"):
        return JSONResponse(status_code=400, content={"error": "No user or assistant messages to send"})

    url = GEMINI_GENERATE_URL.format(model=model)

    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            r = await client.post(
                url,
                headers={
                    "x-goog-api-key": key,
                    "Content-Type": "application/json",
                },
                json=payload,
            )
    except httpx.RequestError:
        return JSONResponse(status_code=502, content={"error": "Upstream request failed"})

    try:
        data = r.json()
    except ValueError:
        return JSONResponse(status_code=502, content={"error": "Invalid upstream response"})

    if not r.is_success:
        return JSONResponse(status_code=502, content={"error": _gemini_error_message(data)})

    if not isinstance(data, dict):
        return JSONResponse(status_code=502, content={"error": "Unexpected API response"})

    text = _extract_reply_text(data)
    if text is None:
        block = (
            data.get("promptFeedback")
            if isinstance(data.get("promptFeedback"), dict)
            else None
        )
        if block and block.get("blockReason"):
            reason = block.get("blockReason", "blocked")
            return JSONResponse(
                status_code=502,
                content={"error": f"Prompt blocked: {reason}"},
            )
        return JSONResponse(status_code=502, content={"error": "No text in Gemini response"})

    return JSONResponse(content={"reply": text})


@app.post("/api/tts", response_model=None)
async def text_to_speech(body: TtsRequest) -> Response:
    """Synthesize speech (MP3) for lip-sync via Web Audio on the client. Uses Microsoft Edge TTS (edge-tts)."""
    text = body.text.strip()
    if not text:
        return JSONResponse(status_code=400, content={"error": "text required"})

    voice = (os.environ.get("EDGE_TTS_VOICE") or "en-US-AriaNeural").strip()

    try:
        import edge_tts

        communicate = edge_tts.Communicate(text, voice)
        chunks: list[bytes] = []
        async for chunk in communicate.stream():
            if chunk.get("type") == "audio":
                data = chunk.get("data")
                if isinstance(data, bytes):
                    chunks.append(data)
        audio = b"".join(chunks)
    except Exception as e:  # noqa: BLE001 — return safe message to client
        return JSONResponse(status_code=502, content={"error": f"TTS failed: {e!s}"})

    if not audio:
        return JSONResponse(status_code=502, content={"error": "empty audio"})

    return Response(content=audio, media_type="audio/mpeg")
