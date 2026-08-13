import path from "path";
import { defineConfig } from "vite";

/**
 * Builds the tabs keyboard rig (`main.tsx`) into a standalone page that a
 * real browser can load and a real keyboard can drive.
 *
 * Separate from everything else on purpose: it is not a Next route (console
 * routes need a session no agent here can get), not a vitest suite (there is
 * no DOM environment installed), and not a preview render (SSR has no focus
 * and no key events). It exists because "does the Tabs primitive still
 * handle Home/End" is a question about a running browser.
 *
 *   npx vite build --config preview/tabs-keyboard/vite.config.ts
 */
export default defineConfig({
  root: __dirname,
  resolve: {
    alias: { "@": path.resolve(__dirname, "../..") },
  },
  esbuild: { jsx: "automatic" },
  build: {
    outDir: process.env.RIG_OUT ?? path.resolve(__dirname, "dist"),
    emptyOutDir: true,
  },
});
