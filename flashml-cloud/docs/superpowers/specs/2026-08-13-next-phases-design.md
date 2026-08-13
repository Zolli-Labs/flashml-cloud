# Next implementation phases — from submission to the agent platform

**Status: approved direction, 2026-08-13.** Written after surveying the merged
state of three sessions (evidence: `2026-08-12-shipped-and-verified.md`,
`2026-08-12-next-phase.md`, `.evidence/`), not from memory.

**This document sequences; it does not re-decide.** Competition requirements
stay owned by `2026-08-11-competition-requirements.md` (G-/D-/C-/X- ids).
Agent-surface decisions stay owned by `2026-08-12-agent-surface-decisions.md`
(AS-1…AS-17). Capacity decisions stay owned by
`2026-08-12-on-demand-capacity-design.md` (OC-1…OC-10). Where a requirement
below descends from one of those, it cites the parent id. Re-opening a parent
decision happens in the parent document, never here.

**New ids minted here:** `AG-*` (agent layer), `MK-*` (market surface),
`OB-*` (observability surfaces), `PF-*` (platform functions), `FT-*`
(future tier — triggers, not dates).

**Priority vocabulary** matches the competition spec: P0 (fails without it),
P1 (core value), P2 (multiplier). **Status** values: `not started` ·
`in progress` · `evidence captured` · `verified`. A requirement is met when
its named artifact exists and re-running produces it again.

---

## 0. Phase map

| Phase | Window | Gate to enter |
|---|---|---|
| **Phase 0** — competition closure | now → 2026-08-15 23:59 PT | none; in flight |
| **Phase 0.5** — the hosted agent (AS-3) | parallel with Phase 0 | AS-15 ordering: lands only behind a live public page |
| **Phase 1** — designed-but-unbuilt features | after submission | submission out the door |
| **Phase 2** — platform substrate | interleaved with Phase 1 | first Phase 1 consumer exists |
| **Phase 3** — future tier | trigger per row | see §5 |

The owner-lane items (G-2 video, G-4 sheet row, console walkthrough, live
testing) are **owner-owned and out of engineering scope** — decided
2026-08-13. They appear nowhere below.

---

## 1. Phase 0 — competition closure (cites existing ids only)

Every row here already has an id in the competition spec; this table records
what *remains* and names the artifact that closes it.

| Parent id | What remains | Pri | Evidence that closes it | Status |
|---|---|:--:|---|---|
| **D-3 / D-8** | The full loop has never run on Alibaba compute. Run `scripts/competition/run_demo.py` once end-to-end against real FC, then three consecutive unattended runs | P0 | Three run logs + `.evidence/` JSON per run, each showing prepare → hibernate → artifact event → wake → accepted evaluation → cleanup | not started |
| **G-1 durability** | Mirrored artifacts go invisible when the free-tier coordinator forgets a job (`files=0` while OSS holds the objects). Serve the listing from `_mirror/manifest.json` when `mirrored_at` is set; coordinator becomes the fallback | P0 | A job listed correctly with the coordinator's registry empty (restart it or use a forgotten job) | not started |
| **C-6.2** | Isolation probe: a task that attempts to read outside its workspace, reach a forbidden host, and see host processes — and fails. Pair with the stated reason: we execute user-submitted repo code | P1 | Probe output: attempted and denied, per surface | not started |
| **D-9** | Cost worksheet from the 2026-08-13 hibernation-modes probe data × published rates, both baselines, incl. the OSS disk-shrink result | P1 | Worksheet with per-figure source trail and units | in progress (probe data captured) |
| **C-6.5 / C-6.4** | Narrative layer over the captured probes: mode chosen **on measured latency**, deep for the long wait, light for the short gap — stated in the evidence bundle, not only raw JSON | P1 | One paragraph per mode in the evidence bundle citing the probe files | evidence captured |
| **hygiene** | `develop` is ~60 commits ahead of origin, on one laptop. Push. Audit `render.yaml` literal-declared vars on both services (the `OSS_ENDPOINT` class). Re-check what the public share URL exposes against the artifact-read findings of the 2026-08 security audit | P0 | `git push` accepted; var audit note; share-URL exposure note | not started |
| **statuses** | Move competition-spec statuses as evidence lands (G-1, D-4 shipped; table still says `not started`). A stale ledger makes agents redo finished work | P1 | Status column current in `2026-08-11-competition-requirements.md` | not started |

**Ordering inside Phase 0:** the D-3/D-8 run first — every other row survives
a bad answer from it; nothing survives the demo not working.

---

