# Team pools — collaborate on your own resources first

**Date:** 2026-08-03
**Status:** approved design (brainstormed with the owner 2026-08-03).
**Origin:** owner's direction for the first tester release: testers have 1–2
machines plus GPU accounts (Colab, RunPod), and should be able to pool them
in a group and work on that group's combined fleet together — before any
open volunteer marketplace.
**Positioning:** this ships the **"team's own fleet"** model that
`2026-08-02-colab-gpu-pooling-strategy-note.md` §5 and
`2026-08-02-supply-side-positioning-note.md` identified as the adjacent
market. It does not abandon the volunteer network; untagged jobs behave
exactly as today. See the POSITIONING_LOG entry of the same date.

---

## 1. Decisions made during brainstorming

1. **Teams via invites.** A pool is a group: the creator shares one invite
   link; members join and their resources join with them. A solo tester is a
   team of one.
2. **Own-repo jobs, pool-scoped.** The existing submit flow (GitHub repo →
   preflight → job) gains a pool selector. No new demo-launch flow; the
   public federated example repo is the documented starter.
3. **Trusted-pool isolation.** Jobs submitted to a pool may execute via the
   subprocess runner on that pool's workers. Members chose to trust each
   other by joining; the sandbox exists to protect a host from a stranger,
   and inside a pool there are no strangers. The console says this in plain
   words at submit time.
4. **Invite-only alpha.** Result verification enforces nothing yet and pool
   jobs run unsandboxed on members' machines — the cohort must be people the
   owner chose.
5. **Enforcement: a first-class pool gate in the coordinator** (approach 1
   of 3 considered). Rejected: allow-list stamping of machine ids per spec
   (late joiners invisible until the next job; buries "teams" in an
   infrastructure mechanism) and per-team self-hosted coordinators
   (bypasses the console, submit flow and ledger — the product would go
   untested — and someone must host an inbound-reachable endpoint, which
   NAT and Colab make painful).

## 2. What ships

An invite-only alpha of team pools on the **existing deployed stack** — no
new Render services, no new spend; pools are rows in the existing Supabase
Postgres plus code on the three services already deployed. Testers' compute
is their own.

- A tester signs in, creates a pool, shares one invite link. The invite does
  double duty: it admits a new account through the alpha's signup gate AND
  joins it to the pool. An account that has never consumed an invite can
  sign in but sees only an "enter invite" screen — admission is enforced in
  this API, not in Supabase configuration.
- Members attach workers through the existing device-code enrolment:
  - **Laptop/desktop** — today's flow unchanged, Docker sandbox where
    available.
  - **Colab** — a documented notebook: install `flashnode`, print the
    device-code URL, run `flashnode work --runner subprocess`. The notebook
    and docs state plainly this is for **paid Colab only** (§8).
  - **RunPod** — the same three commands in a pod, documented with a
    template.
- A machine's pools are **inherited from its owner**: join a pool and all
  your enrolled workers serve it. No per-machine assignment in v1.
- Any member submits a repo job to the pool; it runs only on the pool's
  workers. The jobs page shows which member's machine did what, backed by
  the existing contributions ledger — the per-member credit view is the
  collaboration payoff.

## 3. Architecture

**flashml-cloud (private) owns all pool truth.** Three new tables with the
same deny-by-default RLS as the rest of the schema:

- `pools` — id, name, owner sub, created_at.
- `pool_members` — pool id, user sub, joined_at. Owner is also a member row.
- `pool_invites` — token, pool id, created_by, expires_at, uses_remaining.

Console: a `/pools` page (create, invite link, member list, workers online
now) and a pool selector on `/submit`.

**The API stamps, the coordinator enforces.** On the register/heartbeat
proxy — the same operator-asserted path that already forces `node_id` — the
API **overwrites** the node's `capabilities.pools` with the machine owner's
memberships, resolved from Postgres. Never merged with anything the agent
sent: an agent-supplied value is discarded, so a node cannot claim its way
into a pool. Because the field is injected server-side, **released
flashnode agents (0.3.2) work unchanged** — only the coordinator must speak
the new protocol. A membership change propagates to all of a member's
workers at their next heartbeat; no re-enrolment.

