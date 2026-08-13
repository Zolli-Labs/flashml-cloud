"use client";

import Link from "next/link";
import { Warning } from "@phosphor-icons/react";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { PageShell } from "@/components/shell/PageShell";
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
 *
 * WHY THIS IS NOT A `StatePanel`. It looks like the four-state contract and
 * it is not: its four states are `loading / ready / not-found / error`, and
 * `not-found` is deliberately absent from `PanelState`. `lib/console/
 * panel-state.ts` says why — "not found is a route-level answer, not a
 * panel-level one… the console replaces the PAGE for it", and names this
 * component as the thing that does the replacing. Folding a 404 into a
 * panel's `empty` is the exact substitution that module exists to prevent.
 * So this stays a page-level switch, and what it adopts from the sweep is
 * the container and the controls, not the panel.
 *
 * WHAT IT PROVIDES THAT A PANEL CANNOT. Because the provider reads all four
 * endpoints in ONE `Promise.all`, a workspace is loaded or it is not — there
 * is no partial ready. That is what lets the five tabs treat an empty
 * `jobs`/`machines`/`members` array as an OBSERVED empty rather than as a
 * possible failed read, and it is why those tabs' panels correctly carry two
 * states instead of four. This component holds the other two for all of
 * them.
 */
export function WorkspaceGate({ children }: { children: React.ReactNode }) {
  const { state, error, reload, reloading } = useWorkspace();

  if (state === "loading") {
    return (
      // `wide` — every one of the five tabs this gate fronts is filed `wide`
      // in `lib/console/page-width.ts`, so the skeleton occupies the column
      // the content will arrive into rather than a narrower one that shifts
      // the page as it resolves.
      <PageShell width="wide">
        {/* `role="status"` + `aria-busy` + an `sr-only` sentence, matching
            `StatePanel`'s loading slot. The bare `<div className="skeleton">`
            this replaces was silent to a screen reader: nothing announced
            that a read was in flight, so the page read as genuinely empty
            until it resolved — the same loading/empty collapse in the
            assistive layer that §1.1 forbids in the visual one. */}
        <div role="status" aria-busy="true">
          <Skeleton className="h-32" />
          <span className="sr-only">Loading this Workspace…</span>
        </div>
      </PageShell>
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
          This Workspace doesn&apos;t exist, or you&apos;re not a member.
        </p>
        {/* `/workspaces`, not `/pools`. `/pools` is a RESOLVER now
            (`WorkspaceResolver`): it drops you inside whichever workspace
            you were last in, which for someone who just failed to load one
            is the opposite of a way out. `/workspaces` is the page that
            actually lets you create or join one. */}
        <Link href="/workspaces" className="text-sm text-brand-foreground hover:underline">
          Create or join a Workspace
        </Link>
      </Shell>
    );
  }

  if (state === "error") {
    return (
      <Shell>
        <Warning className="h-5 w-5 text-destructive" weight="fill" />
        <p className="text-sm text-muted-foreground">{error}</p>
        {/* `reloading`, not `state === "loading"`. A retry deliberately does
            NOT move `state` — that would unmount this branch and take the
            only sentence explaining the failure off screen mid-click — so
            before the provider carried a separate "a reload the user asked
            for is outstanding" flag, this button absorbed a click and then
            said nothing for four network round trips. Silence that long is
            indistinguishable from a dead control, and retrying a failed read
            is the one thing there is to do on this screen. */}
        <Button
          variant="outline"
          size="sm"
          onClick={reload}
          disabled={reloading}
        >
          {reloading ? "Trying…" : "Try again"}
        </Button>
      </Shell>
    );
  }

  return <>{children}</>;
}

function Shell({ children }: { children: React.ReactNode }) {
  return (
    <PageShell width="wide">
      {/* `.panel` rather than the hand-written `rounded-lg border
          border-border bg-surface` — the same three declarations, and the
          string was byte-identical to one in `jobs/[jobId]/page.tsx`. */}
      <div className="panel flex flex-col items-center gap-3 py-10 text-center">
        {children}
      </div>
    </PageShell>
  );
}
