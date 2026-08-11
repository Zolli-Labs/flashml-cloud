# First-Run Quickstart Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A newly-admitted user is told, by the console itself, that they need
a machine before they need a job — and is handed a known-good sample repo to
run on it.

**Architecture:** Every behaviour this plan adds lands as a **pure function in
`lib/`** with its own `lib/*.test.ts`, and components become thin renderers of
those functions. This is the repo's established pattern (`lib/machine-scope.ts`
was extracted from `MachineToggleRow` for exactly this reason: "so the header
count and the table's own dots cannot disagree"). Two of the spec's tests are
genuinely about markup; those use `renderToStaticMarkup` from `react-dom/server`,
which the existing `lib/zolli-brand.test.ts` already does under vitest's `node`
environment — verified working against `EnrolInstructions` and `ConnectPanel`
before this plan was written.

**Tech Stack:** Next.js 16 App Router, React 19, TypeScript, Tailwind v4,
vitest 4 (`environment: "node"`, `include: ["**/*.test.ts"]`), `@phosphor-icons/react`.

---

## Status correction to the spec — the gate in §3 is LIFTED

**The spec's §3 says this whole spec is gated on unshipped `flashnode` work.
That is stale. Do not sequence around it, and do not repeat it in `PROGRESS.md`.**

The spec was written against `plans/2026-08-09-trusted-tier-runner-contract.md`,
whose checkboxes are all `- [ ]`. The work did not go through that plan file —
it landed directly on `flashml` trunk on 2026-08-09/10 and is tagged. Verified
2026-08-10:

| Spec §3 claim | Reality | Evidence |
|---|---|---|
| `FLASHML_WORK_DIR` undelivered on trusted hosts | **Shipped** | `flashml/flashnode/flashnode/executor/trusted_runner.py:50`, `.../executor/runner.py:107` |
| Trusted host self-quarantines and blames Docker | **Shipped** (tier-scoped health checks) | `.../executor/health.py:92-104`, `.../agent/cli.py:348-368` |
| `flashnode login` prints a wrong `--runner docker` hint | **Shipped** (hint reads the real tier) | `.../executor/health.py:79-89`, `.../agent/cli.py:143-153` |
| Trusted tier discards the job's declared image (§4/§5) | **Shipped** (image manifest as base + per-job cooldown) | `.../executor/environments.py:147,151`, `.../executor/loop.py:55-68,306-336` |

All six commits are contained in **`flashnode-v0.3.5`**, and
`flashml-cloud/Makefile:60` already reads `NODE_VERSION := 0.3.5`. The sample
this plan points users at (`flashml-examples` `main` → `jobs/hello.py:33`)
reads `FLASHML_WORK_DIR`, which is now set on trusted hosts.

**Consequence:** the spec's closing "**Do not announce to users until the
`flashnode` release in §3 has shipped**" is satisfied. This work may be
announced when it merges. Task 8 records the correction in the spec itself so
the next reader is not misled.

---

## Global Constraints

Every task's requirements implicitly include this section.

- **Vocabulary: `machine` and `workspace`.** No string this plan adds or edits
  may contain "Zolli", "Zollis", "Crew" or "Crews" (owner decision §6.3 as
  amended; spec §1.7). **Each task fixes the retired words in the file it
  touches** — do not leave a half-swept file behind, and do not sweep files no
  task touches (that is the separate P2.3 vocabulary sweep). Task 7's guard
  test enforces this for every file this plan creates.
- **Keep `workspace` in the UI and `pool` in the API/DB/routes/types.** The
  split is deliberate (`flashml-cloud/CLAUDE.md`, Vocabulary). Props and
  variables stay `poolId`; user-visible strings say "workspace". **No route
  changes, no identifier renames, no API change, no migration.**
- **Tests are `lib/*.test.ts` only.** `vitest.config.ts` sets
  `include: ["**/*.test.ts"]` — a `.test.tsx` file is silently never run.
  Component assertions go in a `.test.ts` that calls `createElement` +
  `renderToStaticMarkup`.
- **`renderToStaticMarkup` renders only the ACTIVE tab of a `Tabs` component.**
  `ConnectPanel` has `defaultValue="colab"`, so the RunPod tab's markup is
  absent from a static render. Assert Colab-tab markup only; cover the rest
  with pure-function tests.
- **A route module (`app/**/{page,layout,route}.tsx`) may export only a default
  component plus route config.** `lib/route-exports.test.ts` enforces this and
  the failure mode is a runtime white screen, not a build error. New shared
  code goes in `lib/` or `components/`, never exported off a page.
- **The submit button must not become disabled by machine count** (spec
  decision 4). The enable predicate is deliberately given a signature that
  cannot see machines.
- **Run from `flashml-cloud/flashml-cloud/apps/web/`** for every `npx vitest`
  command below. Full suite: `npm test`.
- **Commit after every task.** Conventional-commit subjects, in the house
  voice: say what changed and why, not which files moved.

---

## File Structure

**New — `flashml-cloud/flashml-cloud/apps/web/lib/`**

| File | Responsibility |
|---|---|
| `first-run.ts` | Derives the three-step checklist (and whether it is complete) from `machines` + `jobs`. The whole of spec §4. |
| `first-run.test.ts` | Spec tests 1 and 2. |
| `example-repo.ts` | The sample repo URL and its one line of copy. Spec §5. |
| `submit-form.ts` | `SubmitStatus` + the submit-button enable predicate. Spec test 4's guard. |
| `submit-form.test.ts` | Spec test 3 (prefill shape) and 4. |
| `enrol-steps.ts` | The four enrolment commands per platform, lifted out of `EnrolInstructions`. Spec §6.1. |
| `enrol-steps.test.ts` | Spec test 5. |
| `runner-tier.ts` | The one shared host→tier sentence, used by three surfaces. Spec §6.2. |
| `activate-url.ts` | `/activate` path and absolute URL, always carrying `?pool=`. Spec §6.3–6.4. |
| `activate-url.test.ts` | Spec test 6's pure half. |
| `console-vocabulary.test.ts` | Spec test 7 — source scan of the files this plan creates. |

**New — `flashml-cloud/flashml-cloud/apps/web/components/workspace/`**

| File | Responsibility |
|---|---|
| `FirstRunChecklist.tsx` | Renders `firstRunChecklist()`. Pure props (`poolId`, `machines`, `jobs`) so it is SSR-testable without a `WorkspaceProvider`. |

**Modified**

| File | Change |
|---|---|
| `app/(console)/w/[poolId]/overview/page.tsx` | Render the checklist above the stats; fix the two retired words in this file. |
| `app/(console)/w/[poolId]/submit/page.tsx` | Sample prefill control, real placeholder, `canSubmitRepo`, vocabulary. |
| `components/machines/EnrolInstructions.tsx` | Consume `enrolSteps` (now four steps); add the tier sentence; vocabulary. |
| `components/pools/ConnectPanel.tsx` | Approve URL becomes an anchor + copy button; use the shared tier sentence; vocabulary. |
| `components/shell/ConsoleShell.tsx` | The "Add a machine" card carries `?pool=` when a workspace is in context; vocabulary. |
| `app/(console)/workspaces/page.tsx` | One sentence about attaching a machine next; vocabulary. |
| `app/(console)/docs/page.tsx` | Link the example repo in "Run a job"; use the shared tier sentence. |
| `app/(console)/w/[poolId]/machines/page.tsx` | Vocabulary in the two headings this plan's links land on. |
| `docs/superpowers/specs/2026-08-10-first-run-quickstart-design.md` | Status → approved; §3 correction. |

**Modified — separate repo `~/Work/Zolli-Labs/flashml-examples`**

| File (branch `federated`) | Change |
|---|---|
| `flashml.yaml` | v1 → v2: `rounds`/`min_participants`/`shards` → `epochs` + `sync_every`. |
| `jobs/fed_train.py` | Emit `chunks_done` in `metrics.json`. The CLI arguments are unchanged. |

---

## Task 1: The first-run checklist derivation

