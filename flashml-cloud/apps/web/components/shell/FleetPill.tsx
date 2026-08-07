"use client";

import { useCallback, useEffect, useState } from "react";
import { listJobs, listMachines, NotAuthenticated } from "@/lib/cloud-api";
import { isActiveJob } from "@/lib/job-scope";
import { isMachineOnline } from "@/lib/machine-scope";

// Sits where RunPod puts the credit balance: persistent system state that is
// not navigation. It is the smallest, highest-value transparency affordance
// in the console, because it answers "is anything alive?" without making you
// go and look.
//
// Composed from the two endpoints that already exist. When /v1alpha1/fleet
// lands (spec 7.2) this becomes one call instead of two, and the shape it
// renders does not change.

const POLL_MS = 15_000;

type Fleet = { online: number; running: number } | null;

export function FleetPill() {
  const [fleet, setFleet] = useState<Fleet>(null);
  const [signedOut, setSignedOut] = useState(false);

  const load = useCallback(() => {
    Promise.all([listMachines(), listJobs()])
      .then(([machines, jobs]) => {
        // Both predicates are imported, not spelled out here. This pill sits
        // in the console header on every workspace page, inches from
        // `WorkspaceHeader`'s own online count — two definitions of "online"
        // on one screen is exactly the drift `lib/machine-scope.ts` exists
        // to stop. `m.status === "active"` was the wrong one: that is
        // ENROLMENT state, so a machine that enrolled months ago and has
        // been asleep since still counted as online.
        //
        // The SCOPE difference is real and stays: this pill is personal
        // (`listMachines`/`listJobs` are caller-scoped) while
        // `WorkspaceHeader` counts one workspace's fleet. Only the
        // definition is shared.
        setFleet({
          online: machines.filter(isMachineOnline).length,
          running: jobs.filter(isActiveJob).length,
        });
        setSignedOut(false);
      })
      .catch((err) => {
        // A 401 means signed out, which is not "zero machines". Rendering
        // "0 online" for a signed-out user would be a lie about their fleet.
        if (err instanceof NotAuthenticated) setSignedOut(true);
      });
  }, []);

  useEffect(() => {
    load();
    const t = setInterval(load, POLL_MS);
    return () => clearInterval(t);
  }, [load]);

  if (signedOut || !fleet) return null;

  const live = fleet.online > 0;

  return (
    <div className="hidden items-center gap-2 rounded-full border border-border bg-surface px-3 py-1.5 shadow-sm sm:inline-flex">
      <span
        className="status-dot"
        data-state={live && fleet.running > 0 ? "live" : undefined}
        style={{
          background: live ? "var(--node-green)" : "var(--muted-foreground)",
        }}
      />
      <span className="font-mono text-xs tabular-nums text-foreground">
        {fleet.online}
      </span>
      <span className="text-xs text-muted-foreground">Zollis online</span>
      <span aria-hidden className="text-xs text-muted-foreground/40">
        /
      </span>
      <span className="font-mono text-xs tabular-nums text-foreground">
        {fleet.running}
      </span>
      <span className="text-xs text-muted-foreground">jobs running</span>
    </div>
  );
}
