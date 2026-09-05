import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Real application entrypoint; never load local/production .env or overwrite react_dist.
export default defineConfig({
  plugins: [react()],
  envDir: false,
  base: "/new/",
  build: { outDir: "node_modules/.cache/storage-picker-e2e/site", emptyOutDir: true },
});
