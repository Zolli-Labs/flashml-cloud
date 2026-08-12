# Zolli Marketplace — design

**Status:** design, 2026-08-11. Not scheduled against the Aug 15 competition
deadline; §11 names the one thin slice that could ship early if the Alibaba
work lands ahead of time.

**What this is.** A two-sided market for ML compute. Hosts list machines with
an asking price; jobs post bids; a matching engine pairs them inside the
placement gates the scheduler already enforces. Owned and rented capacity
appear on one page, in their own currencies, with a cost and an ETA for each
plan — so choosing where to run is a decision a person can actually make.

**What it is not.** Not a wallet. Zolli credits are earned and spent inside
the product and **never convert to or from money**, in either direction. That
is a deliberate limit, stated plainly on every surface that shows a balance.

---

## 0. Corrections from design review (2026-08-11, two independent passes)

Two reviews — one optimising for capability, one for defensibility — reached
the same structural conclusion by different routes, and between them found
three errors in the first draft. **Read this section before §3, §4, or §6;
those sections are corrected but the reasoning lives here.**

### 0.1 FlashML is a PULL system. A match cannot assign work.

`flashruntime/scheduler/__init__.py:628` states it outright:

> **FlashML is a PULL system.** Nodes claim; the coordinator never picks a
> node for a task. By the time a policy runs, the machine has already asked,
> and the only question left is *which of the pending tasks it gets, or none*.

The first draft's §4 described a push allocator — "match: job-8821 ↔
phong-rig". **There is no push path in the runtime**, so a `matches` row
cannot cause a host to run anything.

**The correction, and it is clean:** a match is not an assignment, it is a
**priced entitlement**. It makes a task *eligible* for a host that would
otherwise not be eligible, at an agreed price, by writing a grant the existing
pool gate already reads. The host still claims. Matching decides **who may
pull what, and at what price**; the pull loop is untouched.

Two consequences the first draft missed:

- **A match does not guarantee a fill.** A matched host that never claims
  produces no work and no charge. That is correct, and the UI must show it, or
  buyers will read a match as a reservation.
- **Escrow holds on `claimed`, not on `granted`** — otherwise a host who never
  shows up locks a buyer's balance. Match states are
  `granted → claimed → settled`.

Equivalently, from the capability pass: **the router chooses the fleet, not
the assignment.** It decides which machines are in the eligible set; the pull
scheduler distributes work inside it. Same mechanism, and it is why this is
buildable at all — the router sits entirely above placement and never competes
with `choose()`.

### 0.2 `P(accepted)` is NOT computable from Postgres today

`migrations/0004_attempts.sql` stores `lease_id, machine_id, job_id, task_id,
claimed_at, accepted_at` — and its own table comment says *"failed and expired
attempts leave no mark."* A failed attempt and an in-flight attempt are the
same row: `accepted_at IS NULL`. `POST /attempts/{lease_id}/fail` is a pure
proxy that writes nothing, and lease expiry is decided by the coordinator's
sweeper, which never tells the API.

So `tasks_accepted / tasks_attempted` has a denominator that includes every
still-running attempt and every attempt ever abandoned. **It drifts downward
forever and never recovers.** `metrics.py` already refuses to compute
`lost_task_seconds`, `mttd_seconds` and `mttr_seconds` for exactly this
reason, documenting `None` as "a first-class answer."

**A router that computed `P(accepted)` from Postgres would be inventing the
number `metrics.py` explicitly declines to invent.** The first draft's
`effective_price = ask ÷ goodput` (§3) and `retry_overhead = 1/P(accepted)`
(§6.1) both did exactly that. Corrected below.

The coordinator *does* keep an honest record — `accepted_tasks`,
`failed_attempts`, `abandoned_leases` in `service/modea.py:584` — but it is
**in-memory and cleared by any restart**, and the dev coordinator has no disk
at all. So in practice most hosts are `unproven` most of the time.

### 0.3 Therefore cold start is the steady state, not an edge case

Given 0.2, a router whose value depends on accumulated history has no value in
the deployment that actually exists. Roughly 29 credited tasks exist in total.

**The resolution is to measure instead of predict.** For a 40-trial sweep,
spend one trial calibrating the other thirty-nine — a **canary probe**. Six
seconds converts "we have no data for this workload" into a measured number.
It is simultaneously the most impressive thing the router does and the most
defensible, because the result is *measured*, not estimated.

