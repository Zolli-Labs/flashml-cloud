"use client";

import { HardDrives, Lightning } from "@phosphor-icons/react";

import { Badge } from "@/components/ui/badge";
import { coordinatorChipLabel } from "@/lib/job-coordinator";

/**
 * Which control plane coordinated a job — Render or Alibaba Function
 * Compute — on the job row and in the job detail header.
 *
 * This is the artifact that makes the render-vs-FC comparison legible, so
 * it must be readable at a glance and never by colour alone: the two states
 * carry different TEXT ("Render" / "Function Compute" — never "render" /
 * "fc", see `coordinatorChipLabel`) and different ICON SHAPES (a stack for
 * the always-on private service, a bolt for the serverless one). A reader
 * who cannot distinguish tints — or who is skimming a list too fast to read
 * either word — still tells the two apart by shape alone.
 *
 * Deliberately the neutral `outline` badge treatment `StateBadge` uses for
 * its non-alarming states, not a new palette: this chip states an identity
 * ("which deployment ran this"), not a verdict on the job, and inventing a
 * third colour family for a fact that is neither good nor bad news is how a
 * job list stops reading as one system.
 */
export function CoordinatorChip({
  coordinator,
}: {
  coordinator: string | null | undefined;
}) {
  const label = coordinatorChipLabel(coordinator);
  const isFc = (coordinator ?? "render") === "fc";
  const Icon = isFc ? Lightning : HardDrives;

  return (
    <Badge
      variant="outline"
      className="font-mono text-xs text-muted-foreground border-border"
    >
      <Icon
        data-icon="inline-start"
        weight={isFc ? "fill" : "regular"}
        className="h-3 w-3"
      />
      {label}
    </Badge>
  );
}
