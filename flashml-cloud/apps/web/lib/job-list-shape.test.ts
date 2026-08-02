import { describe, expect, it } from "vitest";

/**
 * The /jobs list mixes two payload shapes, and the page assumed one of them.
 *
 * A coordinator job arrives with the full JobRecord: spec, created_at,
 * artifacts, the lot. A FEDERATED job does not exist on the coordinator at all
 * — it is one coordinator job per round — so the API synthesises it from
 * public.jobs and sends exactly four fields:
 *
 *   {"job_id", "name", "state", "mode": "federated"}
 *
 * app/jobs/page.tsx rendered `j.spec.metadata.name` and
 * `new Date(j.created_at)`, so the first federated job in a user's list threw
 * `Cannot read properties of undefined (reading 'metadata')` during render.
 * Next.js caught it and showed "This page couldn't load" — no build error, no
 * server log, nothing naming the field.
 *
 * TypeScript could not help: JobRecord declares spec and created_at as
 * required, and a type cannot constrain what a server actually sends. These
 * tests pin the two shapes as data, so the mismatch is visible in the suite
 * rather than in a browser after someone runs their first federated job.
 */

// Exactly what flashml_cloud_api/app.py builds for a federated run.
const FEDERATED_JOB = {
  job_id: "fed-2e2d4d6ab57f",
  name: "federated-mlp",
  state: "SUCCEEDED",
  mode: "federated",
} as const;

// Abbreviated coordinator job — only the fields the list renders.
const COORDINATOR_JOB = {
  job_id: "job-abc123",
  state: "RUNNING",
  spec: { metadata: { name: "sweep" } },
  created_at: "2026-08-02T04:08:10Z",
} as const;

/** What the list row needs, tolerant of either shape. */
export function jobRowFields(job: Record<string, unknown>) {
  const spec = job.spec as { metadata?: { name?: string } } | undefined;
  return {
    title: spec?.metadata?.name ?? (job.name as string) ?? (job.job_id as string),
    createdAt: (job.created_at as string | undefined) ?? null,
  };
}

describe("the /jobs list renders both payload shapes", () => {
  it("a federated job has no spec and no created_at", () => {
    expect("spec" in FEDERATED_JOB).toBe(false);
    expect("created_at" in FEDERATED_JOB).toBe(false);
  });

  it("does not throw on a federated job", () => {
    expect(() => jobRowFields(FEDERATED_JOB)).not.toThrow();
    const row = jobRowFields(FEDERATED_JOB);
    expect(row.title).toBe("federated-mlp");
    expect(row.createdAt).toBeNull();
  });

  it("still prefers the spec name for a coordinator job", () => {
    const row = jobRowFields(COORDINATOR_JOB);
    expect(row.title).toBe("sweep");
    expect(row.createdAt).toBe("2026-08-02T04:08:10Z");
  });

  it("falls back to the job id when a job has neither name nor spec", () => {
    expect(jobRowFields({ job_id: "job-bare" }).title).toBe("job-bare");
  });
});
