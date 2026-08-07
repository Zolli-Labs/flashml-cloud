"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { CaretUpDown, Check, Plus } from "@phosphor-icons/react";
import { listPools, type PoolSummary } from "@/lib/cloud-api";
import { onWorkspacesChanged } from "@/lib/workspace-events";
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
    // One `cancelled` flag for every fetch this effect ever starts — the
    // initial one and each later refresh — so unmounting stops all of them
    // rather than only the first.
    let cancelled = false;
    const load = () => {
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
    };

    load();
    // Mounting is not the only time this list can change. `ConsoleShell` is
    // a layout Next keeps mounted across client navigations, so this
    // component does NOT remount on the way back from Settings — a rename
    // left the rail showing the old name until a full page reload. See
    // `lib/workspace-events.ts` for why the signal is a window event rather
    // than context or a store.
    const unsubscribe = onWorkspacesChanged(load);
    return () => {
      cancelled = true;
      unsubscribe();
    };
  }, []);

  const current = pools.find((p) => p.id === currentId) ?? null;

  return (
    <div className="relative px-3 pb-3">
      <p className="label-caps mb-1.5 px-0.5">Crew</p>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-haspopup="menu"
        aria-expanded={open}
        className="flex w-full items-center gap-2 rounded-md border border-border bg-surface px-2.5 py-2 text-left text-sm text-foreground shadow-sm transition-colors hover:border-primary/30 hover:bg-surface-2"
      >
        <span className="min-w-0 flex-1 truncate font-medium">
          {current ? current.name : "Choose a Crew"}
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
            aria-label="Close Crew menu"
            onClick={() => setOpen(false)}
            className="fixed inset-0 z-40"
          />
          <div
            role="menu"
            className="absolute left-3 right-3 top-full z-50 mt-1.5 overflow-hidden rounded-lg border border-border bg-surface shadow-lg"
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
                      className="flex items-center gap-2.5 px-3 py-2 text-sm text-muted-foreground transition-colors hover:bg-primary/5 hover:text-foreground"
                    >
                      <Check
                        size={13}
                        weight="bold"
                        className={isCurrent ? "shrink-0 text-brand-foreground" : "shrink-0 text-transparent"}
                      />
                      <span className="min-w-0 flex-1 truncate">{p.name}</span>
                      <span className="meta shrink-0">{p.member_count}</span>
                    </Link>
                  </li>
                );
              })}
            </ul>
            {/* `/workspaces` (`app/(console)/workspaces/page.tsx`) is the
                create-or-join landing page — same route the resolver sends
                someone with no workspace to. */}
            <div className="border-t border-border py-1">
              <Link
                href="/workspaces"
                role="menuitem"
                onClick={() => setOpen(false)}
                className="flex items-center gap-2.5 px-3 py-2 text-sm text-muted-foreground transition-colors hover:bg-primary/5 hover:text-foreground"
              >
                <Plus size={14} className="shrink-0" />
                New Crew
              </Link>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
