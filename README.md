# Local Hologram

Personal AI agent with a **2D face**: type a message → the model replies → **TTS audio** (`/api/tts`, Edge TTS) plays in the browser and drives the mouth via **Web Audio RMS**; if TTS fails, it falls back to **Web Speech** + word-boundary bumps. The default avatar uses your **photo** in `web/public/avatar-photo.png` with a **mouth hotspot** (CSS “soft rig”). Designed to grow toward hologram display later.

## Architecture

```
User text → POST /api/chat (Gemini) → reply text → POST /api/tts (MP3) → Web Audio analyser → photo mouth (--mouth-open)
                                                      ↳ (fallback) Web Speech + decay lip sync
```

The API key stays on the server; the browser never sees it.

## Setup

1. Copy `.env.example` to `.env` and set `GEMINI_API_KEY` ([Google AI Studio](https://aistudio.google.com/apikey)). `GOOGLE_API_KEY` is also accepted.

2. **Python API** (3.11+ recommended):

```bash
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

3. **Frontend**:

```bash
npm install
npm run dev
```

4. Open the URL Vite prints (usually `http://localhost:5173`).

Activate the same venv before `npm run dev` so `python3` finds `uvicorn`. On Windows, if `python3` is missing, change the `dev` script in `package.json` to use `python -m uvicorn`.

- **Send** — asks the model, then speaks the answer with lip sync.
- **Speak only** — reads your textarea aloud (no LLM), useful for testing voice and mouth.

### Port 8001 already in use

Another process (often a leftover `uvicorn`) is bound to the API port. Free it:

```bash
# macOS / Linux: find and stop the listener on 8001
kill $(lsof -tiTCP:8001 -sTCP:LISTEN)
```

Or set a different port in `.env` as `API_PORT=8002` (Vite reads this for the proxy; `npm run dev` uses the same value).

## Photo avatar, lip sync, and rigging

**What’s in the app now**

- Replace `web/public/avatar-photo.png` with your portrait (same filename, or change the `src` in `web/index.html`).
- The **frame** is sized by an in-flow **`<img>`** (`object-fit: contain`, `max-height: min(90vh, 960px)`) so the **whole image** is visible. A **canvas** on top redraws the photo.
- **MediaPipe Face Landmarker** (`@mediapipe/tasks-vision`, runs in the browser): detects **face landmarks** once, auto-places the **mouth** ellipse and **eyes** for **idle blinks**. **Lip motion** still comes from your **audio** (Web Audio RMS from `/api/tts`) or Web Speech fallback — the model does **not** infer visemes from audio; it **aligns** the warp to real facial regions.
- If the model fails (network, GPU, no face), the app falls back to **manual** CSS variables on `.avatar-host` (table below). First visit downloads the **~4 MB** model from Google’s CDN.

**TTS**

- `POST /api/tts` with JSON `{ "text": "..." }` returns `audio/mpeg` (Edge TTS, no extra API key).
- Optional env: `EDGE_TTS_VOICE` (default `en-US-AriaNeural`). List voices: `edge-tts --list-voices`.

**Manual mouth** (only needed if AI detection fails — values are **fractions of the drawn image** after `contain`):

| Variable | Purpose |
| -------- | ------- |
| `--mouth-cx`, `--mouth-cy` | Mouth center (**0–1**). |
| `--mouth-rx`, `--mouth-ry` | Ellipse radii. |
| `--mouth-stretch` | Vertical warp strength at full voice (try **5–9**). |

**Higher realism (other AI / products)**

| Approach | What you get |
| -------- | -------------- |
| **This repo (browser)** | Landmarks + simple **2D warp** + **blink** + **audio-driven** mouth. Not a full muscle rig. |
| **Cloud talking heads** (e.g. **D-ID**, **HeyGen**, **Synthesia**, **Tavus**) | Upload image + audio → **video** with learned lip sync; best “out of the box” for marketing avatars. Usually paid APIs. |
| **Self-hosted ML** (**SadTalker**, **Wav2Lip**, **LiveTalking**) | GPU server, full **talking-head** video from one image + audio; heavy ops, most flexible. |
| **Live2D / Spine** | Artist-built **2D rig** with bones; you drive parameters from audio/visemes — not automatic from a single photo. |

## Scripts

| Command | Action |
| ------- | ------ |
| `npm run dev` | FastAPI on `http://127.0.0.1:8001` + Vite on `:5173` (`/api` proxied) |
| `npm run build` | Production build to `dist/web` |
| `npm run preview` | Preview built static assets |

API only (no Vite):

```bash
source .venv/bin/activate
uvicorn server.main:app --reload --host 127.0.0.1 --port 8001
```

## Next steps (toward hologram)

- Add **mic + STT** for listen → think → speak.
- Optional: integrate a **cloud talking-head** API and replace the canvas with a `<video>` player when you need broadcast-quality lip sync.
- For **3D** hologram: drive **Three.js** / **glTF** blendshapes from the same audio RMS or viseme pipeline.
