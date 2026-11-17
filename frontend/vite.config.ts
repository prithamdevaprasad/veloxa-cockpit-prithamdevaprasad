import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The frontend is independent of the backend (ARCHITECTURE.md P12, §19): it only calls the versioned JSON API.
// In development and preview, /v1 and /health are proxied to the API (default http://127.0.0.1:8000).
const target = process.env.VELOXA_API_URL ?? "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [react()],
  server: { port: 5173, strictPort: true, proxy: { "/v1": target, "/health": target } },
  preview: { port: 4173, strictPort: true, proxy: { "/v1": target, "/health": target } },
  build: { outDir: "dist", sourcemap: true },
});
