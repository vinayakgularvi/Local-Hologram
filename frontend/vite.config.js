import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";

const HX = "/holuminex";
/** Must match uvicorn `PORT` from `npm run dev` (scripts/dev.mjs). */
const backendOrigin = `http://127.0.0.1:${process.env.PORT || "6064"}`;

export default defineConfig({
  base: `${HX}/`,
  plugins: [
    vue(),
    {
      name: "spa-fallback-analytics",
      configureServer(server) {
        server.middlewares.use((req, res, next) => {
          if (req.method === "GET" && req.url) {
            const raw = req.url.split("?")[0];
            if (raw === "/") {
              res.writeHead(302, { Location: `${HX}/` });
              res.end();
              return;
            }
          }
          next();
        });
        server.middlewares.use((req, _res, next) => {
          if (req.method !== "GET" || !req.url) {
            next();
            return;
          }
          const path = req.url.split("?")[0];
          if (path === `${HX}/favicon.ico`) {
            req.url = `${HX}/favicon.svg`;
          }
          if (path === `${HX}/analytics` || path === `${HX}/analystics`) {
            req.url = `${HX}/`;
          }
          next();
        });
      },
    },
  ],
  server: {
    port: 6066,
    proxy: {
      [`${HX}/api`]: { target: backendOrigin, changeOrigin: true },
      [`${HX}/outputs`]: { target: backendOrigin, changeOrigin: true },
      [`${HX}/offer`]: { target: backendOrigin, changeOrigin: true },
      [`${HX}/human`]: { target: backendOrigin, changeOrigin: true },
      [`${HX}/record`]: { target: backendOrigin, changeOrigin: true },
    },
  },
  preview: {
    port: 6067,
  },
});
