import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    // Same-origin API calls in development: no CORS, same shape as a reverse-proxied deploy.
    proxy: { "/api": "http://127.0.0.1:8000" },
    // Tests read generated reference docs from the repository root (e.g. the policy rule catalogue);
    // the dev server keeps Vite's default file access.
    ...(process.env.VITEST ? { fs: { allow: [".."] } } : {}),
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
    restoreMocks: true,
  },
});
