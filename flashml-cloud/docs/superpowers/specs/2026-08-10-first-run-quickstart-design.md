# First run — attach a machine, then run the sample

**Date:** 2026-08-10
**Status:** proposed design, awaiting owner review.
**Repos touched:** `flashml-cloud` only (console copy + one zero-state
component + one prefill). **Depends on a `flashnode` release — see §3.**
**Roadmap item:** P0.2 (`ROADMAP.md`), reshaped by owner decision §6.4.

**Origin.** The owner decided on 2026-08-10 that FlashML hosts **no compute
for users** — every machine is theirs (Colab, RunPod, or hardware they own).
That makes the first run a two-part act: *get a machine attached*, then *run
something*. The console today only helps with the second part, and points
every empty-state button at it.

**The one-sentence problem.** A newly-admitted user is shown a screen that
says "Build your crew", a workspace overview whose only two buttons are
`New job` and `Submit a job`, and a submit form whose placeholder is a
fictional `acme/trainer` — while the thing actually blocking them is that
they have no machine, which nothing on that path mentions until they reach a
tab they have no reason to open.

---

## 1. Decisions

1. **The first-run guide is the workspace overview's zero-state, not a new
   route.** It becomes a three-step checklist that ticks itself off from real
   state and disappears when complete. No new nav item, no wizard to escape
   from, no route to keep alive after it stops being useful. Rejected: a
   `/w/[poolId]/start` page (a route that is dead weight the day after
   onboarding) and a modal after workspace creation (dismissable exactly
   once, then unreachable).
2. **Compute comes before the job, everywhere.** Step 2 is *attach a
   machine*; step 3 is *run the sample*. Today both zero-state CTAs point at
   submit — the one action guaranteed to produce a job that queues forever.
3. **The sample is `Zolli-Labs/flashml-examples` at default branch.** It
   exists, it is CPU-only, stdlib-only, `python-slim`, and its README calls
   `main` "the smallest job that proves the whole path". Nothing new to
   author or maintain. In the submit form this is a **prefill**, not a
   separate code path — one button that fills the two existing fields.
4. **Submit stays unblocked when no machine is online.** It already warns;
   the warning is correct and a hard block would be wrong (queueing ahead of
   a machine you are about to start is legitimate). The fix is that the user
   should have been offered the machine step first, not that submit should
   refuse.
5. **The runner tier is a property of the host, not a preference,** and every
   surface must say so in the same words: a laptop with Docker uses
   `--runner argv`; Colab and rented pods cannot nest Docker and use
   `--runner trusted`. The console's three surfaces currently give three
   different answers, and `flashnode login` prints a fourth that is wrong on
   every host — that last one is `flashnode`'s to fix (§3).
6. **No house-hosted demo pool, now or in this spec.** Owner decision §6.4.
   The guide's job is to make *your own* machine easy to attach.
7. **Vocabulary is `machine` and `workspace`** per owner decision §6.3 as
   amended. Every string this spec adds or rewrites drops "Zolli" and "Crew".
   This overlaps the vocabulary sweep; whichever ships first, the other must
   not reintroduce them.

---

## 2. What exists today — the journey, and its five traps

The path, in the order a real user walks it:

| Step | What they see | file:line |
|---|---|---|
| Admitted | Whole nav appears at once; no breadcrumb across the transition | `ConsoleShell.tsx:191` |
| No workspace | `/workspaces` — "Build your crew… where you and the people you invite share Zollis and jobs" | `workspaces/page.tsx:69-130` |
| Workspace, no machines | Overview: `0 machines online`, three zero stats, `No jobs in this Crew yet.` Buttons: **`New job`** and **`Submit a job`** | `overview/page.tsx:35-134`, `WorkspaceHeader.tsx:34-44` |
| Submit | Two fields; placeholder `https://github.com/acme/trainer`; warns `0 Zollis online… the job will queue until one connects` | `submit/page.tsx:168-226` |
| Machines tab (unprompted) | `ConnectPanel` — Colab and RunPod tabs with real commands | `machines/page.tsx:37-40` |

**Trap 1 — the compute step is never offered.** `/workspaces` does not
mention machines, compute, or hardware anywhere. The overview zero-state's
two CTAs both go to submit. `ConnectPanel` lives behind a tab.

**Trap 2 — the sample does not exist in the product.** `flashml-examples`
is not linked from the submit form, the docs page's "Run a job" section, or
anywhere else in the console. The user's first job must be code they wrote,
against a `flashml.yaml` they have never written, validated by a preflight
that (correctly) refuses networking imports.

**Trap 3 — four different runner instructions.**

