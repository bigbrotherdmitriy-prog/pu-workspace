import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { pwaShellPlugin } from "./build/pwaShellPlugin.mjs";

export default defineConfig({
  plugins: [react(), pwaShellPlugin()],
  base: "/new/",
  build: {
    outDir: "../backend/app/react_dist",
    emptyOutDir: true,
  },
});
