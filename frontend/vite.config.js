import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";

export default defineConfig({
  plugins: [
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
          if (path === "/analytics" || path === "/analystics") {
            req.url = "/";
          }
          next();
        });
      },
    },
  ],
  server: {
    port: 5173,
    proxy: {
      "/api": { target: "http://127.0.0.1:8000", changeOrigin: true },
      "/outputs": { target: "http://127.0.0.1:8000", changeOrigin: true },
      "/offer": { target: "http://127.0.0.1:8000", changeOrigin: true },
      "/human": { target: "http://127.0.0.1:8000", changeOrigin: true },
      "/record": { target: "http://127.0.0.1:8000", changeOrigin: true },
    },
  },
});
