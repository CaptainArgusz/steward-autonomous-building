import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Relative base so the built site works from any path (GitHub Pages, file server).
// insights.md in the repo root is read at build time by the Lessons page.
export default defineConfig({ plugins: [react()], base: "./", server: { fs: { allow: [".."] } } });
