import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    // Same-origin API calls in development: no CORS, same shape as a reverse-proxied deploy.
    proxy: { "/api": "http://127.0.0.1:8000" },
  },
});
