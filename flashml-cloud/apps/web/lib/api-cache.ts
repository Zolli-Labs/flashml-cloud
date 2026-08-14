// Request-level dedupe + a very short read cache for `lib/cloud-api.ts`.
//
// WHY THIS EXISTS
//
// The console shell is a set of independent components that each fetch what
// they need on mount — `ConsoleShell` and `UserMenu` both want `GET /me`,
// `FleetPill` and `WorkspaceProvider` and `CommandPalette` all want
// `GET /jobs` and `GET /machines`, `WorkspaceSwitcher` and
// `WorkspaceResolver` both want `GET /pools`. That independence is the right
// shape — no component should have to know who else is on screen — but it
// meant one cold load of a workspace page issued `GET /me` three times,
// `GET /jobs` twice and `GET /machines` twice, each paying a full
// cross-continent round trip AND its own CORS preflight.
//
// So the fix belongs in the transport, not in the components: identical
// reads collapse into one request, and the components stay unaware of each
// other.
//
// TWO SEPARATE MECHANISMS, DELIBERATELY
//
// 1. **In-flight dedupe**, applied to every plain GET. Two callers asking
//    for the same path while a request is outstanding share one promise.
//    This cannot serve stale data by construction: the response they share
//    is a response that was in flight for both of them anyway.
//
// 2. **A `TTL_MS` read cache**, applied only to `CACHEABLE_PATHS`. This one
//    CAN serve a stale body, so the window is deliberately far shorter than
//    any poll interval in the console (`WorkspaceProvider` polls at 5s,
//    `FleetPill` at 15s) — a poll tick must always reach the network, or
//    "refresh" would silently mean "re-read what we already had".
//
// Mechanism 1 alone was enough at today's 2.4–6.2s server latencies, where
// every duplicate lands inside the same in-flight window. It stops working
// the moment the API gets fast, which is exactly when the duplicates would
// quietly come back. Mechanism 2 is what keeps the win after that.
//
// STALENESS IS BOUNDED BY MUTATION, NOT ONLY BY TIME
//
// `invalidate()` runs on every non-GET this client sends, and clears both
// maps. Submitting a job, cancelling one, revoking a machine, creating or
// renaming a workspace, accepting an invite — each drops the cached reads,
// so the next list is fetched fresh. The TTL is the bound on changes made
// somewhere OTHER than this tab (another session, a host agent enrolling,
// the scheduler moving a job on); polling is what surfaces those, and the
// TTL is short enough never to swallow a poll.

/** How long a cacheable read stays fresh.
 *
 * 1.5s. The number is chosen against the *shortest* poll interval in the
 * console (5s, `WorkspaceProvider`) with room to spare, not against a sense
 * of how fresh data ought to feel: at any TTL >= a poll interval, polling
 * would start reading its own cache and the console would stop updating.
 * Raising this is only safe if you check every `setInterval` that calls this
 * client first. */
export const TTL_MS = 1500;

/** The reads that may be answered from cache.
 *
 * An allowlist rather than "every GET", because the cost of a wrong entry
 * here is a user seeing state that contradicts what they just did. Each of
 * these is a list or summary that at least two mounted components ask for
 * independently, and each is re-read by a poll or invalidated by the
 * mutation that would change it.
 *
 * Deliberately NOT here, and why:
 *
 * - `/v1alpha1/jobs/{id}` and everything under it (events, tasks, rounds,
 *   plans, artifacts, verifications) — a job detail page IS a live view of
 *   a changing thing, polled tightly, and it is where a user watches for
 *   the effect of an action they just took.
 * - `/v1alpha1/pools/{id}` and `/v1alpha1/pools/{id}/machines` — fetched
 *   once per workspace by a single owner (`WorkspaceProvider`), so there is
 *   no duplicate to collapse; caching them would add staleness risk for no
 *   saved request. In-flight dedupe still covers them.
 *   `/v1alpha1/pools/{id}/invite` is excluded for the same reason plus a
 *   stronger one: it is read to decide whether a live invite exists.
 * - `/v1alpha1/preflight` and `/v1alpha1/jobs/preview-plans` — POSTs, and
 *   verdicts about a spec the user is still editing.
 * - `/v1alpha1/market/*` and `/v1alpha1/prices` — single-reader pages, no
 *   duplication to remove.
 * - `/v1alpha1/access-requests` (the admin queue) — an approval must be
 *   visibly gone from the queue on the next read, and the queue is small
 *   and rarely opened. */
export const CACHEABLE_PATHS: ReadonlySet<string> = new Set([
  "/v1alpha1/me",
  "/v1alpha1/me/storage",
  "/v1alpha1/pools",
  "/v1alpha1/machines",
  "/v1alpha1/jobs",
  "/v1alpha1/credits",
]);

interface Entry {
  at: number;
  value: unknown;
}

const inflight = new Map<string, Promise<unknown>>();
const fresh = new Map<string, Entry>();

/** Injectable only so the tests can advance time without sleeping. */
let now: () => number = () => Date.now();

/** Test seam. Pass nothing to restore the real clock. */
export function setClock(clock?: () => number): void {
  now = clock ?? (() => Date.now());
}

export function isCacheable(path: string): boolean {
  return CACHEABLE_PATHS.has(path);
}

/** Drop every cached read and detach every in-flight promise.
 *
 * Called by `cloud-api`'s `send()` on every non-GET. Detaching the in-flight
 * map matters as much as clearing the cache: a read issued AFTER a mutation
 * must not be handed a promise that was already in flight BEFORE it, which
 * would answer with the pre-mutation world. The detached request still
 * completes for whoever is already awaiting it — this only stops new callers
 * joining it. */
export function invalidate(): void {
  fresh.clear();
  inflight.clear();
}

/** Full reset, including the clock. For tests; `invalidate()` is what
 * production calls. */
export function resetApiCache(): void {
  invalidate();
  setClock();
}

/** Run `load`, or don't, and answer with the same shape either way.
 *
 * Order is fresh-cache, then in-flight, then network. Rejections are never
 * cached and never left in the in-flight map — a failed read must be
 * retryable immediately, and a cached error would turn one blip into
 * `TTL_MS` of a broken page. */
export function through<T>(path: string, load: () => Promise<T>): Promise<T> {
  const hit = fresh.get(path);
  if (hit !== undefined && now() - hit.at < TTL_MS) {
    return Promise.resolve(hit.value as T);
  }
  // Expired: drop it rather than leave it to be re-checked forever.
  if (hit !== undefined) fresh.delete(path);

  const pending = inflight.get(path);
  if (pending !== undefined) return pending as Promise<T>;

  const started: Promise<T> = load().then(
    (value) => {
      // `inflight.get(path) === started` — not an unconditional delete.
      // `invalidate()` may have run while this was outstanding, and a newer
      // request may already occupy the slot; neither this promise's entry
      // nor its result belongs anywhere once that has happened.
      if (inflight.get(path) === started) {
        inflight.delete(path);
        if (isCacheable(path)) fresh.set(path, { at: now(), value });
      }
      return value;
    },
    (err) => {
      if (inflight.get(path) === started) inflight.delete(path);
      throw err;
    }
  );
  inflight.set(path, started);
  return started;
}

/** Inspection seam for the tests — production code must not branch on
 * whether something is cached. */
export function cacheSize(): { fresh: number; inflight: number } {
  return { fresh: fresh.size, inflight: inflight.size };
}
