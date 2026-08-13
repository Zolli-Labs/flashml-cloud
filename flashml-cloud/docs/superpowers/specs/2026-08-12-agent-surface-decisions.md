# The agent surface and the market — decision record

**Date:** 2026-08-12. **Status:** decisions D1–D10 made with the owner in
conversation, this session. **Deadline context:** submission 2026-08-15.

**Why this document exists.** This session runs unattended for ~4–5 hours in
parallel with `zolli-labs-d7`'s rented-capacity work. Every decision below
changes what gets built, and several reverse or amend an existing approved
document. A decision an agent made alone, for reasons nobody wrote down, is
indistinguishable later from a mistake — so each entry names the reason and
**what would reverse it**.

Companion documents: `2026-08-10-developer-surface-and-mcp-design.md` (the
approved design being executed), `2026-08-11-competition-requirements.md`
(the requirements source, amended by D3), `2026-08-12-market-design-v2.md`,
`2026-08-12-next-phase.md`.

---

## D1 — The decision layer stays deterministic. No trained model.

**Owner question:** *"the decision layers is constraints based right now —
should we train a model for this, or use context, or hardcodes for the
resource allocation part?"*

**Decision:** neither train nor hardcode. The layer is already the right
thing and it is not hardcoded: `router/estimator.py` is an evidence ladder —
rung 1 the same job observed on other machines (`measured`), rungs 2–3 the
same shape elsewhere (`estimated`), anything thin or cross-class
(`projected`, *"always a range and never a point"*).

**Why.**

1. **Sample size.** Total measured spend across every experiment to date is
   ~$0.89 — one 200-epoch job and a 6-trial sweep. A learned allocator
   trained on that fits noise. The rung ladder is correct at this sample size
   *and* at every larger one.
