import { describe, expect, it } from "vitest";

import { NOT_MEASURED, summariseMetrics } from "./platform-metrics";
import type { PlatformMetrics } from "./cloud-api";

function metrics(over: Partial<PlatformMetrics>): PlatformMetrics {
  return {
    window_days: 30,
    jobs_total: 0,
    jobs_succeeded: 0,
    jobs_partial: 0,
    jobs_failed: 0,
    tasks_attempted: 0,
    tasks_resolved: 0,
    tasks_accepted: 0,
    goodput_ratio: null,
    lost_task_seconds: null,
    mttr_seconds: null,
    mttd_seconds: null,
    machines_contributing: 0,
    ...over,
  };
}

describe("summariseMetrics", () => {
  it("carries plain counts through as real numbers, zero included", () => {
    // A window with nothing in it is a true zero, not "unmeasured" — this
    // is the opposite case from the nullable reliability fields below, and
    // must stay a plain number the UI renders without qualification.
    const summary = summariseMetrics(metrics({}));
    expect(summary.windowDays).toBe(30);
    expect(summary.jobCounts).toEqual([
      { label: "Total", value: 0 },
      { label: "Succeeded", value: 0 },
      { label: "Partial", value: 0 },
      { label: "Failed", value: 0 },
      { label: "In flight", value: 0 },
    ]);
    expect(summary.taskCounts).toEqual([
      { label: "Attempted", value: 0 },
      { label: "Resolved", value: 0 },
      { label: "Accepted", value: 0 },
    ]);
    expect(summary.machinesContributing).toBe(0);
  });

  it("reports the real counts when a window has activity", () => {
    const summary = summariseMetrics(
      metrics({
        jobs_total: 12,
        jobs_succeeded: 9,
        jobs_partial: 1,
        jobs_failed: 2,
        tasks_attempted: 140,
        tasks_resolved: 136,
        tasks_accepted: 128,
        machines_contributing: 5,
      })
    );
    expect(summary.jobCounts).toEqual([
      { label: "Total", value: 12 },
      { label: "Succeeded", value: 9 },
      { label: "Partial", value: 1 },
      { label: "Failed", value: 2 },
      { label: "In flight", value: 0 },
    ]);
    expect(summary.taskCounts).toEqual([
      { label: "Attempted", value: 140 },
      // The goodput denominator, shown next to the pair a reader would
      // otherwise divide themselves. 140 attempted with 136 resolved means
      // four are still in flight and in neither reliability number.
      { label: "Resolved", value: 136 },
      { label: "Accepted", value: 128 },
    ]);
    expect(summary.machinesContributing).toBe(5);
  });

  it("marks every derived stat unmeasured when the API sends null, never a fabricated 0", () => {
    const summary = summariseMetrics(metrics({}));
    expect(summary.goodput.measured).toBe(false);
    expect(summary.lostTaskTime.measured).toBe(false);
    expect(summary.mttr.measured).toBe(false);
    expect(summary.mttd.measured).toBe(false);
    // The display text says plainly why it is absent — never a bare "—"
    // that could be mistaken for "no data" without an explanation.
    //
    // Goodput and lost task time share the more specific reason: this
    // fixture has nothing resolved, and both fields are null for exactly
    // that arithmetic. MTTR and MTTD are null for their own reasons and get
    // the generic sentence.
    expect(summary.goodput.display).toBe(summary.lostTaskTime.display);
    expect(summary.lostTaskTime.display.toLowerCase()).toContain("resolved");
    expect(summary.mttr.display).toBe(NOT_MEASURED);
    expect(summary.mttd.display).toBe(NOT_MEASURED);
  });

  it("explains goodput's null as 'nothing resolved' when tasks_resolved is 0, not as a generic unmeasured", () => {
    // The API's own contract: goodput_ratio is null specifically when
    // RESOLVED is 0 — there is nothing to divide, which is a different
    // (and more informative) reason than "not instrumented yet".
    const summary = summariseMetrics(
      metrics({ tasks_resolved: 0, goodput_ratio: null })
    );
    expect(summary.goodput.measured).toBe(false);
    expect(summary.goodput.display).not.toBe(NOT_MEASURED);
    expect(summary.goodput.display.toLowerCase()).toContain("no");
  });

  it("keys the reason off tasks_resolved, not tasks_attempted", () => {
    // The live shape of the bug this fixes. Migration 0015 split the two
    // counts: `tasks_attempted` is every lease claimed, `tasks_resolved` is
    // what goodput divides by. An account whose only attempts are still in
    // flight has attempted > 0 and resolved == 0 — and used to read "Not
    // measured yet", blaming our instrumentation for arithmetic that has no
    // denominator.
    const inFlightOnly = summariseMetrics(
      metrics({ tasks_attempted: 12, tasks_resolved: 0, goodput_ratio: null })
    );
    expect(inFlightOnly.goodput.measured).toBe(false);
    expect(inFlightOnly.goodput.display).not.toBe(NOT_MEASURED);
    // And it must not claim there were no attempts either — there were 12.
    expect(inFlightOnly.goodput.display.toLowerCase()).toContain("resolved");
  });

  it("gives lost task time the same reason, because it has the same null guard", () => {
    // The sibling copy with the identical bug. `lost_task_seconds` is null
    // under exactly the same condition as `goodput_ratio` (`resolved <= 0`,
    // and for the same reason: 0.0 is the flattering claim "no work was
    // wasted"). It used to say "Not measured yet" for all of them.
    const summary = summariseMetrics(
      metrics({
        tasks_attempted: 12,
        tasks_resolved: 0,
        lost_task_seconds: null,
      })
    );
    expect(summary.lostTaskTime.measured).toBe(false);
    expect(summary.lostTaskTime.display).toBe(summary.goodput.display);
    expect(summary.lostTaskTime.display).not.toBe(NOT_MEASURED);
  });

  it("still calls goodput unmeasured when work resolved but the ratio is null anyway", () => {
    const summary = summariseMetrics(
      metrics({ tasks_attempted: 140, tasks_resolved: 140, goodput_ratio: null })
    );
    expect(summary.goodput.measured).toBe(false);
    expect(summary.goodput.display).toBe(NOT_MEASURED);
  });

  it("still calls lost task time unmeasured once something has resolved", () => {
    const summary = summariseMetrics(
      metrics({ tasks_resolved: 140, lost_task_seconds: null })
    );
    expect(summary.lostTaskTime.measured).toBe(false);
    expect(summary.lostTaskTime.display).toBe(NOT_MEASURED);
  });

  it("formats a measured goodput ratio as a percentage, not a fraction", () => {
    const summary = summariseMetrics(
      metrics({ tasks_attempted: 140, goodput_ratio: 0.914 })
    );
    expect(summary.goodput.measured).toBe(true);
    expect(summary.goodput.display).toBe("91.4%");
  });

  it("renders a whole-number ratio without a trailing .0", () => {
    const summary = summariseMetrics(
      metrics({ tasks_attempted: 10, goodput_ratio: 1 })
    );
    expect(summary.goodput.display).toBe("100%");
  });

  it("renders a real zero goodput as 0%, distinct from unmeasured", () => {
    // 0 is a legitimate, measured answer (every attempt this window was
    // rejected) and must read differently from "not measured yet".
    const summary = summariseMetrics(
      metrics({ tasks_attempted: 10, goodput_ratio: 0 })
    );
    expect(summary.goodput.measured).toBe(true);
    expect(summary.goodput.display).toBe("0%");
  });

  it("formats measured durations under a minute in whole seconds", () => {
    const summary = summariseMetrics(metrics({ mttd_seconds: 8 }));
    expect(summary.mttd.measured).toBe(true);
    expect(summary.mttd.display).toBe("8s");
  });

  it("formats measured durations in minutes and seconds", () => {
    const summary = summariseMetrics(metrics({ mttr_seconds: 135 }));
    expect(summary.mttr.display).toBe("2m 15s");
  });

  it("drops a zero seconds remainder instead of printing '0s'", () => {
    const summary = summariseMetrics(metrics({ mttr_seconds: 120 }));
    expect(summary.mttr.display).toBe("2m");
  });

  it("formats measured durations past an hour in hours and minutes", () => {
    const summary = summariseMetrics(metrics({ lost_task_seconds: 3661 }));
    expect(summary.lostTaskTime.display).toBe("1h 1m");
  });

  it("renders a real zero lost-task-time as 0s, distinct from unmeasured", () => {
    const summary = summariseMetrics(metrics({ lost_task_seconds: 0 }));
    expect(summary.lostTaskTime.measured).toBe(true);
    expect(summary.lostTaskTime.display).toBe("0s");
  });
});

