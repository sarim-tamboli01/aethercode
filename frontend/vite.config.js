/**
 * vite.config.js — Phase 2
 * Vite dev-server config for the AetherCode React frontend.
 *
 * Proxies /health and /run-task* to the FastAPI backend on port 8000
 * so the frontend can call the API without CORS issues during dev.
 */

import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/health": "http://127.0.0.1:8000",
      "/run-task": "http://127.0.0.1:8000",
    },
  },
});