2. **Explainability is shipped product.** The Placement tab renders verbatim
   per-venue reasons and deliberately separates `suited: false` ("physically
   cannot — no price changes it") from `acquirable: false` ("a real fit, our
   gap, not a verdict on the venue"). A learned policy cannot produce that
   sentence truthfully.
3. **The rubric.** D-4 scores an end-to-end trace; the anti-pattern list names
   "no unlocatable failure"; D-7 wants human confirmation on high-risk
   actions, and spending money is one.
4. **It is a market.** Price discovery does the allocation. `market-design-v2`
   already states the doctrine — the suggestion is *"the market itself, never
   a model"*, the median of a real order book.
5. AGENTS.md hard rule 5 already says recovery is typed, deterministic,
   logged, with no LLM involved.

**Where a model would earn its place:** the *estimate*, never the decision.
"How long will this shape take on this hardware" is a real regression problem
with an objective label, and `ExecutionEvidence` already collects it (wall
clock, mean CPU/GPU utilisation, image digest, exit code).

**What would reverse it:** order 10³ accepted tasks with evidence, plus a
held-out comparison showing a learned estimator beating rung 1–3 on tasks it
has not seen. Even then the swap is *inside* the estimator — `plan.py` and the
market above it must not notice.

**Architectural consequence to protect:** keep the estimator replaceable and
the decision deterministic.

---

## D2 — Intelligence goes in the agent layer, outside the execution path.

**Decision:** the split is *the agent decides what the human wants; the market
decides where it runs.*

An LLM is genuinely good at "turn this training script into a valid workload,
tell me what it will cost, ask before spending" and genuinely bad at "silently
pick a machine". D1 keeps it out of the second. This decision puts it firmly
in the first, which is also the "Software for Agents" track thesis.

**What would reverse it:** nothing foreseeable. This is the load-bearing
distinction that makes D3 defensible.

---

## D3 — §9's "Qwen / Model Studio: never for this submission" is AMENDED.

**Supersedes:** `2026-08-11-competition-requirements.md` §9, which reads
*"never for this submission — no LLM exists in our execution path; adding one
is the 'model wrapper' the rubric penalises."*

**Decision:** permitted, narrowly, as **a workload — not a wrapper**.

**What changed since 2026-08-11, and it is evidence rather than appetite:**

1. **The sponsor published the pattern.** Alibaba Cloud's own article, sent to
   the owner by the sponsors — *"Deep Dive: How Kimi's AI Agent Runs on
   Alibaba Cloud"* — documents an agent whose orchestration loop runs inside
   Agent Sandboxes, hibernating between turns.
2. **Hibernation was approved for this account.** Alibaba support (Discord,
   2026-08-12) enabled `PauseSession`/`ResumeSession` for UID
   5055584162230015 in `ap-southeast-1`.

**The distinction that makes this not a wrapper.** A model wrapper is a thin
UI over someone else's model. FlashML is a compute market with a deterministic
allocator (D1). Running an agent *inside an FC Agent Sandbox that hibernates
between turns* does not put a model in the execution path — it makes the agent
**a workload that demonstrates the sandbox's core advantage**, which is D-6,
the largest scoring axis.

**What it unlocks that the previous plan was straining for.** C-6.5 requires
"both modes, for two genuinely different waits". The design had to reach for
"gaps between evaluation shards" as the short wait. An agent supplies a
natural pair: **deep** hibernation across the long wait (training running,
user away), **light** hibernation across the short gap between tool calls.
C-6.4 (state across the boundary) becomes self-evidencing — the conversation
*is* the state.

**Precision required when claiming this.** Kimi runs on **ACK + ACS**, not FC.
The defensible sentence is: *"Alibaba documents this pattern for Kimi on ACS;
we run it on FC's Agent Sandbox through the same E2B-compatible API."*
Claiming "we do what Kimi does, on FC" is correctable by any judge who read
the article.

**What would reverse it:** the P0 gates (G-1…G-4) still being unmet on
2026-08-14. A hosted agent attached to a submission with no public URL and no
sheet row scores zero. Sequencing enforces this — D8.

---

## D4 — The model provider is Qwen via Bailian / DashScope.

**Decision:** Qwen, not Anthropic or OpenAI. **Owner decision, 2026-08-12.**

**Why:** it is an Alibaba competition, and this adds a third Alibaba service
to a submission currently scoped to two (FC Agent Sandbox + OSS). A
non-Alibaba model in an Alibaba submission is a question the owner would have
to answer on stage for no compensating benefit.

**Verified before deciding:** no LLM provider is wired anywhere in
`flashml-cloud` today — no Anthropic, no OpenAI, no DashScope. This is a
from-scratch integration whichever provider wins, so provider choice cost
nothing to defer to the strongest reason.

**Build shape (D5) makes this cheap to revisit.**

---

## D5 — The provider sits behind a Protocol with a fake.

**Decision:** the agent loop talks to a `ModelProvider` Protocol; a fake
implementation backs every test; the DashScope implementation is wired when a
key exists.

**Why:** it mirrors `SandboxGateway`, which already has a fake for exactly
this reason, and it is what D-5 ("extensible skills; swap tools without
rewriting") scores. It also means the loop is testable and committable
tonight, before any key exists — the owner is away and no key is available.

---

## D6 — `POST /v1alpha1/preflight` answers 200 with findings; 4xx is reserved
## for a malformed request.

**Decision:** a *verdict about a workload* is a successful response.
`ok: false` carries the answer. Only genuinely malformed requests — absent
`config`, a non-string `config`, an unauthenticated or unadmitted caller —
get 4xx.

**Why:** this route exists to be called in a loop by an agent fixing a config.
A linter that HTTP-errors when your code is wrong is hostile to the loop it
exists to serve, and it trains callers to treat 400 as "retry" — which is
exactly wrong when the 400 means "your YAML is broken".

**Note the divergence from `from-repo`,** which answers 400 with the same
findings array. That is correct *there*: the caller asked to create a job and
no job was created, so the request failed. Here the caller asked for a verdict
and got one.

**Unchanged and load-bearing:** the same `parse_flashml_yaml` + `preflight`
pair, never a copy. Drift between a public CLI's rules and the API's means the
CLI blesses a config the API refuses.

---

## D7 — G-1 reuses the existing `shr_` share-token mechanism. No new repo,
## no new branch, no middleware change.

**Owner instruction:** *"with special token access on url they won't need to
worry about different repo or branch."*

**Decision:** confirmed against the code, and the mechanism already exists:

* `GET /v1alpha1/public/sandbox-sessions/{share_token}` — `public`-tagged,
  no session required;
* `apps/web/app/share/[token]/page.tsx`;
* `shr_` + `secrets.token_urlsafe(32)`, with `SESSION_SHARE_COLUMNS`
  narrowing the public payload **in SQL** rather than in the handler;
* a `middleware.ts` rule anchored at both ends, one path segment, rejecting
  `/shareholders`, `/share/abc/edit` and percent-encoded traversal.

**The actual gap is narrower than "build a public surface":** the share token
is scoped to a *sandbox session*, so the FC hibernation story is publicly
viewable and the **fault-tolerance job story is not**. Extending the same
pattern to a job is the work.

**Constraint carried forward:** widening the middleware pattern is how a
matcher meant to satisfy one requirement quietly unauthenticates the console.
A job share must be a *sibling* rule of the same tightness, never a loosened
one.

---

## D8 — Sequencing: `app.py` first, public URL last, deploy never (this
## session).

**Owner instruction:** *"build all three, deploy nothing"* and *"let's build
the public url afterwards"*.

**Order:** (1) `app.py` — it is a contended resource handed over by the peer
session and may be needed back; (2) the `flashml` package + MCP; (3) the
agent-in-sandbox loop; (4) market contract; (5) C-6.5 dual-mode hibernation;
(6) the public share page and console QA.

**Deploy is excluded, and not only because the owner said so.** Nothing is
deployed — ~28 local commits — and capacity migrations `0022`/`0023` have
never been applied to `flashml-dev`. The first deploy satisfying G-1 would
also apply another session's untested migrations to a real database. That is
not an unattended decision.

---

## D9 — The agent must not be able to spend rented money without a human.

**Decision:** the D-7 confirmation gate is built into the agent loop from the
start, not retrofitted.

**Why this is urgent rather than theoretical.** The peer session's rented
capacity is deliberately inert today (empty provider registry,
`RENTED_CAPACITY_DESTROY` off). The moment that registry is populated, this
session's agent surface can *see and spend* rented capacity. Retrofitting a
confirmation gate after the two halves meet is how an agent rents a GPU
because a prompt said so.

**Shape:** the MCP tool surface already omits pool administration, invite
minting, machine revocation and artifact deletion — the line being *whose
decision is it*, not *is it dangerous*. Spending joins that list.

---

## D10 — One price vocabulary across both sessions.

**Trigger:** a review of the peer's `/jobs/{id}/tradeoff` route found the
response's `price_reason` asserting *"priced at $0.16/hr … Another SKU costs
more"* while the `RoutingCard` six inches above on the same tab renders
`$1.279/hr` for `ecs-gpu` verbatim from `router/venues.py`. Same job, same
screen, two contradictory prices — and the completeness claim is simply false,
since the venue this deployment actually rents from is not in the list the
$0.16 was drawn from.

**Decision:** the trade-off panel and the market board answer two different
questions and must say so. Trade-off = *time vs money for this job*. Board =
*what compute is trading at*. Neither may make a completeness claim about the
other's scope.

**Immediate fix (this session, `app.py`):** copy only — state that $0.16 is a
published **RunPod pod-hour** and that RunPod is `acquisition: manual` (a
person starts that pod), state that the venue this deployment provisions
automatically is `ecs-gpu` which publishes no rate here and is priced live at
acquisition, and **drop the completeness claim**.

**Deliberately deferred:** `_tradeoff_rented_price` picks the cheapest RunPod
row with no regard for the job's requirements — `gpus_per_task` only gates
`has_gpu`, so a job needing 80 GB is still quoted a 24 GB A5000. Cheapest
*listed*, not cheapest *viable*. The proper fix needs an `alibaba-ecs` row in
`price_quotes` from a recorded `DescribePrice` reading, which is not a
two-day change. Documented as a clause rather than silently left.

**Standing operational fact this surfaced:** `price_quotes` rows go stale at
24 h and the seeded RunPod capture is from `2026-08-12T03:55:00Z`. Every price
on every surface renders `STALE` by demo day unless a refresh runs. The peer
session owns that script; this session must not write a second one.

**Correction, later the same day.** The peer measured dev rather than
estimating: the quotes were **already expired at 24.1 h**, not eight minutes
from it. Dev has been re-stamped (25 rows, amounts unchanged, `price_quotes`
is append-only so nothing was rewritten). **Prod is unverified and
untouched** — it needs `.env.prod` credentials no agent should source
unattended. Owner action, using the peer's script:
`flashml-cloud/scripts/prices/restamp_price_quotes.py --write` (dry-runs by
default; re-stamps a known rate with a fresh `captured_at` and says so — it
does not observe a new price or invent one).

---

## D11 — `_TRADEOFF_MAX_RENTED_STEPS` raised 16 → 64.

**Decision:** raise it.

**Why:** a caveat is only honest if it is *rare*. The truncation sentence
("the sweep stops at 16 machines, which is this answer's size limit and not a
fleet size where renting stops helping") is truthful, but at 16 it fires on
essentially every real HPO sweep — so the caveat becomes the normal answer and
the panel stops answering the question it exists to answer. At 64 it becomes
genuinely unusual.

**Consequence surfaced by the peer, and accepted rather than dismissed:** the
payload is trivial at 64 rows but the *rendering* is not — a 64-row table on a
job page scrolls the interesting rows (the `helps` → `no_marginal_gain`
boundary) off a demo screen. The peer is adding console-side row collapsing
around that transition, keeping the data intact. Noted here because "the
payload is small" was my reasoning and it was the wrong axis.

---

## D12 — Two tradeoff-route rendering fixes, and a correction to my reasoning.

**`price_reason` is suppressed when `renting.suited` is false.** A sentence
about how well we priced a machine that can never run this work is noise, and
implies a choice we did not make.

**`slots_reason` renders once, and the route's string is authoritative.**

**Correction worth recording, because the wrong reason was nearly acted on.**
I justified this as "the console's fallback can't know why the curve truncated,
so it would be guessing". That was **wrong**: both renderings display the
route's own `renting.slotsReason` verbatim — one string in two places, not a
console-authored paraphrase. The outcome is unchanged, but the stated reason
would have implied a general rule that the console may never re-display a
route-supplied string, which is a rule neither session wants. Position: kept
adjacent to where the curve stops, not in the header, because that is where a
reader is when the question arises.

---

## D13 — C-6.5 / C-6.4 hibernation evidence is ceded to `zolli-labs-d7`.

**Decision:** dropped from this session's queue.

**Why:** their second agent is already writing in `sandbox_orchestrator.py`
and `alibaba_sandbox.py`. Two writers in one subsystem on a deadline is how a
subtle merge defect ships. They were there first.

**Consequence for D3's agent-in-sandbox work:** it is built in a **new
module** and does not enter `sandbox_orchestrator.py` or `alibaba_sandbox.py`.
If that turns out to be impossible, this session asks before writing.

**Offered to them, not imposed:** the hosted agent supplies C-6.5's "two
genuinely different waits" more naturally than the current framing — deep
hibernation across the long training wait, light across the short gap between
tool calls, where the spec currently reaches for "gaps between evaluation
shards".

---

## D14 — This session runs as an orchestrator; subagents build.

**Owner instruction:** *"for not losing context you are the main orchestrator
and building by sub agent."*

**Decision:** the main session holds context, decisions, cross-session
negotiation and review. Subagents do file-level implementation against
explicit boundaries.

**Concurrency rule that makes this safe:** exactly one agent may own a
contended file. `app.py` has a single exclusive owner; the market agent is
scoped to *pure logic with no route wiring* precisely so it cannot collide
with the `app.py` agent; route wiring for the market contract happens in a
later serialized pass. Subagents do **not** commit — the main session reviews
and commits with explicit paths, because `git add -A` across interleaved
sessions already broke HEAD once today.

---

## D15 — D3's hosted agent is demoted behind the public page. The MCP
## surface is not.

**Amends D3 and D8's ordering. Argument made by `zolli-labs-d7`, accepted
here; recorded in their terms rather than paraphrased, because the reasoning
is the valuable part and a summary would blunt it.**

**The argument.** It is not the model-wrapper reading — Alibaba's own Kimi
article makes hibernating-between-turns a blessed use case, and PauseSession
is enabled. The objection is that it is **a detour from the thesis**:

> FlashML's claim is *we run your ML jobs across unreliable machines and lose
> nothing when one dies.* An agent hibernating between turns demonstrates the
> sandbox beautifully and advances that claim not at all. A judge who has just
> watched a training job survive its machine being destroyed and resume on a
> different GPU in another country will reasonably ask what the agent has to
> do with it — and the honest answer is "nothing, it's a second demo."

And the stronger version already exists: the **evaluation session that
hibernates while training runs** is the product using the sandbox for its own
purpose. Same C-6.5 pair, plus one thing the agent cannot carry — it answers
the rubric's listed deduction ("using VM isolation without explaining why you
actually needed it") with *"we execute user-supplied ML code from a submitted
repository"*, the strongest isolation justification available in this field.

**The cost side:** G-1 is a P0 disqualification gate, unstarted, with judging
running across a weekend behind a login wall. A second demo subject spends the
scarcest resource on the work least connected to the pitch.

**What is demoted:** the hosted agent-in-sandbox. Built only if time remains
after the public page.

**What is NOT demoted, and why the argument does not reach it:** the `flashml`
client and the **MCP server**. That is not a demo subject — it is the
developer surface for a compute market, on the track this submission is
entered in, answering "how does a person get work into this thing without a
browser" with the same book and the same deterministic router. It continues at
full priority.

**No conflict with the owner's instruction.** They said *"let's build the
public url afterwards"*, which this session had read as *last*. It only ever
meant *after the developer surface*. New wave-2 order: market route wiring →
job share page → hosted agent if time remains.

**One correction to the cost argument, in its favour.** G-1 cannot be closed
by this session at any ordering — it needs a deploy, and the owner restricted
this session to local commits. So the deliverable is the page *built and
green*, such that the owner's single deploy closes the gate rather than
starting it.

**Still unstartable by any agent, and still disqualifying:** G-2 (a ≤3-minute
public video) and G-4 (a row in the submission sheet).

---

## D16 — The public job payload publishes our typed events, never the
## submitter's bytes.

**Raised by `zolli-labs-d7` while reviewing D7's design. Accepted, with one
refinement.**

**The problem.** Reusing `/share/<token>` for jobs moves the entire security
property onto the column list, and `SESSION_SHARE_COLUMNS` was chosen against
an object with a handful of fields. A job reaches repo URL and ref, dataset
paths, artifact keys, machine ids, owner id, task parameters, and task stderr.

Their framing, which is the part that matters:

> A session's output is ours; a job's output is theirs.

We execute user-supplied code from a submitted repository. That code prints
whatever it prints. An unauthenticated page rendering a failed task's stderr
publishes whatever the submitter logged, including what they should not have.
The session page never had this problem.

**The refinement — excluding "failure detail" wholesale would delete the
story the page exists to tell.** The public run page's entire purpose is a
completed lifecycle *including* a machine dying and the work resuming
elsewhere. Failure is the demo. So the line is not failure-vs-success, it is
**authorship**:

* **Published:** our typed lifecycle events — `TASK_ATTEMPT_FAILED`,
  `TASK_EXHAUSTED`, `FAILURE_CLASSIFIED`, `RECOVERY_ACTION_SELECTED` — as
  kind, classification and timing. These are strings this codebase authored.
* **Withheld:** every event's `data` dict, any stderr tail, and any message
  interpolating user content. These are strings the submitter's code
  authored.

That keeps "an RTX 4090 was destroyed mid-run and a 3090 in another country
resumed at step 16298" fully renderable while publishing none of the
submitter's bytes.

**Also withheld:** repo URL and ref (the URL alone confirms a private repo
exists), owner id, dataset source paths, raw machine ids.

**Second point, accepted unchanged:** two token kinds in one URL space means a
bad token must not reveal which kind it is not. "No such token", "exists but
is the other kind", and "exists but is revoked" answer identically. This is
the doctrine `redeem_device_code` already runs — unknown, unapproved, expired
and already-redeemed are one indistinguishable answer so polling cannot
enumerate. A discriminated resolver is exactly where those three answers want
to diverge, which is why it is written down before it is built.

**Enforcement:** narrowed in SQL, as `SESSION_SHARE_COLUMNS` is — not as a
handler-side filter. A filter that lives in the handler is one refactor away
from being skipped.

**Note for the demo specifically:** the job on the public page will be one we
authored, so the leak is theoretical *for that job*. The mechanism still has
to be right, because it generalises to any job any user shares.

**Review:** the job column list goes to `zolli-labs-d7` before it lands.

### D16.1 — The provenance test, and what it disqualifies

**Raised by `zolli-labs-d7`. The question is not "which keys are safe" but
"which values did our code assign" — and the two come apart, because a key we
named can carry a value the submitter chose.**

> **The test, per field: can you name the line in *our* code that assigned
> this value, with no user input reaching it?**

* Timestamps, latencies, state-machine transitions, our own classification
  enums, event `seq` — ours. Publishable.
* Anything read back from a file the task wrote, parsed from stdout, or
  derived from a user-chosen name — **not ours**, whatever its type. Numbers
  are not safer than strings here, only harder to notice.

**This disqualifies the demo's headline number, and it was verified rather
than assumed.** `"resumed at step 16298"` looks like our integer. It is not:
`preflight.py:125` records that the relay *"globs `step-*.json` under
`workdir/out/ckpt`"*, and `:149` repeats that it *"globs `step-*.json`
files"*. The step is parsed from a **filename the task's code writes**. Its
key is ours; its value is the submitter's.

Corroborating evidence that this is not theoretical: `2026-08-11-open-gaps.md`
§5 item 4 records that *a non-numeric `step-*.json` re-fails every 0.3 s for a
whole task* — non-numeric step filenames are a real thing that occurs at
runtime, so the `\d+` in `_CKPT_CONVENTION` is a static advisory in preflight,
not an enforced guarantee.

**Consequence for the public page's claim.** The fault-tolerance story must
rest on control-plane facts, all of which are ours and all of which are
sufficient: the pod was destroyed, a different machine in another country
claimed the lease ~30 s later, and the job completed — timings, lease
transitions, attempt counts and the claiming machine are all assigned by this
codebase. **The step number is the submitter's and is not published.**

**Consequence beyond the page:** if a demo claim rests on a submitter-authored
value, that is worth knowing before Friday rather than after a judge asks
where the number came from.
