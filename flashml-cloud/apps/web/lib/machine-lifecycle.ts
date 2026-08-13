/** The lifecycle half of "My machines": which rows are still the fleet,
 * which are only a record that a machine used to be, and what each of those
 * two piles is allowed to say.
 *
 * WHY THIS EXISTS. A revoked machine rendered the same full-detail row an
 * enrolled one does, for ever — hostname, platform, capability badge,
 * workspace chips, heartbeat — so a fleet of three working machines read as
 * eleven rows, eight of them dead. The fix is a split, and a split is a
 * decision about what "enrolled" means. `vitest.config.ts` collects
 * `**\/*.test.ts` only, so a decision made inside a `.tsx` gets no coverage;
 * it lives here, and the page is markup.
 *
 * `machine-scope.ts` next door answers "is this one machine online" and is
 * reused rather than re-derived, so the header count and the dots in the
 * table cannot drift apart.
 */

import { isMachineOnline } from "./machine-scope";
import { relativeTime } from "./machine-status";

/** A machine as this module needs to see it. Structural rather than
 * `Machine` from `cloud-api`, matching `machine-scope.ts`: every field here
 * is one this module actually reads, so a test does not have to invent
 * capability flags and pool chips to ask a question about status. */
export interface LifecycleMachine {
  status: string;
  last_seen_at: string | null;
}

/** The list, cut the way the page renders it.
 *
 * `online` is a count of ENROLLED machines only and is computed here rather
 * than by the caller — the header says "Online now" directly above
 * "Enrolled", and two numbers that disagree about which machines they are
 * counting is the bug this whole split exists to remove. */
export interface FleetSplit<M> {
  enrolled: M[];
  revoked: M[];
  online: number;
}

/**
 * Enrolled first, revoked second, input order preserved inside each.
 *
 * A `deleted` tombstone falls out of BOTH piles rather than into either.
 * `list_machines_for_owner` stops returning a deleted row the moment the
 * route tombstones it, so this should never see one — but a console build
 * newer than the API it is talking to is the ordinary state of a deploy, and
 * of the two ways to be wrong about a row that says `deleted`, counting it
 * as part of somebody's fleet is the one that misreports capacity. Falling
 * out is silent and correct; `status !== "revoked"` as the definition of
 * enrolled would not be.
 */
export function splitFleet<M extends LifecycleMachine>(
  machines: M[]
): FleetSplit<M> {
  const enrolled = machines.filter(
    (m) => m.status !== "revoked" && m.status !== "deleted"
  );
  return {
    enrolled,
    revoked: machines.filter((m) => m.status === "revoked"),
    online: enrolled.filter(isMachineOnline).length,
  };
}

/** What to call a machine on screen: its own name, or the agent's node id
 * when it never reported one. An empty string is treated as absent — a row
 * whose only label is "" is a row nobody can tell apart from the next one. */
export function machineLabel(machine: {
  name: string | null;
  node_id: string;
}): string {
  return machine.name || machine.node_id;
}

/** "revoked 3d ago", or plain "revoked" when the moment was not recorded.
 *
 * `relativeTime(null)` is `"never"`, which composed naively reads "revoked
 * never" — a sentence that says the opposite of the row it is on. Every
 * machine revoked through this console has a `revoked_at`; a row without one
 * is old data, and the honest thing to say about it is the part we know. */
export function revokedLabel(machine: { revoked_at: string | null }): string {
  return machine.revoked_at ? `revoked ${relativeTime(machine.revoked_at)}` : "revoked";
}

/** What one Delete attempt turned out to be.
 *
 * `already-gone` is a 404 and deliberately not a failure: the route answers
 * 404 for an unknown id, someone else's machine and a second delete alike,
 * and on a page that only ever offers Delete on a row it just read, the
 * overwhelmingly likely cause is that the row is stale. The list is what was
 * wrong, not the request — so it gets refetched exactly like a success, and
 * the reader gets told once, quietly. */
export type DeleteOutcome =
  | { kind: "deleted" }
  | { kind: "already-gone" }
  | { kind: "failed"; detail: string };

/**
 * The one line the revoked section shows after a Delete, or null when there
 * is nothing to say.
 *
 * Null for a success on purpose: the row leaves the list, which is the whole
 * of the feedback the reader asked for. A notice that survives the thing it
 * describes is a notice about nothing.
 *
 * A failure carries the API's own `detail` verbatim rather than a paraphrase
 * — the same doctrine `StatePanel` follows for an unreadable panel. The one
 * detail that matters most is the 409's ("still enrolled — revoke it
 * first"), which this UI should never provoke because Delete is only offered
 * on a revoked row; if it ever appears, it is evidence of a real
 * disagreement between the console's list and the server's, and paraphrasing
 * it away would hide exactly that.
 */
export function deleteNotice(
  outcome: DeleteOutcome,
  label: string
): string | null {
  switch (outcome.kind) {
    case "deleted":
      return null;
    case "already-gone":
      return `${label} was already deleted.`;
    case "failed":
      return `Couldn't delete ${label} — ${outcome.detail}`;
  }
}
