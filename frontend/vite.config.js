import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// API base URL is injected at build/run time via VITE_API_BASE_URL
// (see docker-compose.yml / README) so the same build works whether the
// backend is on localhost:8000 in dev or a service name inside Docker.
export default defineConfig({
  plugins: [react()],
  server: {
    host: "0.0.0.0",
    port: 5173,
  },
  preview: {
    host: "0.0.0.0",
    port: 4173,
  },
});
