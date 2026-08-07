import { WorkspaceProvider } from "@/components/workspace/WorkspaceProvider";
import { WorkspaceGate } from "@/components/workspace/WorkspaceGate";

export default async function WorkspaceLayout({
  children,
  params,
}: {
  children: React.ReactNode;
  params: Promise<{ poolId: string }>;
}) {
  const { poolId } = await params;
  // Keyed on `poolId`: this is what makes a workspace switch a remount
  // rather than a prop change. React discards the old WorkspaceProvider
  // instance and mounts a fresh one, so the new instance's `useState` calls
  // start at their initial values — there is no render, not even one frame,
  // in which the previous workspace's pool/members/jobs/machines can appear
  // under this workspace's URL labelled "ready". A `useEffect`-based reset
  // cannot give that guarantee: it runs after React has already committed
  // (and, absent a layout effect, painted) a frame with the stale data still
  // in state. Do not remove this key as "redundant" — it is the mechanism,
  // not decoration.
  return (
    <WorkspaceProvider key={poolId} poolId={poolId}>
      <WorkspaceGate>{children}</WorkspaceGate>
    </WorkspaceProvider>
  );
}
