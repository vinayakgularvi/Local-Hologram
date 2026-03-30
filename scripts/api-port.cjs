#!/usr/bin/env node
/** Print API port for dev (reads API_PORT from .env, default 8001). */
const fs = require("fs");
const path = require("path");
const envPath = path.join(__dirname, "..", ".env");
let port = "8001";
if (fs.existsSync(envPath)) {
  const text = fs.readFileSync(envPath, "utf8");
  const m = text.match(/^API_PORT\s*=\s*(.+)$/m);
  if (m) {
    port = m[1].trim().replace(/^["']|["']$/g, "");
  }
}
process.stdout.write(port);
