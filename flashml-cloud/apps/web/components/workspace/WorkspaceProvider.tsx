"use client";

import { createContext, useCallback, useContext, useEffect, useState } from "react";
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

  const load = useCallback(() => {
    Promise.all([getPool(poolId), getMe(), listJobs(), listPoolMachines(poolId)])
      .then(([detail, me, allJobs, fleet]) => {
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
          err instanceof Error ? err.message : "Couldn't load this workspace."
        );
        setState("error");
      });
  }, [poolId, router]);

  useEffect(() => {
    load();
  }, [load]);

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
    const t = setInterval(load, POLL_MS);
    return () => clearInterval(t);
  }, [jobs, state, load]);

  const isOwner =
    viewerId !== null && pool !== null && viewerId === pool.owner_id;

  return (
    <WorkspaceContext.Provider
      value={{ pool, members, machines, jobs, viewerId, isOwner, state, error, reload: load }}
    >
      {children}
    </WorkspaceContext.Provider>
  );
}