describe("job outcome counts", () => {
  it("accounts for every job, so the outcomes sum to the total", () => {
    // Total >= succeeded + partial + failed, always: the remainder is jobs
    // still running plus jobs that finished while nobody had the console
    // open (the API only records a terminal state it has observed). Showing
    // four tiles that visibly do not add up invites the reader to conclude
    // the page is broken — which is the one conclusion this page cannot
    // afford, since its whole purpose is to be believed.
    const summary = summariseMetrics({
      window_days: 30,
      jobs_total: 10,
      jobs_succeeded: 5,
      jobs_partial: 1,
      jobs_failed: 2,
      tasks_attempted: 0,
      tasks_resolved: 0,
      tasks_accepted: 0,
      goodput_ratio: null,
      lost_task_seconds: null,
      mttr_seconds: null,
      mttd_seconds: null,
      machines_contributing: 0,
    });

    const inFlight = summary.jobCounts.find((c) => c.label === "In flight");
    expect(inFlight?.value).toBe(2);

    const outcomes = summary.jobCounts
      .filter((c) => c.label !== "Total")
      .reduce((n, c) => n + c.value, 0);
    expect(outcomes).toBe(10);
  });

  it("never shows a negative remainder", () => {
    // Counts come from separate queries and could in principle disagree.
    // A tile reading "-3 in flight" is a bug report, not information.
    const summary = summariseMetrics({
      window_days: 30,
      jobs_total: 1,
      jobs_succeeded: 5,
      jobs_partial: 0,
      jobs_failed: 0,
      tasks_attempted: 0,
      tasks_resolved: 0,
      tasks_accepted: 0,
      goodput_ratio: null,
      lost_task_seconds: null,
      mttr_seconds: null,
      mttd_seconds: null,
      machines_contributing: 0,
    });
    expect(summary.jobCounts.find((c) => c.label === "In flight")?.value).toBe(0);
  });
});
