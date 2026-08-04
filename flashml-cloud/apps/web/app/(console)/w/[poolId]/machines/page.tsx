"use client";

import { ConnectPanel } from "@/components/pools/ConnectPanel";
import { PoolFleetTable } from "@/components/workspace/PoolFleetTable";
import { WorkspaceHeader } from "@/components/workspace/WorkspaceHeader";
import { YourMachines } from "@/components/workspace/YourMachines";
import { useWorkspace } from "@/components/workspace/WorkspaceProvider";

// `WorkspaceGate` (in the layout) already handles loading / not-found /
// error, and guarantees `pool` is loaded by the time this renders. The
// `if (!pool) return null` below exists only to satisfy `Pool | null` at
// the type level — do not turn it back into a `state !== "ready"` skeleton.
export default function WorkspaceMachinesPage() {
  const { pool, machines } = useWorkspace();
  if (!pool) return null;

  return (
    <div className="mx-auto max-w-6xl px-4 py-8 sm:px-6">
      <WorkspaceHeader />

      <section className="mt-8">
        <h2 className="text-sm font-semibold">Serving this workspace</h2>
        <p className="mt-1 text-xs text-muted-foreground">
          Every machine your teammates have opted in, not only yours.
        </p>
        <div className="mt-3">
          <PoolFleetTable machines={machines} />
        </div>
      </section>

      <div className="mt-8">
        <YourMachines poolId={pool.id} poolName={pool.name} />
      </div>

      {/* The anchor `YourMachines`' empty state links to. Keep the id. */}
      <div id="connect-panel" className="mt-8">
        <h2 className="text-sm font-semibold">Connect a machine</h2>
        <p className="mt-1 text-xs text-muted-foreground">
          No spare laptop? Point a Colab notebook or a rented pod at this
          workspace instead.
        </p>
        <div className="mt-4">
          <ConnectPanel poolId={pool.id} />
        </div>
      </div>
    </div>
  );
}
