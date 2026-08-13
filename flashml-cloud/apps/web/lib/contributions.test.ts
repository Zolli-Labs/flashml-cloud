import { describe, expect, it } from "vitest";

import { summariseContributions } from "./contributions";
import type { MyContributions } from "./cloud-api";

describe("summariseContributions", () => {
  it("passes the account-wide totals through unchanged", () => {
    const payload: MyContributions = {
      accepted_tasks: 41,
      jobs_contributed_to: 6,
      machines: [],
    };
    const summary = summariseContributions(payload);
    expect(summary.acceptedTasks).toBe(41);
    expect(summary.jobsContributedTo).toBe(6);
  });

  it("renders a machine's real hostname and a recent last-seen time", () => {
    const payload: MyContributions = {
      accepted_tasks: 30,
      jobs_contributed_to: 2,
      machines: [
        {
          machine_id: "m-1",
          hostname: "gpu-box",
          accepted_tasks: 30,
          last_seen_at: new Date(Date.now() - 5_000).toISOString(),
        },
      ],
    };
    const summary = summariseContributions(payload);
    expect(summary.machines).toHaveLength(1);
    expect(summary.machines[0].machineId).toBe("m-1");
    expect(summary.machines[0].hostnameLabel).toBe("gpu-box");
    expect(summary.machines[0].acceptedTasks).toBe(30);
    expect(summary.machines[0].lastSeenLabel).toMatch(/ago|just now/);
  });

  it("gives a machine with no reported hostname its own explicit label, not an empty string", () => {
    // Still being seen, so this is an agent that never introduced itself —
    // not a machine that has been deleted.
    const payload: MyContributions = {
      accepted_tasks: 4,
      jobs_contributed_to: 1,
      machines: [
        {
          machine_id: "m-2",
          hostname: null,
          accepted_tasks: 4,
          last_seen_at: new Date(Date.now() - 5_000).toISOString(),
        },
      ],
    };
    const summary = summariseContributions(payload);
    expect(summary.machines[0].hostnameLabel).not.toBe("");
    expect(summary.machines[0].hostnameLabel.toLowerCase()).toContain("no hostname");
    expect(summary.machines[0].hostnameKnown).toBe(false);
  });

  it("names a deleted machine as deleted, and still counts its work", () => {
    // The delete route tombstones the row and nulls `name` and
    // `last_seen_at` together, keeping the credit. Both device fields gone
    // is the signal; calling this "No hostname reported" would describe an
    // agent that is still out there.
    const payload: MyContributions = {
      accepted_tasks: 12,
      jobs_contributed_to: 3,
      machines: [
        { machine_id: "m-4", hostname: null, accepted_tasks: 12, last_seen_at: null },
      ],
    };
    const summary = summariseContributions(payload);
    expect(summary.machines[0].hostnameLabel).toBe("Deleted machine");
    expect(summary.machines[0].hostnameKnown).toBe(false);
    expect(summary.machines[0].acceptedTasks).toBe(12);
    expect(summary.acceptedTasks).toBe(12);
  });

  it("marks a real hostname as known so the panel can set it in mono", () => {
    const payload: MyContributions = {
      accepted_tasks: 1,
      jobs_contributed_to: 1,
      machines: [
        { machine_id: "m-5", hostname: "gpu-box", accepted_tasks: 1, last_seen_at: null },
      ],
    };
    expect(summariseContributions(payload).machines[0].hostnameKnown).toBe(true);
  });

  it("gives a machine that has never been seen its own explicit label, not a fabricated date", () => {
    const payload: MyContributions = {
      accepted_tasks: 0,
      jobs_contributed_to: 0,
      machines: [
        { machine_id: "m-3", hostname: "idle-box", accepted_tasks: 0, last_seen_at: null },
      ],
    };
    const summary = summariseContributions(payload);
    expect(summary.machines[0].lastSeenLabel.toLowerCase()).toContain("never");
  });

  it("carries a genuine zero through as zero for a brand-new account", () => {
    // A count of 0 is a real, complete answer — "nothing accepted yet" —
    // and must render as the number 0, the same doctrine `lib/platform-metrics.ts`
    // follows for its own plain counts.
    const payload: MyContributions = {
      accepted_tasks: 0,
      jobs_contributed_to: 0,
      machines: [],
    };
    const summary = summariseContributions(payload);
    expect(summary.acceptedTasks).toBe(0);
    expect(summary.jobsContributedTo).toBe(0);
    expect(summary.machines).toEqual([]);
  });
});
