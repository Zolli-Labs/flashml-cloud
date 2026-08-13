"use client";

import { PageShell } from "@/components/shell/PageShell";
import { StatePanel } from "@/components/shell/StatePanel";
import { ConnectPanel } from "@/components/pools/ConnectPanel";
import { PoolFleetTable } from "@/components/workspace/PoolFleetTable";
import { WorkspaceHeader } from "@/components/workspace/WorkspaceHeader";
import { YourMachines } from "@/components/workspace/YourMachines";
import { useWorkspace } from "@/components/workspace/WorkspaceProvider";
import { StatTile } from "@/components/ui/stat-tile";
import {
  isEmptyList,
  resolvePanelState,
  type PanelRead,
} from "@/lib/console/panel-state";
import { poolFleetCounts } from "@/lib/machine-scope";
import type { PoolMachine } from "@/lib/cloud-api";

// `WorkspaceGate` (in the layout) already handles loading / not-found /
// error, and guarantees `pool` is loaded by the time this renders. The
// `if (!pool) return null` below exists only to satisfy `Pool | null` at
// the type level — do not turn it back into a `state !== "ready"` skeleton.
export default function WorkspaceMachinesPage() {
  const { pool, machines, reload } = useWorkspace();
  if (!pool) return null;

  // `"read"` because the provider's single `Promise.all` already succeeded —
  // see the same note in `people/page.tsx`. `YourMachines` below is the one
  // panel on this page that does its OWN fetch, which is why it is the one
  // that carries all four states itself.
  const read: PanelRead<PoolMachine[]> = { status: "read", data: machines };
  const panel = resolvePanelState(read, isEmptyList);
  const fleet = poolFleetCounts(machines);

  return (
    <PageShell width="wide">
      <WorkspaceHeader />

      {/* The tab's own numbers — before this, every figure a viewer saw
          here came from the shared WorkspaceHeader above, and the Machines
          tab itself showed none of its own (density audit §3, gap 4). All
          four are `poolFleetCounts` of the same `machines` array the table
          below renders, so they cannot disagree with it. */}
      <div className="mt-6 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <StatTile label="Total" value={fleet.total} size="lg" />
        <StatTile
          label="Online"
          value={fleet.online}
          total={fleet.total}
          size="lg"
        />
        <StatTile label="Pending" value={fleet.pending} size="lg" />
        <StatTile label="Revoked" value={fleet.revoked} size="lg" />
      </div>

      <section className="mt-8">
        <h2 className="text-sm font-semibold">Serving this Workspace</h2>
        <p className="mt-1 text-xs text-muted-foreground">
          Every Machine your workspace members have opted in, not only yours.
        </p>
        <div className="mt-3">
          <StatePanel
            state={panel}
            label="the machines serving this Workspace"
            // The sentence `PoolFleetTable` used to carry itself, moved up
            // here unchanged. It belongs to whoever knows the read
            // succeeded, and a component handed an array never does.
            empty={{
              title:
                "No machines are serving this workspace yet. Select one of yours below, or connect a new one.",
            }}
            unreadable={{ retry: reload }}
          >
            {(rows) => <PoolFleetTable machines={rows} />}
          </StatePanel>
        </div>
      </section>

      <div className="mt-8">
        <YourMachines poolId={pool.id} poolName={pool.name} />
      </div>

      {/* The anchor `YourMachines`' empty state links to. Keep the id. */}
      <div id="connect-panel" className="mt-8">
        <h2 className="text-sm font-semibold">Connect a Machine</h2>
        <p className="mt-1 text-xs text-muted-foreground">
          No spare laptop? Point a Colab notebook or a rented pod at this
          Workspace instead.
        </p>
        <div className="mt-4">
          <ConnectPanel poolId={pool.id} />
        </div>
      </div>
    </PageShell>
  );
}
