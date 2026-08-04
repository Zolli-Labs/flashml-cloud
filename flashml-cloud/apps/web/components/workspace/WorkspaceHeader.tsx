"use client";

import Link from "next/link";
import { Plus } from "@phosphor-icons/react";
import { useWorkspace } from "@/components/workspace/WorkspaceProvider";
import { isMachineOnline } from "@/lib/machine-scope";
import { workspacePath } from "@/lib/workspace-scope";

/**
 * The name line, the member/online summary, and the "New job" button —
 * shared by all five tabs under `/w/[poolId]`. Reads `useWorkspace()`
 * directly rather than taking `pool`/`members`/`machines` as props, since
 * every caller already sits inside the same `WorkspaceProvider` this reads
 * from.
 *
 * Callers sit inside `WorkspaceGate`, so `pool` is guaranteed non-null at
 * runtime; the `if (!pool) return null` below exists only to satisfy
 * `Pool | null` at the type level.
 */
export function WorkspaceHeader() {
  const { pool, members, machines } = useWorkspace();
  if (!pool) return null;

  // `isMachineOnline`: heartbeat recency, not enrolment state — see
  // lib/machine-scope.ts. Must agree with PoolFleetTable's own dots.
  const online = machines.filter(isMachineOnline).length;

  return (
    <div className="flex flex-wrap items-end justify-between gap-3">
      <div>
        <h1 className="title">{pool.name}</h1>
        <p className="meta mt-1.5">
          {members.length} people · {online} machines online
        </p>
      </div>
      <Link
        href={workspacePath(pool.id, "submit")}
        className="interactive inline-flex items-center gap-2 rounded-md bg-primary px-3.5 py-2 text-sm font-semibold text-primary-foreground hover:brightness-110"
      >
        <Plus size={15} weight="bold" />
        New job
      </Link>
    </div>
  );
}
