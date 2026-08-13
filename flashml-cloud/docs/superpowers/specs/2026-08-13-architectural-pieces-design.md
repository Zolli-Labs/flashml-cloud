# The pieces that need a decision, not a rushed build

**Status: design note, 2026-08-13.** Written while the contained platform pieces
(wait_for, cost-quote, PF-3) build. These four are deferred *deliberately* — each
crosses a boundary (the public runtime, a hot path, an infra measurement, a
product contract) where the honest move is to design and let the owner decide,
not to momentum-dispatch an agent. Each section ends with **the decision** it
needs.

Parent spec: `2026-08-13-next-phases-design.md` (PF-1, PF-2, and follow-ups #16,
#12).

---

## 1. PF-1 — the event stream. Decide the transport before building it.

**What it is:** one push/subscription layer keyed by `correlation_id`, so the
console's polling, an agent's `wait_for`, and wake-triggers all read from one
place instead of each polling separately.

**Why it's not a quick build:** the spec itself says *"SSE or long-poll — decide
by measuring both against Render's proxy behavior."* That measurement hasn't been
done, and it is load-bearing:

- **SSE** gives true push (no poll latency, one connection) but Render's proxy
  and the free-tier coordinator's restarts make a long-lived connection fragile —
  a dropped SSE stream that silently stops delivering looks like "nothing is
  happening" and is the worst failure mode for a judge watching a demo.
- **Long-poll** (what `wait_for` already does, bounded at 60s) is proxy-safe and
  restart-tolerant — each request is short-lived — at the cost of up-to-poll
  latency and more requests. It reuses machinery that already works.

**The honest current state:** `wait_for` (built this session) is the first
concrete consumer and it is a long-poll. So PF-1's long-poll path is *already
partly real*; the SSE path is the unmeasured bet.

**Decision needed:** measure a long-lived SSE connection through Render's proxy
(does it survive 5 minutes? a coordinator restart?) before committing to it. If
SSE is fragile there, PF-1 is "generalize `wait_for`'s long-poll to a
correlation-keyed multiplexer," which is a contained cloud change. If SSE holds,
it's a bigger build. **Do not build the SSE version on the assumption it works —
that assumption is the whole risk.** The console migration note in the demo plan
(§8, "do not migrate polling to SSE for the deadline") already reached this
conclusion once.

---

## 2. PF-2 — the dependency edge. It lives in the public runtime, and that is the whole caution.

**What it is:** "task B becomes claimable when artifact X exists," generalized
from the one hardcoded instance inside `sandbox_orchestrator`. It is the
substrate for pipelines, sleeping-agent handoff (FT-2), and every wake-on-event
feature.

**Why it is the riskiest item here:** the claim/placement gate — the code that
decides whether a node may claim a task — lives in **`flashruntime.scheduler`, in
the public repo**, consumed by `flashml-cloud` as the pinned `flashruntime==0.6.0`
dependency. A dependency edge that gates claiming has to be enforced *there*, at
the seventh placement gate, alongside pool membership and capability checks.

That means PF-2 is a **cross-repo change with a release in the middle**:

1. Add the edge to `flashruntime.protocol` (a task/job carries an
   `awaits: [{artifact: "..."}]` field) and enforce it in the scheduler's claim
   path.
2. Release `flashruntime` to PyPI (a new pinned version).
3. Bump all **four** pin sites (Makefile, both render.yaml coordinators, the API
   pyproject) in one commit — the rule the workspace `CLAUDE.md` is built around.

**This is exactly the shape of change that shipped the 2026-08-01 drift bug**
(`images.py` present in one copy, absent in the mirror). A dependency gate that
the coordinator enforces but the API doesn't understand — or vice versa — is a
job that hangs forever (B never becomes claimable) or claims too early (B runs
before A's artifact exists). Both are silent.

**Decision needed:** confirm this is worth a runtime release before the deadline
(it competes with the demo for the same release window), and confirm the design:
- The edge is **artifact-exists only** — not a general DAG, per the spec's own
  restraint ("one edge type, nothing else until two more real edge kinds appear").
- Enforcement is **fail-closed** — an unsatisfied `awaits` makes the task
  unclaimable, the same posture as an unmet pool membership.
- "Artifact exists" is checked against the **artifact store** (OSS manifest /
  coordinator listing), the same source `trace` and the listing route already
  read — not against a control-plane opinion.

Until that release, the honest position is: the one hardcoded edge in
`sandbox_orchestrator` works for the demo; the general edge is roadmap.

---

## 3. OB-2 wiring (#16) — three sub-parts, only one is cheap.

The OB-2 slice *functions* are built and tested (`f0995c5`). Wiring them into the
settlement path so they actually write verdicts breaks into three, and they are
not equally cheap:

**(a) The constraint migration — cheap, do first.** `migrations/0006:69`
constrains `slice in ('timing','evidence','redundancy')`. A migration `0028`
widening it to include `'artifact-presence'` and `'checkpoint-monotonicity'` is a
one-line forward-compatible schema change. It orphans nothing (no writer emits
those slices yet). **Blocker for both, and it is trivial.** (Note: `0027` is now
taken by agent principals, so this is `0028`.)

**(b) artifact-presence — a hot-path fetch.** The slice needs the task's
registered artifacts and the store's actual listing. Neither is in scope at
`attempt_complete` (app.py:8390) — the coordinator's reply there is only
`{"accepted": bool}`. Getting them is one HTTP round-trip to the coordinator
artifact listing / OSS manifest. **The design question is whether to put that
round-trip on the settlement hot path** (every accepted task pays it) or run it
asynchronously after settlement. Recommendation: async / deferred — a verification
verdict is advisory and never gates, so it must never slow the thing it observes.

**(c) checkpoint-monotonicity — needs instrumentation that doesn't exist.**
Nothing records a task's checkpoint step *history* today. `POST .../checkpoints/
commit` (app.py:8489) is a bare proxy; the coordinator's `CheckpointCatalog` is
in-memory and only answers `/latest`. Wiring this slice means **building a
per-task step-history record** (hook the commit endpoint to parse the manifest's
`step` and append it somewhere durable). That is a real feature, not a wiring.

**Decision needed:** do (a) now (trivial); schedule (b) as async post-settlement;
treat (c) as its own small feature (step-history table + a commit hook) or drop
it — it is the least cheap and the least load-bearing of the three.

---

## 4. FAILED-task artifact durability (#12) — a product-contract question.

**What surfaced (during the G-1 durability work):** a FAILED task's diagnostic
artifacts — e.g. `shard-001/stderr.txt`, the "why did it fail" bytes — are
**never mirrored to OSS**, because the mirror is accepted-work-only (hard rule 4).
So they are reachable only while the coordinator remembers the job. After a
free-tier coordinator restart, they are gone from the product — precisely the
artifact someone returning to a finished, failed job wants most.

**Why it's a decision, not a bug:** mirroring failed-task output changes the
mirror's contract (accepted-work-only is a deliberate rule, tied to what counts
as billable/durable). The two honest resolutions are different products:

- **(a) Mirror failed diagnostics under a separate, clearly-non-accepted
  prefix** (e.g. `jobs/{id}/_diagnostics/`), so "why it failed" survives without
  polluting the accepted-artifact set or the billing/goodput accounting.
- **(b) Surface the gap honestly in the UI** — "failure logs are available only
  while the run is live" — and don't mirror. Cheaper, less useful.

**Decision needed:** which product this is. (a) is the better answer if failed
jobs are things people investigate later; (b) if a failed run is a throwaway. Not
a fabrication-safe quick fix either way — the G-1 work deliberately did *not*
paper over it by inventing the missing key.

---

## What is NOT deferred (built this session, for contrast)

Everything contained and well-specified shipped: the agent trust boundary
(AG-1/2/3/6), the trace surface (cloud route + MCP tool), OB-1's read surface +
panel, OB-2's slice functions, the console refactor, and — landing as this note
is written — `wait_for`, `cost-quote`, and PF-3's generalized approver. The four
above are deferred because each one's risk is real and specific, not because they
are hard.
