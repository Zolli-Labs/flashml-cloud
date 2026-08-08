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
    const payload: MyContributions = {
      accepted_tasks: 0,
      jobs_contributed_to: 0,
      machines: [
        { machine_id: "m-2", hostname: null, accepted_tasks: 0, last_seen_at: null },
      ],
    };
    const summary = summariseContributions(payload);
    expect(summary.machines[0].hostnameLabel).not.toBe("");
    expect(summary.machines[0].hostnameLabel.toLowerCase()).toContain("no hostname");
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
