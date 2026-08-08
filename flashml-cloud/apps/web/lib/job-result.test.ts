import { describe, expect, it } from "vitest";

import { summariseJobResult } from "./job-result";
import type { JobResult } from "./cloud-api";

function result(over: Partial<JobResult>): JobResult {
  return {
    job_id: "job-abc",
    reducer: "none",
    accepted: 3,
    total: 3,
    complete: true,
    result: null,
    ...over,
  };
}

describe("summariseJobResult", () => {
  it("shows nothing for a job that has no reduction to show", () => {
    // A federated job (the API 409s, the client returns null) and a job
    // that declared no reducer are different causes with the same correct
    // outcome: no panel. Rendering an empty card would imply the answer
    // was computed and came back blank.
    expect(summariseJobResult(null)).toBeNull();
    expect(summariseJobResult(result({ reducer: "none" }))).toBeNull();
  });

  it("names the winning trial and the number that won it", () => {
    const summary = summariseJobResult(
      result({
        reducer: "rank",
        result: {
          metric: "accuracy",
          best: { task_id: "task-001", value: 0.93 },
          ranking: [],
        },
      })
    );
    expect(summary?.headline).toBe("task-001");
    expect(summary?.detail).toBe("accuracy 0.93");
  });

  it("says plainly when a ranked job produced no rankable result", () => {
    // Every task committed; none carried the metric the job named. The
    // honest answer is that there is no winner, not a blank where a task
    // id should be.
    const summary = summariseJobResult(
      result({ reducer: "rank", result: { metric: "accuracy", best: null } })
    );
    expect(summary?.headline).toMatch(/no/i);
    expect(summary?.detail).toContain("accuracy");
  });

  it("reports an aggregate's mean alongside the spread it came from", () => {
    const summary = summariseJobResult(
      result({
        reducer: "aggregate",
        result: { metric: "acc", count: 3, mean: 0.9, sum: 2.7, min: 0.8, max: 1 },
      })
    );
    expect(summary?.headline).toBe("0.9");
    expect(summary?.rows).toEqual([
      { label: "metric", value: "acc" },
      { label: "tasks", value: "3" },
      { label: "min", value: "0.8" },
      { label: "max", value: "1" },
    ]);
  });

  it("points at the merged artifact a concat produced", () => {
    const summary = summariseJobResult(
      result({
        reducer: "concat",
        result: {
          destination: "jobs/job-abc/reduced/rows.jsonl",
          bytes: 2048,
          parts: ["task-000", "task-001"],
        },
      })
    );
    expect(summary?.headline).toBe("rows.jsonl");
    expect(summary?.rows).toContainEqual({ label: "merged from", value: "2 tasks" });
  });

  it("counts what a collect gathered", () => {
    const summary = summariseJobResult(
      result({ reducer: "collect", result: { count: 12, tasks: [] } })
    );
    expect(summary?.headline).toBe("12 artifacts");
  });

  it("marks a partial answer as partial", () => {
    const summary = summariseJobResult(
      result({
        reducer: "rank",
        accepted: 2,
        total: 3,
        complete: false,
        result: { metric: "acc", best: { task_id: "task-001", value: 0.9 } },
      })
    );
    expect(summary?.partial).toBe(true);
    expect(summary?.coverage).toBe("2 of 3 tasks accepted so far");
  });

  it("does not nag about coverage once every task is in", () => {
    const summary = summariseJobResult(
      result({
        reducer: "rank",
        result: { metric: "acc", best: { task_id: "task-000", value: 0.9 } },
      })
    );
    expect(summary?.partial).toBe(false);
    expect(summary?.coverage).toBe("3 tasks");
  });

  it("survives a reducer the console does not know about", () => {
    // The runtime may ship a reducer before the console learns to render
    // it. Showing the raw kind beats crashing the whole job page.
    const summary = summariseJobResult(
      result({ reducer: "histogram", result: { buckets: 4 } })
    );
    expect(summary?.headline).toBe("histogram");
  });
});