| Surface | Says | file:line |
|---|---|---|
| Docs page, laptop path | `--runner argv` | `docs/page.tsx:97-130` |
| Docs page, BYO path | `--runner trusted` | `docs/page.tsx:135-177` |
| ConnectPanel (Colab + RunPod) | `--runner trusted` | `ConnectPanel.tsx:126-229` |
| `EnrolInstructions` | **omits `flashnode work` entirely** | `EnrolInstructions.tsx:56-66` |
| `flashnode login`'s own printed hint | `--runner docker` — wrong on every BYO host | noted at `ConnectPanel.tsx:68-73` |

Docs-vs-ConnectPanel is not actually a contradiction — they describe
different host types — but nothing says that, so it reads as one.
`EnrolInstructions` is a real defect: follow it and you enrol a machine that
enrols, heartbeats, and never claims anything. Getting the tier wrong fails
silently and "reads as 'no work available' rather than as a
misconfiguration" (`docs/page.tsx:105-115`).

**Trap 4 — the approve URL is plain text.** `ConnectPanel`'s caption tells
the user to visit `{origin}/activate?pool={poolId}`, rendered as prose — not
a link, not a copy button (`ConnectPanel.tsx:74-97`). That `?pool=` is
load-bearing: with it, approval enrols **and** binds to the workspace in one
atomic call; without it the machine is account-only and must be ticked in
manually, which only `YourMachines.tsx:125-127` explains.

**Trap 5 — the published `federated` example is now invalid.** Its
`flashml.yaml` uses `rounds` / `min_participants` / `shards`, which the v2
schema now **refuses** (`PROGRESS.md:227`, 2026-08-10). Not on the
quickstart path, but it is a broken link in a repo we are about to send
every new user to.

**Two earlier concerns, checked and dismissed:** `/machines` is a permanent
redirect to `/account/machines` (`next.config.ts:54`), so the `/activate`
success links work. And `cloudApiBase()`'s `localhost:8000` fallback cannot
reach production — `next.config.ts` throws at build time if
`NEXT_PUBLIC_CLOUD_API` is unset, which makes the trusted-tier spec §7's
worry about that stale.

---

## 3. The dependency that gates this whole spec

**The BYO path does not work end-to-end today, and no console change can fix
it.** Owner decision §6.4 makes Colab and RunPod the front door; both must
use `--runner trusted`; and the trusted tier has two open defects designed
in `specs/2026-08-09-trusted-tier-execution-contract-design.md` whose plan
(`plans/2026-08-09-trusted-tier-runner-contract.md`) is **written and
entirely unexecuted** — every task still `- [ ]`, no `PROGRESS.md` entry.

The one that bites this spec directly is **§3, workdir delivery**: a
workload that resolves its own output directory from `FLASHML_WORK_DIR`
writes to a literal host `/work/out` on a trusted host, exits 0, and the
attempt fails with *"task produced no metrics.json"*. That is **exactly what
the sample does** — `flashml-examples/jobs/hello.py:33` reads
`os.environ.get("FLASHML_WORK_DIR", "/work")`.

So: **on Colab or RunPod today, the sample job fails.** Also §2 — a trusted
host self-quarantines after three failures and blames Docker.

**Sequencing, therefore:** trusted-tier §2/§3 → a `flashnode` release →
`NODE_VERSION` in the `Makefile` → *then* this spec's console work is
truthful. The console work can be built and merged in parallel; it must not
be *announced* to users before the agent release. This is what `ROADMAP.md`
means by the trusted-tier fixes being on the P0 critical path.

---

## 4. The first-run checklist

Replaces the overview's zero-state (`overview/page.tsx:115-134`). Renders
only while incomplete; each step derives from state the page already
fetches.

| # | Step | Done when | Action |
|---|---|---|---|
| 1 | **Create a workspace** | always, on arrival | — (pre-ticked; it orients rather than instructs) |
| 2 | **Attach a machine** | `machines.length > 0` | `Connect a machine` → machines tab, `#connect-panel` |
| 3 | **Run your first job** | `jobs.length > 0` | `Run the sample` → submit, prefilled (§5) |

Rules that keep it honest:

- **Step 3 is not disabled when step 2 is incomplete** — it is
  de-emphasised, with the existing submit-page warning left to do its job.
  Decision 4: queueing ahead of a machine is legitimate.
- **A machine that is attached but offline** shows step 2 ticked with a
  secondary line — "attached, none online right now" — because the fix at
  that point is to start the agent, not to attach another machine.
- The whole block unmounts once steps 2 and 3 are both done. No dismiss
  control, no persisted "onboarding complete" flag, nothing to reset.
- Copy names the constraint plainly, once, at the top: *FlashML runs your
  jobs on machines you attach — a Colab notebook, a rented pod, or your own
  hardware.* This is the sentence the product currently never says.

---

## 5. The sample

**On the submit page** (`submit/page.tsx`), beside the repo field: a
`Use the example repo` control that sets `repo` to
`https://github.com/Zolli-Labs/flashml-examples` and leaves `ref` blank
(blank ⇒ `main` ⇒ the hello-world job). One line of copy: *A stdlib-only
CPU job that finishes in seconds — the fastest way to prove the path.*

