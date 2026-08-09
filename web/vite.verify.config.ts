// Throwaway dev config for local visual verification against the TEST cluster.
// Identical to vite.config.ts except it proxies to an API on 8081, so it can run alongside a
// developer's own stack (5173 + 8080) without disturbing it. Not referenced by any build.
import path from "node:path";
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: { alias: { "@": path.resolve(__dirname, "./src") } },
  server: {
    port: 5180,
    proxy: {
      "/api": { target: "http://127.0.0.1:8081", changeOrigin: true },
      "/healthz": { target: "http://127.0.0.1:8081", changeOrigin: true },
    },
  },
});
