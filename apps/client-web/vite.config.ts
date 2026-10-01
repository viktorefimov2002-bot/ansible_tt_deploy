import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
export default defineConfig({
  plugins: [react(), tailwindcss()],
  base: "/client/",
  server: { proxy: { "/api": "http://127.0.0.1:8080" } },
  test: {
    testTimeout: 10000,
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
  },
});
