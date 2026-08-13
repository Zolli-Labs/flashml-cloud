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
  // No JSX option: `main.tsx` imports React by name, so esbuild's default
  // transform for .tsx resolves without one — and the option is not in this
  // Vite version's `ESBuildOptions` type, so naming it fails `tsc`.
  build: {
    outDir: process.env.RIG_OUT ?? path.resolve(__dirname, "dist"),
    emptyOutDir: true,
  },
});
