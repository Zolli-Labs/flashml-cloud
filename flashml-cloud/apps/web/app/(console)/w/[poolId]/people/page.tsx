"use client";

import Link from "next/link";
import { MemberTable } from "@/components/workspace/MemberTable";
import { WorkspaceHeader } from "@/components/workspace/WorkspaceHeader";
import { useWorkspace } from "@/components/workspace/WorkspaceProvider";
import { workspacePath } from "@/lib/workspace-scope";

// `WorkspaceGate` (in the layout) already handles loading / not-found /
// error, and guarantees `pool` is loaded by the time this renders. The
// `if (!pool) return null` below exists only to satisfy `Pool | null` at
// the type level — do not turn it back into a `state !== "ready"` skeleton.
export default function WorkspacePeoplePage() {
  const { pool, members, isOwner } = useWorkspace();
  if (!pool) return null;

  return (
    <div className="mx-auto max-w-6xl px-4 py-8 sm:px-6">
      <WorkspaceHeader />

      <MemberTable members={members} ownerId={pool.owner_id} />

      {isOwner && (
        <p className="mt-4 text-sm text-muted-foreground">
          Want to add someone?{" "}
          <Link
            href={workspacePath(pool.id, "settings")}
            className="text-primary hover:underline"
          >
            Manage the invite link in Settings
          </Link>
          .
        </p>
      )}
    </div>
  );
}
