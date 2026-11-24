import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";
import { mockApi } from "./mock/mockApi.ts";

// `npm run dev:mock` serves a small in-memory API so the UI can be seen
// without the Python server. It is never part of the production build.
const useMock = process.env.JIMMYHQ_MOCK === "1";

export default defineConfig(({ command }) => ({
  plugins: [react(), tailwindcss(), ...(command === "serve" && useMock ? [mockApi()] : [])],
  build: { outDir: "dist", emptyOutDir: true },
  server: {
    host: "127.0.0.1",
    proxy: useMock ? undefined : { "/api": { target: "http://127.0.0.1:8440", changeOrigin: false } },
  },
}));
