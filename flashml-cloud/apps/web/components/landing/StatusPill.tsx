"use client";

import { useEffect, useState } from "react";
import { cloudApiBase } from "@/lib/cloud-api";

// A real health check, not a decorative dot.
//
// I left this off the footer originally on the grounds that a green
// "all systems operational" with nothing behind it is the one lie that would
// cost most on a page selling reliability. This version earns it: it calls
// the API's own /healthz and reports what came back, including when that is
// bad news.
//
// Deliberately no auth and no credentials — /healthz is public, and a status
// indicator that only works for signed-in users is not a status indicator.

type State = "checking" | "up" | "down";

export function StatusPill() {
  const [state, setState] = useState<State>("checking");

  useEffect(() => {
    let cancelled = false;

    async function check() {
      // Render's free tier cold-starts, which can take 30s+. A short timeout
      // would report a sleeping service as down, so this waits rather than
      // being confidently wrong.
      const ctrl = new AbortController();
      const timer = setTimeout(() => ctrl.abort(), 45_000);
      try {
        const r = await fetch(`${cloudApiBase()}/healthz`, {
          signal: ctrl.signal,
          cache: "no-store",
        });
        if (!cancelled) setState(r.ok ? "up" : "down");
      } catch {
        if (!cancelled) setState("down");
      } finally {
        clearTimeout(timer);
      }
    }

    check();
    const id = setInterval(check, 60_000);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, []);

  const label =
    state === "checking"
      ? "checking status"
      : state === "up"
        ? "API operational"
        : "API unreachable";

  const color =
    state === "checking"
      ? "var(--muted-foreground)"
      : state === "up"
        ? "var(--node-green)"
        : "var(--warning)";

  return (
    <span
      className="inline-flex items-center gap-2 rounded-full border border-border px-3 py-1.5"
      // Announce transitions, but do not interrupt: this is ambient
      // information, not something the reader asked for.
      aria-live="polite"
    >
      <span
        className="status-dot"
        data-state={state === "up" ? "live" : undefined}
        style={{ background: color }}
      />
      <span className="meta">{label}</span>
    </span>
  );
}
