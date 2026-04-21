import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";
import basicSsl from '@vitejs/plugin-basic-ssl'

const backendPort = process.env.PORT || "8080";
const backendOrigin = `http://127.0.0.1:${backendPort}`;

export default defineConfig({
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
          if (path === "/analytics" || path === "/analystics") {
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
      "/human": { target: backendOrigin, changeOrigin: true },
      "/record": { target: backendOrigin, changeOrigin: true },
    },
  },
});
