"use client";

import { useCallback, useEffect, useState } from "react";
import { listJobs, listMachines, NotAuthenticated } from "@/lib/cloud-api";

// Sits where RunPod puts the credit balance: persistent system state that is
// not navigation. It is the smallest, highest-value transparency affordance
// in the console, because it answers "is anything alive?" without making you
// go and look.
//
// Composed from the two endpoints that already exist. When /v1alpha1/fleet
// lands (spec 7.2) this becomes one call instead of two, and the shape it
// renders does not change.

const TERMINAL = new Set(["SUCCEEDED", "FAILED", "CANCELLED"]);
const POLL_MS = 15_000;

type Fleet = { online: number; running: number } | null;

export function FleetPill() {
  const [fleet, setFleet] = useState<Fleet>(null);
  const [signedOut, setSignedOut] = useState(false);

  const load = useCallback(() => {
    Promise.all([listMachines(), listJobs()])
      .then(([machines, jobs]) => {
        setFleet({
          online: machines.filter((m) => m.status === "active").length,
          running: jobs.filter((j) => !TERMINAL.has(j.state)).length,
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
    <div className="hidden items-center gap-2 rounded-md border border-border bg-surface px-2.5 py-1.5 sm:inline-flex">
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
      <span className="text-xs text-muted-foreground">online</span>
      <span aria-hidden className="text-xs text-muted-foreground/40">
        /
      </span>
      <span className="font-mono text-xs tabular-nums text-foreground">
        {fleet.running}
      </span>
      <span className="text-xs text-muted-foreground">running</span>
    </div>
  );
}
