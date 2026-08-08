"use client";

import { Badge } from "@/components/ui/badge";
import type { JobState } from "@/lib/cloud-api";

/**
 * A job's state, as a coloured pill.
 *
 * This lives here, and not in `app/jobs/page.tsx`, because Next.js App Router
 * allows a `page.tsx` to export ONLY a default component plus a fixed set of
 * route-config keys. A stray named export makes the route module invalid, and
 * the symptom is not a build error — it is a browser showing "This page
 * couldn't load" with no clue where to look.
 *
 * It cost a real debugging session: `/jobs` white-screened while `/machines`
 * degraded gracefully, which pointed at data handling rather than at the
 * module's shape. `tsc` had been reporting it all along, in a generated file
 * under `.next/dev/types/`, and it was dismissed as unrelated noise:
 *
 *   error TS2344: Property 'StateBadge' is incompatible with index signature
 *
 * `app/jobs/[jobId]/page.tsx` imported it from `../page`, so one bad export
 * broke both routes.
 */
const stateStyles: Record<JobState, string> = {
  PENDING: "text-muted-foreground border-muted",
  SUBMITTED: "text-brand-foreground border-brand/40",
  RUNNING: "text-brand-foreground border-brand/40",
  RECOVERING: "text-warning-foreground border-warning/50",
  SUCCEEDED: "text-evergreen border-evergreen/40",
  // Warning, not success: the run finished and did not finish whole.
  PARTIAL: "text-warning-foreground border-warning/50",
  FAILED: "text-destructive border-destructive/40",
  CANCELLED: "text-muted-foreground border-muted",
};

export function StateBadge({ state }: { state: JobState }) {
  return (
    <Badge
      variant="outline"
      className={`font-mono text-xs ${stateStyles[state] ?? "text-muted-foreground border-muted"}`}
    >
      {state}
    </Badge>
  );
}
