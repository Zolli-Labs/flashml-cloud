# Zolli AI × Alibaba — hackathon submission dossier

**Status:** working document, 2026-08-11. Companion to
`plans/2026-08-11-alibaba-competition-demo.md`. That plan says *how we build
it*; this says *what we claim, why, and what we put in the sheet*.

**Event:** Beta × Alibaba Cloud × AMD · AI Agent Builder Challenge.

**Rule for this document:** every factual claim is either (a) verified against
this account/codebase on the date shown, (b) quoted from official Alibaba
documentation with a doc id, or (c) explicitly labelled `UNVERIFIED`. Nothing
here is recalled from memory. If the deadline forces a claim we cannot source,
we cut the claim, not the sourcing.

---

## 1. Hard deadlines and disqualifiers

From `daniel@betauniversity.org`, 2026-08-10 20:50 PT (supersedes the Aug 4
mail's Aug 10 cutoff):

| Item | Value |
|---|---|
| Submission cutoff | **2026-08-15, 11:59 PM PT** |
| Top 20 announced | 2026-08-17 (email) |
| Demo Day | 2026-08-22, Beta House, 555 Hamilton Ave, Palo Alto |
| Submission sheet | `docs.google.com/spreadsheets/d/1DBnsDEDsZRwFIyC5AdjFsKuiD2JDIwKEBBdTQmFYJvY` |

**Calendar discrepancy, resolved:** the mail calls Aug 15 and Aug 22 "Friday".
Both are **Saturdays** in 2026 (verified). The dates govern; treat Friday Aug 14
as the working deadline and submit then.

**Auto-disqualifiers** (Aug 4 mail, still in force):

1. Live URL that **opens without a login**.
2. Demo video ≤ 3 minutes, publicly viewable.
3. One-sentence product description: WHO + WHAT + WHY NOW.
4. Submitted before the cutoff. Late penalty −5 points per 5 minutes.

**Scoring shape:** 5 keywords; 10 dimensions; dimension 6 (FC Sandbox core
advantage validation) is stated as the single biggest axis, with six
sub-capabilities of which 6.5 hibernation/wake carries a double star.

### 1.1 Two disqualifier-adjacent gaps in our current state

- **We are not in the sheet.** As of the 2026-08-11 03:20 snapshot, 19 teams
  occupy rows 3–21. There is no Zolli/FlashML row. Whatever is in the sheet at
  cutoff is what gets judged. **Claim the row with placeholders today.**
- **A judge cannot reach the product.** `apps/web/middleware.ts` redirects
  everything except the `(marketing)` routes to `/sign-in`, and access is
  `access_requests` → manual admin approval. Judging happens over a weekend.
  Either someone watches the approval queue continuously Aug 15–17, or we ship
  a **read-only public demo route** showing one completed session end to end.
  The second is smaller and de-risks both the auto-DQ and dimension 2
  ("first success in 5 min").

---

## 2. Verified account and platform facts

### 2.1 Our account, checked live 2026-08-11 via the Alibaba Cloud MCP CLI

| Check | Result |
|---|---|
| Identity | AccountId `5055584162230015`, **root** principal |
| Site | **International**. China-site BSS endpoints return `AuthSiteFail`; `business.ap-southeast-1.aliyuncs.com` works |
| Cash balance | **USD 0.00** available (`QueryAccountBalance`) |
| Function Compute | **Activated and reachable in `ap-southeast-1`** — `GET /2023-03-30/functions` returned `{"functions": []}` |
| Credit | USD 300 voucher `501018860410110`, Cash Coupon, general use, valid **2026-07-16 → 2026-08-15 00:00:00** |

Two consequences, both load-bearing:

- **The voucher is a drawdown, not single-use.** Checkout on a USD 50 order
  shows `Coupon − USD 50.00`, and the voucher listing displays face value and
  remaining balance as separate amounts. Spending $50 leaves $250. Verify after
  the first purchase via *View Deduction Details*.
- **Cash balance is $0.00 and the voucher dies before Demo Day.** Official
  billing doc (3045213): *"After an overdue payment occurs, FC Agent Sandbox
  instances may be automatically stopped and no longer provide runtime
  capabilities."* Judging is Aug 15–17 and Demo Day is Aug 22, both **after**
  voucher expiry. Without a funded balance the demo stops itself during
  judging. See §7.

**Security note, for our own hygiene and for dimension 7:** the MCP connection
authenticates as **root**, not a scoped RAM user. Before we publish anything
about least privilege, replace it with a RAM user holding only the FC, OSS,
and SLS actions this work needs.

### 2.2 FC Agent Sandbox — official documentation

**Regions** (doc 3045189): `cn-beijing`, `cn-shanghai`, `cn-hangzhou`,
`cn-shenzhen`, `cn-hongkong`, `ap-southeast-1`, `us-east-1`, `us-west-1`.
GA landed first in China North 2 (Beijing).

> **`us-west-1` is Silicon Valley.** Demo Day is in Palo Alto. Competitors
> (SeaWeb, Verity, Knovo) all used `ap-southeast-1`. Task 0 must measure wake
> latency in **both** and we pick on the number, not on convention. A visible
> lag on stage is a scored defect under 6.5.

**Pause/resume is allowlist-gated** (doc 3045170, verbatim):

> "Pause and resume is currently available only to allowlisted accounts.
> Complete allowlist enablement before calling `sandbox.pause()` or connecting
> to a paused Sandbox."

This is the P0 external dependency. It has an approver we do not control.

**`Sandbox.connect(sandboxId)` auto-resumes a paused sandbox** (doc 3045170).
The lifecycle controller is simpler than the plan assumed — no separate resume
call.

**Exact integration constants** (doc 3045135) — these close TODOs in the plan:

```bash
export E2B_API_KEY="<from FC console, doc 3045205>"
export E2B_API_URL="https://api.<region>.e2b.fc.aliyuncs.com"
export E2B_DOMAIN="<region>.e2b.fc.aliyuncs.com"
```

```
pip install e2b==2.31.0 e2b-code-interpreter==2.8.1
```

Built-in template: `code-interpreter-v1`.

**Three hibernation states, not two** (doc 3045181):

| State | Preserved | Resume latency | Fit for us |
|---|---|---|---|
| Active | everything, running | — | evaluating |
| **Light hibernation** | runtime context | **millisecond-level** | short gaps between eval waves |
| **Deep hibernation** | runtime context, lower cost | **~1 second** | waiting for training to commit a model |

Doc 3045181 also names exactly what to record: `sandboxId`, task ID, template
version, current phase, key file paths, key process/port info — plus a
lightweight health check after resume. That is the plan's evidence ledger,
independently specified by the vendor. Cite it.

### 2.3 The billing model changed on 2026-07-31 — the plan's cost math is stale

Announcement doc 3047104; rates doc 3045213. Three plans:

| Plan | Active | Deep hibernation | Light hibernation |
|---|---|---|---|
| Eco | ✅ | ❌ | ❌ |
| Std | ✅ | ✅ | ❌ |
| **Pro** | ✅ | ✅ | ✅ |

Two things that directly affect us:

- *"The revised pricing structure is exclusively valid for FC Agent Sandbox
  clients utilizing the **E2B SDK** integration."* We are using the E2B SDK.
  Correct by construction — do not use AgentRun Sandbox or Sandbox Functions.
- *"Existing E2B SDK instances will be **automatically transitioned to the Pro
  tier**."* Pro is the only tier with **both** hibernation modes.

**Open question, `UNVERIFIED`, and it is the most valuable one we have.** Doc
3045170 says E2B `pause()` needs an allowlist; doc 3047104 says E2B SDK users
auto-transition to Pro "enabling immediate access to Shallow Hibernation".
These may be two different mechanisms (the E2B-compatible `pause()` API versus
FC-native hibernation tiers), or the tier transition may itself satisfy the
gate. **Task 0 answers this empirically in one call.** In parallel, ask
directly — doc 3047104 gives a DingTalk group for tier configuration:
**179855020297**.

Billing is also still **invite-only preview**, and until independent
pay-as-you-go is GA, sandboxes bill under ordinary *Function* pay-as-you-go
rules. So the rates below are for **estimation**; the invoice is the truth.

---

## 3. The money model, from published rates

Preview pay-as-you-go, International site (doc 3045213). Pro tier:
vCPU **$0.01872**/vCPU/hr · memory **$0.009360**/GiB/hr · disk
**$0.00031896**/GiB/hr in mainland China, **$0.00025308** outside.

Billing rules that shape the arithmetic:

- Active: vCPU + memory + disk, with **15 GiB free disk**.
- Light hibernation: **vCPU stops**. Memory + disk, 15 GiB free disk still applies.
- Deep hibernation: **vCPU and memory stop**. Disk only — and the free
  allowance **does not apply**, with occupied disk computed as
  `memory × 2 + disk`.

For our evaluator shape (2 vCPU, 4 GiB, 30 GiB disk), computed:

| State | Mainland $/hr | Saving vs active | Outside mainland $/hr | Saving |
|---|---:|---:|---:|---:|
| Active | 0.079664 | — | 0.078676 | — |
| Light hibernation | 0.042224 | **47.0%** | 0.041236 | 47.6% |
| **Deep hibernation** | **0.012120** | **84.8%** | 0.009617 | **87.8%** |

### 3.1 The honest version, which is also the stronger one

A naive reading is "deep hibernation saves 85%." That is true **against the
right baseline and only against that one**. Stated carefully:

**Baseline A — keep the prepared evaluator active while it waits.** This is
what an unthinking implementation does, and it is precisely the rubric's own
anti-pattern ("sandbox spinning idle", "high-frequency polling to fake
waiting"). Against A, deep hibernation saves **84.8%** of the wait cost.

**Baseline B — destroy the sandbox and rebuild it when the model is ready.**
Here the arithmetic goes the other way, and we should say so before a judge
does. A 90-second rebuild costs about **$0.0020** of active time. Deep
hibernation costs **$0.01212/hr**. So:

| Wait | Active | Deep hibernation | Destroy + rebuild |
|---|---:|---:|---:|
| 30 min | $0.0398 | $0.0061 | $0.0020 |
| 2 h | $0.1593 | $0.0242 | $0.0020 |
| 6 h | $0.4780 | $0.0727 | $0.0020 |

**Beyond roughly 10 minutes of waiting, destroy-and-rebuild is cheaper in raw
dollars than deep hibernation.** So the case for hibernation is not "it is the
cheapest possible thing." It is:

1. **~1 second versus ~90 seconds** to be ready. When a training job may commit
   its model at any moment, the evaluator has to be ready *now*, and a 90×
   latency gap is the difference between a pipeline and a queue.
2. **Exact state continuity** — warm caches, loaded dependencies, live process
   identity — which a rebuild cannot reproduce and which our marker/PID check
   proves rather than asserts.
3. **85% off the baseline anyone actually ships**, which is Baseline A.

Volunteering Baseline B before we are asked is worth more than the 85% headline
alone. It is the difference between a team that measured and a team that
quoted, and it is the sort of thing that survives live Q&A.

**Every number above is modelled from published preview rates, not billed.**
Reconcile against the real invoice before the number goes on a slide, and label
modelled figures as modelled.

---

## 4. The field — what 19 teams built and how they used Alibaba

Source: `Ship The Next Hackathon.xlsx`, snapshot 2026-08-11 03:20, rows 3–21.

### 4.0 Tracks are free text, and a third of the field is disqualifiable

**The Track column carries no data validation.** Verified against the
workbook XML: `has dataValidation: False`. No dropdown constrains it, and no
Beta email in the archive defines an official list. "Vertical Agents" (11) and
"Software for Agents" (4) dominate — presumably what registration offered — but
four teams typed free text and nothing stopped them.

| Track string | Teams |
|---|---:|
| Vertical Agents | **11** |
| Software for Agents | 4 |
| `Ai Agent search Infra` (SeaWeb) | 1 |
| `infrastructure` (Decision Infrastructure) | 1 |
| `Software` (Advan) | 1 |
| `Video AI, Vertical applications` (Firassa) | 1 |

**Completeness against the stated auto-disqualifiers:**

| Team | Video | Deck | Code | **Live URL** | Cost |
|---|:--:|:--:|:--:|:--:|---|
| Quacker | ✅ | ✅ | ✅ | ❌ | 40.00 |
| MentorAI | ✅ | ✅ | ✅ | ❌ | — |
| Proofjury | ✅ | ✅ | ✅ | ❌ | — |
| Polybius | ✅ | ✅ | ✅ | ❌ | — |
| Decision Infrastructure | ✅ | ❌ | ❌ | ❌ | — |
| Aegis | ❌ | ❌ | ❌ | ❌ | — |

Six of nineteen rows fail a hard requirement — five have **no live URL**, one is
empty. **Thirteen complete submissions exist today against twenty slots.**

**Strategic consequence, and it is the most important line in this document:**
if the field lands anywhere near its current shape, **reaching the Top 20 is
likely for anyone who submits completely.** The $2M is decided on stage on
Aug 22. So: be ruthlessly complete (the missing live URL is the single most
common failure in this sheet — see §1.1), then optimise everything else for
Demo Day delivery and live Q&A rather than for sheet aesthetics.

**Every entrant is an LLM-agent application.** Healthcare intake, merchant
settlement, fact-checking, mentoring, crawl/search, idea validation,
voice/image generation. **No team runs GPU work, training, HPO, or
checkpointing.** No entrant's workload actually needs compute; they need API
calls. That is the gap we occupy.

### 4.1 Service adoption

| Service | Teams | Read |
|---|---:|---|
| FC Agent Sandbox / AgentRun | **14/19** | table stakes, not differentiation |
| Qwen / Model Studio / DashScope | 12/19 | near-universal logo |
| ECS | 5 | ordinary, uncontroversial |
| OSS | 5 | |
| SLS | 4 | |
| Function Compute (non-sandbox) | 4 | |
| RAM | 4 | |
| ACR · MNS · VPC · EventBridge · PAI · ACK · CloudMonitor | 2 each | |
| Tablestore · Hologres · AnalyticDB · NLS · CosyVoice · Wanx | 1 each | logo-maximising outliers |

### 4.2 Reported spend — the column we would currently fill with $0.00

SeaWeb **201.62** · MentorAI ~101 (in prose) · Firassa **100.00** · Advan 50.00
· Quacker 40.00 · Unbounded Minds 30.00 · Knovo 20.00 · Verity 19.00 ·
ButterOffice 10.98 · Wimbly / AgentLoop / Adina 1.00 · Inzo 0.00 · seven blank.

Median among reporters ≈ $25. SeaWeb has consumed roughly two-thirds of the
same $300 credit we hold.

### 4.3 The two to beat

**SeaWeb — $201.62, infra track.** Their sheet cell is written as a rubric map:
FC Sandbox + AgentRun, standing-query waits → PauseSession deep hibernation
(no idle poll) → quantified $ save → ResumeSession with same SessionID and
checkpoint · MicroVM isolation for untrusted extract · E2B API compat via
endpoint swap · elasticity stress-tested · OSS/skills mount · SLS + Trace ID
across hibernate/wake · FC metrics · live CloudMonitor alert · real debug story
· Qwen · RAM. They knew the rubric and wrote to it.

**Verity — $19.00, repo named `verity-fc-hibernation`.** Deep hibernation on
*both* waits, region `ap-southeast-1`, and a published elasticity number:
**80 concurrent, 14.3 per second**. Cheapest path to a high score in the field.

**LoopChat** wrote the best one-sentence description in the sheet; §6.2 borrows
its shape. **Decision Infrastructure** is the only team touching AMD (EPYC
Turin, Instinct) — and their row has no deck, no code link and no cost.

### 4.4 What this implies

We do not out-logo SeaWeb; they have eleven services and a two-week head start
on spend. We win, if we win, on **being the only team whose workload is real
compute** — and on being the only team that can show a job surviving the death
of the machine running it.

---

## 5. Positioning — Zolli AI × Alibaba

### 5.1 The thesis

> **Zolli AI is a reliability and economic control plane for ML work over
> fragmented compute supply.** Teams already own some machines, rent others,
> and borrow the rest. Zolli turns that mixture into one pool, routes each
> piece of work to the venue that can finish it most cheaply, and preserves the
> logical job when any individual machine disappears.

Two customer-facing reasons, in this order:

1. **Utilization** — use the capacity you already own before renting more;
   burst only for the deficit.
2. **Survivability** — cheap capacity is not cheap if a failure erases the run.
   Leases, checkpoints, and typed recovery make interruptible capacity usable.

### 5.2 Where Alibaba fits, and why it is not decoration

The naive story is "we added Alibaba to a list of clouds." The real one is
sharper, and it is what makes hibernation load-bearing rather than a box we
ticked:

| Tier | Cost while working | **Cost while waiting** | Ready in |
|---|---|---|---|
| Owned laptop / workstation | power only | power — always on | instant |
| Alibaba **ECS** (rented) | $/hr | $/hr — paid for nothing | instant |
| Alibaba **FC Agent Sandbox** | $/hr | **~15% of active, hibernated** | **~1 s deep, ms light** |

**FC Agent Sandbox is the only tier in the fleet whose price collapses while it
waits and comes back in about a second.** An allocator optimising cost per
accepted task will therefore choose FC precisely for wait-heavy phases. That is
not a hackathon narrative; it is the reason a third tier exists in the design
at all.

### 5.3 The claim ladder — what we may and may not say

| Claim | Status |
|---|---|
| One lease runtime across owned and manually attached heterogeneous machines | **Ships today** |
| FC Sandbox joins as a stateful, hibernatable evaluation worker while FlashRuntime preserves the job | **This submission** |
| Zolli can acquire and release capacity on demand | Roadmap — needs a provider adapter |
| Zolli chooses capacity by effective cost and completion risk | Roadmap — needs price and interruption data |
| Zolli resumes a provider-bound training job on another provider | Roadmap — needs a migration qualification suite |

Say the first two. Label the last three as roadmap, out loud, on the slide.
`ROADMAP.md` §7 currently forbids a wallet, payouts, and a marketplace, and
`contributions.py` structurally prevents a credit balance. The marketplace idea
is real and the `ResourceProvider` seam already exists for it — but it is not
this week's claim, and asserting it with zero provider adapters is the
overclaim that loses a Q&A.

### 5.4 What we can demonstrate that no other entrant can

1. The same logical ML task survives a **real worker death** — lease expiry,
   checkpoint restore, different node, one accepted commit.
2. **Attempted work and accepted work are distinct**, with exactly-once result
   acceptance and no double counting.
3. One pool spans **owned machines, an Alibaba ECS box, and a hibernating FC
   sandbox** under a single worker protocol.
4. Placement is constrained by GPU, data locality, dependencies, isolation,
   pool membership, and health evidence — a real scheduler, not a dispatcher.
5. The money story is **useful completed work per dollar**, with the
   inconvenient baseline (§3.1) stated rather than hidden.

Phrase all of this as what our demo proves. Do not assert that competitors lack
it — their submissions are not audited.

---

## 6. Submission artifacts

### 6.1 Sheet row — fill today, refine until Aug 14

| Column | Value |
|---|---|
| Team Name | **Zolli AI** |
| Project Name | **FlashML** |
| Track | **Infrastructure for Agents** — see §6.1.1 |
| Live link | public demo route, no login (§1.1) |
| Demo Video | ≤3 min, YouTube unlisted |
| Deck | 3 slides per §6.3 |
| Code | public `Zolli-Labs/flashml` |
| Alibaba Use | §6.4 |
| Alibaba Cost Spent | real, itemisable figure (§7) |

#### 6.1.1 Why "Infrastructure for Agents", and not bare "infrastructure"

The column is free text (§4.0), so this is a choice, not a constraint.

- **It is true.** We are the only entrant whose workload is actual compute
  rather than API calls. Claiming a vertical would be the false claim.
- **It keeps the word "Agents".** This is an *AI Agent Builder Challenge*. A
  bare "infrastructure" label invites "where is the agent?" as the first
  question, from a judge who has not read the architecture yet.
- **It leaves the crowded lane.** Eleven of nineteen sit in Vertical Agents,
  where scoring turns on scenario realism and persona — LoopChat's clinic story
  is strong there and that is their ground, not ours. Infra scoring turns on
  technical depth, reliability, and economics, which is ours.
- **It speaks to who is judging.** Alibaba Cloud's Compute & Storage team and
  AMD engineers ran the July office hours. These are infrastructure people.
- **Precedent exists and the lane is nearly empty.** SeaWeb — the strongest
  entrant — already deviated to `Ai Agent search Infra`, hedging exactly this
  way. The only other infrastructure claimant, Decision Infrastructure, has no
  deck, no code, and no live URL.

**Prepare the "where is the agent?" answer, because it will be asked.** The FC
sandbox worker *is* an autonomous execution agent: it wakes on an external
event, claims work from a queue, executes, validates its own output against a
semantic contract, commits exactly once, and self-quarantines on repeated
failure. Agent in the execution sense, not the conversational sense. This is
risk R10 in the companion plan; the answer belongs on the slide, not improvised
on stage.

**One free action that removes the remaining doubt:** ask Daniel or Discord
whether tracks are a closed list. One line, fast answer. Until then write
"Infrastructure for Agents" — the sheet is editable until the cutoff.

### 6.2 One-sentence description (WHO + WHAT + WHY NOW)

> For ML teams burning rented GPU hours on jobs that fail halfway and on
> evaluators that idle while they wait, **FlashML is a runtime that pools the
> machines you already own with rented capacity, survives any machine dying
> mid-job through leases and checkpoints, and uses Alibaba Cloud FC Agent
> Sandbox deep hibernation to hold a prepared evaluation environment at ~15% of
> active cost until a model is actually ready to score.**

WHO: ML teams. WHAT: fault-tolerant runtime over pooled compute. WHY NOW:
fragmented supply plus interruptible capacity makes wasted and lost compute the
dominant cost.

### 6.3 Three slides

1. **Team** — Zolli AI, who we are, one line on why we build infrastructure.
2. **Product** — the three-tier table from §5.2, the architecture diagram, and
   the "works today / roadmap" boundary from §5.3.
3. **Demo** — embedded video.

Keep the full nine-slide narrative in `plans/…-alibaba-competition-demo.md` §18
as the technical appendix for Demo Day; the submission itself is three.

### 6.4 The "Alibaba Use" cell

Written in the rubric-mapped style the strongest entrants used, and honest
about what each service does:

> **FC Agent Sandbox (E2B SDK, Pro tier)** — hosts a pool-scoped FlashNode as a
> prepared ML evaluation worker · deep hibernation while training runs, resumed
> on a model-artifact event via `Sandbox.connect` on the same sandboxId ·
> marker-hash and PID continuity verified across the wake · measured
> create/pause/resume latency · bounded concurrent-create elasticity probe ·
> MicroVM isolation as the boundary for user-supplied training code ·
> E2B-compatible endpoint swap.
> **ECS** — an Alibaba machine enrolled in the same pool as a FlashNode, making
> Alibaba a first-class tier in the fleet rather than a side service.
> **OSS** — durable checkpoints and model artifacts via native `oss2` with STS,
> portable across sandbox lifecycles.
> **SLS + CloudMonitor** — structured lifecycle logs correlated on
> `session_id`/`sandbox_id`/`job_id`/`task_id`/`lease_id`, plus a configured
> alert for a sandbox left active past its timeout.
> **RAM + STS** — least-privilege role for the artifact path; no long-lived key
> reaches a workload.

### 6.5 Services we deliberately did not use

State this on the slide; restraint reads as judgement.

- **Qwen / Model Studio** — 12 of 19 entrants use it. There is no LLM in our
  execution path, and adding one would be the "model wrapper" the rubric
  penalises. A coding assistant used to *write* our code is development
  tooling, not architecture, and does not belong in the Alibaba Use cell.
- **PAI-DLC / AIMaster** — Alibaba already solves Alibaba-local training fault
  tolerance well. Reimplementing it would be duplication; the right long-term
  move is to delegate to it as an `ExecutionBackend`.
- **ACK · EventBridge · MNS · Hologres · Function Compute (non-sandbox)** —
  logo count is not a scored dimension.
- **AMD / ROCm** — GPU discovery is `nvidia-smi`-oriented and all GPU evidence
  is CUDA. We will not claim ROCm without a real ROCm machine completing the
  same workload.

---

## 7. Spend plan against the USD 300 voucher

Constraint: voucher expires **2026-08-15 00:00:00** (confirm the console's
timezone — if UTC+8 the real cutoff is **Aug 14, 02:00 PDT**, nearly a day
earlier than assumed). Cash balance is $0.00, and overdue payment stops
sandboxes.

**Principle: convert credit into resources that outlive the voucher.**
Subscription and prepaid resources survive to Demo Day; pay-as-you-go usage
after expiry either charges the card or suspends the demo mid-judging.

| When | Spend | Why |
|---|---|---|
| Aug 11 | Task 0 smoke — cents | Gates everything below |
| Aug 11 | **ECS, 1-month subscription** (~$30–50) | Only purchase that survives to Aug 22. Runs FlashNode. Makes §5.2 literal |
| Aug 12 | OSS prepaid storage · SLS project | Code and templates already exist in-repo |
| Aug 13 | FC dev + rehearsal runs · one CloudMonitor alert | Alert is explicitly demanded by the rubric |
| Aug 14 | **Elasticity probe** — bounded concurrent sandboxes, all killed in `finally` | Produces the 6.1 number; largest legitimate line item |
| Aug 14 | Model Studio Coding Plan $50, *if credit remains* | "Try their product". Scores nothing — keep it out of §6.4 |

Target **$40–90**, every dollar itemisable. A number we cannot break down in
live Q&A is worse than a smaller one we can.

**Two mandatory guardrails, before the first purchase:**

1. **Fund the balance or confirm card auto-charge**, so the demo does not stop
   itself between Aug 15 and Aug 22. This is the highest-severity operational
   risk in this document.
2. **Set a budget alert and a hard cap.** Standing house rule on rented
   capacity applies; this is the same failure mode with an 8-day tail.

---

## 8. Risks

| # | Risk | Earliest test | Mitigation | Honest fallback |
|---|---|---|---|---|
| R1 | Pause/resume not allowlisted | **Task 0, today** | Request enablement immediately; ask DingTalk 179855020297 and Discord whether Pro auto-transition satisfies the gate | Demo active-state stateful sessions and state the account limitation. **Never fake hibernation** |
| R2 | Voucher expiry kills the demo during judging | Now | Fund balance; put durable pieces on subscription | Static recorded evidence + video; live URL degrades to the public demo route |
| R3 | Voucher timezone is UTC+8, losing ~22 hours | Now, console check | Move all spend to Aug 13 | — |
| R4 | Preview billing ≠ modelled rates | First invoice | Label every modelled figure as modelled | Present the formula with measured durations and no dollar headline |
| R5 | Wake latency visible on stage | Task 0, both regions | Measure `us-west-1` vs `ap-southeast-1`, pick on the number | Light hibernation for the on-stage hop if Pro allows |
| R6 | Judge cannot reach the product | Now | Public read-only demo route | Watch the approval queue Aug 15–17 |
| R7 | Training recovery demo flakes live | Three-run baseline | Deterministic workload, rehearsed kill, pre-started second node | Recorded uncut recovery evidence in the video |
| R8 | We over-claim allocation/marketplace | Deck review | §5.3 claim ladder on the slide | — |

---

## 9. Open decisions for the owner

1. **Team and project name.** Recommended: team **Zolli AI**, project
   **FlashML**, since the live URL will be `zolliai.com`. Note `ROADMAP.md` §6.3
   retired "Zolli" from the *interface* while keeping it as brand — this is a
   brand use, so it is consistent, but it is the owner's call.
2. **Track — RESOLVED 2026-08-11: "Infrastructure for Agents".** The column is
   free text with no data validation (§4.0), SeaWeb set the precedent, and the
   reasoning is in §6.1.1. Confirm with Discord that tracks are not a closed
   list; the sheet stays editable until the cutoff either way.
3. **Region.** Decide from Task 0's measured wake latency, not in advance.
4. **Public demo route** — approve building it (§1.1), it is the cheapest
   auto-DQ insurance we have.
5. **Whether to fund the account balance** past voucher expiry (§7). Without
   this the demo has a scheduled failure between submission and Demo Day.

---

## 10. Sequence to submission

- **Aug 11** — claim the sheet row · console timezone check · **Task 0
  GO/NO-GO on hibernation, both regions** · open the allowlist request · ECS
  subscription if GO.
- **Aug 12** — sandbox gateway, session ledger, pinned template, sandbox worker
  registration, each independently green. In parallel: freeze the train/eval
  workload and land three clean recovery runs.
- **Aug 13** — three end-to-end runs · API-restart rehearsal · public demo
  route · OSS + SLS + one alert.
- **Aug 14** — elasticity probe · freeze code · record video · assemble
  evidence bundle · fill every sheet column including a real spend figure ·
  **submit**.
- **Aug 15** — buffer only. No new infrastructure.
- **Aug 17–22** — *plan for this now.* Per §4.0 the Top 20 is likely for any
  complete submission, so the real contest is the three minutes on stage plus
  Q&A. Reserve this week for: rehearsed live demo with a tested fallback
  recording, the §3.1 baseline argument, the §5.3 claim ladder, the "where is
  the agent?" answer (§6.1.1), and a funded account so nothing stops itself
  (§7). Do not discover on Aug 18 that this week was unscoped.

**Nothing below Task 0 starts until Task 0 returns GO.** Every architecture
choice and most of the score depends on one question that costs cents to
answer.
