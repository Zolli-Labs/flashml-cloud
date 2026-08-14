# Gap map and agent briefs — 2026-08-14

One brief per gap, written so an agent can start without re-deriving the
grounding. Every claim below was verified against the code on 2026-08-14
(develop @ ba6dd6a unless a branch is named). Sources: the four research
sweeps run this day, plus `docs/research/2026-08-13-automatic-routing-
marketplace-matching.md` and `docs/research/2026-08-02-distributed-training-
landscape.md`.

## House rules that bind every track

- **Read `AGENTS.md` first**, then the brief. Workspace `CLAUDE.md` carries the
  runtime-pin law: four sites move together, never a relative-path install.
- **Submission drives knobs.** Everything user-facing derives from
  `flashml.yaml` or an explicit API parameter. Internal tables resolve; they
  never decree.
- **Copy competitors and improve; never pitch a moat.** The research docs name
  what to steal per feature — start there.
- **Verify through the authoring surface.** A green unit suite on hand-built
  specs proves nothing; every track ends with a real `flashml.yaml` submitted
  through the real path.
- **Shared checkout:** work in a worktree (`git worktree add .worktrees/<name>
  -b <branch> develop`). Commit with pathspecs. Never `git add -A`, stash,
  reset, or clean.
- **Fail-open on submit** for anything hooked into the submit path (mirror
  `_human_spend_guard`, `app.py:5007-5031`).
- **One-door boundary:** only `flashml_cloud_api/placement.py` imports the
  runtime (`test_import_boundary` enforces it).
- Doubled path reminder: the API lives at
  `flashml-cloud/flashml-cloud/apps/api/` — repo root contains a directory of
  the repo's own name.

## Status board

| Track | What | Effort | State |
|---|---|---|---|
| A | Smart routing: land phase 1, unlock GPU classes, stats-aware ranking | M | **CLAIMED — in progress (this session)** |
| B | Console preflight: send the entrypoint, name the remedy | S | open |
| C | `flashml data prepare` + `verify` (stratified/IID-by-construction splits) | M | open |
| D | DiLoCo engine (Slice 5) + kill-a-node hackathon demo | M/L | open |
| E | LoRA adapter payload + content-addressed node-side model cache | M | open, after D |
| F | Agent Wallet + remediation contract (the AI-native ship) | S+M | open |
| G | Webhooks + signed training receipts | S+S/M | open |
| H | Measurement: flashnode probes + heartbeat RTT | M | open — prerequisite for islands/hierarchy |
| I | Chaos certification (`chaos:` flag + resilience report) | M | open, pilot-scoped |

Claim a track by adding your branch name to this table in your first commit.

---

## Track A — Smart routing (CLAIMED)

Goal: priced pool jobs routed against the live listing book with GPU classes
unlocked and ranking that uses the stats we already collect.

State of the world, verified:

- Live path is FIFO + nine boolean gates (`flashruntime/scheduler/__init__.py:441-620`).
  GPU is matched by **count** (live at pin 0.6.0 for command-recipe jobs),
  dataset cache by bytes. No CPU/RAM/VRAM sizing, no geo, no RTT, no bandwidth
  anywhere on the live path.
- The priced matching engine (`flashml_cloud_api/marketplace.py`: 8-class
  ladder, `effective_price = ask ÷ acceptance_rate`, water-fill `match_bid`,
  unproven ¼-share cap) is tested and dormant — no route creates a bid.
- Branch `feat/pool-routing-phase1` (12 commits, all six plan tasks done,
  plan at `flashml-cloud/docs/superpowers/plans/2026-08-13-pool-routing-phase1.md`)
  wires `price:` → classify → plan → bid → grant at submit, plus
  `GET /jobs/{id}/routing` and an e2e. Unmerged.
- Its GPU refusal (`routing.py::GpuRoutingUnavailable`) cites a pin gap that
  is **closed**: the installed flashruntime 0.6.0 declares
  `ResourcesSpec.gpuPerTask` and `recipes/command.py` stamps
  `payload["gpus"]` (verified in the API venv 2026-08-14). Only
  `compile.py`'s docstring is stale.
- Unused stats, already computed: `metrics.acceptance_rates` returns
  `median_seconds` per (machine, class); `reliability_view` counters exist
  runtime-side. Ranking uses acceptance rate only.

Owner-gated questions live in the research doc §7 (over-entitlement factor,
publish-the-formula, one-cap-for-rented). Do not resolve them silently.

## Track B — Console preflight: supply the entrypoint, name the remedy

Goal: "Check this config" runs the full check set for templates, and the
ENTRYPOINT-NOT-SUPPLIED warning tells the user the remedy when it does fire.

