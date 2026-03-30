import { defineConfig, loadEnv } from "vite";

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  const apiPort = env.API_PORT || "8001";

  return {
    root: "web",
    server: {
      port: 5173,
      proxy: {
        "/api": { target: `http://127.0.0.1:${apiPort}`, changeOrigin: true },
      },
    },
    build: {
      outDir: "../dist/web",
      emptyOutDir: true,
    },
  };
});
