import { copyFileSync } from "node:fs";
import { resolve } from "node:path";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Project Pages live at /jp-us-screener/. Local preview and CI keep a relative base.
const base = process.env.VITE_BASE || "./";

export default defineConfig({
  base,
  plugins: [
    react(),
    {
      name: "github-pages-spa-fallback",
      apply: "build",
      closeBundle() {
        const dist = resolve("dist");
        copyFileSync(resolve(dist, "index.html"), resolve(dist, "404.html"));
      },
    },
  ],
  server: { host: "127.0.0.1", port: 5173 },
  preview: { host: "127.0.0.1", port: 4173 },
});