## 2. Phase 0.5 — the hosted agent loop (AS-3, decided and unwritten)

`grep` across `flashml_cloud_api/` finds no `ModelProvider`, no DashScope, no
agent loop. AS-3…AS-5 are decisions with zero code behind them. This phase
builds the smallest honest version, sequenced per AS-15 (behind the public
page, never ahead of it) and per AS-3's own reversal clause (P0 gates unmet on
2026-08-14 → drop without ceremony).

| Id | Requirement | Pri | Evidence | Status |
|---|---|:--:|---|---|
| **AG-1** | `ModelProvider` Protocol + fake + DashScope/Qwen implementation (AS-4, AS-5). The fake backs every test; the real one wires only when a key exists. New module; does not touch `sandbox_orchestrator.py` or `alibaba_sandbox.py` (AS-13 ownership) | P1 | Suite green with no key in the environment; one real DashScope call recorded when a key exists | not started |
| **AG-2** | The loop itself, run **as a workload inside a hibernating FC sandbox**: deep hibernation across the long wait, light between tool calls (AS-3). The loop's working state is written as a checkpoint-relay-conventional file (`out/ckpt/step-*.json`) from day one — this costs nothing now and makes FT-1 (fault-tolerant agents) fall out of existing machinery later | P1 | A session log showing turns separated by observed hibernation states, state file relayed | not started |
| **AG-3** | The wallet, wired: `marketplace.can_cover` gains its first caller on the agent's submit path; a per-agent spend allowance (ZC as allowance, OC-10); rented money always stops at a human (AS-9). Refuse, never queue (OC-4). This closes AS-17's "middle layer is empty" | P1 | A test where the agent's submit is refused for insufficient allowance, and a log line showing the refusal is a ledger read (OC-9) | not started |
| **AG-4** | Agent output obeys the provenance rule (AS-16): everything the agent publishes to a shared surface is our typed events, never submitter bytes; anything self-reported renders attributed | P1 | The AS-16.1 test applied to the agent's output path | not started |

**What Phase 0.5 deliberately is not:** multi-agent anything. One agent, one
sandbox, one wallet, one conversation. The multi-agent features live in
Phase 1/3 and depend on this loop existing.

---

## 3. Phase 1 — designed, tested underneath, and unbuilt

Build-ready backlog. Each row has a finished design elsewhere; the work is
execution.

| Id | Requirement | Design source | Pri | Evidence | Status |
|---|---|---|:--:|---|---|
| **MK-1** | Marketplace HTTP surface over `marketplace.py` + `prices.py` (105 passing tests, zero routes today), then the three console pages | `2026-08-12-market-design-v2.md` §5 (API delta is written pytest-first) | P1 | Routes live on dev; the three pages render real rows | not started |
| **OB-1** | Verification read surface: a route returning a job's verdicts and a console panel that states what they are not — observations, never gates; `unknown` renders as "could not tell", never as a pass | `2026-08-12-observability-and-verification-gaps.md` §3 D-4 | P1 | Panel shows a `flag` verdict on a real job with the advisory copy | not started |
| **OB-2** | Two more advisory slices, no new infrastructure: artifact presence, checkpoint monotonicity. (Duplicate-commit detection third.) Advisory only | same doc, G-D | P2 | Verdict rows written for a live job by each slice | not started |
| **AG-5** | MCP surface for agents-in-a-loop: `wait_for` (long-poll on job/artifact/event — kills the poll-spin in `job_events`), `submit_batch`, a cost-quote tool beside `preview_plans`, `trace(correlation_id)` returning the whole D-4 chain in one call, share-link minting (the `shr_` mechanism as a tool), dataset publish/list | this spec | P1 | Each tool exercised by a scripted agent session transcript | not started |
| **AG-6** | Agent principal: per-agent scoped revocable tokens (machine-token machinery is the template) so an agent stops borrowing the human's JWT. Scopes: read, submit-into-pool, spend-up-to. Identity + AG-3 wallet + ledger = the trust boundary for any fleet | this spec | P1 | A token with read-only scope refused on submit; revocation observed | not started |

---

## 4. Phase 2 — platform substrate (three functions every feature above keeps re-asking for)

