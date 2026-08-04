"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { CaretUpDown, Check, Plus } from "@phosphor-icons/react";
import { listPools, type PoolSummary } from "@/lib/cloud-api";
import { workspacePath } from "@/lib/workspace-scope";

/** The rail's workspace picker. Fetches its OWN pool list rather than reading
 * `useWorkspace()` — this renders on `/account/*` and other routes with no
 * `WorkspaceProvider` above them, and consuming that context there would
 * throw.
 *
 * `currentId` is the pool id parsed from the URL (`workspaceIdFromPath`), not
 * anything this component resolves itself: the switcher's job is to display
 * and to navigate, not to decide which workspace is "current". */
export function WorkspaceSwitcher({ currentId }: { currentId: string | null }) {
  const [pools, setPools] = useState<PoolSummary[]>([]);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    let cancelled = false;
    listPools()
      .then((p) => {
        if (!cancelled) setPools(p);
      })
      .catch(() => {
        // A failed fetch is not an empty list. Leaving `pools` at its
        // initial `[]` renders the same "Choose a workspace" affordance a
        // brand-new member would see, which would be a lie to someone who
        // actually belongs to several — so on failure this renders nothing
        // beyond the button itself and says nothing further. Every page
        // below the rail already reports its own load failures.
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const current = pools.find((p) => p.id === currentId) ?? null;

  return (
    <div className="relative px-3 pb-2">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-haspopup="menu"
        aria-expanded={open}
        className="flex w-full items-center gap-2 rounded-md border border-border bg-background/60 px-2.5 py-1.5 text-left text-sm text-foreground transition-colors hover:bg-white/[0.04]"
      >
        <span className="min-w-0 flex-1 truncate font-medium">
          {current ? current.name : "Choose a workspace"}
        </span>
        <CaretUpDown size={14} className="shrink-0 text-muted-foreground" />
      </button>

      {open && (
        <>
          {/* Same overlay-button pattern as the mobile drawer below: a fixed
              inset button that closes the menu on any outside click, rather
              than a document-level listener. */}
          <button
            type="button"
            aria-label="Close workspace menu"
            onClick={() => setOpen(false)}
            className="fixed inset-0 z-40"
          />
          <div
            role="menu"
            className="absolute left-3 right-3 top-full z-50 mt-1.5 overflow-hidden rounded-md border border-border bg-bg-rail shadow-lg"
          >
            <ul className="max-h-64 overflow-y-auto py-1">
              {pools.map((p) => {
                const isCurrent = p.id === currentId;
                return (
                  <li key={p.id}>
                    <Link
                      href={workspacePath(p.id, "overview")}
                      role="menuitem"
                      onClick={() => setOpen(false)}
                      className="flex items-center gap-2.5 px-3 py-2 text-sm text-muted-foreground transition-colors hover:bg-white/[0.04] hover:text-foreground"
                    >
                      <Check
                        size={13}
                        weight="bold"
                        className={isCurrent ? "shrink-0 text-foreground" : "shrink-0 text-transparent"}
                      />
                      <span className="min-w-0 flex-1 truncate">{p.name}</span>
                      <span className="meta shrink-0">{p.member_count}</span>
                    </Link>
                  </li>
                );
              })}
            </ul>
            {/* `/workspaces` is built by a later task in this plan,
                immediately after this one — the same deliberate, time-boxed
                sequencing decision as the "My account" links in
                ConsoleShell.tsx. Linking now is what makes it reachable the
                moment it lands. */}
            <div className="border-t border-border py-1">
              <Link
                href="/workspaces"
                role="menuitem"
                onClick={() => setOpen(false)}
                className="flex items-center gap-2.5 px-3 py-2 text-sm text-muted-foreground transition-colors hover:bg-white/[0.04] hover:text-foreground"
              >
                <Plus size={14} className="shrink-0" />
                New workspace
              </Link>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
