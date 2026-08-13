"use client";

import { useEffect } from "react";
import { usePathname, useRouter } from "next/navigation";
import { NotAuthenticated, listPools } from "@/lib/cloud-api";
import {
  LAST_WORKSPACE_COOKIE,
  resolveWorkspace,
  workspacePath,
  type WorkspaceTab,
} from "@/lib/workspace-scope";

/** Reads `name=value` out of `document.cookie` without a library — this is a
 * single plain, non-secret cookie (see `LAST_WORKSPACE_COOKIE`'s docstring),
 * not worth a dependency for. */
function readCookie(name: string): string | null {
  const match = document.cookie
    .split("; ")
    .find((row) => row.startsWith(`${name}=`));
  if (!match) return null;
  return decodeURIComponent(match.slice(name.length + 1));
}

/** The waypoint every URL that does not already name a workspace passes
 * through: the legacy `/overview`, `/jobs`, `/pools`, `/submit` routes, and
 * (via `/workspaces`'s own failure path) nowhere else. It fetches the
 * caller's pools, resolves which workspace that should mean, and replaces
 * itself with the answer.
 *
 * `router.replace`, never `push`: this component is never a place worth
 * returning to. Pushing it would mean Back from inside a workspace lands
 * back on this skeleton, which immediately forwards again — a dead hop in
 * history for no benefit. */
export function WorkspaceResolver({
  tab,
}: {
  // `"submit"` beside the tab union for the same reason `workspacePath`
  // accepts it: submit is a real workspace route that is deliberately not a
  // rail tab, and /deploy resolves straight into it.
  tab: WorkspaceTab | "submit";
}) {
  const router = useRouter();
  const pathname = usePathname();

  useEffect(() => {
    let cancelled = false;

    listPools()
      .then((pools) => {
        if (cancelled) return;
        const cookieValue = readCookie(LAST_WORKSPACE_COOKIE);
        const resolution = resolveWorkspace(pathname, pools, cookieValue);
        if (resolution.kind === "workspace") {
          router.replace(workspacePath(resolution.poolId, tab));
        } else {
          router.replace("/workspaces");
        }
      })
      .catch((err) => {
        if (cancelled) return;
        if (err instanceof NotAuthenticated) {
          router.replace("/sign-in");
          return;
        }
        // Any other failure means the pool list itself is unreachable —
        // there is no membership to resolve against, so the only useful
        // thing left to offer is the place to create or join one.
        router.replace("/workspaces");
      });

    return () => {
      cancelled = true;
    };
  }, [pathname, router, tab]);

  return (
    <div className="mx-auto max-w-6xl px-4 py-8 sm:px-6">
      <div className="skeleton h-32 rounded-lg" />
    </div>
  );
}
