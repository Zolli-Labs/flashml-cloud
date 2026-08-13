import { WorkspaceResolver } from "@/components/workspace/WorkspaceResolver";

/**
 * The rail's Deploy entry when no workspace is resolved yet: forwards into
 * the current workspace's submit page — the same waypoint pattern as
 * /overview and /jobs. The old /submit URL redirects here (next.config.ts).
 */
export default function DeployRedirect() {
  return <WorkspaceResolver tab="submit" />;
}
