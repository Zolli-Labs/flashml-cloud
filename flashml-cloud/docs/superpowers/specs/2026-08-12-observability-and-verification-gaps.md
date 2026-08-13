# Gaps: what is built, inert, or absent in observability and verification

**Status: gap analysis + spec, 2026-08-12. Written after surveying the code,
not from memory. Every claim below cites what was checked.**

Three sessions worked this repo today and none of them owned this area. This
document is the reconnaissance, the gaps ranked, and the design for closing the
ones worth closing before Friday.

---

## 1. What exists — and this is more than expected

### 1.1 A verification layer, correct and deliberately advisory

`db.record_verification` (`db.py:3081`) writes a per-slice verdict on a task:
`pass` / `flag` / `unknown`. Its docstring states the design rule plainly:

> *"Nothing in this system reads what this writes to refuse a lease, withhold a
> credit, fail a commit or change placement… This function exists so an
> operator can look; **it is not a gate and must never become one by
> accident.**"*

And the discipline around `unknown` is exactly right:

> *"the one mistake this whole layer is built to avoid is a 'could not tell'
> arriving as a `pass`, and a tolerant writer is exactly how that happens."*

**This is good architecture and none of it should change.** Advisory-by-design
is the correct answer while settlement is unverified — a gate built on one
weak slice would refuse honest work.

### 1.2 One slice, and it is a real one

`verify.timing_verdict` (`verify.py:82`) compares a task's duration against
`db.peer_task_durations` for the same job, and is called from the attempt
settlement path (`app.py:8348`).

---

## 2. The gaps, ranked by value before Friday

### G-A. The verdicts are unreadable. The layer is inert.

**Checked:** no route in `app.py` mentions `verifications`. No console surface
consumes a verdict. `record_verification` has exactly one caller and zero
readers.

So the layer writes to a table an operator cannot open. Its docstring's own
justification — *"so an operator can look"* — is not satisfied by anything in
the product. This is the cheapest high-value gap in the repo: the hard part
(deciding what advisory means, getting `unknown` right) is done.

**Severity: high, cost: low.**

### G-B. No correlation id. D-4 is scored and absent.

**Checked:** `grep` for `correlation_id` / `trace_id` across
`flashml_cloud_api/` returns nothing, and no migration defines such a column.

The competition asks (D-4, P1) for *"one correlation id spanning `session_id` →
`sandbox_id` → `job_id` → `task_id` → `lease_id`, persisted append-only,
covering the hibernated window."*

Every one of those ids exists individually. Nothing joins them, so answering
"what happened to this piece of work" means five queries and a human holding
the thread. **This is also the layer the owner named** — "reading the logs" is
not possible in any useful sense without it.

**Severity: high (scored), cost: medium.**

### G-C. Structured logging is ad-hoc

**Checked:** `json.dumps({"text": ...})` appears in a handful of places
(`artifact_mirror.py:617`, `:638`, the sandbox reconciler); most modules use a
bare `logging.getLogger(__name__)` with f-strings. There is no shared helper,
so no log line is guaranteed to carry the ids that would make it searchable.

**Severity: medium, cost: low — and it is the same work as G-B.**

### G-D. Only one verification slice

Timing is a genuine signal but a narrow one. Slices that need no new
infrastructure and are honest as *advisory*:

- **artifact presence** — the task claimed success; do the artifacts it
  registered actually exist in the store at the sizes recorded?
- **checkpoint monotonicity** — did the relayed steps advance, or did a resumed
  attempt silently restart from zero?
- **duplicate work** — did two attempts of the same task both commit?

**Severity: medium, cost: low each. Advisory only — none of them gates.**

### G-E. `can_cover` has no caller

**Checked:** `marketplace.can_cover` (`marketplace.py:324`) is referenced by
two docstrings and nothing else. Nothing in this system refuses work an account
cannot pay for, on any path. Recorded as OC-D10; not built.

**Severity: medium. Out of scope here — it is a capacity decision, not an
observability one.**

---

## 3. Design — the smallest thing that closes G-A, G-B and G-C

### D-1 — One id, minted at the edge, carried everywhere

A `correlation_id` (uuid) is minted **once**, at the outermost thing a user
initiated — a job submission, a sandbox session, an acquisition — and every
row and log line downstream carries it.

It is **not** a request id. A request id dies with the response; this outlives
the request by design, because the work it names outlives the request. It must
survive a hibernation boundary, which is the specific thing D-4 asks for and
the specific thing a request id cannot do.

**Where it is stored:** a nullable `correlation_id` column on the tables that
already anchor the chain, and nothing else. No new join table — a chain that
needs a join to be read is a chain nobody reads.

### D-2 — Absent is `null`, never a fresh id

A row with no correlation id is honest about it. **Minting one on read, or on
write when none was supplied, is forbidden**: it manufactures a thread that
does not exist and makes two unrelated pieces of work look related. This is
the same rule as `unknown` in `record_verification`, and it fails the same way
if a tolerant writer papers over it.

Pre-existing rows stay null forever. That is correct.

### D-3 — One log helper, and it is the only way to log with ids

A single function that emits structured JSON and takes the ids as arguments,
so a log line either carries the chain or does not exist. Ad-hoc
`json.dumps({"text": ...})` sites migrate to it.

**Never log a value the submitter authored.** `PROGRESS.md` Rule 7's second
question applies here in full: a machine's `name` is its self-reported
`hostname` (`enrolment.py:164`), stderr is the task's bytes, and a log line is
a publication like any other. Log ids, kinds, timings and our own enums.

### D-4 — The read surface makes the verdicts visible, and says what they are not

A route returning a job's verifications, and a console panel rendering them,
with copy that states the property the layer's docstring insists on: **these
are observations, not gates.** A verdict of `flag` did not withhold anything
and did not refuse anyone. A UI that implied otherwise would turn an advisory
layer into a perceived gate, which is the accident its author explicitly warned
against.

`unknown` renders as *"could not tell"* — never as a pass, never as a blank.

---

## 4. Ownership, because three sessions are live

- `app.py` — **claimed by another session** for the public share page. Routes
  here are handed over, not taken.
- `apps/web/**` — **claimed by a third session** for the console sweep.
- Free, and where this work starts: `verify.py`, `db.py`, `migrations/`,
  `capacity/**`, and a new logging module.

**So the plumbing lands first and the surfaces follow.** That ordering is
forced by ownership, and it happens to be the right one anyway: a route over an
empty chain shows nothing.
