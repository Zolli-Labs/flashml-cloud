"use client";

import { InviteManager } from "@/components/workspace/InviteManager";
import { RenameWorkspace } from "@/components/workspace/RenameWorkspace";
import { WorkspaceHeader } from "@/components/workspace/WorkspaceHeader";
import { useWorkspace } from "@/components/workspace/WorkspaceProvider";
import { relativeTime } from "@/lib/machine-status";

// `WorkspaceGate` (in the layout) already handles loading / not-found /
// error, and guarantees `pool` is loaded by the time this renders. The
// `if (!pool) return null` below exists only to satisfy `Pool | null` at
// the type level — do not turn it back into a `state !== "ready"` skeleton.
export default function WorkspaceSettingsPage() {
  const { pool, members, isOwner, reload } = useWorkspace();
  if (!pool) return null;

  // Same fallback `MemberTable` uses: a member can join before setting a
  // display name.
  const owner = members.find((m) => m.user_id === pool.owner_id);

  return (
    <div className="mx-auto max-w-6xl px-4 py-8 sm:px-6">
      <WorkspaceHeader />

      {isOwner ? (
        <>
          <div className="mt-8">
            <RenameWorkspace
              poolId={pool.id}
              currentName={pool.name}
              onRenamed={reload}
            />
          </div>

          <div className="mt-8">
            <InviteManager poolId={pool.id} />
          </div>
        </>
      ) : (
        // `isOwner` is a rendering convenience, not a security boundary — the
        // API is what actually enforces this. A greyed-out rename/invite
        // control would promise a capability the client cannot verify the
        // viewer could ever exercise: the API 404s "not the owner" identically
        // to "no such pool" (see `renamePool`'s doc comment in cloud-api.ts).
        // Absent beats disabled here.
        <p className="mt-8 text-sm text-muted-foreground">
          Only this workspace&apos;s owner can rename it or manage its invite
          link.
        </p>
      )}

      <section className="panel mt-8 p-5">
        <h2 className="text-sm font-semibold">Details</h2>
        <dl className="mt-4 divide-y divide-border">
          <DetailRow label="Workspace id">
            <span className="font-mono text-xs text-muted-foreground">
              {pool.id}
            </span>
          </DetailRow>
          <DetailRow label="Created">
            <span className="text-sm">{relativeTime(pool.created_at)}</span>
          </DetailRow>
          <DetailRow label="Owner">
            <span className="text-sm">{owner?.display_name || "unnamed"}</span>
          </DetailRow>
        </dl>
      </section>
    </div>
  );
}

function DetailRow({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex items-start justify-between gap-6 py-3.5">
      <dt className="text-sm">{label}</dt>
      <dd className="min-w-0 shrink-0 text-right">{children}</dd>
    </div>
  );
}
