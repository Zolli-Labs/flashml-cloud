/** A one-way notification that the signed-in user's workspace LIST has
 * changed — renamed or created — for components that fetch it themselves
 * and have no React path back to whoever changed it.
 *
 * Those two are the only dispatchers. Joining does not use this: both join
 * paths in `pools/join` do a full `window.location` navigation, which
 * remounts the switcher and re-fetches anyway. Leaving does not exist —
 * there is no leave route in `cloud-api.ts` yet. Add to this list only when
 * something actually dispatches; a docstring ahead of its code is the
 * defect this project has already had to fix once.
 *
 * There is exactly one such component today: `WorkspaceSwitcher`. It lives
 * in `ConsoleShell`, which Next keeps mounted across client navigations, and
 * it fetches `listPools()` in a mount-only effect. `RenameWorkspace` sits
 * several routes below, inside `WorkspaceProvider`, and its `onRenamed`
 * reaches only that provider's `reload()`. So renaming in Settings updated
 * the page and left the rail showing the old name until a full reload — an
 * acceptance criterion of the workspace console, failing as built.
 *
 * Why a window event rather than the obvious alternatives:
 *
 * - A React context in `ConsoleShell` cannot reach the page. A Next layout
 *   receives its page as an opaque `children`, so there is no prop to thread
 *   a callback through; the shell would have to publish a context that the
 *   page consumes, which is the same indirection as this with more moving
 *   parts and an import cycle between the shell and the workspace tabs.
 * - A global store is a dependency and a piece of architecture for one
 *   boolean's worth of signal.
 * - Router-based invalidation (`router.refresh()`) is for server data; every
 *   one of these components fetches on the client.
 *
 * The event carries NO payload, deliberately. It says "your list is stale",
 * not "here is the new name" — so a listener always re-reads the API and
 * cannot be handed a shape it then has to merge. `ConsoleShell` already uses
 * `window.dispatchEvent` for the ⌘K affordance, so the pattern is not new
 * here.
 *
 * Both helpers are no-ops when `window` is undefined, so importing this from
 * a module that also renders on the server is safe.
 */
export const WORKSPACES_CHANGED_EVENT = "flashml:workspaces-changed";

/** Tell every listener that `listPools()` would now return something
 * different. Call after a rename/create/join has SUCCEEDED — never
 * optimistically, or the rail refetches and paints the unchanged name. */
export function notifyWorkspacesChanged(): void {
  if (typeof window === "undefined") return;
  window.dispatchEvent(new Event(WORKSPACES_CHANGED_EVENT));
}

/** Subscribe. Returns the unsubscribe function, shaped to be returned
 * straight out of a `useEffect`. */
export function onWorkspacesChanged(handler: () => void): () => void {
  if (typeof window === "undefined") return () => {};
  window.addEventListener(WORKSPACES_CHANGED_EVENT, handler);
  return () => window.removeEventListener(WORKSPACES_CHANGED_EVENT, handler);
}
