import type { PoolSummary } from "./cloud-api";

/** Remembers the workspace you were last in, so an entry point that carries
 * no id — `/overview`, a bookmark of the bare console, the post-sign-in
 * redirect — can resolve to somewhere real instead of guessing.
 *
 * A pool id, which already appears in the path of every workspace URL. No
 * secret moves here, which is why a plain readable cookie is the right
 * mechanism rather than anything server-signed. */
export const LAST_WORKSPACE_COOKIE = "flashml_last_workspace";

/** The five tabs of a workspace, in rail order. The single source of this
 * list: the shell renders it, and the layout validates a segment against
 * it. Adding a sixth means adding a route, and this array is where the
 * compiler will point you. */
export const WORKSPACE_TABS = [
  "overview",
  "jobs",
  "machines",
  "people",
  "settings",
] as const;

export type WorkspaceTab = (typeof WORKSPACE_TABS)[number];

/** `/w/<poolId>/<tab>`. Always build workspace URLs through this rather
 * than interpolating — a pool id is a uuid today, but the encode is what
 * keeps a link correct if that ever stops being true. */
export function workspacePath(poolId: string, tab: WorkspaceTab | "submit"): string {
  return `/w/${encodeURIComponent(poolId)}/${tab}`;
}

/** The pool id in a console pathname, or null if the path is not
 * workspace-scoped. Pure string work: this does not check that the id names
 * a pool you belong to, which is `resolveWorkspace`'s job. */
export function workspaceIdFromPath(pathname: string): string | null {
  const match = /^\/w\/([^/]+)(?:\/|$)/.exec(pathname);
  return match ? decodeURIComponent(match[1]) : null;
}

export type WorkspaceResolution =
  | { kind: "workspace"; poolId: string }
  | { kind: "onboarding" };

/** Which workspace a console request should land in.
 *
 * The order is the whole point:
 *
 * 1. The URL wins. A link pasted into Slack has to open the SENDER's
 *    workspace for the receiver, not whatever the receiver looked at last.
 *    This is the property that makes the console shareable at all.
 * 2. Then the cookie, for entry points carrying no id.
 * 3. Then the first workspace by name — stable and predictable, unlike
 *    "whatever the API listed first".
 * 4. Then onboarding.
 *
 * Both (1) and (2) are checked against live membership: a workspace you
 * were removed from must not resolve just because its id survives in your
 * cookie or your browser history. `pools` is the caller's own membership
 * list from `listPools()`, so presence in it IS the membership check.
 */
export function resolveWorkspace(
  pathname: string,
  pools: PoolSummary[],
  cookieValue: string | null
): WorkspaceResolution {
  const member = new Set(pools.map((p) => p.id));

  const fromPath = workspaceIdFromPath(pathname);
  if (fromPath !== null && member.has(fromPath)) {
    return { kind: "workspace", poolId: fromPath };
  }
  if (cookieValue !== null && member.has(cookieValue)) {
    return { kind: "workspace", poolId: cookieValue };
  }

  const first = [...pools].sort((a, b) => a.name.localeCompare(b.name))[0];
  if (first) return { kind: "workspace", poolId: first.id };

  return { kind: "onboarding" };
}
