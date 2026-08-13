"use client";

import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import {
  NotAuthenticated,
  NotFound,
  getMe,
  getPool,
  listJobs,
  listPoolMachines,
  type JobRecord,
  type Pool,
  type PoolMachine,
  type PoolMember,
} from "@/lib/cloud-api";
import { isActiveJob, jobsInWorkspace } from "@/lib/job-scope";
import { LAST_WORKSPACE_COOKIE } from "@/lib/workspace-scope";

const POLL_MS = 5000;

export type WorkspaceLoadState = "loading" | "ready" | "not-found" | "error";

export interface WorkspaceContextValue {
  pool: Pool | null;
  members: PoolMember[];
  machines: PoolMachine[];
  /** Already filtered to this workspace. A tab must never re-filter. */
  jobs: JobRecord[];
  viewerId: string | null;
  isOwner: boolean;
  state: WorkspaceLoadState;
  error: string | null;
  reload: () => void;
  /** A reload somebody ASKED for is in flight — the retry button in
   * `WorkspaceGate`, the refresh control on the jobs tab, the reload
   * `RenameWorkspace` fires after a successful save.
   *
   * Separate from `state === "loading"` on purpose, and both directions
   * matter. Polling must NOT set it: `load` runs every 5s while a job is
   * active, and flipping the page to a skeleton on each tick would be a
   * worse bug than the one this fixes. And `state` must not be driven back
   * to `"loading"` on a retry either, because the retry button lives inside
   * the `"error"` branch — moving the state would unmount the control
   * mid-click and lose the only thing on screen that says what failed.
   *
   * So this is the third fact neither flag carried: a request the user
   * started, still outstanding. Without it the retry button had no way to
   * acknowledge a click, and four network round trips of silence is
   * indistinguishable from a dead button. */
  reloading: boolean;
}

const WorkspaceContext = createContext<WorkspaceContextValue | null>(null);

/** The five tabs read everything through this. It throws rather than
 * returning null outside the provider: a tab rendering with no workspace is
 * a routing bug, and silently showing an empty page would hide it. */
export function useWorkspace(): WorkspaceContextValue {
  const ctx = useContext(WorkspaceContext);
  if (ctx === null) {
    throw new Error("useWorkspace must be used inside a WorkspaceProvider");
  }
  return ctx;
}

