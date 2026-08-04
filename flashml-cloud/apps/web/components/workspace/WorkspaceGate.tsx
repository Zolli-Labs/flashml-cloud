"use client";

import Link from "next/link";
import { Warning } from "@phosphor-icons/react";
import { useWorkspace } from "@/components/workspace/WorkspaceProvider";

/**
 * The one place that switches on `WorkspaceProvider`'s `state`, so every
 * tab under `/w/[poolId]` can assume `pool` is loaded and just render.
 *
 * Each tab guarding itself with `if (state !== "ready") return <skeleton/>`
 * would multiply a real mistake: the provider can also be `"not-found"` (no
 * such pool, or you aren't a member) or `"error"`, and that guard renders a
 * loading skeleton for either FOREVER — there is no third branch to escape
 * to. Fixing it once here means a sixth tab gets the right behaviour for
 * free instead of inheriting whichever guard got copied last.
 */
export function WorkspaceGate({ children }: { children: React.ReactNode }) {
  const { state, error, reload } = useWorkspace();

  if (state === "loading") {
    return (
      <div className="mx-auto max-w-6xl px-4 py-8 sm:px-6">
        <div className="skeleton h-32 rounded-lg" />
      </div>
    );
  }

  if (state === "not-found") {
    return (
      <Shell>
        {/* The API 404s identically for "no such pool" and "you're not a
            member" (fetch_pool_for_member's doctrine). This copy must not be
            reworded into an access-denied message — that would confirm to
            an outsider that the id names a real workspace. */}
        <p className="text-sm text-muted-foreground">
          This workspace doesn&apos;t exist, or you&apos;re not a member.
        </p>
        {/* `/workspaces`, not `/pools`. `/pools` is a RESOLVER now
            (`WorkspaceResolver`): it drops you inside whichever workspace
            you were last in, which for someone who just failed to load one
            is the opposite of a way out. `/workspaces` is the page that
            actually lets you create or join one. */}
        <Link href="/workspaces" className="text-sm text-primary hover:underline">
          Create or join a workspace
        </Link>
      </Shell>
    );
  }

  if (state === "error") {
    return (
      <Shell>
        <Warning className="h-5 w-5 text-destructive" weight="fill" />
        <p className="text-sm text-muted-foreground">{error}</p>
        <button
          type="button"
          onClick={reload}
          className="rounded-md border border-border px-3 py-1.5 text-sm hover:bg-white/[0.06]"
        >
          Try again
        </button>
      </Shell>
    );
  }

  return <>{children}</>;
}

function Shell({ children }: { children: React.ReactNode }) {
  return (
    <div className="mx-auto max-w-6xl px-4 py-8 sm:px-6">
      <div className="flex flex-col items-center gap-3 rounded-lg border border-border bg-surface py-10 text-center">
        {children}
      </div>
    </div>
  );
}
