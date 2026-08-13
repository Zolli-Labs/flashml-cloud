/** The four states every console panel must be able to render, and the pure
 * resolution from one read into one of them.
 *
 * WHY THIS MODULE EXISTS. The house rule is written down in
 * `docs/superpowers/specs/2026-08-12-frontend-ux-elevation.md` §1.1:
 *
 *   > A failed read must never render as an empty result. Every panel needs
 *   > four distinct states: loading / present / empty / unreadable. "Could
 *   > not read this" and "there is nothing here" are different sentences.
 *
 * Today that rule is *remembered*, once per panel. Three modules next door
 * already got it right on their own — `lib/job-artifacts.ts`,
 * `lib/job-routing.ts`, `lib/task-checkpoints.ts` each hand-roll the same
 * three-variant `…Read` union and the same four-state summary, with the same
 * paragraph of reasoning re-derived in each file. Everything that did not
 * get that treatment renders `data ?? []` and tells a user whose network
 * blipped that they have no machines.
 *
 * This module is that shape, once, with a name — so `components/shell/
 * StatePanel.tsx` can make it a component contract instead of a rule. Nothing
 * here renders; `vitest.config.ts` collects only `**\/*.test.ts`, so a `.tsx`
 * component gets no coverage, and the decision has to live where a test can
 * reach it (the same split the three modules above document).
 *
 * THE DISTINCTION THIS MODULE EXISTS TO PROTECT. There is deliberately no
 * code path from `unreadable` to `empty`. Not through a blank detail string,
 * not through a null payload, not through a caller's own `isEmpty`
 * predicate — that predicate is only ever consulted on a read that
 * SUCCEEDED. If that ever becomes possible, the whole point of the module is
 * gone and the console starts telling people that their data does not exist
 * whenever the API is down.
 *
 * WHAT IS DELIBERATELY NOT MODELLED. "Not found" is a route-level answer, not
 * a panel-level one: a 404 is about the thing the URL names, and the console
 * replaces the page for it (`WorkspaceGate`, `notFound()`), rather than
 * putting an empty table inside a page about a workspace that does not
 * exist. Folding it in here as a fifth kind would invite exactly the
 * substitution the module exists to prevent, one level up.
 */

/** One attempt at reading something, classified rather than thrown — the
 * shape `ArtifactsRead`, `RoutingRead` and `TaskCheckpointRead` already use,
 * kept identical so those three can adopt this without a translation layer.
 *
 * `unreadable` carries the API's own words and is deliberately NOT split by
 * status code. A 404 from an API too old to have the route, a 404 for
 * somebody else's resource, a 502 and a dead network all leave the console
 * equally unable to say what is there; pretending to tell them apart would
 * mean guessing. */
export type PanelRead<T> =
  | { status: "loading" }
  | { status: "read"; data: T }
  | { status: "unreadable"; detail: string };

/** What the panel is allowed to say. Exactly four, and the data rides on
 * `present` alone: a state carrying rows it must not show is how stale rows
 * end up rendered under an error banner. */
export type PanelState<T> =
  | { kind: "loading" }
  | { kind: "present"; data: T }
  | { kind: "empty" }
  | { kind: "unreadable"; detail: string };

export type PanelStateKind = PanelState<unknown>["kind"];

/** What each state CLAIMS. Not UI copy — the copy is the caller's, and
 * `unreadable`'s sentence is the API's. This is the contract in one line
 * each, so a reviewer can check a panel against it without reading this
 * file.
 *
 * `Record<PanelStateKind, …>` is load-bearing: a fifth variant added to
 * `PanelState` above stops this object type-checking, which is the intended
 * way to find out that a new state needs a meaning, a renderer in
 * `StatePanel`, and a decision at every call site. */
export const PANEL_STATE_MEANING: Record<PanelStateKind, string> = {
  loading: "Still reading. Nothing is claimed yet.",
  present: "The read succeeded and returned something to show.",
  empty: "The read succeeded, and there is genuinely nothing here.",
  unreadable: "The read failed. We do not know what is here.",
};

/** The vocabulary, in the order a panel moves through it. Asserted against
 * `PANEL_STATE_MEANING` by the tests so the two cannot drift. */
export const PANEL_STATE_KINDS: readonly PanelStateKind[] = [
  "loading",
  "present",
  "empty",
  "unreadable",
];

/** Used when a failure arrives with nothing to say. An error panel with no
 * sentence in it is the same failure as an empty state: the screen looks
 * settled and tells the reader nothing. Better to admit the gap. */
export const UNREADABLE_WITHOUT_DETAIL =
  "Could not read this. The reason wasn't reported.";

