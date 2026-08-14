"use client";

import { HardDrives, SealCheck } from "@phosphor-icons/react";

import { Skeleton } from "@/components/ui/skeleton";
import type { DemoMachine } from "@/lib/demo";
import { cn } from "@/lib/utils";

/**
 * The fleet, at the top of the page: the "this is real hardware" proof.
 *
 * READS AS HARDWARE, NOT AS A TABLE OF ROWS. A four-across card grid with a
 * machine icon, a live dot and the specs set in mono is the same information
 * a table would carry, arranged so the first impression is "four boxes in a
 * rack" rather than "a query result". That impression is the entire job of
 * this strip — everything below it is about what the boxes DO, and none of it
 * means anything if a judge has not first accepted that the boxes exist.
 *
 * The specs are printed because they are the cheapest possible corroboration:
 * `2 vCPU · 8 GB` is a real instance shape, and a page that claims a network
 * without ever naming what is in it is a page claiming nothing.
 *
 * Borrows `MachineCard`'s composition rules rather than inventing a second
 * machine-card language — icon in its own cell, identity left, status right,
 * a hairline-separated footer so the card has structure. It is deliberately
 * NOT `MachineCard` itself: that component takes a `Machine` from the
 * authenticated API and links into `/machines/<id>`, which is behind the
 * sign-in this page's visitor does not have.
 */
export function FleetStrip({
  fleet,
  loading,
}: {
  fleet: DemoMachine[];
  loading: boolean;
}) {
  // Skeletons shaped like the four cards that are coming, not a spinner —
  // the console's standing rule (`components/ui/skeleton.tsx`): a skeleton
  // tells you what is arriving, a spinner only that something is.
  if (loading && fleet.length === 0) {
    return (
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        {[0, 1, 2, 3].map((i) => (
          <Skeleton key={i} className="h-[5.5rem]" />
        ))}
      </div>
    );
  }

  if (fleet.length === 0) {
    return (
      <div className="panel px-4 py-6 text-center">
        <p className="text-sm text-muted-foreground">
          No machines reported. The network is unreachable from here right now
          — nothing below can be run until it answers.
        </p>
      </div>
    );
  }

  const online = fleet.filter((m) => m.online).length;

  return (
    <div>
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        {fleet.map((machine) => (
          <div
            key={machine.name}
            className="rounded-md border border-border bg-surface p-3"
          >
            <div className="flex items-start gap-2.5">
              <HardDrives
                size={17}
                weight="regular"
                className="mt-px shrink-0 text-muted-foreground"
                aria-hidden="true"
              />
              <div className="min-w-0 flex-1">
                <p className="truncate font-mono text-[12px] font-medium text-foreground">
                  {machine.name}
                </p>
                <p className="mt-0.5 flex items-center gap-1 text-[10.5px] text-muted-foreground">
                  {machine.official && (
                    <SealCheck
                      aria-hidden="true"
                      weight="fill"
                      className="h-[11px] w-[11px] text-evergreen"
                    />
                  )}
                  {machine.region}
                </p>
              </div>
              <span
                className={cn("status-dot mt-1 shrink-0")}
                style={{
                  background: machine.online
                    ? "var(--node-green)"
                    : "var(--z-app-border-strong)",
                }}
                aria-hidden="true"
              />
            </div>

            <p className="mt-2.5 border-t border-border pt-2 font-mono text-[10.5px] text-muted-foreground tabular-nums">
              {/* An em dash, never a confident 0, for a spec the API did not
                  send. A machine listed as "0 vCPU" is worse than one whose
                  size we admit we were not told. */}
              {machine.cpus === null ? "—" : `${machine.cpus} vCPU`}
              {" · "}
              {machine.memory_gb === null ? "—" : `${machine.memory_gb} GB`}
              {" · "}
              <span className={machine.online ? "text-evergreen" : undefined}>
                {machine.online ? "online" : "offline"}
              </span>
            </p>
          </div>
        ))}
      </div>

      <p className="meta mt-2">
        {online} of {fleet.length} online
        {/* Every machine here is one Zolli Labs operates. Saying so is the
            difference between "a network" and "four machines we rented",
            and a judge is entitled to know which. */}
        {fleet.every((m) => m.official) && " · all operated by Zolli Labs"}
      </p>
    </div>
  );
}
