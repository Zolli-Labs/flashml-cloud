"use client";

import { Laptop } from "@phosphor-icons/react";

import { isMyGuest, type DemoMachine, type JoinedMachine } from "@/lib/demo";
import { cn } from "@/lib/utils";

/**
 * The guest fleet: machines judges plugged in themselves.
 *
 * A SEPARATE LIST FROM `FleetStrip`, not a flag inside it, because the two
 * are different claims. Ours is "this is the hardware we operate"; this one
 * is "this is your laptop, and it is on the network" — and the second is the
 * one a judge came to see. Merging them would bury it.
 *
 * VISUALLY THEIRS, NOT OURS: a laptop icon rather than the rack glyph, no
 * seal (a guest machine is not official and the API says so), and the row
 * the CURRENT visitor joined carries an orange edge and a "yours" tag. That
 * highlight is matched on the `prov…` handle the join response returned,
 * because the handle is the only identifier this public list carries — it
 * names nobody, and a visitor who has not joined in this browser matches
 * nothing, which is correct.
 */
export function GuestFleet({
  guests,
  joined,
}: {
  guests: DemoMachine[];
  joined: JoinedMachine | null;
}) {
  if (guests.length === 0) {
    return (
      <div className="rounded-md border border-dashed border-border px-4 py-5 text-center">
        <p className="text-[13px] text-muted-foreground">
          No guest machines yet. The steps above take about two minutes, and
          the machine you join shows up here.
        </p>
      </div>
    );
  }

  const online = guests.filter((m) => m.online).length;

  return (
    <div>
      {/* TWO COLUMNS, not the fleet strip's four. This list sits in half the
          page width and typically holds one to three machines, and at four
          across a `prov…` handle truncates to "prov-8f…" — which defeats the
          one thing the row has to do, which is let a judge recognise their
          own machine. Checked in `.preview/demo.html`. */}
      <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
        {guests.map((machine) => {
          const mine = isMyGuest(machine, joined);
          return (
            <div
              key={machine.name}
              className={cn(
                "rounded-md border p-3 transition-colors",
                mine ? "border-brand/60 bg-brand/[0.05]" : "border-border bg-surface"
              )}
            >
              <div className="flex items-start gap-2.5">
                <Laptop
                  size={17}
                  weight="regular"
                  className={cn(
                    "mt-px shrink-0",
                    mine ? "text-brand-foreground" : "text-muted-foreground"
                  )}
                  aria-hidden="true"
                />
                <div className="min-w-0 flex-1">
                  <p className="truncate font-mono text-[12px] font-medium text-foreground">
                    {machine.name}
                  </p>
                  <p className="mt-0.5 text-[10.5px] text-muted-foreground">
                    {mine ? "your machine" : "a visitor's machine"}
                  </p>
                </div>
                <span
                  className="status-dot mt-1 shrink-0"
                  style={{
                    background: machine.online
                      ? "var(--node-green)"
                      : "var(--z-app-border-strong)",
                  }}
                  aria-hidden="true"
                />
              </div>

              <p className="mt-2.5 border-t border-border pt-2 font-mono text-[10.5px] text-muted-foreground tabular-nums">
                {machine.cpus === null ? "—" : `${machine.cpus} vCPU`}
                {" · "}
                {machine.memory_gb === null ? "—" : `${machine.memory_gb} GB`}
                {" · "}
                <span className={machine.online ? "text-evergreen" : undefined}>
                  {machine.online ? "online" : "offline"}
                </span>
              </p>
            </div>
          );
        })}
      </div>

      <p className="meta mt-2">
        {online} of {guests.length} online · joined by visitors, anonymised to
        everyone but their owner
      </p>
    </div>
  );
}
