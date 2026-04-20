#!/usr/bin/env bash
# Run backend (FastAPI / uvicorn) and frontend (Vite) together for local development.
# Usage:
#   npm run dev          — from repo root (Node runner: scripts/dev.mjs)
#   npm run dev:bash     — same, via this shell script
#   ./scripts/dev.sh     — direct
#
# Backend:  http://127.0.0.1:${PORT:-8000}
# Frontend: http://127.0.0.1:5173  (proxies /api, /outputs, WebRTC paths to the backend)

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PORT="${PORT:-8000}"
export PORT

echo "Local Hologram — dev"
echo "  Backend:  http://127.0.0.1:${PORT}  (uvicorn --reload)"
echo "  Frontend: http://127.0.0.1:5173     (Vite; /api → backend)"
echo ""

cleanup() {
  for pid in "${PIDS[@]:-}"; do
    if [[ -n "${pid:-}" ]] && kill -0 "$pid" 2>/dev/null; then
      kill "$pid" 2>/dev/null || true
    fi
  done
}
trap cleanup EXIT INT TERM

PIDS=()
(
  cd "$ROOT/backend"
  exec python3 -m uvicorn main:app --host 127.0.0.1 --port "$PORT" --reload
) &
PIDS+=("$!")

(
  cd "$ROOT/frontend"
  exec npm run dev
) &
PIDS+=("$!")

wait
