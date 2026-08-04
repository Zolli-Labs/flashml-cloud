import { WorkspaceProvider } from "@/components/workspace/WorkspaceProvider";

export default async function WorkspaceLayout({
  children,
  params,
}: {
  children: React.ReactNode;
  params: Promise<{ poolId: string }>;
}) {
  const { poolId } = await params;
  return <WorkspaceProvider poolId={poolId}>{children}</WorkspaceProvider>;
}