**Files:**
- Create: `flashml-cloud/flashml-cloud/apps/web/lib/first-run.ts`
- Test: `flashml-cloud/flashml-cloud/apps/web/lib/first-run.test.ts`

**Interfaces:**
- Consumes: `isMachineOnline` from `@/lib/machine-scope`; `workspacePath` from
  `@/lib/workspace-scope`.
- Produces: `FIRST_RUN_INTRO: string`, `type FirstRunStepId`,
  `interface FirstRunStep`, `interface FirstRunChecklist`, and
  `firstRunChecklist(input: FirstRunInput): FirstRunChecklist`. Task 2 renders
  exactly these.

- [ ] **Step 1: Write the failing test**

Create `lib/first-run.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { FIRST_RUN_INTRO, firstRunChecklist } from "./first-run";

// `isMachineOnline` reads `Date.now()` and takes no clock injection
// (lib/machine-status.ts), so these build timestamps relative to now rather
// than pinning a fake clock.
const seenJustNow = () => new Date(Date.now() - 5_000).toISOString();
const seenLongAgo = () => new Date(Date.now() - 10 * 60_000).toISOString();

function machine(overrides: { status?: string; last_seen_at?: string | null } = {}) {
  return { status: "active", last_seen_at: seenJustNow(), ...overrides };
}

const POOL = "pool-1";

describe("firstRunChecklist", () => {
  it("names the constraint the product otherwise never states", () => {
    expect(FIRST_RUN_INTRO).toContain("machines you attach");
  });

  it("pre-ticks the workspace step, since arriving here proves it", () => {
    const { steps } = firstRunChecklist({ poolId: POOL, machines: [], jobs: [] });
    expect(steps.map((s) => s.id)).toEqual(["workspace", "machine", "job"]);
    expect(steps[0].done).toBe(true);
    expect(steps[0].action).toBeNull();
  });

  it("leaves the machine step unticked when the workspace has none", () => {
    const { complete, steps } = firstRunChecklist({
      poolId: POOL,
      machines: [],
      jobs: [],
    });
    expect(steps[1].done).toBe(false);
    expect(steps[1].note).toBeNull();
    expect(steps[1].action).toEqual({
      label: "Connect a machine",
      href: "/w/pool-1/machines#connect-panel",
    });
    expect(complete).toBe(false);
  });

  it("ticks the machine step and leaves the job step for a workspace with a machine and no jobs", () => {
    const { complete, steps } = firstRunChecklist({
      poolId: POOL,
      machines: [machine()],
      jobs: [],
    });
    expect(steps[1].done).toBe(true);
    expect(steps[1].note).toBeNull();
    expect(steps[2].done).toBe(false);
    expect(steps[2].action).toEqual({
      label: "Run the sample",
      href: "/w/pool-1/submit",
    });
    expect(complete).toBe(false);
  });

  it("reports complete once there is a machine and a job", () => {
    const { complete, steps } = firstRunChecklist({
      poolId: POOL,
      machines: [machine()],
      jobs: [{}],
    });
    expect(steps.every((s) => s.done)).toBe(true);
    expect(complete).toBe(true);
  });

  it("ticks the machine step for an attached-but-offline machine, and says which fix applies", () => {
    // The fix at this point is to start the agent, not to attach another
    // machine — so the step stays ticked and the note carries the difference.
    const { steps } = firstRunChecklist({
      poolId: POOL,
      machines: [machine({ last_seen_at: seenLongAgo() })],
      jobs: [],
    });
    expect(steps[1].done).toBe(true);
    expect(steps[1].note).toBe("Attached, none online right now — start the agent on it.");
  });

  it("treats a revoked machine as attached-but-offline, never as online", () => {
    const { steps } = firstRunChecklist({
      poolId: POOL,
      machines: [machine({ status: "revoked" })],
      jobs: [],
    });
    expect(steps[1].done).toBe(true);
    expect(steps[1].note).not.toBeNull();
  });

  it("stays incomplete on jobs alone, because a queued job is not a run", () => {
    const { complete, steps } = firstRunChecklist({
      poolId: POOL,
      machines: [],
      jobs: [{}],
    });
    expect(steps[2].done).toBe(true);
    expect(complete).toBe(false);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx vitest run lib/first-run.test.ts`
Expected: FAIL — `Failed to resolve import "./first-run"`.

- [ ] **Step 3: Write minimal implementation**

Create `lib/first-run.ts`:

```ts
import { isMachineOnline } from "./machine-scope";
import { workspacePath } from "./workspace-scope";

/** The first-run guide, as data.
 *
 * It is the workspace overview's zero-state rather than a route, so there is
 * nothing to keep alive after onboarding and nothing to dismiss: the whole
 * block unmounts once `complete` is true. Every step derives from state the
 * overview page has already fetched — no new request, no persisted flag.
 *
 * The order is the point. Both zero-state CTAs used to go to submit, which is
 * the one action guaranteed to produce a job that queues forever. Compute
 * comes before the job here and on every surface.
 */
export const FIRST_RUN_INTRO =
  "FlashML runs your jobs on machines you attach — a Colab notebook, a rented pod, or your own hardware.";

export type FirstRunStepId = "workspace" | "machine" | "job";

export interface FirstRunAction {
  label: string;
  href: string;
}

export interface FirstRunStep {
  id: FirstRunStepId;
  title: string;
  done: boolean;
  /** A second line, only where "done" alone would mislead. */
  note: string | null;
  action: FirstRunAction | null;
}

export interface FirstRunChecklist {
  /** Steps 2 and 3 both done. Step 1 is always done and never gates this. */
  complete: boolean;
  steps: FirstRunStep[];
}

export interface FirstRunInput {
  poolId: string;
  /** Structurally typed, not `PoolMachine[]` — this reads two fields and
   *  nothing else, and the narrower shape is what makes it cheap to test. */
  machines: { status: string; last_seen_at: string | null }[];
  /** Only the count is read. */
  jobs: unknown[];
}

export function firstRunChecklist(input: FirstRunInput): FirstRunChecklist {
  const { poolId, machines, jobs } = input;

  const attached = machines.length > 0;
  const anyOnline = machines.some(isMachineOnline);
  const hasJobs = jobs.length > 0;

  const steps: FirstRunStep[] = [
    {
      id: "workspace",
      title: "Create a workspace",
      // Pre-ticked: you cannot be reading this without one. It orients
      // rather than instructs, which is why it carries no action.
      done: true,
      note: null,
      action: null,
    },
    {
      id: "machine",
      title: "Attach a machine",
      done: attached,
      note:
        attached && !anyOnline
          ? "Attached, none online right now — start the agent on it."
          : null,
      action: {
        // The `#connect-panel` anchor is load-bearing: the machines tab
        // keeps that id precisely so links can land on the Colab/RunPod
        // instructions rather than the top of the page.
        label: "Connect a machine",
        href: `${workspacePath(poolId, "machines")}#connect-panel`,
      },
    },
    {
      id: "job",
      title: "Run your first job",
      done: hasJobs,
      note: null,
      action: { label: "Run the sample", href: workspacePath(poolId, "submit") },
    },
  ];

  return { complete: attached && hasJobs, steps };
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npx vitest run lib/first-run.test.ts`
Expected: PASS (8 tests).

- [ ] **Step 5: Commit**

```bash
git add flashml-cloud/apps/web/lib/first-run.ts flashml-cloud/apps/web/lib/first-run.test.ts
git commit -m "feat(web): derive a first-run checklist from state the overview already has"
```

---

## Task 2: The checklist component, and the overview zero-state it replaces

**Files:**
- Create: `flashml-cloud/flashml-cloud/apps/web/components/workspace/FirstRunChecklist.tsx`
- Modify: `flashml-cloud/flashml-cloud/apps/web/app/(console)/w/[poolId]/overview/page.tsx` (lines 34-42 and 115-135)
- Test: `flashml-cloud/flashml-cloud/apps/web/lib/first-run.test.ts` (append)

**Interfaces:**
- Consumes: `firstRunChecklist`, `FIRST_RUN_INTRO` from Task 1.
- Produces: `FirstRunChecklist` React component with props
  `{ poolId: string; machines: { status: string; last_seen_at: string | null }[]; jobs: unknown[] }`.
  Props, not `useWorkspace()`, so it renders under `renderToStaticMarkup`
  without a provider.

- [ ] **Step 1: Write the failing test**

Append to `lib/first-run.test.ts`:

```ts
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { FirstRunChecklist } from "@/components/workspace/FirstRunChecklist";

