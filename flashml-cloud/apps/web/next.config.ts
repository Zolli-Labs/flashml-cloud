import type { NextConfig } from "next";
import path from "path";

/**
 * Refuse to produce a production build that cannot reach the API.
 *
 * `NEXT_PUBLIC_*` values are inlined by Next.js at BUILD time, so a missing
 * one is frozen into the bundle and cannot be corrected by setting the
 * variable afterwards — the build has to be redone. Nothing about the build
 * or the deploy fails: the site serves, sign-in works (Supabase has its own
 * variables), and the failure only appears once a signed-in user loads a page
 * that calls the API. They see "Failed to fetch" with no indication that a
 * deploy-time setting is missing.
 *
 * The specific failure this guards: without the variable, `cloudApiBase()`
 * falls back to `http://localhost:8000`, and an HTTPS page is not permitted
 * to request an HTTP URL — the browser blocks it as mixed content, which
 * surfaces as `TypeError: Failed to fetch`, indistinguishable from the API
 * being down.
 *
 * So fail here instead, where the message can name the variable and the fix.
 * Development is unaffected: the localhost fallback is correct there, which
 * is exactly why it cannot be trusted to fail loudly on its own.
 */
function assertApiBaseConfigured() {
  if (process.env.NODE_ENV !== "production") return;
  // `next build` runs this file; `next start` does too. Only the build bakes
  // the value, but failing on both is fine and simpler than detecting which.
  const base = process.env.NEXT_PUBLIC_CLOUD_API;
  if (base && base.trim() !== "") return;

  throw new Error(
    [
      "NEXT_PUBLIC_CLOUD_API is missing or empty in a production build.",
      "",
      "It is inlined at build time, so the resulting bundle would fall back to",
      "http://localhost:8000 — which an HTTPS page cannot request at all. Every",
      "API call would fail as 'Failed to fetch' with no further explanation.",
      "",
      "Set it to the API's public URL, e.g. https://flashml-api.onrender.com,",
      "and rebuild. On Render it is declared in render.yaml under flashml-web.",
    ].join("\n"),
  );
}

assertApiBaseConfigured();

const nextConfig: NextConfig = {
  turbopack: {
    root: path.resolve(__dirname),
  },
  async redirects() {
    return [
      { source: "/machines", destination: "/account/machines", permanent: true },
    ];
  },
};

export default nextConfig;
