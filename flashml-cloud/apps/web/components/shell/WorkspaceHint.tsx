"use client";

import { createContext, useContext, useEffect } from "react";

/**
 * How a page whose URL does not name a workspace tells the rail which
 * workspace it is nonetheless part of.
 *
 * `workspaceIdFromPath` can only read `/w/<poolId>/…`, which is right — it
 * is a pure function over the URL and must stay one. But `/jobs/[jobId]` is
 * the normal destination from both the Jobs tab and Overview, and its path
 * carries no pool id, so `ConsoleShell` rendered no workspace tabs and the
 * switcher fell back to "Choose a workspace". One click out of a workspace
 * lost every piece of workspace navigation.
 *
 * The job record already carries `pool_id` (it drives the back link on that
 * page), so the missing piece was never the data — only a way to hand it
 * upward. That is what this is: `ConsoleShell` provides its own setter,
 * `useWorkspaceHint(poolId)` calls it from anywhere below, and the hint is
 * cleared on unmount so it can never outlive the page that set it.
 *
 * Deliberately a HINT and not a second source of truth: the shell still
 * prefers `workspaceIdFromPath`, and only falls back here. A workspace-
 * scoped URL always wins, so this cannot make the rail disagree with the
 * address bar.
 *
 * A context rather than the window event `lib/workspace-events.ts` uses:
 * that one is a pulse ("your list is stale"), this one is a value with a
 * lifetime tied to a mounted component, which is what context is for.
 */
const WorkspaceHintContext = createContext<(poolId: string | null) => void>(
  () => {}
);

export function WorkspaceHintProvider({
  onHint,
  children,
}: {
  /** Must be referentially stable — a `useState` setter, or a `useCallback`.
   * An inline arrow would re-run every consumer's effect on every render of
   * the shell, which means clearing and re-setting the hint each time. */
  onHint: (poolId: string | null) => void;
  children: React.ReactNode;
}) {
  return (
    <WorkspaceHintContext.Provider value={onHint}>
      {children}
    </WorkspaceHintContext.Provider>
  );
}

/** Declare the workspace this page belongs to. Pass `null` while it is still
 * unknown (loading, or a job with no `pool_id` at all) — that leaves the
 * rail exactly as it would have been. */
export function useWorkspaceHint(poolId: string | null | undefined): void {
  const setHint = useContext(WorkspaceHintContext);
  const value = poolId ?? null;
  useEffect(() => {
    setHint(value);
    // Clearing on unmount is what keeps this honest: navigate off the job
    // and the rail goes back to whatever the URL alone says.
    return () => setHint(null);
  }, [value, setHint]);
}
