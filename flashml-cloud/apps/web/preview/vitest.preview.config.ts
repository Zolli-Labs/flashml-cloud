import path from "path";
import { defineConfig } from "vitest/config";

/**
 * A SEPARATE runner for the console preview harness — deliberately not part of
 * the normal suite.
 *
 * The main `vitest.config.ts` collects `**\/*.test.{ts,tsx}`. The harness files
 * are named `*.render.tsx`, so they do not match it and cannot inflate the test
 * baseline or fail CI. This config is the only thing that runs them.
 *
 *   npx vitest run --config preview/vitest.preview.config.ts
 */
export default defineConfig({
  resolve: {
    alias: {
      "@": path.resolve(__dirname, ".."),
    },
  },
  test: {
    root: path.resolve(__dirname, ".."),
    environment: "node",
    include: ["preview/**/*.render.tsx"],
  },
});