describe("FirstRunChecklist rendering", () => {
  const render = (machines: { status: string; last_seen_at: string | null }[], jobs: unknown[]) =>
    renderToStaticMarkup(
      createElement(FirstRunChecklist, { poolId: "pool-1", machines, jobs })
    );

  it("offers the machine step first for an empty workspace", () => {
    const markup = render([], []);
    expect(markup).toContain("Attach a machine");
    expect(markup).toContain("/w/pool-1/machines#connect-panel");
    expect(markup).toContain("machines you attach");
  });

  it("shows the offline note when a machine is attached but not running", () => {
    const markup = render(
      [{ status: "active", last_seen_at: new Date(Date.now() - 10 * 60_000).toISOString() }],
      []
    );
    expect(markup).toContain("start the agent on it");
  });

  it("renders nothing at all once there is a machine and a job", () => {
    const markup = render(
      [{ status: "active", last_seen_at: new Date(Date.now() - 5_000).toISOString() }],
      [{}]
    );
    expect(markup).toBe("");
  });

  it("never disables the job step for want of a machine", () => {
    // Decision 4: queueing ahead of a machine you are about to start is
    // legitimate. De-emphasised is fine; unreachable is not.
    const markup = render([], []);
    expect(markup).toContain("/w/pool-1/submit");
    expect(markup).not.toContain("aria-disabled");
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx vitest run lib/first-run.test.ts`
Expected: FAIL — `Failed to resolve import "@/components/workspace/FirstRunChecklist"`.

- [ ] **Step 3: Write minimal implementation**

Create `components/workspace/FirstRunChecklist.tsx`:

```tsx
"use client";

import Link from "next/link";
import { ArrowRight, CheckCircle, Circle } from "@phosphor-icons/react";
import {
  FIRST_RUN_INTRO,
  firstRunChecklist,
} from "@/lib/first-run";

/** The workspace overview's zero-state.
 *
 * Takes plain props rather than reading `useWorkspace()` so it can be
 * rendered — and asserted on — without mounting a provider. The overview
 * page is the only caller and already has all three values.
 */
export function FirstRunChecklist({
  poolId,
  machines,
  jobs,
}: {
  poolId: string;
  machines: { status: string; last_seen_at: string | null }[];
  jobs: unknown[];
}) {
  const { complete, steps } = firstRunChecklist({ poolId, machines, jobs });

  // No dismiss control and no persisted flag: it is gone the moment it stops
  // being useful, and it comes back if you leave the workspace empty again.
  if (complete) return null;

  return (
    <section className="mt-6 rounded-lg border border-primary/20 bg-surface p-5 shadow-sm">
      <h2 className="text-sm font-semibold">Get your first job running</h2>
      <p className="mt-1.5 max-w-prose text-sm text-muted-foreground">
        {FIRST_RUN_INTRO}
      </p>

      <ol className="mt-4 space-y-3">
        {steps.map((step) => (
          <li key={step.id} className="flex items-start gap-3">
            {step.done ? (
              <CheckCircle
                size={18}
                weight="fill"
                className="mt-0.5 shrink-0 text-[var(--node-green)]"
              />
            ) : (
              <Circle size={18} className="mt-0.5 shrink-0 text-muted-foreground" />
            )}
            <div className="min-w-0 flex-1">
              <p
                className={
                  step.done ? "text-sm text-muted-foreground" : "text-sm font-medium"
                }
              >
                {step.title}
              </p>
              {step.note && (
                <p className="mt-0.5 text-xs text-muted-foreground">{step.note}</p>
              )}
            </div>
            {step.action && !step.done && (
              <Link
                href={step.action.href}
                className="interactive shrink-0 inline-flex items-center gap-1.5 text-sm text-brand-foreground hover:underline"
              >
                {step.action.label}
                <ArrowRight size={13} weight="bold" />
              </Link>
            )}
          </li>
        ))}
      </ol>
    </section>
  );
}
```

- [ ] **Step 4: Wire it into the overview and fix that file's vocabulary**

In `app/(console)/w/[poolId]/overview/page.tsx`:

Add the import beside the existing workspace imports:

```tsx
import { FirstRunChecklist } from "@/components/workspace/FirstRunChecklist";
```

Render it directly below `<WorkspaceHeader />` (currently line 32), above the
stats grid:

```tsx
      <WorkspaceHeader />

      <FirstRunChecklist poolId={pool.id} machines={machines} jobs={jobs} />
```

Change the first `Stat` label (line 36) from `"Zollis online"` to
`"Machines online"`, and `EmptyJobs`' zero copy (line 124) from
`"No jobs in this Crew yet."` to `"No jobs in this workspace yet."`.

- [ ] **Step 5: Run the tests**

Run: `npx vitest run lib/first-run.test.ts && npx tsc --noEmit`
Expected: PASS (12 tests), and no type errors.

- [ ] **Step 6: Commit**

```bash
git add flashml-cloud/apps/web/components/workspace/FirstRunChecklist.tsx \
        flashml-cloud/apps/web/app/\(console\)/w/\[poolId\]/overview/page.tsx \
        flashml-cloud/apps/web/lib/first-run.test.ts
git commit -m "feat(web): the overview asks for a machine before it asks for a job"
```

---

## Task 3: The sample, as a prefill of the form the user already sees

**Files:**
- Create: `flashml-cloud/flashml-cloud/apps/web/lib/example-repo.ts`
- Create: `flashml-cloud/flashml-cloud/apps/web/lib/submit-form.ts`
- Create: `flashml-cloud/flashml-cloud/apps/web/lib/submit-form.test.ts`
- Modify: `flashml-cloud/flashml-cloud/apps/web/app/(console)/w/[poolId]/submit/page.tsx`

**Interfaces:**
- Produces: `EXAMPLE_REPO_URL`, `EXAMPLE_REPO_BLURB` from `@/lib/example-repo`;
  `type SubmitStatus` and `canSubmitRepo(repo, status)` from `@/lib/submit-form`.
  The submit page imports `SubmitStatus` instead of declaring its own local
  `Status` type.

- [ ] **Step 1: Write the failing test**

Create `lib/submit-form.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { EXAMPLE_REPO_BLURB, EXAMPLE_REPO_URL } from "./example-repo";
import { canSubmitRepo } from "./submit-form";

describe("the example repo", () => {
  it("is the real published examples repo, at its default branch", () => {
    expect(EXAMPLE_REPO_URL).toBe("https://github.com/Zolli-Labs/flashml-examples");
  });

  it("says why it is worth clicking", () => {
    expect(EXAMPLE_REPO_BLURB).toContain("stdlib-only");
  });

  it("carries no branch, so submit resolves it to main and the hello-world job", () => {
    // The prefill sets `repo` and leaves `ref` blank; blank means main. The
    // guard is that nothing here encodes a branch that could go stale.
    expect(EXAMPLE_REPO_URL).not.toContain("/tree/");
  });
});

describe("canSubmitRepo", () => {
  it("enables submit once a repo has been typed", () => {
    expect(canSubmitRepo("https://github.com/Zolli-Labs/flashml-examples", "idle")).toBe(true);
  });

  it("refuses an empty or whitespace-only repo", () => {
    expect(canSubmitRepo("", "idle")).toBe(false);
    expect(canSubmitRepo("   ", "idle")).toBe(false);
  });

  it("refuses a second click while preflight is running", () => {
    expect(canSubmitRepo("owner/name", "submitting")).toBe(false);
  });

  it("stays enabled after a rejection, so the form can be corrected and resent", () => {
    expect(canSubmitRepo("owner/name", "rejected")).toBe(true);
    expect(canSubmitRepo("owner/name", "error")).toBe(true);
  });

  // Spec decision 4, guarded by the signature itself: queueing a job ahead of
  // a machine you are about to start is legitimate, and the submit page's
  // zero-machines warning is the right control for it. This function is given
  // no way to see the fleet, so a later "fix" that disables the button when
  // nothing is online cannot be written here without changing the type.
  it("takes no machine information at all", () => {
    expect(canSubmitRepo.length).toBe(2);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx vitest run lib/submit-form.test.ts`
Expected: FAIL — `Failed to resolve import "./example-repo"`.

- [ ] **Step 3: Write minimal implementation**

Create `lib/example-repo.ts`:

```ts
/** The sample every first-run surface points at.
 *
 * `Zolli-Labs/flashml-examples` at its default branch: CPU-only,
 * stdlib-only, `python-slim`, and its own README calls `main` "the smallest
 * job that proves the whole path". Nothing here is authored or maintained by
 * the console — it is a prefill of the two fields the user would otherwise
 * type, which is why there is no second submit path to keep working.
 *
 * Deliberately no `ref`: blank resolves to `main`, and pinning a branch here
 * would be a fourth place a name has to stay in step with that repo.
 */
export const EXAMPLE_REPO_URL = "https://github.com/Zolli-Labs/flashml-examples";

export const EXAMPLE_REPO_BLURB =
  "A stdlib-only CPU job that finishes in seconds — the fastest way to prove the path.";
```

Create `lib/submit-form.ts`:

```ts
export type SubmitStatus =
  | "idle"
  | "submitting"
  | "rejected"
  | "submitted"
  | "error";

/** Whether the submit button is enabled.
 *
 * Note what this does NOT take: anything about the fleet. Submitting with no
 * machine online is legitimate — the job queues until one connects, and the
 * page already warns in those words. The warning is the right control; a
 * disabled button would be wrong, and the fix for a user in that position is
 * that they should have been offered the machine step first, which the
 * overview's first-run checklist now does.
 */
export function canSubmitRepo(repo: string, status: SubmitStatus): boolean {
  return repo.trim().length > 0 && status !== "submitting";
}
```

- [ ] **Step 4: Wire the submit page**

In `app/(console)/w/[poolId]/submit/page.tsx`:

Replace the local `type Status = …` declaration (line 27) — delete it — and add
to the imports:

```tsx
import { EXAMPLE_REPO_BLURB, EXAMPLE_REPO_URL } from "@/lib/example-repo";
import { canSubmitRepo, type SubmitStatus } from "@/lib/submit-form";
```

Change the state declaration (line 34) to use the imported type:

```tsx
  const [status, setStatus] = useState<SubmitStatus>("idle");
```

Replace the inline predicate (line 52):

```tsx
  const canSubmit = canSubmitRepo(repo, status);
```

Change the repo input's placeholder (line 177) from
`"https://github.com/acme/trainer"` to `{EXAMPLE_REPO_URL}` — the form should
teach the shape even when the button below is ignored.

Add the prefill control immediately after the repo input's closing `</div>`
(after line 183):

```tsx
            <div className="rounded-lg border border-border bg-surface-2/50 px-3 py-2.5">
              <button
                type="button"
                onClick={() => {
                  setRepo(EXAMPLE_REPO_URL);
                  setRef("");
                }}
                disabled={status === "submitting"}
                className="interactive text-sm font-medium text-brand-foreground hover:underline disabled:opacity-40"
              >
                Use the example repo
              </button>
              <p className="mt-1 text-xs text-muted-foreground">
                {EXAMPLE_REPO_BLURB}
              </p>
            </div>
```

Fix this file's retired words:
- line 154: `"hand it to the next available Zolli."` → `"hand it to the next available machine."`
- line 206: `<Label>Crew</Label>` → `<Label>Workspace</Label>`
- lines 213-215: `"Jobs in this Crew run without a container sandbox on your crewmates' Zollis. Every member you invited can run code this job stages."` →
  `"Jobs in this workspace run without a container sandbox on the machines its members attach. Every member you invited can run code this job stages."`
- lines 222-223: `"0 Zollis online in this Crew right now — the job will queue until one connects."` →
  `"0 machines online in this workspace right now — the job will queue until one connects."`

- [ ] **Step 5: Run the tests**

Run: `npx vitest run lib/submit-form.test.ts && npx tsc --noEmit`
Expected: PASS (8 tests), no type errors.

- [ ] **Step 6: Commit**

```bash
git add flashml-cloud/apps/web/lib/example-repo.ts \
        flashml-cloud/apps/web/lib/submit-form.ts \
        flashml-cloud/apps/web/lib/submit-form.test.ts \
        flashml-cloud/apps/web/app/\(console\)/w/\[poolId\]/submit/page.tsx
git commit -m "feat(web): offer the example repo on submit, and stop suggesting a fictional one"
```

---

## Task 4: Four enrolment steps, and one sentence about runner tiers

**Files:**
- Create: `flashml-cloud/flashml-cloud/apps/web/lib/enrol-steps.ts`
- Create: `flashml-cloud/flashml-cloud/apps/web/lib/runner-tier.ts`
- Create: `flashml-cloud/flashml-cloud/apps/web/lib/enrol-steps.test.ts`
- Modify: `flashml-cloud/flashml-cloud/apps/web/components/machines/EnrolInstructions.tsx` (lines 40-67, 185-204)

**Interfaces:**
- Produces: `type Platform`, `interface EnrolStep`, `enrolSteps(platform, base)`
  from `@/lib/enrol-steps`; `RUNNER_TIER_SENTENCE` and
  `runnerFor(host)` from `@/lib/runner-tier`. Tasks 5 and 6 import
  `RUNNER_TIER_SENTENCE`.

- [ ] **Step 1: Write the failing test**

Create `lib/enrol-steps.test.ts`:

```ts
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { EnrolInstructions } from "@/components/machines/EnrolInstructions";
import { enrolSteps } from "./enrol-steps";
import { RUNNER_TIER_SENTENCE, runnerFor } from "./runner-tier";

const BASE = "https://api.example.test";

describe("enrolSteps", () => {
  it("ends with the command that actually takes work", () => {
    // Without it the component teaches a dead end: follow the old three
    // steps and you enrol a machine that enrols, heartbeats, and never
    // claims anything.
    const unix = enrolSteps("unix", BASE);
    expect(unix).toHaveLength(4);
    expect(unix[3].cmd).toBe(
      `flashml/bin/flashnode work --coordinator ${BASE} --runner argv`
    );
  });

  it("uses Windows paths for the same four steps", () => {
    const win = enrolSteps("windows", BASE);
    expect(win).toHaveLength(4);
    expect(win[3].cmd).toBe(
      `flashml\\Scripts\\flashnode work --coordinator ${BASE} --runner argv`
    );
  });

  it("keeps the venv install and login steps unchanged", () => {
    const unix = enrolSteps("unix", BASE);
    expect(unix[0].cmd).toBe("python3 -m venv flashml");
    expect(unix[1].cmd).toBe("flashml/bin/python -m pip install flashnode");
    expect(unix[2].cmd).toBe(`flashml/bin/flashnode login --coordinator ${BASE}`);
  });

  it("pins no version — a volunteer should get the current agent", () => {
    expect(enrolSteps("unix", BASE).some((s) => /flashnode==/.test(s.cmd))).toBe(false);
  });
});

describe("runnerFor", () => {
  it("is a property of the host, not a preference", () => {
    expect(runnerFor("own-machine")).toBe("argv");
    expect(runnerFor("notebook-or-pod")).toBe("trusted");
  });

  it("states both halves in one sentence", () => {
    expect(RUNNER_TIER_SENTENCE).toContain("--runner argv");
    expect(RUNNER_TIER_SENTENCE).toContain("--runner trusted");
    expect(RUNNER_TIER_SENTENCE).toContain("nest Docker");
  });
});

describe("EnrolInstructions", () => {
  it("renders four steps, the fourth of which takes work", () => {
    const markup = renderToStaticMarkup(
      createElement(EnrolInstructions, { base: BASE })
    );
    expect(markup).toContain(`flashnode work --coordinator ${BASE} --runner argv`);
    expect(markup).toContain(RUNNER_TIER_SENTENCE);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx vitest run lib/enrol-steps.test.ts`
Expected: FAIL — `Failed to resolve import "./enrol-steps"`.

- [ ] **Step 3: Write minimal implementation**

Create `lib/runner-tier.ts`:

```ts
/** Which runner tier a host can use, and the one sentence that says so.
 *
 * The tier is a property of the host, not a preference — a machine that
 * cannot nest a Docker daemon cannot run the sandboxed tiers, full stop. The
 * console used to give three different answers to this across the docs page,
 * `ConnectPanel` and `EnrolInstructions`, which read as a contradiction
 * rather than as two host types. One constant, three surfaces.
 *
 * Getting it wrong fails SILENTLY: the agent registers, heartbeats, polls
 * forever and claims nothing, which reads as "no work available" rather than
 * as a misconfiguration.
 */
export type HostKind = "own-machine" | "notebook-or-pod";
export type RunnerTier = "argv" | "trusted";

export function runnerFor(host: HostKind): RunnerTier {
  return host === "own-machine" ? "argv" : "trusted";
}

export const RUNNER_TIER_SENTENCE =
  "Your own machine with Docker uses --runner argv. Colab or a rented pod can't nest Docker, so they use --runner trusted, which runs unsandboxed and only ever runs jobs from this workspace.";
```

Create `lib/enrol-steps.ts`:

```ts
/** The commands that get a volunteer from nothing to taking work.
 *
 * Lifted out of `EnrolInstructions` so the list can be asserted on directly —
 * the fourth step is the one this file exists for. The component shipped
 * three steps and stopped at `login`, which enrols a machine that enrols,
 * heartbeats, and never claims a task.
 *
 * `pip install flashnode` is the whole install: flashnode and flashruntime
 * are published, so pip resolves the dependency itself. The VIRTUAL
 * ENVIRONMENT is not ceremony — it sidesteps two live failures at once, macOS
 * shipping no `pip` on PATH and Homebrew Python refusing to install into
 * itself (PEP 668). The venv's binaries are used by full path rather than
 * `activate`, because activation does not survive closing the terminal and
 * the follow-up command would fail with `command not found: flashnode` a day
 * later.
 *
 * Deliberately NOT version-pinned: a volunteer should get the current agent,
 * and pinning here would create a fourth place a version has to be kept in
 * step with pyproject.toml, render.yaml and the Makefile.
 */
export type Platform = "unix" | "windows";

export interface EnrolStep {
  label: string;
  cmd: string;
}

export function enrolSteps(platform: Platform, base: string): EnrolStep[] {
  if (platform === "windows") {
    return [
      { label: "Create an isolated environment", cmd: "py -m venv flashml" },
      {
        label: "Install the agent",
        cmd: "flashml\\Scripts\\python -m pip install flashnode",
      },
      {
        label: "Connect it to your account",
        cmd: `flashml\\Scripts\\flashnode login --coordinator ${base}`,
      },
      {
        label: "Start taking work",
        cmd: `flashml\\Scripts\\flashnode work --coordinator ${base} --runner argv`,
      },
    ];
  }
  return [
    { label: "Create an isolated environment", cmd: "python3 -m venv flashml" },
    {
      label: "Install the agent",
      cmd: "flashml/bin/python -m pip install flashnode",
    },
    {
      label: "Connect it to your account",
      cmd: `flashml/bin/flashnode login --coordinator ${base}`,
    },
    {
      label: "Start taking work",
      cmd: `flashml/bin/flashnode work --coordinator ${base} --runner argv`,
    },
  ];
}
```

- [ ] **Step 4: Rewire `EnrolInstructions`**

In `components/machines/EnrolInstructions.tsx`:

Delete the local `type Platform` (line 40) and the whole local `steps()`
function (lines 42-67). Add to the imports:

```tsx
import { enrolSteps, type Platform } from "@/lib/enrol-steps";
import { RUNNER_TIER_SENTENCE } from "@/lib/runner-tier";
```

Change the map call (line 172) from `steps(platform, base)` to
`enrolSteps(platform, base)`.

In the footer block, replace the `/activate` paragraph's retired word
(line 189) — `"to approve the Zolli."` → `"to approve the machine."` — and add
the tier sentence as a new paragraph immediately after that one:

```tsx
        <p>{RUNNER_TIER_SENTENCE}</p>
```

- [ ] **Step 5: Run the tests**

Run: `npx vitest run lib/enrol-steps.test.ts && npx tsc --noEmit`
Expected: PASS (7 tests), no type errors.

- [ ] **Step 6: Commit**

```bash
git add flashml-cloud/apps/web/lib/enrol-steps.ts \
        flashml-cloud/apps/web/lib/runner-tier.ts \
        flashml-cloud/apps/web/lib/enrol-steps.test.ts \
        flashml-cloud/apps/web/components/machines/EnrolInstructions.tsx
git commit -m "fix(web): enrolment stopped one command short of taking work"
```

---

## Task 5: The approve URL becomes a link you can actually use

**Files:**
- Create: `flashml-cloud/flashml-cloud/apps/web/lib/activate-url.ts`
- Create: `flashml-cloud/flashml-cloud/apps/web/lib/activate-url.test.ts`
- Modify: `flashml-cloud/flashml-cloud/apps/web/components/pools/ConnectPanel.tsx` (lines 74-97, 173-179, 214-227)
- Modify: `flashml-cloud/flashml-cloud/apps/web/components/shell/ConsoleShell.tsx` (lines 255-274)

**Interfaces:**
- Consumes: `RUNNER_TIER_SENTENCE` from Task 4.
- Produces: `activatePath(poolId?)` and `activateUrl(origin, poolId?)` from
  `@/lib/activate-url`.

- [ ] **Step 1: Write the failing test**

Create `lib/activate-url.test.ts`:

```ts
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { ConnectPanel } from "@/components/pools/ConnectPanel";
import { activatePath, activateUrl } from "./activate-url";

describe("activatePath", () => {
  it("carries the workspace, because that is what makes approval one step", () => {
    expect(activatePath("pool-1")).toBe("/activate?pool=pool-1");
  });

  it("encodes the id rather than interpolating it", () => {
    expect(activatePath("a b/c")).toBe("/activate?pool=a%20b%2Fc");
  });

  it("falls back to the bare route with no workspace in context", () => {
    expect(activatePath()).toBe("/activate");
    expect(activatePath(null)).toBe("/activate");
  });
});

describe("activateUrl", () => {
  it("is absolute when an origin is known, for typing on a second device", () => {
    expect(activateUrl("https://console.example.test", "pool-1")).toBe(
      "https://console.example.test/activate?pool=pool-1"
    );
  });

  it("stays relative during server render, where there is no origin", () => {
    expect(activateUrl("", "pool-1")).toBe("/activate?pool=pool-1");
  });
});

describe("ConnectPanel", () => {
  // renderToStaticMarkup renders only the ACTIVE tab of a Tabs component, and
  // ConnectPanel defaults to Colab — so this asserts the Colab tab. The RunPod
  // tab shares the same ApproveCaption component and the same activatePath().
  const markup = () =>
    renderToStaticMarkup(createElement(ConnectPanel, { poolId: "pool-1" }));

  it("renders the approve URL as a link, not as prose", () => {
    // `?pool=` is load-bearing: with it, approval enrols AND binds to the
    // workspace in one atomic call; without it the machine is account-only
    // and has to be ticked in by hand afterwards.
    expect(markup()).toContain('href="/activate?pool=pool-1"');
  });

  it("offers a copy control, because this is a URL you visit on a phone", () => {
    expect(markup()).toContain("Copy approval link");
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx vitest run lib/activate-url.test.ts`
Expected: FAIL — `Failed to resolve import "./activate-url"`.

- [ ] **Step 3: Write minimal implementation**

Create `lib/activate-url.ts`:

```ts
/** Where a machine gets approved.
 *
 * `?pool=` is the whole reason this is a function. With it, approving enrols
 * the machine AND binds it to the workspace in one atomic call. Without it
 * the machine is account-only and has to be ticked into the workspace by hand
 * afterwards — a second step only `YourMachines`' empty state explains, on a
 * screen the person is not currently looking at.
 *
 * Built with `encodeURIComponent` rather than interpolated: a pool id is a
 * uuid today, and this is what keeps the link correct if that stops being
 * true.
 */
export function activatePath(poolId?: string | null): string {
  if (!poolId) return "/activate";
  return `/activate?pool=${encodeURIComponent(poolId)}`;
}

/** The same target, absolute where the browser origin is known.
 *
 * `origin` is `""` during server rendering (`window` does not exist), and an
 * empty origin deliberately yields the relative form — which is still a
 * working link in the browser. The absolute form exists for the case the
 * relative one cannot serve: reading the URL off one screen and typing it on
 * a phone.
 */
export function activateUrl(origin: string, poolId?: string | null): string {
  return `${origin}${activatePath(poolId)}`;
}
```

- [ ] **Step 4: Rewire `ConnectPanel`**

In `components/pools/ConnectPanel.tsx`, add to the imports:

```tsx
import Link from "next/link";
import { activatePath, activateUrl } from "@/lib/activate-url";
import { RUNNER_TIER_SENTENCE } from "@/lib/runner-tier";
```

Replace the body of `ApproveCaption` (lines 83-96) with a link plus a copy
control. Note the comment above it (lines 68-73) claims `flashnode login`
prints a wrong `--runner docker` hint — **that is fixed as of flashnode 0.3.5**
(`flashml/flashnode/flashnode/agent/cli.py:143-153` now computes the hint from
the host), so the caption stops warning about it:

```tsx
function ApproveCaption({
  origin,
  poolId,
}: {
  origin: string;
  poolId: string;
}) {
  const href = activatePath(poolId);
  const shown = activateUrl(origin, poolId);

  async function copy() {
    try {
      await navigator.clipboard.writeText(shown);
      toast.success("Approval link copied");
    } catch {
      toast.error("Your browser blocked clipboard access");
    }
  }

  return (
    <div className="mt-1.5 text-xs leading-relaxed text-muted-foreground">
      <p>
        This prints a short code and a URL. Approve it from any signed-in
        browser — your phone works:
      </p>
      <p className="mt-1 flex flex-wrap items-center gap-2">
        <Link
          href={href}
          className="interactive font-mono text-brand-foreground hover:underline"
        >
          {shown || href}
        </Link>
        <button
          type="button"
          onClick={copy}
          className="interactive rounded-md border border-border px-1.5 py-0.5 text-[11px] text-muted-foreground hover:bg-surface hover:text-foreground"
        >
          Copy approval link
        </button>
      </p>
    </div>
  );
}
```

Update both call sites to drop the now-unused `hostNoun` prop — line 159-163
becomes `<ApproveCaption origin={origin} poolId={poolId} />`, and line 204
likewise.

Fix this file's retired words and use the shared sentence:
- lines 173-179 (Colab, Cell 3 caption): replace `"it runs this Crew's jobs unsandboxed, directly in the runtime."` so the paragraph reads
  `"Colab can't nest a Docker daemon inside its own container, so trusted is the only runner tier that works here — it runs this workspace's jobs unsandboxed, directly in the runtime. Runs until the cell is interrupted or the runtime disconnects."`
- lines 214-218 (RunPod): `"trusted runs this Crew's jobs unsandboxed on the pod"` → `"trusted runs this workspace's jobs unsandboxed on the pod"`.
- Add `<p className="mt-1.5 text-xs leading-relaxed text-muted-foreground">{RUNNER_TIER_SENTENCE}</p>` once at the top of the component, above `<TabsList>`, so the rule is stated before either tab is chosen.

- [ ] **Step 5: Carry the workspace into the rail's card**

In `components/shell/ConsoleShell.tsx`, the "Add a machine" card (lines 255-274)
links to a bare `/activate`. Where a workspace is in context, carry it.

Add the import:

```tsx
import { activatePath } from "@/lib/activate-url";
```

Change the `<Link href="/activate"` to use the workspace this component has
already resolved. That value is `currentWorkspace` (line 175:
`workspaceIdFromPath(pathname) ?? workspaceHint`) — the same one the rail's
tabs are built from at line 226. It is `string | null`, which is exactly what
`activatePath` takes, so a non-workspace route still gets the bare `/activate`:

```tsx
            <Link
              href={activatePath(currentWorkspace)}
```

Do **not** add a `useWorkspace()` call here — `ConsoleShell` renders outside
the provider on non-workspace routes and that hook throws by design.

Then fix every retired word in this file — there are four, and
`grep -niE "\b(zolli|zollis|crew|crews)\b" components/shell/ConsoleShell.tsx`
is how you confirm you got them all:

- line 63: `machines: { label: "Zollis", icon: Desktop }` → `label: "Machines"`
  (this is the workspace rail's tab label — the most-seen string in the file)
- line 249: `label="My Zollis"` → `label="My machines"`
- line 264: `label="Scout, your guide for adding a Zolli"` → `label="Scout, your guide for adding a machine"`
- line 268: `Add a Zolli` → `Add a machine`

Line 52's `const REPO = "https://github.com/Zolli-Labs/flashml"` is an org
identifier, not interface vocabulary. **Leave it.** The `ZolliCharacter`
component and its import also stay — the character survives as brand
(decision §6.3), and only the words retire.

- [ ] **Step 6: Run the tests**

Run: `npx vitest run lib/activate-url.test.ts && npx tsc --noEmit`
Expected: PASS (7 tests), no type errors.

- [ ] **Step 7: Commit**

```bash
git add flashml-cloud/apps/web/lib/activate-url.ts \
        flashml-cloud/apps/web/lib/activate-url.test.ts \
        flashml-cloud/apps/web/components/pools/ConnectPanel.tsx \
        flashml-cloud/apps/web/components/shell/ConsoleShell.tsx
git commit -m "fix(web): the approval URL is a link now, and it carries the workspace"
```

---

## Task 6: The copy on the screens either side of the checklist

**Files:**
- Modify: `flashml-cloud/flashml-cloud/apps/web/app/(console)/workspaces/page.tsx`
- Modify: `flashml-cloud/flashml-cloud/apps/web/app/(console)/docs/page.tsx`
- Modify: `flashml-cloud/flashml-cloud/apps/web/app/(console)/w/[poolId]/machines/page.tsx`

**Interfaces:**
- Consumes: `EXAMPLE_REPO_URL`, `EXAMPLE_REPO_BLURB` (Task 3);
  `RUNNER_TIER_SENTENCE` (Task 4).

- [ ] **Step 1: `/workspaces` — say that compute comes next**

The screen never hints that a machine is required. In
`app/(console)/workspaces/page.tsx`:

- line 70: `<h1 className="title mt-2">Build your crew</h1>` → `Create a workspace`
- line 72: `"A Crew is where you and the people you invite share Zollis and jobs."` →
  `"A workspace is where you and the people you invite share machines and jobs. You'll attach a machine next — that's what runs the work."`
- line 77: `<CardTitle className="text-sm">Create a Crew</CardTitle>` → `Create a workspace`
- lines 97/98: `placeholder="Crew name"` and `aria-label="Crew name"` → `"Workspace name"`
- line 107: `{submitting ? "Creating…" : "Create Crew"}` → `"Create workspace"`
- line 49: `toast.success("Crew created", …)` → `toast.success("Workspace created", …)`
- line 59: `"Couldn't create that Crew. Try again."` → `"Couldn't create that workspace. Try again."`
- lines 118-119: `"Been sent an invite link? Open it and you'll join that Crew."` → `…join that workspace.`
- line 125: `"Have a Crew invite code?"` → `"Have a workspace invite code?"`

- [ ] **Step 2: Docs — link the sample, and state the tier rule once**

In `app/(console)/docs/page.tsx`:

Add to the imports:

```tsx
import { EXAMPLE_REPO_BLURB, EXAMPLE_REPO_URL } from "@/lib/example-repo";
import { RUNNER_TIER_SENTENCE } from "@/lib/runner-tier";
```

In the `#run` section (around line 190), after the existing "Point Submit at a
GitHub repository…" paragraph, add:

```tsx
            <p className="mt-4 max-w-prose text-sm leading-relaxed text-muted-foreground">
              No repo of your own yet? Start with{" "}
              <a
                href={EXAMPLE_REPO_URL}
                target="_blank"
                rel="noreferrer"
                className="text-brand-foreground hover:underline"
              >
                Zolli-Labs/flashml-examples
              </a>
              . {EXAMPLE_REPO_BLURB}
            </p>
```

At the head of the "From a notebook or a rented pod" subsection (the `<h3>`
around line 133), add the shared sentence so the docs page and `ConnectPanel`
stop giving what reads as two different answers:

```tsx
            <p className="mt-2 max-w-prose text-sm leading-relaxed text-muted-foreground">
              {RUNNER_TIER_SENTENCE}
            </p>
```

Fix every retired word in this file. There are fourteen strings plus one
identifier to leave alone; confirm with
`grep -niE "\b(zolli|zollis|crew|crews)\b" "app/(console)/docs/page.tsx"`:

- line 26: nav entry `{ id: "attach", label: "Add a Zolli" }` → `label: "Add a machine"`
- line 72: `<h2 …>Add a Zolli</h2>` → `Add a machine`
- line 74: `"A machine becomes a Zolli after it is enrolled."` → `"A machine joins your fleet after it is enrolled."`
- line 133 `<h3>`: `"for a Crew"` → `"for a workspace"`
- lines 144-148: `<Link href="/pools">Crew</Link>` → `workspace`
- lines 171-173, the trusted callout: `"No job from outside your Crew ever runs here — argv work is confined to your Crew by three fail-closed checks. Only run this for a Crew you'd hand a shell account to."` →
  `"No job from outside your workspace ever runs here — argv work is confined to your workspace by three fail-closed checks. Only run this for a workspace you'd hand a shell account to."`
- line 200: `"to a job, a Zolli or a page."` → `"to a job, a machine or a page."`
- line 216: `"plus which Zollis contributed to each."` → `"plus which machines contributed to each."`
- line 220: `"One lane per Zolli, one block per attempt, so a Zolli that died mid-task and the Zolli that finished the work both appear."` →
  `"One lane per machine, one block per attempt, so a machine that died mid-task and the machine that finished the work both appear."`

Line 23's `const REPO` and line 277's `Zolli-Labs/flashml` are the org's real
GitHub identifier. **Leave both.**

- [ ] **Step 3: The machines tab headings the checklist links into**

In `app/(console)/w/[poolId]/machines/page.tsx`:

- line 22: `"Serving this Crew"` → `"Serving this workspace"`
- line 24: `"Every Zolli your crewmates have opted in, not only yours."` → `"Every machine this workspace's members have opted in, not only yours."`
- line 36: `"Connect a Zolli"` → `"Connect a machine"`
- lines 38-40: `"No spare laptop? Point a Colab notebook or a rented pod at this Crew instead."` → `"No spare laptop? Point a Colab notebook or a rented pod at this workspace instead."`

**Keep the `id="connect-panel"` div exactly as it is** — the checklist's step 2
links to that anchor, and so does `YourMachines`' empty state.

- [ ] **Step 4: Verify**

Run: `npx vitest run && npx tsc --noEmit && npm run lint`
Expected: whole suite PASS, no type errors, no lint errors.

- [ ] **Step 5: Commit**

```bash
git add flashml-cloud/apps/web/app/\(console\)/workspaces/page.tsx \
        flashml-cloud/apps/web/app/\(console\)/docs/page.tsx \
        flashml-cloud/apps/web/app/\(console\)/w/\[poolId\]/machines/page.tsx
git commit -m "docs(web): the screens either side of the first run mention the machine"
```

---

## Task 7: A guard so the sweep does not come undone

**Files:**
- Create: `flashml-cloud/flashml-cloud/apps/web/lib/console-vocabulary.test.ts`

**Interfaces:** none — a source scan, in the shape `lib/route-exports.test.ts`
already established.

- [ ] **Step 1: Write the failing test**

The scope is deliberately narrow: **the files this plan created**. Widening it
to the whole console is the separate P2.3 vocabulary sweep's job, and a test
that fails on files nobody in this plan touched would just be noise.

Create `lib/console-vocabulary.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";

/**
 * "Zolli" and "Crew" are retired from the interface (owner decision §6.3 as
 * amended, 2026-08-10). "Workspace" is NOT — the UI-says-workspace /
 * API-says-pool split is deliberate and stays.
 *
 * This guards the files the first-run work created, so the vocabulary cannot
 * come back through the surface a new user sees first. The console-wide sweep
 * is its own spec; this is not it, and widening this list is that spec's job.
 *
 * The Zolli character survives as brand/marketing — hence `lib/zolli-brand.ts`
 * and `components/brand/` are not in scope here and must not be added.
 */
const FILES = [
  "lib/first-run.ts",
  "lib/example-repo.ts",
  "lib/submit-form.ts",
  "lib/enrol-steps.ts",
  "lib/runner-tier.ts",
  "lib/activate-url.ts",
  "components/workspace/FirstRunChecklist.tsx",
];

const RETIRED = /\b(zolli|zollis|crew|crews|crewmate|crewmates)\b/i;

describe("first-run surfaces", () => {
  it("use machine and workspace, never the retired nouns", () => {
    const offenders: string[] = [];

    for (const file of FILES) {
      const source = readFileSync(file, "utf8");
      source.split("\n").forEach((line, i) => {
        // The examples repo is a real URL under the Zolli-Labs org — an
        // identifier, not interface vocabulary.
        if (line.includes("Zolli-Labs")) return;
        if (RETIRED.test(line)) offenders.push(`${file}:${i + 1}: ${line.trim()}`);
      });
    }

    expect(
      offenders,
      'the interface says "machine" and "workspace"; "Zolli" and "Crew" are ' +
        "retired from it (ROADMAP.md §6.3). The character survives as brand " +
        "only, in components/brand/ and lib/zolli-brand.ts.",
    ).toEqual([]);
  });

  it("watches every file the first-run work created", () => {
    // A file added to this feature and not to the list above is a hole in
    // the guard, so the count is asserted rather than left implicit.
    expect(FILES).toHaveLength(7);
  });
});
```

- [ ] **Step 2: Run test to verify it passes for the right reason**

Run: `npx vitest run lib/console-vocabulary.test.ts`
Expected: PASS. Then prove it can fail — temporarily add the line
`// a Zolli in a Crew` to `lib/first-run.ts`, re-run, confirm it FAILS naming
that file and line, then remove the line and re-run to green.

- [ ] **Step 3: Commit**

```bash
git add flashml-cloud/apps/web/lib/console-vocabulary.test.ts
git commit -m "test(web): the first-run surfaces cannot regrow the retired vocabulary"
```

---

## Task 8: Correct the spec's stale gate

**Files:**
- Modify: `flashml-cloud/flashml-cloud/docs/superpowers/specs/2026-08-10-first-run-quickstart-design.md` (lines 4, 122-145, 286-288)

The spec's §3 is the load-bearing wrong claim in this feature's paper trail:
it tells the next reader that the sample cannot pass on Colab or RunPod. Leave
the original text visible — amend, do not rewrite — in the same style the
transactional-email spec used.

- [ ] **Step 1: Update the status line**

Line 4: `**Status:** proposed design, awaiting owner review.` →

```markdown
**Status:** approved (owner, 2026-08-10). Implemented by
`plans/2026-08-10-first-run-quickstart.md`.
**AMENDED 2026-08-10:** §3's gate is lifted — see the correction inside it.
```

- [ ] **Step 2: Amend §3 rather than deleting it**

Insert at the top of §3, immediately under its heading, keeping every original
paragraph below it intact:

```markdown
> **CORRECTION, 2026-08-10 — this gate no longer exists.** Everything below
> was true of the *plan file*, whose checkboxes are all unchecked, and false of
> the *repo*. The trusted-tier work landed directly on `flashml` trunk on
> 2026-08-09/10 and is tagged `flashnode-v0.3.5`, which
> `flashml-cloud/Makefile:60` already pins.
>
> - §3 workdir delivery — `flashnode/executor/trusted_runner.py:50`,
>   `executor/runner.py:107`
> - §2 self-quarantine — tier-scoped health checks,
>   `executor/health.py:92-104`, `agent/cli.py:348-368`
> - §2 wrong `--runner docker` login hint — `executor/health.py:79-89`,
>   `agent/cli.py:143-153`
> - §4/§5 image manifest + per-job dependency cooldown —
>   `executor/environments.py:147,151`, `executor/loop.py:55-68,306-336`
>
> The sample (`flashml-examples` `main` → `jobs/hello.py:33`) reads
> `FLASHML_WORK_DIR`, which trusted hosts now set. **This spec may ship and be
> announced.** The paragraphs below are kept as written, unamended, because
> the reasoning about *why* the tier matters is still correct — only the
> sequencing conclusion is stale.
```

- [ ] **Step 3: Amend the closing warning**

Line 286-288, the bolded "Do not announce…" paragraph — leave the sentence and
append:

```markdown
**Lifted 2026-08-10** — that release is `flashnode-v0.3.5` and it has shipped;
see the correction at the top of §3.
```

- [ ] **Step 4: Commit**

```bash
git add flashml-cloud/docs/superpowers/specs/2026-08-10-first-run-quickstart-design.md
git commit -m "docs: the first-run spec's flashnode gate shipped as 0.3.5"
```

---

## Task 9: Repair the example the docs now send people to

**Files (separate repo — `~/Work/Zolli-Labs/flashml-examples`, branch `federated`):**
- Modify: `flashml.yaml`
- Modify: `jobs/fed_train.py`

**Reference implementation:** `~/Work/Zolli-Labs/flashml/examples/federated/`
(`flashml.yaml`, `train.py`, `README.md`) — already correct for v2, and the
source to port from rather than reinvent.

Spec §5 calls this "in scope to not send users at a broken branch". The
`main` branch — the one the prefill and the docs link actually point at — is
fine and needs nothing. This is the `federated` branch, which that repo's
README advertises.

- [ ] **Step 1: Confirm the breakage**

```bash
cd ~/Work/Zolli-Labs/flashml-examples
git checkout federated
grep -nE "^(rounds|min_participants|shards):" flashml.yaml
grep -c chunks_done jobs/fed_train.py
```

Expected: three matches in `flashml.yaml`, and `0` for `chunks_done`. Both are
now refused: `flashml_cloud_api/flashml_yaml.py:44-63` rejects all three keys
by name with a migration message, and preflight's `federated-contract` check
requires `chunks_done`.

- [ ] **Step 2: Port the v2 config**

Replace `flashml.yaml` with the reference at
`~/Work/Zolli-Labs/flashml/examples/federated/flashml.yaml`, keeping this
repo's `name: federated-mlp` and `entrypoint: jobs/fed_train.py` (the reference
uses `train.py` at the root):

```yaml
version: 2
name: federated-mlp

image: pytorch-cpu
entrypoint: jobs/fed_train.py
args: ["--epochs", "8", "--lr", "0.05"]

mode: federated

# How much training to do, in passes over your data — independent of how
# often the model is averaged. `epochs` is the work, `sync_every` is the
# combining, and the round count is derived (epochs / sync_every).
epochs: 5

# Passes between combines. 1.0 is one combine per pass, which is what
# `rounds: 5` used to mean.
sync_every: 1.0

# There is deliberately no shard count and no quorum. `shards` was a guess
# about the fleet made before submitting; the platform now cuts a pass into
# chunks and hands each online machine as many as it can finish.
# `min_participants` is gone with it — a round closes when the chunks that
# came back COVER `sync_every` of a pass, which is the property a headcount
# was standing in for and could not actually deliver.

timeout_seconds: 900
```

- [ ] **Step 3: Add the one field that makes the work count**

**The CLI contract did not change.** The reference `train.py` still takes
`--round`, `--num-shards` and `--shard` (`examples/federated/train.py:180-182`),
and FlashML still appends exactly those three. So `jobs/fed_train.py`'s
argument parsing is fine as it stands — leave it alone. One task trains one
chunk, and that chunk's id **is** `args.shard`.

What is missing is `chunks_done` in `metrics.json`. Find the
`METRICS_OUT.write_text(...)` call and replace its payload with the reference's
(`examples/federated/train.py:237-259`), keeping this file's own `last` and
`samples` variables:

```python
    METRICS_OUT.write_text(
        json.dumps(
            {
                # THE load-bearing field. The platform credits this
                # contribution by the chunk ids reported here, intersected
                # with the ids it handed this task — so an entrypoint that
                # omits it is credited nothing and the round combines an
                # empty set after waiting out its timeout. One task trains
                # one chunk here, hence the single id.
                "chunks_done": [args.shard],
                # Reported for the job view, NOT used as the averaging
                # weight — that is `chunks_done`, which the coordinator can
                # check. A self-reported sample count would let a machine
                # choose its own influence over the averaged model.
                "samples": samples,
                "loss": last,
                "round": args.round,
                "shard": args.shard,
            }
        )
    )
```

Then fix the one error message that names a removed key. Around line 195:

```python
        raise SystemExit(
            f"shard {args.shard} of {args.num_shards} is empty "
            f"({N_SAMPLES} samples total) — reduce `shards` in flashml.yaml"
        )
```

becomes:

```python
        raise SystemExit(
            f"shard {args.shard} of {args.num_shards} is empty "
            f"({N_SAMPLES} samples total) — the fleet was cut into more "
            f"chunks than this dataset has rows"
        )
```

`shards` is no longer a key anyone can set, so an error telling them to reduce
it sends the reader hunting for a typo in a file that cannot contain one.

- [ ] **Step 4: Verify against the simulator, not against production**

```bash
cd ~/Work/Zolli-Labs/flashml
python examples/federated/simulate.py --help
```

`simulate.py:78` asserts `chunks_done` is a non-empty list — it is the cheapest
check that this contract is now met. Adapt its invocation to the ported file,
or copy the assertion into a scratch run of `jobs/fed_train.py`.

- [ ] **Step 5: Commit — and stop there**

```bash
cd ~/Work/Zolli-Labs/flashml-examples
git add flashml.yaml jobs/fed_train.py
git commit -m "federated: move to the v2 chunk contract, which is what the platform now accepts"
```

**Do not push.** `flashml-examples` is a public repo and this plan has no
mandate to publish to it. Leave the commit on the local `federated` branch and
report it as ready for the owner to push.

---

## Manual verification — the proof this feature actually exists for

The failure this spec prevents is a human one, so the tests above do not close
it. From the repo root:

```bash
cd flashml-cloud
./scripts/dev.sh --all
```

Then, with a fresh account admitted through the queue:

1. `/workspaces` — does it say a machine comes next? Create one.
2. Overview — the checklist is there, step 2 is unticked, and its button goes
   to the machines tab's `#connect-panel`, not to submit.
3. Follow the Colab or RunPod tab on a real host. The approve URL is a link;
   click it on a phone and the machine binds to this workspace with no
   second step.
4. Back on the overview, step 2 is ticked. Submit → `Use the example repo` →
   Submit.
5. The job reaches `succeeded`, and the checklist is gone from the overview.

Time step 1 → step 5. That number is TTFJ, the roadmap's activation metric;
the target is **under 30 minutes** including attaching the first machine.

---

## What this plan does not do

- **No house-hosted demo pool** (owner decision §6.4; parked in `ROADMAP.md`).
- **No hosted `.ipynb`** for Colab — a distribution problem, not a console one.
- **No change to preflight, placement, or the submit API.** Nothing here
  touches Python.
- **No console-wide vocabulary sweep.** Each task fixes the file it touches;
  the rest is P2.3's own spec and plan.
- **No queue-position ("you're #N") pending state.** P0.3's remaining half was
  recommended against in the transactional-email work — a number promises a
  speed manual review cannot keep — and the approval email already shipped.
- **No route changes, no identifier renames, no migration.**