Grounding (all verified):

- `POST /v1alpha1/preflight` already accepts `entrypoint` / `entrypoint_path`
  (text, ≤1 MiB body) — `app.py:6206-6310`, worker `_preflight_supplied_workload`
  at `app.py:452-543`. When entrypoint is present it materialises a temp tree
  and runs the same `preflight()` as from-repo. Nothing to change API-side.
- The console never sends it: `preflightConfig()` builds `{config, pool}` —
  `apps/web/lib/cloud-api.ts:1795-1805`, called from `handleCheck()` in
  `app/(console)/w/[poolId]/submit/page.tsx:144-173`.
- Templates ship YAML without entrypoints **by design** —
  `lib/deploy/templates.ts:24-27` and `TEMPLATE_SUBMIT_GAP` at `:488-503`.
- The demo-suite entrypoints exist in the public repo next to each yaml
  (`flashml/examples/demo-suite/*/`); train.py is 15.9 KB — fits the body cap.
- No console page mentions `flashml check` (grep of `apps/web` = zero hits).

Scope: (1) add optional `entrypoint`/`entrypoint_path` to `preflightConfig`;
(2) second textarea (or template-supplied text) in
`components/deploy/YamlPanel.tsx`; (3) ship each template's entrypoint text in
`DEPLOY_TEMPLATES` (deliberate reversal of the yaml-only decision — say so in
the commit); (4) when the warning still fires, render the remedy line naming
`flashml check <dir>`. Console cannot be seen by agents — verify with the
`preview/*.render.tsx` harness, never edit `middleware.ts`.

## Track C — Developer-local data tooling

Goal: `flashml data prepare <input> --out <dir>` and `flashml data verify`,
as a `flashml[data]` extra in the public repo.

Why (the code-level argument): the control plane path-sorts manifest entries
and slices **contiguously** (`flashml_cloud_api/elastic.py::dataset_chunks`).
A class-per-directory dataset therefore yields class-pure shards — the worst
case for federated averaging — deterministically. The platform never reads
user bytes, so only a local tool can fix this.