| Id | Requirement | Pri | Evidence | Status |
|---|---|:--:|---|---|
| **PF-1** | One event push/subscription layer, keyed by correlation id (SSE or long-poll — decide by measuring both against Render's proxy behavior). Console polling, agent polling, and wake-triggers all converge on it. The correlation chain (D-4, shipped) is the key; this is its read path | P1 | Console job page and an agent `wait_for` both served by the same stream | not started |
| **PF-2** | The dependency edge, first-class: "task B becomes claimable when artifact X exists." Today this exists once, hardcoded, inside `sandbox_orchestrator`. Generalized, it is the substrate for pipelines, agent handoff (FT-2), and every wake-on-event feature. Explicitly **not** a DAG engine — one edge type, artifact-exists, nothing else until two more real edge kinds appear (mirrors the restraint in the demo plan §7.3) | P1 | A two-job chain where B was submitted before A finished and ran only after A's artifact committed | not started |
| **PF-3** | Wallets generalized to per-principal (human, agent, pool) with one vocabulary (AS-10): allowance, spend, refusal — every guard a ledger read (OC-9) | P2 | The same `can_cover` path serving AG-3 and a human submit | not started |

---

## 5. Phase 3 — future tier. Triggers, not dates.

Nothing here starts on appetite. Each row names the observable trigger that
un-defers it, in the style of the competition spec's §9.

| Id | Feature | What it is, in one sentence | Trigger |
|---|---|---|---|
| **FT-1** | Fault-tolerant agents | `AGENT` as a workload kind: the session is a leased task, conversation state checkpoints through the relay, a dead sandbox's conversation resumes elsewhere — the flashruntime headline claim applied to agents | AG-2 running in anger and its state file proven restorable by hand once |
| **FT-2** | Sleeping agent teams | Agents hand off by committing artifacts that wake hibernated peers; idle agents bill ~storage only | PF-2 verified + two real single-agent workloads exist |
| **FT-3** | The incident agent | A hibernated agent woken by failure events; walks the correlation chain and verification verdicts; posts a typed, attributed diagnosis; sleeps again | PF-1 verified + OB-1 verified |
| **FT-4** | Topology-aware placement | Venues advertise **measured** bandwidth (intra-cluster vs WAN); tasks declare a bandwidth class; tightly-coupled work routes inside one low-latency venue, fan-out work scatters | a real user workload demonstrably misplaced by the current gates |
| **FT-5** | Data-gravity pricing | Already-cached shards (cache capacity gate, shipped) become a price signal: hosts holding hot data earn more, work routes to the bytes | MK-1 live + dataset cache hit rates observable |
| **FT-6** | Outcome pricing | Sell accepted work, not hours: $/accepted-task quotes from goodput history; later, completion insurance hedged across venues | MK-1 live + per-venue interruption rates measured over ≥2 weeks |
| **FT-7** | WAN-tolerant training | DiLoCo-style local-step/periodic-sync training on the FEDERATED skeleton — one model trained across venues because lease expiry makes stragglers survivable. **Never** claimed as WAN DDP | a measured single-venue baseline of the same model to compare against |
| **FT-8** | Follow-the-price migration | Snapshot a venue-bound job, acquire a cheaper venue, resume from the portable checkpoint (claim ladder's last rung; OC-8 drain-never-cut is the first brick) | ECS adapter has completed ≥10 real rentals + FT-7 sync format settled |

---

## 6. Standing rules this spec inherits (stated so no phase re-litigates them)

1. **The allocator stays deterministic; intelligence stays outside the
   execution path** (AS-1, AS-2). No FT row reverses this.
2. **Advisory never gates** — verification verdicts observe (OB-*); wallets
   refuse (AG-3/PF-3). Those are different layers and stay different.
3. **Provenance** (AS-16): any surface an agent writes to publishes our typed
   events; self-reported values render attributed. The test: *name the line in
   our code that assigned this value.*
4. **Claim ladder** (demo plan §4): each FT row's public claim waits for its
   evidence. No WAN DDP claim, no cross-provider migration claim, no AMD claim
   without a ROCm run.
5. **Runtime changes ship by release**: anything touching `flashruntime` /
   `flashnode` means merge → PyPI → four-site pin bump, never a path install.
6. **Shared checkout discipline**: explicit-path commits only; repo-scoped git
   commands are forbidden; broad work goes to a worktree.

## 7. Evidence register (new ids)

| Artifact | Proves |
|---|---|
| Three unattended full-loop run logs + JSON | Phase 0 D-3/D-8 remainder |
| Listing served from `_mirror/manifest.json` with coordinator amnesiac | G-1 durability |
| Agent session log with hibernation-separated turns and relayed state file | AG-2 |
| Refused submit with ledger-read log line | AG-3 |
| Scripted agent transcript exercising each new MCP tool | AG-5 |
| Two-job chain, B claimable only after A's artifact | PF-2 |
| One stream serving console and agent `wait_for` | PF-1 |
