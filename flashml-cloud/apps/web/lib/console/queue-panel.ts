/** How a REFRESHABLE queue resolves into the four panel states.
 *
 * WHY THIS IS NOT `resolvePanel`. `panel-state.ts` puts `loading` ahead of
 * `data` on purpose: a page that reads once should claim nothing while the
 * read is in flight. The admin queues are a different shape — they have a
 * refresh button, and `/admin/requests` already refetches after a
 * double-decide race. Running them through `resolvePanel` would blank a
 * queue an admin is working through into a skeleton on every refresh, which
 * loses their place in it.
 *
 * So this module encodes one extra rule: **a refresh with rows already on
 * screen stays `present`.** Only a read with nothing behind it shows a
 * skeleton.
 *
 * It lives in `lib/` with a test rather than as four chained ternaries
 * inside a 700-line page because it is a decision, and because the decision
 * it replaces was previously readable only by tracing the order of a
 * five-branch conditional. The order below is the whole content of this
 * file, and it is the order the page already had.
 */

import {
  UNREADABLE_WITHOUT_DETAIL,
  type PanelState,
} from "./panel-state";

/** The load states an admin queue tracks. `forbidden` is in here because the
 * page tracks it, not because a panel can render it — see below. */
export type QueueLoadState = "loading" | "ready" | "forbidden" | "error";

/**
 * `forbidden` is deliberately EXCLUDED from what this function accepts.
 *
 * A 403 on `/admin/requests` is not a failed read to apologise for — the
 * caller is signed in and simply is not an admin, and the page's own
 * doctrine is that this "renders a flat refusal for it rather than an error
 * dump". Routing it through `unreadable` would put a destructive warning
 * icon and "Could not read this." in front of somebody who is exactly as
 * authorised as the product intends. Excluding it in the TYPE means the page
 * has to narrow the state before it can ask this question, so the refusal
 * cannot be forgotten into an error banner.
 */
export type QueueReadState = Exclude<QueueLoadState, "forbidden">;

export function queuePanelState<T>(
  state: QueueReadState,
  error: string | null | undefined,
  rows: readonly T[]
): PanelState<readonly T[]> {
  // Failure first, and it discards the rows. A list rendered under an error
  // claims those rows are current, and after a failed read we do not know
  // that. Same ordering, same reason, as `panelRead`.
  if (state === "error") {
    const detail = (error ?? "").trim();
    return {
      kind: "unreadable",
      detail: detail.length > 0 ? detail : UNREADABLE_WITHOUT_DETAIL,
    };
  }

  // The rule this module exists for: rows already read stay on screen while
  // the next read runs. This is the branch that makes a refresh a refresh
  // rather than a reload.
  if (rows.length > 0) return { kind: "present", data: rows };

  // Nothing on screen and a read in flight — the only case that has earned a
  // skeleton, because it is the only one where "there is nothing here" has
  // not been established.
  if (state === "loading") return { kind: "loading" };

  // A read that finished and found nothing. Reachable only as `ready`, so an
  // in-flight or failed read can never arrive here — the distinction
  // `panel-state.ts` exists to protect.
  return { kind: "empty" };
}

/**
 * The count a tab trigger's badge may show, from the SAME panel this
 * module already resolved — never recomputed from `rows.length` directly,
 * because that would drift from `queuePanelState`'s "stays `present` during
 * a refresh" rule the moment one of the two call sites changed and the
 * other did not.
 *
 * `null` on `loading` (nothing established yet) and `unreadable` (a failed
 * read does not get to claim a count) — a badge is a claim about how many
 * requests are pending, and `queuePanelState` is the one place that already
 * knows when that claim is backed by a real read. `0` on `empty`, because a
 * read that succeeded and found nothing pending really does mean 0.
 */
export function queueBadgeCount(panel: PanelState<readonly unknown[]>): number | null {
  if (panel.kind === "present") return panel.data.length;
  if (panel.kind === "empty") return 0;
  return null;
}
