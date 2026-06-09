import path from "node:path";
import { fileURLToPath } from "node:url";
import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";
import basicSsl from '@vitejs/plugin-basic-ssl'

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(__dirname, "..");
const backendPort = process.env.PORT || "8080";
const backendOrigin = `http://127.0.0.1:${backendPort}`;

export default defineConfig({
  /** Load VITE_* from repository root `.env` (not only `frontend/.env`). */
  envDir: repoRoot,
  plugins: [
    basicSsl(),
    vue(),
    {
      name: "spa-fallback-analytics",
      configureServer(server) {
        server.middlewares.use((req, _res, next) => {
          if (req.method !== "GET" || !req.url) {
            next();
            return;
          }
          const path = req.url.split("?")[0];
          if (path === "/favicon.ico") {
            req.url = "/favicon.svg";
          }
          if (path === "/analytics" || path === "/analystics" || path === "/video-rag") {
            req.url = "/";
          }
          next();
        });
      },
    },
  ],
  server: {
    host: '0.0.0.0',
    https: true,
    port: 5173,
    proxy: {
      "/api": { target: backendOrigin, changeOrigin: true },
      "/outputs": { target: backendOrigin, changeOrigin: true },
      "/offer": { target: backendOrigin, changeOrigin: true },
      "/session": { target: backendOrigin, changeOrigin: true },
      "/human": { target: backendOrigin, changeOrigin: true },
      "/record": { target: backendOrigin, changeOrigin: true },
      "/interrupt_talk": { target: backendOrigin, changeOrigin: true },
      // Avatar MP4 may exist on backend (frontend/public) before Vite picks up the file
      "^/(?!@|src/|node_modules/).*\\.mp4$": { target: backendOrigin, changeOrigin: true },
    },
  },
});
