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
 */

import type { ContributionMachine, MyContributions } from "./cloud-api";
import { relativeTime } from "./machine-status";

export interface ContributionMachineRow {
  machineId: string;
  /** The machine's own reported hostname, or an explicit label saying it
   * has never reported one. */
  hostnameLabel: string;
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

function hostnameLabel(machine: ContributionMachine): string {
  return machine.hostname ?? NO_HOSTNAME_LABEL;
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
      acceptedTasks: m.accepted_tasks,
      lastSeenLabel: relativeTime(m.last_seen_at),
    })),
  };
}