**flashruntime (public) gains the seventh placement gate**, riding the
pending 0.4.2 release train alongside ExecutionEvidence and the
node-exclusion gate:

- `NodeCapabilities.pools: list[str] = []`.
- Task payload: `pool: str | None = None`, stamped by `compile.py`.
- Gate: a task with `pool` set places only on a node whose `pools` contains
  that exact id. A node view with a missing, empty, or type-confused
  `pools` is refused. **Fail-closed polarity** — follow the local-data and
  GPU gates' pattern, not the module gate's, and do not "harmonize" (the
  scheduler docstring's standing warning). A task without `pool` never
  engages the gate: today's behaviour, bit for bit.

**Isolation reuses existing machinery — no new tier logic.** Pool jobs
compile as `{"tier": "sandboxed", "allowFallback": true}`; `allowFallback`
already means "waive the sandbox *tier* requirement" and waives no other
gate. The standing rule in `compile.py` ("a submitter can never downgrade
the isolation their own arbitrary code runs under") becomes a coupled
invariant:

> **`allowFallback` may be true if and only if `pool` is set.**

Unsandboxed placement can therefore never escape the pool boundary. Both
directions are enforced at compile time and pinned by test.

## 4. Data flow — one pool job, end to end

1. Member joins via invite link → `pool_members` row; the signup gate admits
   the account if it is new.
2. They enroll any worker with the existing device code; every
   register/heartbeat proxy stamps the owner's pool ids into the node view.
3. Another member submits a repo job and picks the pool → `compile.py`
   stamps `pool: <id>` and `allowFallback: true`.
4. The coordinator's pool gate places tasks only on nodes listing that
   pool; a Docker-less Colab worker qualifies because `allowFallback`
   waives the sandbox tier. Subprocess GPU access needs no `--gpus` flag,
   sidestepping the still-unproven Docker GPU executor path.
5. Completion → the existing attempts/contributions ledger credits each
   member's machine → the job page shows per-member contribution.

## 5. Errors and edge cases

- **Empty or offline pool.** An unplaceable job sits PENDING while every
  claim answers 204 — the symptom shape PROGRESS records as the worst kind
  (2026-08-02, "the volunteer docs printed a command that can never claim
  work"). The submit page therefore shows *eligible workers online right
  now* for the chosen pool and warns before submitting to zero.
- **Member leaves or is removed.** The next heartbeat re-stamp drops the
  pool from their nodes; they claim nothing new. In-flight leases finish or
  expire naturally — no revocation machinery in v1.
- **Colab preemption mid-task.** The designed case: lease expires, task
  requeues to another pool worker, checkpoint resume where the workload
  supports it.
- **Old coordinator.** The gate ships in flashruntime 0.4.2. The deploy
  order rule from PROGRESS (2026-08-02, "render.yaml is not the deployed
  truth") applies: merge to main, Blueprint sync, then deploy — a 0.4.1
  coordinator would silently drop the `pool` field under `extra="ignore"`,
  which for a pool job means placing it on ANY node. The e2e suite must
  therefore include a test that fails when the coordinator predates the
  gate (same pattern as `test_gpu_placement`: keyed on a feature probe, not
  a version string).
- **Doctor gate.** `flashnode work --runner subprocess` already skips the
  Docker doctor checks (the gate sits inside the `docker`/`argv` branch of
  `agent/cli.py`) — verified 2026-08-03, no change needed.

## 6. Testing

Unit:
- Gate polarity — fail-closed on missing/empty/type-confused `pools`;
  untagged tasks unaffected.
- The compile invariant, pinned in both directions: `allowFallback` without
  `pool` refused; `pool` implies `allowFallback: true`.
- API stamping — agent-sent `pools` is overwritten, never merged; membership
  changes appear at next heartbeat.
- RLS on the three new tables (deny-by-default, member-scoped reads).
- Invite consumption — expiry, uses_remaining, double-consume.

E2E, against a real coordinator:
- Two pools, one agent each: pool A's job is claimed only by A's agent while
  B's idles beside it (and the converse).
- A subprocess-only worker claims a pool job and never a public sandboxed
  one.
- The invite → join → enroll → contribute loop, credited in the ledger.

## 7. Prerequisites (the release train)

1. Fix the flaky `test_fedavg_survives_a_closed_laptop` fixture (round 0
   with `min_participants=2`, then rounds 1–2 with 1) — it gates production
   CI.
2. Release flashruntime 0.4.2 (ExecutionEvidence + node exclusion + pool
   gate) and flashnode 0.3.3 with floor `>=0.4.2,<0.5` — the floor rule
   PROGRESS already recorded the hard way.
3. Move the four pin sites together; Blueprint sync before deploy.

## 8. The Colab terms-of-service constraint

Google's Colab FAQ (read 2026-08-02) prohibits "running distributed
computing workers" on the **free** tier by name, and prohibits "using
multiple accounts to work around access or resource usage restrictions" on
**all** tiers. The ban lands on the tester's Google account, not on us.
Paid plans explicitly lift the distributed-workers restriction.

Rules this design follows:
- The Colab notebook prints a paid-tier-only warning at the top; the docs
  repeat it.
- Nothing is built or documented toward pooling free accounts, and nothing
  automates multiple accounts.
- Re-read the FAQ before public launch; the quotes are dated.

RunPod needs no such caveat: renting compute to run compute is the product
being sold.

## 9. Corrections (2026-08-03, found while planning — recorded, not rewritten)

Three claims above did not survive contact with the code. The intent stands;
the mechanism moved. Implementation plan:
`../plans/2026-08-03-team-pools.md`.

1. **§3/§7 "rides the pending 0.4.2 release" — stale the same day.** A
   concurrent session released flashruntime 0.4.2 and flashnode 0.3.3 and
   moved the pins while this spec was being written. The pool fields and
   gate land in **flashruntime 0.4.3** instead (additive-in-0.4.x precedent:
   local_datasets 0.4.0, GpuInfo 0.4.1, ExecutionEvidence 0.4.2). §7's
   prerequisite items 1–2 are already done.

2. **§3 "no new tier logic" is wrong for repo jobs, which are argv jobs.**
   Two released rules block the trusted-pool path as written:
   `CommandRecipe.expand` refuses `allowFallback` for command jobs
   outright, and the placement argv gate is deliberately NOT waived by
   `allowFallback` — argv places only on `argv_capable` (containerised)
   nodes. So `allowFallback` alone can never put a repo job on a Docker-less
   Colab/RunPod worker. The mechanism becomes a **host-side opt-in**:
   `NodeRegistration.unsandboxed_argv_capable` (fail-closed, default
   False), set only by `flashnode work --runner trusted`, plus a
   `TrustedArgvRunner` that executes argv payloads without a container
   (rewriting the `/work` prefix onto the real workdir). The argv gate
   accepts the trusted alternative only when all three legs hold: task is
   pool-scoped AND carries the waiver AND the node opted in.
   `CommandRecipe` keeps refusing the waiver for non-pool jobs — the
   coupled invariant of §3, enforced upstream too. Consequence: **flashnode
   0.3.4 ships after all** (floor >=0.4.3). §3's "released agents work
   unchanged" narrows to Docker-capable hosts on 0.3.3; Docker-less hosts
   need 0.3.4, which is what a fresh `pip install flashnode` resolves
   anyway.

3. **§5 "membership propagates at next heartbeat" needed a wire field.**
   `NodeHeartbeat` carries no capabilities, so there was nothing to stamp.
   0.4.3 adds `NodeHeartbeat.pools: list[str] | None` (None = no statement;
   a list replaces the registration's pools wholesale), stamped by the same
   proxy hop.

Also recorded: the wire already had a dead `PlacementSpec.pool` (a closed
Literal of infrastructure names, read by nothing) and a singular
`NodeRegistration.pool` deployment label. The JobSpec carrier is the former,
widened to `str`; the latter is unrelated to teams and stays untouched.

## 10. Deliberately not in v1

Roles beyond owner/member; per-machine pool assignment; pool quotas or
billing; lease revocation on leave; Kaggle; any change to result
verification (thread 4 stays open — a pool trusts itself, which is the
alpha's honest posture); the desktop app (neither Colab nor RunPod needs an
installer, per the supply-side note).
