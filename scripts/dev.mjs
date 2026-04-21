#!/usr/bin/env node
/**
 * Run backend (FastAPI / uvicorn) and frontend (Vite) together for local development.
 *
 * Usage (repo root):
 *   npm run dev
 *   npm start
 *   node scripts/dev.mjs
 *
 * Environment:
 *   PORT   Backend port (default 8080). Frontend still uses Vite (5173); proxy /api to backend.
 *
 * Shell alternative: npm run dev:bash
 */

import { spawn } from "node:child_process";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(__dirname, "..");
const port = String(process.env.PORT || "8080");

const isWin = process.platform === "win32";
const python = process.env.PYTHON || (isWin ? "python" : "python3");
const npmCmd = isWin ? "npm.cmd" : "npm";

const children = [];

function spawnLogged(cmd, args, cwd) {
  const child = spawn(cmd, args, {
    cwd,
    stdio: "inherit",
    env: { ...process.env, PORT: port },
    shell: isWin,
  });
  children.push(child);
  return child;
}

console.log("Local Hologram — dev");
console.log(`  Backend:  http://127.0.0.1:${port}  (uvicorn --reload)`);
console.log("  Frontend: http://127.0.0.1:5173     (Vite; /api → backend)");
console.log("");

const backend = spawnLogged(
  python,
  ["-m", "uvicorn", "main:app", "--host", "127.0.0.1", "--port", port, "--reload"],
  path.join(root, "backend"),
);

const frontend = spawnLogged(npmCmd, ["run", "dev"], path.join(root, "frontend"));

let stopping = false;
function killOthers(exited) {
  if (stopping) return;
  stopping = true;
  for (const c of children) {
    if (c !== exited && c.pid && !c.killed) {
      c.kill(isWin ? undefined : "SIGTERM");
    }
  }
}

function onSignal() {
  stopping = true;
  for (const c of children) {
    if (c.pid && !c.killed) {
      c.kill(isWin ? undefined : "SIGTERM");
    }
  }
  process.exit(0);
}

process.on("SIGINT", onSignal);
process.on("SIGTERM", onSignal);

backend.on("exit", (code) => {
  killOthers(backend);
  if (code && code !== 0) process.exitCode = code;
});

frontend.on("exit", (code) => {
  killOthers(frontend);
  if (code && code !== 0) process.exitCode = code;
});

await Promise.all([
  new Promise((resolve) => backend.once("exit", resolve)),
  new Promise((resolve) => frontend.once("exit", resolve)),
]);
