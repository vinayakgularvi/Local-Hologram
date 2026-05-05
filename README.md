# Local Hologram

Monorepo for a **lip-sync hologram** stack: a **FastAPI** backend (voice turns, RAG, lip-sync pipeline, WebRTC signaling proxies) and a **Vue 3** SPA (live session, analytics, avatar studio).

All browser-facing routes and the HTTP API share a configurable path prefix (default **`/holuminex`**) so one origin can host the UI and API together. Override with **`HOLUMINEX_PREFIX`** in `.env` (must match Vite `base` in `frontend/vite.config.js`).

## Features

- **Live hologram** (SPA root under the prefix, e.g. **`/holuminex/`** with default `HOLUMINEX_PREFIX`): WebRTC-style flow with mic, LLM replies, TTS, and lip-sync video segments.
- **Avatar Studio** (`…/avatar`): voice samples, connector settings, Chroma-backed RAG (documents and cloud sources when configured).
- **Analytics** (`…/analytics`): voice-turn summaries and metrics.
- **Docker**: production-style image (built SPA + FastAPI); optional Compose **Ollama** profile.

## Requirements

- **Node.js** 20+ (22 in the frontend Docker stage)
- **Python** 3.12+ with `pip` (3.12 in the runtime image)
- **ffmpeg** (lip-sync pipeline; installed in the Docker image)

## Quick start (local development)

1. Copy the environment template and edit for your hosts (LLM, lip-sync, WebRTC, etc.):

   ```bash
   cp .env.example .env
   ```

2. Backend virtualenv and dependencies:

   ```bash
   cd backend
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   cd ..
   ```

3. Frontend dependencies:

   ```bash
   cd frontend && npm ci && cd ..
   ```

4. Run **API + Vite** from the repository root (recommended):

   ```bash
   npm run dev
   ```

   This starts:

   - **Backend**: `http://127.0.0.1:6064` — uvicorn with `--reload` (override with `PORT=...`; defaults avoid Chrome’s **6000–6063** X11 block, see `.env.example`).
   - **Frontend**: `http://127.0.0.1:6066` — Vite dev server with `base` `/holuminex/`; proxies `{prefix}/api`, `{prefix}/outputs`, `{prefix}/offer`, `{prefix}/human`, `{prefix}/record` to the backend.

   Open **`http://127.0.0.1:6066/holuminex/`** (live), **`http://127.0.0.1:6066/holuminex/analytics`**, **`http://127.0.0.1:6066/holuminex/avatar`** (paths follow Vite `base`).

   **Preview build** (optional): `npm --prefix frontend run preview` — default port **6067**.

**Backend only** (e.g. debugging): from `backend/` with venv active, `python -m uvicorn main:app --host 127.0.0.1 --port 6064 --reload`.

## Production (Docker Compose)

```bash
docker compose up --build -d
```

- **Web** service maps **`${WEB_PORT:-6064}:6064`** (see `docker-compose.yml`).
- App URL (default prefix): **`http://localhost:6064/holuminex/`** (root `/` redirects into the prefix when static SPA is mounted).
- **Health**: `GET /holuminex/api/health` (see Compose `healthcheck`).
- **Data**: bind mounts `./backend/data` and `./backend/outputs`.
- **Optional Ollama**: `docker compose --profile ollama up --build -d` — host port **6065** → container **11434**; point **`LLM_BASE`** / embed settings at that service from `.env` as needed.

Use TLS and a reverse proxy in real deployments; set **`CORS_ORIGINS`** (and related vars in `.env.example`) for your public origins.

## Configuration

Variables are documented in **`.env.example`**. Summary:

| Area | Purpose |
|------|---------|
| **`HOLUMINEX_PREFIX`** | URL prefix for API, static SPA, `/outputs`, WebRTC proxy routes (default `/holuminex`). |
| **`LLM_BASE` / `LLM_MODEL`** | Chat / voice / lip-sync LLM API root and model id. Legacy **`OLLAMA_BASE` / `OLLAMA_MODEL`** still apply if `LLM_*` are unset. |
| **`MODEL_API_STYLE`** | `auto` \| `openai` \| `ollama` — use **`openai`** for `/v1/chat/completions` gateways; **`LLM_BASE`** may end with `/v1`. |
| **`OLLAMA_EMBED_BASE` / `OLLAMA_EMBED_MODEL`** | RAG (Chroma) embeddings via native Ollama **`/api/embed`** (often local **11434** while chat uses another URL). |
| **`LLM_PROVIDER`** | `local` \| `openai` \| `anthropic` \| `google`. |
| **`LIPSYNC_*` / `LIPSYNC_FILE_API_URL`** | Remote Wav2Lip-style lip-sync HTTP API and tuning. |
| **`WEBRTC_SIGNALING_BASE`** | LiveTalking-style signaling origin; **`{prefix}/offer`**, **`human`**, **`record`** are proxied to that host. |
| **`AVATAR_API_BASE`** | Optional Gradio-style avatar / voice-clone service. |
| **`RAG_*` / `VOICE_RAG_*`** | Chroma ingest limits, optional voice-turn RAG, reset secret. |
| **`VITE_API_BASE`** | Optional frontend override for API origin (origin only — do not append `HOLUMINEX_PREFIX`; the UI adds it). |
| **`CORS_*` / `TRUSTED_HOSTS` / `PROXY_*`** | Browser and reverse-proxy security (production notes in `.env.example`). |

Dotenv is loaded from the **repository root** and **`backend/`** when present. Avatar Studio can persist overrides to **`backend/data/studio_integrations.json`**.

## Project layout

```
backend/           # FastAPI (main.py), Chroma RAG, sync connectors, requirements.txt
docker-compose.yml # web + optional ollama profile
Dockerfile         # multi-stage: frontend build → Python runtime
frontend/          # Vue 3 + Vite (base /holuminex/)
scripts/           # dev.mjs (npm run dev)
.env.example
```

## API and docs

With default prefix **`/holuminex`**:

- **`GET /holuminex/api/health`** — health, LLM hints, RAG status, feature flags.
- **`POST /holuminex/api/voice-turn`** — voice pipeline (STT → LLM → TTS → lip-sync) per `main.py`.
- **`/holuminex/api/rag/*`**, **`/holuminex/api/avatar/*`**, **`/holuminex/api/analytics/*`**, studio and connector routes — see **`main.py`**.
- **OpenAPI**: **`/holuminex/docs`** (Swagger UI).

## Scripts (root `package.json`)

| Command | Description |
|---------|-------------|
| `npm run dev` | uvicorn on `PORT` (default **6064**) + Vite on **6066**. |
| `npm run dev:frontend` | Vite only (`npm --prefix frontend run dev`). |
| `npm run dev:bash` | Shell alternative (`scripts/dev.sh`). |

## Troubleshooting

- **502 / wrong host from the UI**: confirm **`VITE_API_BASE`** is only an origin (no path suffix) if set; duplicate prefix breaks the Vite proxy and can surface as **405** on POSTs.
- **LLM 404 / not_found**: **`LLM_BASE`** must match your server (native Ollama **`/api/generate`** vs OpenAI-compatible **`/v1`** + **`MODEL_API_STYLE=openai`**). **`LLM_MODEL`** must be an id that host accepts.
- **RAG embed errors after changing `OLLAMA_EMBED_*`**: reset the vector store (`POST /holuminex/api/rag/reset` with **`RAG_RESET_SECRET`**) or remove **`backend/data/chroma/`** if dimensions no longer match.
- **`POST …/offer` returns 500**: the FastAPI app **proxies** to **`WEBRTC_SIGNALING_BASE`** (LiveTalking). A **500** is almost always from **that** server (check its logs / console). If you get **502** instead, the proxy could not connect (wrong host/port, TLS, or firewall). Set **`WEBRTC_SIGNALING_VERIFY_TLS=0`** only for dev with self-signed HTTPS signaling. This repo logs upstream errors under logger **`local_hologram.webrtc_proxy`** (warning level).

---

For live sync and cloud connectors, see **`backend/main.py`** and the `*_sync.py` modules.