Scope for `prepare`: seeded global shuffle by default (that is 90% of the
win and covers LM/regression); `--stratify-by <column>` explicit, **never
auto-detected**; `--holdout N%` writing the existing `holdout/` convention;
`--shards K` / `--target-shard-mb N` with under-sharding warnings ("K shards
means at most K machines"); sha256 per entry; manifest in the exact
`_resolve_https_manifest` shape with a `provenance` block (seed, stratify
key, tool version, per-shard label histograms — extra manifest keys are
compatible today and the revision digest pins them). Print the two-entry
`datasets:` block and the upload command; do not touch hosting.

Scope for `verify`: anonymous ranged-GETs of a sample of entry URLs from the
developer's machine, hashes checked against the manifest — productizing
`scripts/competition/publish_dataset.py --verify`. Today an unreadable bucket
is discovered by volunteers burning lease attempts.

Steal from: HF `train_test_split(stratify_by_column=)`, sklearn
`StratifiedKFold` (k folds = k shards), Flower's `plot_label_distributions`
(copy the report, invert the purpose), WebDataset `ShardWriter` UX.
Streaming fallback: per-class round-robin dealing across K writers.

Do NOT build: PII scanning (point to Presidio), local preflight parity (the
one-authority doctrine in `workspace.py` and the preflight route docstring is
deliberate), anything that hosts bytes. Second phase, separate decision:
`flashml simulate` (productize `examples/federated/simulate.py`, kill
injection included) — blocked on the flashml-must-not-depend-on-flashruntime
packaging question; put the options in front of the owner first.

## Track D — DiLoCo engine (Slice 5) + the hackathon demo

Goal: sparse-sync local-SGD as a parameterization of the existing FedAvg
driver, demoed by killing an anchor mid-round on camera.

Grounding: `flashruntime/flashml_workloads/fedavg_driver.py` already has
rounds-as-jobs, coverage-based round close, late-delta discard, dead-driver
resume — ~80% of DiLoCo's control flow. The landscape doc §6.5 specs the
delta: outer Nesterov, int8 pseudo-gradients, over-selection, safetensors
codec. `sync_every` is currently locked at 1.0.

The demo: 100-300M model across the four Alibaba anchors (pool
`zolli-anchors`), kill one mid-round, coverage close carries the round, model
converges. This is fault-tolerance-as-product on Alibaba infrastructure.

Surface (per submission-drives-knobs): `execution.sync: {strategy: diloco,
local_steps: N, compress: int8, round: {coverage, min_participants, timeout}}`
in flashml.yaml. Runtime changes land in the public repo ⇒ release + 4-site
pin bump before the cloud can consume them; rehearse with
`make e2e-setup LOCAL=1`, which is not release evidence.

## Track E — LoRA adapter payload + node-side model cache

Goal: adapter-only federated fine-tuning (10-50 MB per round instead of full
checkpoints) and the one new primitive it needs: a content-addressed blob
cache on the host agent ("task needs blob sha256:…; fetch if absent, reuse").

Sequence after Track D — it is the same engine with a different payload.
The cache also relieves coordinator egress for every mode; design it with
presigned-R2 fetch in mind (same pattern the dataset-hosting decision chose).
Steal from Flower's FlowerTune line and OpenDiLoCo-with-LoRA.

## Track F — Agent Wallet + remediation contract

Goal: make the platform safe and convergent for a coding agent driving it.

Grounding: `agent_identity.py`, `agent_wallet.py` (AgentAllowance /
SpendAllowance / spend-approver), and `agent-principals` routes exist,
unshipped as a product; the MCP server already refuses unchecked submits and
unapproved spend. Preflight findings are `{level, code, message}` — codes are
machine-readable, `message` is prose, and there is **no remediation field**.

Scope: (1) productize principal minting `{budget_usd, ttl, scopes}` with
server-enforced refusal past the box (402 carrying `remaining_usd` and a
grant URL); (2) extend findings and job-failure events with
`fix: {kind: patch|yaml|command, ...}` — preflight already knows the answer
for `writes-outside-out`, `no-metrics-json`, `unknown-import`; serialize it.
Wire both through MCP. This is the "first platform whose primary customer is
a coding agent" claim, enforced server-side.

## Track G — Webhooks + training receipts

Goal: let external systems build on ZolliAI without polling, and emit one
signed lineage artifact per job.

Grounding: events are poll-only (`/events?since=` cursor). Receipts aggregate
what already exists: `contributions`, `verifications`, `trace`,
`checkpoints/lost-work`, artifacts, cost. Scope: `POST /v1alpha1/subscriptions
{url, events[], secret}` with HMAC signing and retries, backed by the events
table; `GET /jobs/{id}/receipt` returning signed JSON. The receipt doubles as
funding-deck material and the supply-side credit story. Public share reuses
the existing share machinery.

## Track H — Measurement (prerequisite, do before any hierarchy)

Goal: the platform can tell a LAN pair from a transatlantic one.

Grounding: `flashnode/flashnode/benchmark/__init__.py` names four probes
(`cpu_hash_mbps`, `mem_bandwidth_mbps`, `disk_write_mbps`, `net_down_mbps`)
with zero implementations and no caller; `NodeCapabilities` has no field to
carry results; nothing measures RTT. Routing doc Phase 2 wants heartbeat RTT.

Scope: implement the probes, run at enrol + periodically, extend the
protocol (public repo ⇒ release + pin bump), record per-node `net_down`,
`rtt_ms`. Consumers come later (verification thresholds, checkpoint-heavy
placement preference, island auto-formation). Do not build islands here —
just make the numbers exist.

## Track I — Chaos certification (pilot)

Goal: `chaos: {kills: N, min_interval_s: S}` on a job runs it under scripted
churn and returns a resilience report (recovery latency per kill, lost work
per death via `checkpoints/lost-work`, checkpoint-cadence advice).

The runtime already survives kills; the work is orchestrating deliberate ones
and formatting the report. Reuse the elasticity-probe and recovery-latency
tooling built for the hackathon. Pilot scope: one flag, one report, one
write-up. This converts churn — free for us, expensive for owned fleets —
into the product's proof.

---

## Explicitly rejected (do not pick these up)

- Fleet **interactive** inference serving / KV-sticky sessions — deferred
  until batch inference proves supply quality; needs a gateway, sticky
  leases, SLOs we don't have.
- Petals/SWARM pipeline parallelism, synchronous DP/ZeRO-3 over WAN, MoE
  expert parallelism — physics and fleet size; see landscape doc §4.
- Open auctions, clearing prices, capacity futures, staking — routing doc §5.5.
- NL-magic submission bypassing flashml.yaml — generate and show the yaml
  instead (Track F's philosophy).
- PII scanner, local preflight ruleset copy, platform-hosted datasets —
  Track C's rejects, reasons there.
- A third GPU classifier — unification is a prerequisite, not a variant.

## Coordination

- Log completions in `PROGRESS.md` per its logging protocol.
- Anything touching the public repo's runtime/agent needs a release and the
  four-site pin bump (see workspace `CLAUDE.md`) — plan it, don't improvise.
- Owner-decision items are marked in each brief; collect them rather than
  deciding silently.
