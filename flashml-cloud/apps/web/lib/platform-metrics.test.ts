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
    ]);
    expect(summary.taskCounts).toEqual([
      { label: "Attempted", value: 0 },
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
        tasks_accepted: 128,
        machines_contributing: 5,
      })
    );
    expect(summary.jobCounts).toEqual([
      { label: "Total", value: 12 },
      { label: "Succeeded", value: 9 },
      { label: "Partial", value: 1 },
      { label: "Failed", value: 2 },
    ]);
    expect(summary.taskCounts).toEqual([
      { label: "Attempted", value: 140 },
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
    // The display text says plainly that it is not measured — never a bare
    // "—" that could be mistaken for "no data" without an explanation.
    expect(summary.lostTaskTime.display).toBe(NOT_MEASURED);
    expect(summary.mttr.display).toBe(NOT_MEASURED);
    expect(summary.mttd.display).toBe(NOT_MEASURED);
  });

  it("explains goodput's null as 'no attempts' when tasks_attempted is 0, not as a generic unmeasured", () => {
    // The API's own contract: goodput_ratio is null specifically when
    // attempted is 0 — there is nothing to divide, which is a different
    // (and more informative) reason than "not instrumented yet".
    const summary = summariseMetrics(
      metrics({ tasks_attempted: 0, goodput_ratio: null })
    );
    expect(summary.goodput.measured).toBe(false);
    expect(summary.goodput.display).not.toBe(NOT_MEASURED);
    expect(summary.goodput.display.toLowerCase()).toContain("no");
  });

  it("still calls goodput unmeasured (not 'no attempts') when attempts happened but the ratio is null anyway", () => {
    const summary = summariseMetrics(
      metrics({ tasks_attempted: 140, goodput_ratio: null })
    );
    expect(summary.goodput.measured).toBe(false);
    expect(summary.goodput.display).toBe(NOT_MEASURED);
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
