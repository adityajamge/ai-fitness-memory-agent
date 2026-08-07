import path from "node:path";
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: { "@": path.resolve(__dirname, "./src") },
  },
  build: {
    // FastAPI mounts this directory as the SPA (see api/main.py). Keep the name in sync there.
    outDir: "dist",
    sourcemap: true,
  },
  server: {
    port: 5173,
    // Dev only: the SPA runs on 5173 while FastAPI runs on 8080. In production both are the same
    // origin inside one container, so this proxy exists purely so `credentials: "same-origin"`
    // and relative /api paths behave identically in dev and prod.
    proxy: {
      "/api": { target: "http://127.0.0.1:8080", changeOrigin: true },
      "/healthz": { target: "http://127.0.0.1:8080", changeOrigin: true },
    },
  },
});
