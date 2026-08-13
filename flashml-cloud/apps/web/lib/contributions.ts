/** Turning `GET /v1alpha1/me/contributions` into something the account page
 * can render. Same reasoning as every other module in this family — see
 * `lib/job-result.ts`'s header for why the logic lives here and the
 * component around it stays markup.
 *
 * FlashML's premise is that people contribute machines and get credited for
 * it, and until this route existed that credit was visible only inside one
 * job's own page. This module carries the one rule that matters once the
 * total is account-wide: these are COUNTERS, not a currency. Nothing here is
 * ever spent, redeemed, or drawn down — a machine only ever adds to
 * `accepted_tasks`, never subtracts from it — so every string this module
 * produces is a running tally, past tense, with no notion of a balance to
 * spend against. "41 tasks accepted" is honest; "41 credits available"
 * claims something this system does not do.
 *
 * `hostname` and `last_seen_at` are genuinely nullable per machine: an agent
 * that has never reported a hostname, or a machine that has never been
 * leased a task at all, is a real and different state from "we know and
 * it's blank". Both get their own explicit label below — never an empty
 * string standing in for "unknown", never a fabricated date standing in for
 * "never happened".
 *
 * Since machines became deletable there is a third state behind a null
 * hostname: a machine whose owner retired it, whose credit is deliberately
 * still counted here. `hostnameLabel` below is where those three are told
 * apart, and how.
 */

import type { ContributionMachine, MyContributions } from "./cloud-api";
import { relativeTime } from "./machine-status";

export interface ContributionMachineRow {
  machineId: string;
  /** The machine's own reported hostname, or an explicit label saying why
   * there is not one. */
  hostnameLabel: string;
  /** True only when `hostnameLabel` is the machine's own hostname. The two
   * substitutes below are sentences about a machine, not names of one, so
   * the panel must not set them in the mono face it uses for ids — a label
   * that looks like a hostname is indistinguishable from a host actually
   * called that. */
  hostnameKnown: boolean;
  acceptedTasks: number;
  /** `relativeTime` already renders a null `last_seen_at` as `"never"` —
   * reused rather than reimplemented so a machine's last-seen time reads
   * identically here and on `account/machines`, the other place this exact
   * field is shown. */
  lastSeenLabel: string;
}

export interface ContributionsSummary {
  acceptedTasks: number;
  jobsContributedTo: number;
  machines: ContributionMachineRow[];
}

const NO_HOSTNAME_LABEL = "No hostname reported";
const DELETED_LABEL = "Deleted machine";

/**
 * What to call a machine with no hostname — and there are now two reasons
 * for that, which are not the same fact.
 *
 * Deleting a machine tombstones its row and nulls every column that
 * described the device, `name` and `last_seen_at` in the same UPDATE, while
 * deliberately keeping its accepted-work credit: a contribution total that
 * FELL because somebody tidied their fleet would be indistinguishable from a
 * bug. So this list can now contain a machine that no longer exists, and
 * calling it "No hostname reported" describes an agent that never introduced
 * itself — which is a different, and still real, machine.
 *
 * BOTH device fields null is the signal, not either alone. A machine only
 * appears in this payload because it was credited for accepted work, and
 * work arrives over a heartbeat, so a credited machine has a `last_seen_at`
 * — unless something scrubbed it, and the delete is the only thing that
 * does. The residual false positive is a machine that contributed, reported
 * no hostname and had its last-seen cleared some other way; there is no such
 * path today, and the row still says something true of a machine rather than
 * nothing at all.
 *
 * The alternative — labelling every null hostname "Deleted machine" — reads
 * as certainty this payload does not carry, and would tell somebody a laptop
 * that is enrolled and running right now has been deleted.
 */
function hostnameLabel(machine: ContributionMachine): string {
  if (machine.hostname) return machine.hostname;
  return machine.last_seen_at === null ? DELETED_LABEL : NO_HOSTNAME_LABEL;
}

export function summariseContributions(
  payload: MyContributions
): ContributionsSummary {
  return {
    acceptedTasks: payload.accepted_tasks,
    jobsContributedTo: payload.jobs_contributed_to,
    machines: payload.machines.map((m) => ({
      machineId: m.machine_id,
      hostnameLabel: hostnameLabel(m),
      hostnameKnown: Boolean(m.hostname),
      acceptedTasks: m.accepted_tasks,
      lastSeenLabel: relativeTime(m.last_seen_at),
    })),
  };
}
