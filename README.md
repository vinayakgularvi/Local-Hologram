# Local Hologram

Monorepo for a **lip-sync hologram** stack: a **FastAPI** backend (voice turns, RAG, lip-sync pipeline, WebRTC signaling proxies) and a **Vue 3** SPA (live session, analytics, avatar studio).

## Features

- **Live hologram** (`/hologram`): WebRTC-style flow with mic, LLM replies, TTS, and lip-sync video segments.
- **Avatar Studio** (`/avatar`): voice samples, connector settings, Chroma-backed RAG ingest from documents and cloud sources (when configured).
- **Analytics** (`/analytics`): voice-turn summaries and metrics.
- **Docker**: single image builds the frontend, ships static assets with the API, and runs as a non-root user with hardened defaults.

## Requirements

- **Node.js** 20+ (22 used in Docker for the frontend build)
- **Python** 3.12+ (3.12 in Docker) with `pip`
- **ffmpeg** (used by the lip-sync pipeline; installed in the container image)

## Quick start (local development)

1. Copy environment template and edit values for your machine:

   ```bash
   cp .env.example .env
   ```

2. Create a Python virtualenv and install backend dependencies:

   ```bash
   cd backend
   python3 -m venv .venv
   source .venv/bin/activate   # Windows: .venv\Scripts\activate
   pip install -r requirements.txt
   uvicorn main:app --host 0.0.0.0 --port 8080 --reload
   cd ..
   ```

3. Install frontend dependencies:

   ```bash
   cd frontend && npm ci && cd ..
   npm run dev
   ```

4. Run backend and Vite together (from the **repository root**):

   ```bash
   npm run dev
   ```

   - **API**: `http://127.0.0.1:8080` (override with `PORT=...`)
   - **UI**: `https://127.0.0.1:5173` (Vite dev server with HTTPS; proxies `/api`, `/outputs`, `/offer`, `/human`, `/record` to the backend)

   Open the app at the Vite URL and use routes `/hologram`, `/avatar`, and `/analytics`.

## Production (Docker)

```bash
docker compose up --build -d
```

- Listens on **host port 8080** by default (`HOST_PORT` in `.env` overrides the host mapping).
- Persists **`backend/data`** and **`backend/outputs`** via named volumes (`hologram_data`, `hologram_outputs`).
- Health check: `GET /api/health` inside the container.

Use a TLS-terminating reverse proxy in front of the service in real deployments; set **`CORS_ORIGINS`** (and related vars in `.env.example`) for your public origin(s).

## Configuration

Environment variables are documented in **`.env.example`**. Notable groups:

| Area | Purpose |
|------|---------|
| **`LIPSYNC_*` / `LIPSYNC_FILE_API_URL`** | Remote Wav2Lip-style lip-sync HTTP API and tuning. |
| **`OLLAMA_BASE` / `OLLAMA_MODEL` / `MODEL_API_STYLE`** | Local / compatible LLM for voice and RAG answers (`LLM_PROVIDER=local`). |
| **`LLM_PROVIDER`** | `local` \| `openai` \| `anthropic` \| `google`. |
| **`WEBRTC_SIGNALING_BASE`** | Origin for LiveTalking-style signaling (`/offer`, `/human`, `/record` are proxied from the API). |
| **`AVATAR_API_BASE`** | Optional Gradio-style avatar / voice-clone service. |
| **`RAG_*` / `VOICE_RAG_*`** | Chroma path, chunking, optional voice-turn RAG, reset secret. |
| **`CORS_*` / `TRUSTED_HOSTS` / `PROXY_*`** | Browser and reverse-proxy security (see comments in `.env.example`). |

The API loads `.env` from the **repository root** and from **`backend/`** when present.

## Project layout

```
backend/           # FastAPI app (main.py), Chroma RAG, sync connectors, requirements.txt
docker/            # Container entrypoint
frontend/          # Vue 3 + Vite SPA
scripts/           # dev.mjs (npm run dev)
docker-compose.yml
Dockerfile
.env.example
```

## API sketch

- **`GET /api/health`** — process health, model hints, RAG status, feature flags.
- **`POST /api/voice-turn`** — speech pipeline (STT → LLM → TTS → lip-sync) as implemented in `main.py`.
- **`/api/rag/*`**, **`/api/avatar/*`**, **`/api/analytics/*`**, connector **`/api/*`** routes — see `main.py` and OpenAPI at **`/docs`** when the server is running.

## Scripts (root `package.json`)

| Command | Description |
|---------|-------------|
| `npm run dev` | Starts uvicorn (reload) on `PORT` and Vite on 5173. |
| `npm run dev:frontend` | Frontend only (`npm --prefix frontend run dev`). |
| `npm run dev:bash` | Shell alternative (`scripts/dev.sh`). |

---

For deeper behavior (live sync, SharePoint/Drive/etc.), inspect `backend/main.py` and the matching `*_sync.py` modules.
