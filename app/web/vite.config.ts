import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// `npm run build` writes into the Python package, which serves it.
// `npm run dev` serves the pages with hot reload and forwards /api to a
// running `uv run robocup-web`.
export default defineConfig({
  plugins: [react()],
  build: { outDir: "../server/src/robocup_web/static", emptyOutDir: true },
  server: {
    port: 5173,
    proxy: { "/api": { target: "http://127.0.0.1:5801", ws: true } },
  },
});