Deliberately a prefill of the existing fields, not a separate submit path:
the user sees exactly what they would have typed, the same preflight runs,
and there is no second code path to keep working.

**Also fix the placeholder** from the fictional `acme/trainer` to the real
example URL, so the form teaches the shape even when the button is ignored.

**Upstream housekeeping in `flashml-examples`** (separate repo, small):
repair the `federated` branch's `flashml.yaml` to v2 (`epochs` + `sync_every`)
and confirm its entrypoint emits `chunks_done`, since preflight's
`federated-contract` check now requires it. Out of scope to *design* here;
in scope to not send users at a broken branch.

---

## 6. Making "attach a machine" survivable

Console-side only; the `flashnode`-side hint is §3's.

1. **`EnrolInstructions` gains the missing fourth step** —
   `flashnode work --coordinator <base> --runner argv` — with the existing
   argv warning from `docs/page.tsx:116-130`. Without it the component
   teaches a dead end.
2. **Every surface states the host→tier rule in the same sentence.** One
   shared constant, used by `EnrolInstructions`, `ConnectPanel` and the docs
   page: *Your own machine with Docker → `--runner argv`. Colab or a rented
   pod (they cannot nest Docker) → `--runner trusted`, which runs
   unsandboxed and only ever runs jobs from this workspace.*
3. **The approve URL becomes a real link and a copy button**
   (`ConnectPanel.tsx:74-97`), carrying `?pool=` so approval binds to the
   workspace atomically. Prose is the wrong control for a URL you must
   visit on a second device.
4. **The rail's `Add a machine` card** (`ConsoleShell.tsx:255-274`) links to
   `/activate` with no `?pool=`. Where a workspace is in context, carry it.

---

## 7. Copy changes elsewhere

- **`/workspaces`** — one sentence added: a workspace is where jobs and
  machines are shared, *and you will attach a machine next*. Currently the
  screen never hints that compute is required.
- **Docs page "Run a job"** (`docs/page.tsx:182-202`) — link the example
  repo. Two paragraphs today, no example, no `flashml.yaml` sample.
- All new and touched strings use **machine** / **workspace** (§1.7).

---

## 8. Out of scope

- A house-hosted demo pool (owner decision §6.4; parked in `ROADMAP.md` as a
  post-launch growth lever).
- A hosted `.ipynb` for Colab. There is none today — the flow is three
  copy-paste cells. A one-click notebook is the obvious follow-up and is a
  distribution problem, not a console one; the trusted-tier spec §7 makes
  the same call about a RunPod template image.
- Any change to preflight, placement, or the submit API.
- Email (P0.1, its own spec) and the vocabulary sweep (P2.3, its own spec).
- `flashnode`'s printed post-login hint — trusted-tier §2.

---

## 9. Testing

Console tests follow the existing web test conventions
(`lib/*.test.ts`, vitest).

1. Checklist renders with step 2 unticked when the workspace has no
   machines; with step 2 ticked and step 3 unticked when it has a machine
   and no jobs; and **not at all** when it has both.
2. A machine attached but offline ⇒ step 2 ticked, offline note shown.
3. `Use the example repo` sets the repo field to the examples URL and leaves
   `ref` empty; submitting then calls `submitFromRepo` with `ref: undefined`.
4. The submit page's zero-machines warning still renders and still does
   **not** disable the button (guards decision 4 against a later "fix").
5. `EnrolInstructions` renders four steps and the fourth contains
   `--runner argv`.
6. The ConnectPanel approve URL renders as an anchor whose href contains
   `?pool=<poolId>`.
7. No string added by this spec contains "Zolli" or "Crew" — a cheap
   regex test over the touched components, which also serves P2.3.

**Manual demo that closes the plan** (and is the real proof, since the
failure this spec exists to prevent is a human one): a fresh account, on a
real Colab or RunPod host, from admission to a green sample job, following
only what the console tells them. **Requires the `flashnode` release from
§3.**

---

## 10. Implementation plan

`plans/2026-08-10-first-run-quickstart.md`, after the trusted-tier plan.

1. The checklist component + overview zero-state replacement (§4) — tests 1, 2.
2. Sample prefill + placeholder on submit (§5) — tests 3, 4.
3. Attach-a-machine surfaces: `EnrolInstructions` fourth step, shared
   host→tier sentence, ConnectPanel link + copy (§6) — tests 5, 6.
4. Copy elsewhere (§7) + the no-old-vocabulary test (test 7).
5. `flashml-examples`: repair the `federated` branch (§5).

**Do not announce to users until the `flashnode` release in §3 has shipped**
— until then the console would confidently walk someone into a sample job
that cannot pass on the very hosts it recommends.