/** Used when a read is neither in flight nor failed and still produced
 * nothing at all.
 *
 * This is the honesty guard that catches the realistic bug rather than the
 * theoretical one. `{ loading: false, error: null, data: null }` is what a
 * `useState<T[] | null>(null)` page looks like when its fetch threw and the
 * `catch` forgot to set the error — and treating it as `empty` is precisely
 * the lie §1.1 forbids, arrived at by accident instead of by decision. We do
 * not know that there is nothing here; we know that we have nothing. */
export const READ_WITHOUT_RESULT =
  "Could not read this. The request finished without returning a result.";

function detailOrFallback(detail: string): string {
  const trimmed = detail.trim();
  return trimmed.length > 0 ? trimmed : UNREADABLE_WITHOUT_DETAIL;
}

/**
 * Turn one read into the state a panel renders.
 *
 * `isEmpty` is required and has no default, deliberately. A default would
 * have to guess what "nothing" means for `T`, and the only interesting cases
 * are the ones where the guess is wrong: a listing object whose array is
 * empty but whose `storage` field is the point, a summary whose zero rows
 * still carry a total. The caller knows; this module does not.
 *
 * `isEmpty` is consulted ONLY on `status: "read"`. There is no arrangement of
 * inputs — a throwing predicate aside — under which a failed or in-flight
 * read comes back `empty`.
 */
export function resolvePanelState<T>(
  read: PanelRead<T>,
  isEmpty: (data: T) => boolean
): PanelState<T> {
  if (read.status === "loading") return { kind: "loading" };

  if (read.status === "unreadable") {
    return { kind: "unreadable", detail: detailOrFallback(read.detail) };
  }

  if (isEmpty(read.data)) return { kind: "empty" };
  return { kind: "present", data: read.data };
}

/** A collection the API returned with nothing in it.
 *
 * Takes an array and only an array, so that `null` cannot be passed in and
 * silently answered "empty". Per the house rule a null is *not observed*,
 * which is a different claim from *observed, and there was nothing* — a
 * panel whose successful read can still yield null has to say what it means
 * by that in its own predicate rather than letting a shared helper decide. */
export function isEmptyList(items: readonly unknown[]): boolean {
  return items.length === 0;
}

/** The three flags a console page already keeps for a fetch. Named as an
 * adapter rather than as the primary type: a page that can classify its own
 * outcome should build a `PanelRead` directly and skip the guessing below. */
export interface PanelInputs<T> {
  /** A read is in flight. */
  loading: boolean;
  /** The failure, in the API's words. `null` — not `""` — means no failure;
   * every error state in this codebase is already `useState<string | null>`.
   * A non-null blank string is still treated as a failure, and gets
   * `UNREADABLE_WITHOUT_DETAIL`, because dropping it would turn a reported
   * failure into a successful read of nothing. */
  error: string | null | undefined;
  /** What the read returned. Required, not optional, so that a caller has to
   * decide what to pass rather than omitting it into `undefined`. */
  data: T | null | undefined;
}

/**
 * Classify the ordinary `{ loading, error, data }` triple.
 *
 * THE ORDER IS THE DECISION, and it is: error, then loading, then a missing
 * result, then the data.
 *
 * `error` before `loading` because a `loading` flag that never clears hides
 * an error forever, and that is not hypothetical — it is the bug
 * `components/workspace/WorkspaceGate.tsx` was written to fix, in its own
 * words: a guard that only checks "is it ready" "renders a loading skeleton
 * for either FOREVER — there is no third branch to escape to." The opposite
 * ordering costs one frame of a stale message during a retry that forgot to
 * clear its error. Those are not comparable.
 *
 * `error` before `data` because a screen showing rows claims those rows are
 * current. A panel that wants to keep showing the last good read under a
 * warning is a different, louder component than this one, and it should have
 * to be asked for by name.
 */
export function panelRead<T>(inputs: PanelInputs<T>): PanelRead<T> {
  if (inputs.error != null) {
    return { status: "unreadable", detail: detailOrFallback(inputs.error) };
  }
  if (inputs.loading) return { status: "loading" };
  if (inputs.data == null) {
    return { status: "unreadable", detail: READ_WITHOUT_RESULT };
  }
  return { status: "read", data: inputs.data };
}

/** `panelRead` then `resolvePanelState`. The call most pages want. */
export function resolvePanel<T>(
  inputs: PanelInputs<T>,
  isEmpty: (data: T) => boolean
): PanelState<T> {
  return resolvePanelState(panelRead(inputs), isEmpty);
}
