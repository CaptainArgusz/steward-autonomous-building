import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";
import { fileURLToPath } from "node:url";
import { recommendationsPlugin } from "./recommendations-plugin.js";

const root = fileURLToPath(new URL("../", import.meta.url));

export default defineConfig(({ mode }) => ({
  plugins: [react(), recommendationsPlugin(() => loadEnv(mode, root, "GROQ_"))],
  base: "./",
  server: { fs: { allow: [".."] } },
}));
