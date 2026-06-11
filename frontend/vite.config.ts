import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "node:path";

// The backend (FastAPI) runs on :8000. We proxy the same path prefixes the
// existing client uses so the dev server hits the real API with no CORS setup.
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "src"),
    },
  },
  server: {
    port: 5174,
    proxy: {
      "/agents": "http://localhost:8000",
      "/dashboards": "http://localhost:8000",
      "/conversations": "http://localhost:8000",
      "/healthz": "http://localhost:8000",
    },
  },
});