export function WorkspaceProvider({
  poolId,
  children,
}: {
  poolId: string;
  children: React.ReactNode;
}) {
  const router = useRouter();
  const [pool, setPool] = useState<Pool | null>(null);
  const [members, setMembers] = useState<PoolMember[]>([]);
  const [machines, setMachines] = useState<PoolMachine[]>([]);
  const [jobs, setJobs] = useState<JobRecord[]>([]);
  const [viewerId, setViewerId] = useState<string | null>(null);
  const [state, setState] = useState<WorkspaceLoadState>("loading");
  const [error, setError] = useState<string | null>(null);
  const [reloading, setReloading] = useState(false);

  // Bumped by every `load()` call and compared when that call's response
  // settles, so a response that is no longer the latest one this instance
  // requested never reaches `setState`. A workspace switch (A -> B) no
  // longer needs this token to protect it — the `key={poolId}` on this
  // component in the layout means A's instance is unmounted and B's is a
  // fresh one with its own ref starting at 0, so A's response has nowhere
  // of B's to land even without a token check. What this still guards,
  // within a single workspace's lifetime: two `reload()` calls in quick
  // succession, a poll tick landing while a manual reload is in flight, and
  // a response settling after this instance has unmounted (see the cleanup
  // below) — all real, none of them fixed by the key.
  const requestIdRef = useRef(0);

  // Takes no arguments and writes no state synchronously — the mount effect
  // below calls it, and a setState in an effect's synchronous pass
  // double-renders EVERY consumer of this provider on every mount
  // (`react-hooks/set-state-in-effect`). `reloading` is therefore initialised
  // to `false` and only ever raised by `reload`, which runs from a click.
  const load = useCallback(() => {
    const requestId = ++requestIdRef.current;
    Promise.all([getPool(poolId), getMe(), listJobs(), listPoolMachines(poolId)])
      .then(([detail, me, allJobs, fleet]) => {
        if (requestIdRef.current !== requestId) return; // stale: superseded or unmounted
        setPool(detail.pool);
        setMembers(detail.members);
        setViewerId(me.id);
        // Filtered once, here. `listJobs` returns everything the viewer can
        // see across every workspace they belong to, and a tab that filtered
        // it again would be one refactor away from forgetting to.
        setJobs(jobsInWorkspace(allJobs, poolId));
        setMachines(fleet);
        setState("ready");
        setError(null);
      })
      .catch((err) => {
        if (requestIdRef.current !== requestId) return; // stale: superseded or unmounted
        if (err instanceof NotAuthenticated) {
          const next = window.location.pathname + window.location.search;
          router.push(`/sign-in?next=${encodeURIComponent(next)}`);
          return;
        }
        if (err instanceof NotFound) {
          // The API 404s for "does not exist" and "exists but you're not a
          // member" identically (fetch_pool_for_member's doctrine). This must
          // not be reworded into an access-denied message that would confirm
          // the id is real to someone outside the pool.
          setState("not-found");
          return;
        }
        setError(
          err instanceof Error ? err.message : "Couldn't load this Workspace."
        );
        setState("error");
      })
      .finally(() => {
        // Guarded by the same token as the two branches above, so a
        // superseded response cannot clear a flag a NEWER request owns.
        if (requestIdRef.current !== requestId) return;
        // Cleared by whichever request is current when it settles, whether
        // or not that is the one the user started. A poll tick can supersede
        // a manual reload — same four endpoints, so its response IS the
        // answer they asked for — and clearing only on the manual request
        // would leave the flag stuck true for the rest of the page's life in
        // exactly that case. `setReloading(false)` when it is already false
        // is a no-op React bails out of, so the poll pays nothing for this.
        setReloading(false);
      });
  }, [poolId, router]);

  /** What every caller outside this file gets: the same read, plus the flag
   * that says somebody asked for it. Raising `reloading` here rather than
   * inside `load` is what keeps the mount path free of a synchronous state
   * write — this only ever runs from a click. */
  const reload = useCallback(() => {
    setReloading(true);
    load();
  }, [load]);

  // NOTE: there is deliberately no `useEffect` here resetting state when
  // `poolId` changes. That reset can't happen "in time" — a `useEffect` runs
  // after React commits (and, absent a layout effect, after paint), so a
  // workspace switch would still render one frame of the previous
  // workspace's data under the new URL, labelled "ready", before the
  // corrective effect fired. Instead `app/(console)/w/[poolId]/layout.tsx`
  // keys this component on `poolId`, which makes React discard the old
  // instance and mount a brand new one on a switch: fresh `useState` values,
  // structurally, with no stale-data frame possible. Do not reintroduce a
  // reset effect here to "help" — it would be redundant with the key and add
  // back the extra mount-time render this replaced.

  useEffect(() => {
    load();
  }, [load]);

  // Invalidate whatever is still in flight when the provider itself goes
  // away, so a response that settles after unmount finds a token mismatch in
  // `load` above and returns before calling `setState` on an unmounted
  // component.
  useEffect(() => {
    return () => {
      // The rule assumes a cleanup READS a ref and warns that the value may
      // have moved on; its advice is to copy `requestIdRef.current` into a
      // local at mount and use that. This cleanup WRITES the ref, and it
      // has to write the live cell — `load`'s stale check compares against
      // `requestIdRef.current`, so bumping a mount-time copy would leave
      // the real token untouched and silently disable the whole
      // post-unmount guard. The "changed by now" the rule warns about is
      // exactly the value this needs.
      // eslint-disable-next-line react-hooks/exhaustive-deps
      requestIdRef.current++;
    };
  }, []);

  // Remember where we were, so `/overview` and the post-sign-in redirect can
  // resolve to somewhere real. Written only once the fetch SUCCEEDS: caching
  // an id we just failed to load would send the next bare entry straight
  // back into the same failure.
  useEffect(() => {
    if (state !== "ready") return;
    document.cookie = `${LAST_WORKSPACE_COOKIE}=${encodeURIComponent(poolId)}; path=/; max-age=31536000; SameSite=Lax`;
  }, [state, poolId]);

  // Stop polling once nothing is in flight. A settled workspace changes only
  // when someone acts on it, and the console is the kind of thing left open
  // in a background tab for days.
  useEffect(() => {
    if (state !== "ready") return;
    if (!jobs.some(isActiveJob)) return;
    // Polling calls `load` directly and never `reload`: a background tick is
    // not something the user asked for, so it must not raise `reloading` and
    // spin the refresh control every five seconds.
    const t = setInterval(load, POLL_MS);
    return () => clearInterval(t);
  }, [jobs, state, load]);

  const isOwner =
    viewerId !== null && pool !== null && viewerId === pool.owner_id;

  return (
    <WorkspaceContext.Provider
      value={{ pool, members, machines, jobs, viewerId, isOwner, state, error, reload, reloading }}
    >
      {children}
    </WorkspaceContext.Provider>
  );
}