Lead with the canary. It is the honest answer to a novel workload and it turns
0.2's weakness into the demo's best moment.

### 0.4 The prerequisite worth blocking on

```sql
alter table public.attempts
  add column resolved_at timestamptz,
  add column outcome text check (outcome in ('accepted','failed','expired','abandoned'));
```

Written by the existing `fail` proxy and by a coordinator→cloud expiry
notification. Roughly a day of work. It unlocks `P(accepted)`,
`lost_task_seconds`, MTTD and MTTR — the metrics currently returning `None` —
and removes the survivorship bias that makes every ETA optimistic (F5 below).

**Until this lands, no router claim about reliability is defensible.** It is
the first thing to build, before any marketplace surface.

### 0.5 Other corrections carried into the sections below

| First draft said | Corrected to |
|---|---|
| `effective_price = ask ÷ P(accepted)` | Rank on **ask** for unproven hosts; use a reliability **tier**, never a synthesised float |
| `retry_overhead ← 1 / P(accepted)` | Removed. Also `duration_s` **already includes staging** (`db.py:1307`), so the separate staging term double-counted |
| `CONFIDENCE 91%` in the UI mock | Not defensible at current n. **Ranges and `n=`**, no percentages until 0.4 lands and n≥5 |
| "Nothing here is invented; it is arithmetic over the ledger" | **False.** True for duration; false for `P(accepted)` and interruption rate, which are not in the ledger at all |
| K1 verification listed as an accepted risk | It is a **correctness precondition** for settlement, not a risk to accept. Money moving on unverified work is where the no-enforcement stance becomes untenable |

---

## 1. Decisions taken

| # | Decision | Source |
|---|---|---|
| M1 | **Workspace is free; the open market is priced.** Members of a workspace consume each other's machines at no charge. Renting from outside the workspace costs credits | owner, 2026-08-11 |
| M2 | **Open listing.** Any user may host and set their own ask | owner |
| M3 | **Bid/ask order book with matching**, per capability class | owner |
| M4 | **Dual currency.** Owned machines in Zolli credits (ZC); rented cloud in USD. Side by side, never converted | owner |
| M5 | **Prices do not drift on a timer.** They move when someone posts a better ask or a bid clears. No synthetic demand curve | owner |
| M6 | **Zolli capacity is structurally cheaper** than rented cloud, and the comparison view is where that becomes visible | owner |
| M7 | **Everyone starts with a large credit grant.** No purchase path, no payout path | owner |
| M8 | Target workloads are **HPO and federated training** — many independent tasks, which is what a market can actually allocate | owner |
| M10 | **The credit grant is one-time.** No refill. Scarcity is what makes a price mean anything; a refilling allowance would make the market a toy | owner, 2026-08-11 |
| M11 | **No Zolli fee in v1**, and no mechanism to add one. This phase is concept proof, not revenue | owner |
| M12 | **Hosts earn, and that is the point.** A workspace member whose team has no queued work may list the same machine on the open market and take outside jobs. **Workspace demand has priority**; the open listing is what the machine does when its team is idle | owner |
| M13 | **A zero ask is legal**, labelled *donated* rather than priced. Volunteers are a real supply tier and pricing them at a floor would misrepresent them | owner |
| M14 | **Result verification is deferred to a later layer.** Acknowledged: settlement on unverified work is a real gap (§0.5). It is bounded here because credits never convert to money in either direction (M4/M7) — the exposure is reputational inside the product, not financial. **Revisit before credits ever gain an exit** | owner |

### M9 — `contributions` is not touched

`contributions.py` forbids a spendable balance in the strongest terms:

> *"It must never grow a `balance`, a `remaining`, or a `credits` — a name that
> implies a drawdown promises an exchange rate this product has not designed
> and cannot honour."*

That rule stands. `contributions` remains the immutable record of **accepted
work**. Credits live in a **separate double-entry ledger** (§5) that references
contributions but never mutates them. The rule's intent — never let a
measurement become a promise — is preserved, because the ledger's own terms
say credits are not redeemable.

---

## 2. The three venues, one page

```
                        submit a job
                             │
                ┌────────────┴────────────┐
                │   PLACEMENT GATES       │  capability · pool · GPU · data
                │   (existing, unchanged) │  deps · isolation · exclusions
                └────────────┬────────────┘
                             │  only what may legally run
        ┌────────────────────┼────────────────────┐
        ▼                    ▼                    ▼
  WORKSPACE            OPEN MARKET           RENTED CLOUD
  your team's          anyone's machines     Alibaba FC · RunPod
  machines             ask in ZC             price in USD
  free · priority      order book            provider rate card
```

