"use client";

import Link from "next/link";
import { Plus } from "@phosphor-icons/react";
import { buttonVariants } from "@/components/ui/button";
import { PageHeader } from "@/components/shell/PageHeader";
import { useWorkspace } from "@/components/workspace/WorkspaceProvider";
import { isMachineOnline } from "@/lib/machine-scope";
import { countOf } from "@/lib/plural";
import { workspacePath } from "@/lib/workspace-scope";

/**
 * The name line, the member/online summary, and the "New job" button —
 * shared by all five tabs under `/w/[poolId]`. Reads `useWorkspace()`
 * directly rather than taking `pool`/`members`/`machines` as props, since
 * every caller already sits inside the same `WorkspaceProvider` this reads
 * from.
 *
 * WHY THIS STILL EXISTS NEXT TO `PageHeader`. It is no longer a second
 * header — it is the workspace BINDING of the one header. The markup it used
 * to hand-write (`flex flex-wrap items-end justify-between gap-3`, `.title`,
 * `.meta mt-1.5`) was `components/shell/PageHeader.tsx` line for line, so
 * that copy is gone and this composes the primitive instead. What is left is
 * the part `PageHeader` cannot know: which fields of a workspace make up its
 * heading, and that the online count is `isMachineOnline` (heartbeat
 * recency) rather than enrolment. Deleting this and calling `PageHeader`
 * from each tab would re-derive that in five places — which is the drift the
 * primitive exists to remove, one level up.
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
    <PageHeader
      title={pool.name}
      // Both counts are lengths of arrays the API returned. `members` and
      // `machines` are only ever populated from a read that SUCCEEDED — the
      // provider's `Promise.all` fails the whole workspace otherwise and
      // `WorkspaceGate` replaces the page — so a `0` here is an observed
      // zero and never a stand-in for a failed read (§1.1).
      meta={`${countOf(members.length, "person", "people")} · ${countOf(online, "machine")} online`}
      actions={
        // `buttonVariants()` on a `<Link>` rather than `<Button>`: this
        // navigates, so it must be an anchor for middle-click, copy-link and
        // prefetch. Same single source of truth for the styling either way —
        // the variant table in `components/ui/button.tsx` — which is the
        // point, since this string was one of the eight verbatim copies of
        // the hand-rolled primary CTA.
        <Link
          href={workspacePath(pool.id, "submit")}
          className={buttonVariants()}
        >
          <Plus size={15} weight="bold" />
          New job
        </Link>
      }
    />
  );
}
