"use client";

import Link from "next/link";
import { PageShell } from "@/components/shell/PageShell";
import { StatePanel } from "@/components/shell/StatePanel";
import { MemberTable } from "@/components/workspace/MemberTable";
import { WorkspaceHeader } from "@/components/workspace/WorkspaceHeader";
import { useWorkspace } from "@/components/workspace/WorkspaceProvider";
import {
  isEmptyList,
  resolvePanelState,
  type PanelRead,
} from "@/lib/console/panel-state";
import { workspacePath } from "@/lib/workspace-scope";
import type { PoolMember } from "@/lib/cloud-api";

// `WorkspaceGate` (in the layout) already handles loading / not-found /
// error, and guarantees `pool` is loaded by the time this renders. The
// `if (!pool) return null` below exists only to satisfy `Pool | null` at
// the type level — do not turn it back into a `state !== "ready"` skeleton.
export default function WorkspacePeoplePage() {
  const { pool, members, isOwner, reload } = useWorkspace();
  if (!pool) return null;

  // WHY THIS READ IS ALWAYS `"read"`, and why that is honest rather than a
  // shortcut. `WorkspaceProvider` fetches the pool, the viewer, the jobs and
  // the fleet in ONE `Promise.all`, so a failure in any of them fails the
  // whole workspace and `WorkspaceGate` replaces this page before it mounts.
  // By the time this component runs, the read that produced `members` has
  // already succeeded — which is exactly what licenses treating an empty
  // array as an OBSERVED empty instead of a possible failed read. Building
  // the `PanelRead` explicitly says so at the one place it matters, rather
  // than leaving it as a fact you have to know.
  const read: PanelRead<PoolMember[]> = { status: "read", data: members };
  const panel = resolvePanelState(read, isEmptyList);

  return (
    <PageShell width="wide">
      <WorkspaceHeader />

      <div className="mt-6">
        <StatePanel
          state={panel}
          label="this Workspace's members"
          // DEFECT FIX: there was no empty state. `MemberTable` rendered its
          // four column headings over an empty `<tbody>` and said nothing —
          // a headed table with no rows, which reads as a broken table
          // rather than as an answer. Latent rather than live today, since
          // a workspace always has at least its owner as a member, but it
          // was the "present" branch standing in for "empty" and the next
          // change to membership would have made it visible.
          empty={{
            title: "No members yet.",
            description:
              "Everyone who joins this Workspace through its invite link appears here.",
          }}
          // Unreachable from here — the gate owns a failed read for all five
          // tabs — but `StatePanel` requires an answer, and `reload` is the
          // right one if this panel ever gets a read of its own.
          unreadable={{ retry: reload }}
        >
          {(rows) => <MemberTable members={rows} ownerId={pool.owner_id} />}
        </StatePanel>
      </div>

      {isOwner && (
        <p className="mt-4 text-sm text-muted-foreground">
          Want to add someone?{" "}
          <Link
            href={workspacePath(pool.id, "settings")}
            className="text-brand-foreground hover:underline"
          >
            Manage the invite link in Settings
          </Link>
          .
        </p>
      )}
    </PageShell>
  );
}