**The gates come first, always.** The market proposes; the scheduler disposes.
A price never buys past a capability, pool, data-locality, or isolation
requirement. This is the single most important invariant in the design: it
means the marketplace can be wrong about money without ever being wrong about
safety.

---

## 2.5 The unit, and the grant

### 2.5.1 One Zolli credit = one `gpu-24gb`-hour

**ZC is denominated in compute, never in money.** Defining "1 ZC = $1" would
create exactly the exchange rate M4 forbids and `contributions.py` warns
against — a number a person could believe they are owed. So the unit is
physical:

> **1 ZC buys one hour of the reference class `gpu-24gb`.**

`gpu-24gb` is the reference because it is where the market is densest
(RTX 3090, 4090, A5000, 3090 Ti, L4 all sit there) and because it is what a
home rig actually contains — the supply tier the 2026-08-02 supply-side note
identified as the one that matters.

### 2.5.2 The class ladder, derived from real prices

RunPod community prices, **captured 2026-08-11** — recorded with a timestamp
per F6, because a scraped price presented as live is a lie with a delay:

| Class | RunPod community $/h (2026-08-11) | ZC/h |
|---|---|---:|
| `cpu-*` | ~0.02–0.05 | **0.1** |
| `gpu-8gb` (RTX 3070) | 0.13 | **0.5** |
| `gpu-16gb` (RTX 4080S) | 0.28 | **1** |
| **`gpu-24gb` (3090 / 4090 / A5000)** | **0.16 – 0.34** | **1** ← reference |
| `gpu-40gb` (A100 SXM 40GB) | 1.00 | **3** |
| `gpu-48gb` (A6000 / L40 / L40S) | 0.33 – 0.79 | **2** |
| `gpu-80gb` (A100 PCIe / SXM) | 1.19 – 1.39 | **4** |
| `gpu-80gb-hopper` (H100 / H200) | 2.69 – 3.59 | **10** |

**`gpu-40gb` added 2026-08-11**, because two implementation agents independently
hit the same contradiction: §4's example book named a `gpu-40gb` class that this
ladder did not have, so a 40 GB A100 fell through to `gpu-24gb` and
**under-claimed its own capability** — priced at 1 ZC while renting for $1.00.
Note it sits *above* `gpu-48gb` in price and below it in VRAM: capacity and
cost are different axes and the ladder must carry both, because the class
decides **placement** (does the task fit?) while the ZC figure decides
**price**. Sorting the table by either one alone hides that.

The **ratios** come from a real market, so the ladder is defensible. The
**unit** is an hour of compute, so nothing here implies a dollar you could
claim. Both properties matter and they are not in tension.

### 2.5.3 The grant: 250 ZC, one time (M10)

Stored as `250_000` millicredits — integers only, per §5.

Sized against what work actually costs:

| Job | Cost |
|---|---|
| 40-trial HPO sweep, 6 min each, `gpu-24gb` | **4 ZC** |
| 10-round federated run, 5 participants, 3 min | **~2.5 ZC** |
| 200-trial sweep, 10 min each, `gpu-24gb` | **33 ZC** |
| That same sweep on `gpu-80gb-hopper` | **330 ZC** |

**The design test a grant has to pass: large enough that ordinary work is never
blocked, small enough that ambitious work forces a choice.** 250 ZC passes.
It funds roughly fifty small sweeps or eight serious ones — but a 200-trial
sweep on H100-class capacity costs more than the entire grant. That is the
moment the price stops being decoration and starts being information, and it
is the moment the router's cheaper alternatives become worth reading.

**One-time is sustainable because hosting earns.** The grant seeds the economy;
contribution is the flow that sustains it. A user who runs out has an obvious
route back — put a machine in, which is precisely the behaviour the product
wants (M12). A refilling allowance would remove that pressure and with it every
reason to care what anything costs.

---

## 3. What is priced, and in what unit

Two numbers, and conflating them is the classic marketplace mistake.

| | Meaning | Who uses it |
|---|---|---|
| **Ask** | ZC per machine-hour | what a host sets — the unit hosts understand |
| **Effective price** | ZC per **accepted task** | what a buyer actually pays for a result |

```
effective_price = ask_per_hour × hours_per_task ÷ P(accepted)
```

