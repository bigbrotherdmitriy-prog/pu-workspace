import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  base: "/new/",
  build: {
    outDir: "../backend/app/react_dist",
    emptyOutDir: true,
  },
});