`P(accepted)` is the host's goodput, which `metrics.py` already computes from
attempted-versus-accepted work. So a machine asking 0.22 with 0.81 goodput is
more expensive per result than one asking 0.30 with 0.98 — and the comparison
view says so rather than making the buyer do the arithmetic.

### 3.1 Settlement is on accepted work, not elapsed time

**A buyer is never charged for an attempt that did not produce accepted
output.** A host whose machine dies mid-task earns nothing for that attempt;
the task requeues and another host earns it.

This follows directly from hard rule 4 (*"Distinguish attempted work from
accepted work everywhere money, credits, or metrics are involved"*) and it is
the marketplace's most distinctive property. Every GPU marketplace bills for
time and pushes interruption risk onto the buyer. Zolli can bill for results
because the runtime already knows the difference — and that is only possible
because leases, attempts, and exactly-once acceptance already exist.

Consequence to state plainly to hosts: **reliability is your revenue.** A host
that completes 98% of claims out-earns a faster host that completes 60%.

---

## 4. The order book

Machines are not fungible, so there is no single book. There is **one book per
capability class**:

```
cpu-small · cpu-large · gpu-8gb · gpu-16gb · gpu-24gb · gpu-40gb · gpu-80gb
```

Class is derived from the capabilities a machine already reports at
registration — no new host input.

```
ORDER BOOK · gpu-24gb

  ASKS (hosts)                       BIDS (jobs)
   0.42  mira-ws       goodput 0.94   0.38  job-8821  ×4 tasks
   0.40  phong-rig     goodput 0.98   0.36  job-8817  ×1
   0.30  ada-home      goodput 0.71   0.35  job-8802  ×2
  ──────────────────────────────────────────────────────────
  effective: phong-rig 0.41 · mira-ws 0.45 · ada-home 0.42
  match: job-8821 ↔ phong-rig @ 0.40   (best effective, bid clears)
```

**Matching rule.** For each open bid, in bid-price order:

1. Filter asks through the existing placement gates for that job's tasks.
2. Rank surviving asks by **effective price**, not headline ask.
3. Match while `bid ≥ effective_price`.
4. **Execute at the host's ask.** The host gets the number they posted; the
   buyer never pays more than their bid. No hidden spread, nothing for Zolli
   to take — there is no fee in v1 and no mechanism to add one.
5. Partial fills are normal and expected: a 40-trial HPO job may match eight
   machines at four different prices. That is the point.

**Why no clearing-price auction.** A uniform clearing price is elegant and
unexplainable on stage in ninety seconds. Pay-the-ask is legible: every host
sees exactly what they asked for, every buyer sees a list of what they paid.

**Why prices still move.** Not on a timer (M5). They move because a host posts
a cheaper ask to win work, or a buyer raises a bid to fill a queue. The book is
the mechanism; scarcity is the cause. On a demo you can *make* it move by
taking machines offline, and that is a real cause rather than a simulated one.

---

## 5. Credits — a separate double-entry ledger

```sql
credit_accounts (
  user_id uuid primary key references auth.users(id),
  balance_zc bigint not null default 0,      -- millicredits, integer only
  updated_at timestamptz not null default now()
);

credit_entries (                              -- APPEND ONLY. Never updated.
  id bigserial primary key,
  account_id uuid not null references credit_accounts(user_id),
  delta_zc bigint not null,                   -- signed
  reason text not null check (reason in (
    'grant','escrow_hold','escrow_release','escrow_refund',
    'earned_accepted_work','spent_accepted_work','adjustment')),
  ref_type text, ref_id text,                 -- job / task / contribution
  created_at timestamptz not null default now(),
  unique (reason, ref_type, ref_id, account_id)   -- idempotency
);
```

Invariants, each enforced by a test:

- `balance_zc == sum(delta_zc)` for every account, always.
- Every debit has a matching credit. Credits are conserved except at `grant`.
- The unique index makes double-settlement impossible at the schema level —
  the same discipline migration `0003` already applies to `contributions`.
- **No row in `contributions` is ever written or altered by this module.**

**Escrow, because settlement is on accepted work:**

```
match          → escrow_hold      buyer −(bid × est_hours)
accepted commit→ escrow_release   buyer −actual, host +actual
attempt failed → escrow_refund    buyer +held, host +0
job cancelled  → escrow_refund    buyer +remaining
```

**Integers only.** Balances are millicredits (`bigint`). Money-shaped values in
floats is how ledgers silently stop balancing.

**The honesty surface.** Every screen showing a balance carries, non-dismissibly:

> Zolli credits are not money. They cannot be bought, sold, withdrawn, or
> converted. They exist to allocate compute inside Zolli.

---

## 6. Recommendation and ETA — the part that makes it usable

An order book alone tells nobody what to do. On submit, the planner produces
**three plans**, priced and timed:

```
JOB  hpo-sweep-8821    40 trials · ~6 min each · needs ≥16GB VRAM

  PLAN         WHERE                          COST      ETA     CONFIDENCE
  ▸ Cheapest   8 Zolli machines               14.2 ZC   38 min      86%
  ★ Balanced   4 workspace + 4 Zolli market    9.8 ZC   31 min      91%   ← recommended
  ▸ Fastest    4 workspace + 12 RunPod A100   $6.40     9 min       97%

  Balanced: your workspace machines are free and idle. The market covers the
  tail at 0.30–0.40 ZC/h. Two hosts have goodput below 0.85, so they are
  priced out on effective cost rather than headline ask.
```

### 6.1 The estimator

Deterministic. No model in the loop.

```
per_task_seconds   ← historical median for this task shape on this machine class,
                     else a capability-scaled estimate from the admission benchmark
parallelism        ← machines matched, capped by task count
retry_overhead     ← 1 / P(accepted), per host
eta                ← ceil(tasks / parallelism) × per_task_seconds × retry_overhead
                     + staging time (dataset present? env cached?)
confidence         ← P(all tasks accepted before eta), from per-host goodput
cost               ← Σ per matched host: ask × hours × tasks_assigned
```

Every input already exists: `contributions`, `attempts`, the checkpoint
catalog, machine capabilities, and `flashnode/benchmark/` admission probes.
**Nothing here is invented; it is arithmetic over the ledger.**

### 6.2 Why "Balanced" is usually recommended

It encodes the product thesis in one line: **consume what you already own,
then buy only the deficit.** Workspace machines are free (M1), so a plan that
leaves them idle while paying for cloud is nearly always wrong. The
recommendation is not a heuristic about markets — it is the utilization
argument, computed.

---

## 7. The agent harness

You asked whether an agent can be wired in. Yes — with one hard boundary.

**The planner decides. The agent explains and proposes.** House rule 5 —
*"Recovery actions are typed, deterministic, logged. No LLM-driven recovery"* —
extends here: no LLM chooses a placement, moves credits, or commits a bid.

```
┌───────────────────────────────────────────────────────────┐
│  AGENT (conversational surface)                           │
│  "finish before 6pm, under 20 credits, prefer my own gear"│
└───────────────┬───────────────────────────────────────────┘
                │ typed tool calls, read-only except the last
                ▼
┌───────────────────────────────────────────────────────────┐
│  PLANNER (deterministic · the authority)                  │
│  list_offers · quote · compare · simulate · place_bid     │
└───────────────┬───────────────────────────────────────────┘
                ▼
       placement gates → order book → escrow → leases
```

Tool surface:

| Tool | Effect | Confirmation |
|---|---|---|
| `list_offers(requirements)` | read | — |
| `quote(plan)` | read — cost, ETA, confidence | — |
| `compare(plans)` | read | — |
| `simulate(plan, failures)` | read — "what if two hosts die?" | — |
| `place_bid(job, price, plan)` | **writes** — holds escrow | **human confirmation required** |

That last row is not decoration: the competition rubric's dimension 7 asks for
*"human confirmation for high-risk actions"*, and spending a balance to acquire
compute is exactly that. The agent may recommend a bid; a person commits it.

**What the agent is genuinely good for:** turning "I need this done by six for
under twenty credits" into constraints, explaining why a cheap machine was
skipped, and narrating what changed when the book moved. Those are language
problems. Allocation is not.

---

## 8. Data model additions

```sql
listings (                       -- a host's standing offer
  id, machine_id, owner_id,
  capability_class text,         -- derived, not user-entered
  ask_zc_per_hour bigint,
  max_concurrent_tasks int,
  available_from timestamptz, available_until timestamptz,
  state text check (state in ('open','paused','withdrawn')),
  created_at, updated_at
);

bids (                           -- a job's willingness to pay
  id, job_id, owner_id,
  capability_class text,
  max_zc_per_hour bigint,
  tasks_wanted int,
  deadline timestamptz,
  state text check (state in ('open','partial','filled','cancelled','expired'))
);

matches (                        -- append-only; the audit trail
  id, bid_id, listing_id, machine_id,
  agreed_zc_per_hour bigint,
  tasks_assigned int,
  escrow_entry_id bigint references credit_entries(id),
  matched_at, settled_at,
  state text check (state in ('held','settled','refunded'))
);

price_observations (             -- for the chart, and for honesty
  id, capability_class, observed_at,
  best_ask_zc bigint, best_bid_zc bigint, last_match_zc bigint,
  open_listings int, open_bids int
);
```

`price_observations` is written on every book change, never derived after the
fact. A price chart assembled retroactively from matches is a story; one
recorded as it happened is evidence.

---

## 9. What makes this demo well

1. **Submit a 40-trial HPO job.** Three plans appear with real cost and ETA.
2. **Pick Balanced.** Watch it fill across workspace machines and market hosts
   at different prices — a partial fill across four asks, visible in the book.
3. **Kill a host mid-run.** The lease expires, the task requeues, another host
   claims it — and **the buyer's escrow refunds for the lost attempt.** No
   other compute market can show that, because no other one knows the
   difference between attempted and accepted.
4. **Watch the book move.** Take three machines offline; the best ask worsens
   because supply left, not because a timer fired.
5. **Compare currencies.** 9.8 ZC on Zolli vs $6.40 on RunPod for the same
   work, side by side, honestly not converted.
6. **Ask the agent** "why not the cheapest?" and get the goodput answer.

---

## 10. Risks

| # | Risk | Response |
|---|---|---|
| K1 | **Verification enforces nothing.** A host can return garbage and earn credit. Thread 4 in the positioning log is still open | Settlement on accepted work helps but does not solve it. Open market stays invite-gated until spot-check verification exists. **This blocks M2 going fully public** |
| K2 | Credits read as money to a user | Non-dismissible disclosure on every balance surface; no purchase or payout path exists to imply otherwise |
| K3 | Order book is thin — few hosts, no liquidity | Expected and fine at this size. Show the book honestly; a two-sided market with six machines is still a market |
| K4 | ETA is wrong and erodes trust | Show confidence, not just a time. Record predicted-vs-actual and surface the error rate |
| K5 | Sybil hosting — one person, many fake listings | Bounded by invite-gating and by settlement on accepted work: a fake machine that completes nothing earns nothing |
| K6 | Scope. This is much larger than the competition | §11 |

---

## 11. Sequencing, and the one slice that could ship early

**This does not land before Aug 15.** The order book, ledger, escrow, and agent
harness are weeks, not days, and the competition build is FC Sandbox + OSS.

**The exception — the comparison view.** §6's three-plan panel needs **no**
credits, **no** order book, and **no** settlement. It needs only the estimator
over data that already exists, plus captured provider rates. It is the
utilization thesis made visible, and it is the single strongest addition to the
competition submission if the Alibaba work lands early:

> *One job. Your machines, the market, and two clouds — priced side by side,
> with an ETA on each.*

Build order after the competition:

1. Comparison view + deterministic estimator *(candidate for Aug 13 if ahead)*
2. Listings + capability classes — hosts can post an ask
3. Credit ledger + grants, read-only balances
4. Bids + matching engine + escrow settlement
5. Price observations + book UI
6. Agent harness over the planner tools
7. Verification (thread 4) — **gates the open market going public (K1)**

---

## 12. Owner decisions — all closed 2026-08-11

Every question this document opened has been answered. They are recorded as
M10–M14 in §1; this section exists so nobody re-opens them without cause.

| Question | Answer |
|---|---|
| Grant size / refill | **One-time, no refill** (M10) |
| Zolli fee | **None in v1**, concept proof first (M11) |
| Workspace member listing on the open market | **Yes** — hosts earning is the point. Workspace demand takes priority; the open listing is what the machine does when its team is idle (M12) |
| Minimum ask | **Zero allowed**, labelled *donated* (M13) |
| Result verification | **Deferred** to a later layer, with the exposure bounded by credits having no exit (M14) |

**Still open, and genuinely blocking implementation rather than design:**

1. **§0.4 — the `attempts.outcome` column.** Not a preference, a prerequisite:
   until it exists, no reliability number the router shows can survive the
   question "how do you know?" This is the first thing to build.
2. **Grant amount.** M10 settles that it is one-time; the number itself is
   still unset. It should be large enough that a first job is never blocked by
   balance, small enough that a price still means something.
