# Alibaba integration — design spec and implementation plan

**Status:** authoritative for the competition build, 2026-08-11. Supersedes the
architecture sections of `plans/2026-08-11-alibaba-competition-demo.md`, which
was written before the account was measured and assumed `us-west-1` and process
continuity. Requirements IDs come from
`2026-08-11-competition-requirements.md`; positioning from
`2026-08-11-alibaba-submission-dossier.md`.

**Scope:** two services — **FC Agent Sandbox** (compute) and **OSS**
(artifacts). §8 lists what stays unbuilt.

**Deadline reality.** Now: 2026-08-11 ~10:20 PT. Code freeze **Aug 13 EOD**.
Video and evidence **Aug 14**. Submit **Aug 15**, buffer only. That is roughly
**2.5 working days**. Every decision below is made for that budget; §7 states
what gets cut and in what order.

---

## 1. What today established

Measured on account `5055584162230015`, not assumed.

Measured n=5 unless noted. Evidence in `.evidence/`:
`alibaba-lifecycle-*`, `alibaba-flashnode-probe-*`, `alibaba-open-questions-*`.

| Fact | Value | Consequence |
|---|---|---|
| `pause()` in `us-west-1` | `403 PauseSessionForbidden` | **us-west-1 is dead for this feature.** Not a whitelist problem — a region problem |
| `pause()` in `ap-southeast-1` | **works** | Region is fixed: `ap-southeast-1` |
| Create latency | **p50 901 ms · p95 1851 ms** | Off the demo's critical path either way |
| **Pause latency** | **p50 2635 ms · p95 3075 ms** | Pausing is not instant. Budget for it; never pause inside a latency-sensitive step |
| **Wake latency** | **p50 1085 ms · p95 1598 ms** | Matches Alibaba's documented ~1 s baseline. The headline number for C-6.5 |
| State continuity | **5/5** | Filesystem and prepared environment survive |
| **Background process across hibernation** | **SURVIVES — 5/5** | See D2. A live `flashnode work` kept polling across the pause; lease claims went 13 → 19 |
| **TTL while paused** | **clock STOPS — confirmed at 45 minutes** | `end_at` was byte-identical across **10 polls over 2709 s** of hibernation. A separate 90 s-TTL sandbox reconnected 64 s past its own expiry was also intact. Evidence: `alibaba-long-hibernation-20260811T193839Z.json` |
| **Wake after a LONG hibernation** | **1212 ms** after 45 min | Essentially the same as the 1085 ms p50 after short pauses. **Hibernation duration does not slow the wake** — the number we show on stage does not degrade with the length of the wait |
| **Process + filesystem after 45 min** | **both intact** | marker sha256 identical, pid still in `/proc` |
| `connect()` without `timeout` | **re-arms TTL to exactly 300 s** | Always pass `timeout` explicitly, or every wake silently cuts the sandbox to five minutes |
| `pause(keep_memory=False)` | behaves identically to `True` | FC appears to always take a memory snapshot; the flag is a no-op here. n=1 per variant — **uncertain, do not claim** |
| Sandbox environment | Debian 13, **Python 3.13.13**, 2 vCPU, 2 GB RAM, 10 GB disk, non-root `user` with sudo, **no Docker, no `ps`** | `--runner trusted` is the only viable tier, as expected |
| `flashnode` install in sandbox | **1.7 s**, 21 MB | Pre-baking a template is an optimisation, not a requirement |
| OSS | activated, mirror verified live | Artifact path is open |
| Total Alibaba spend for all of the above | **≈ $0.035** | Far under the ceiling |

### 1.1 A correction: processes DO survive hibernation

The first smoke test reported `process GONE` and the spec was written around it. **That was a measurement artifact and the conclusion was wrong.**

The template has no `procps`, so the check `ps -p $PID >/dev/null && echo ALIVE || echo GONE` failed on `ps` itself with exit 127 and took the `GONE` branch every single time — it never tested anything. Re-measured with `[ -d /proc/$PID ]`, the process survives, and more than survives: a running `flashnode work` **continued serving lease claims across the pause**.

The lesson is general enough to write down: a negative result from a probe whose failure mode is indistinguishable from the thing it is probing is not a result. `ps` missing and the process missing produced identical output.

---

## 2. Architecture decisions

### D1 — FC Agent Sandbox hosts a FlashNode. It is a machine, not a new backend.

The sandbox runs `flashnode work --runner trusted` against a dedicated pool.

**Why.** It reuses registration, pool-scoped placement, lease claim, heartbeat,
checkpoint relay, artifact staging, output validation, and exactly-once
acceptance — all shipped and tested. A new `ExecutionBackend` or a
controller-mediated command path would reimplement each of those in two days
and would forfeit the claim that matters: **one worker protocol spanning owned
machines and Alibaba compute.**

Rejected: `ExecutionBackend` (freezes the wrong public interface on a deadline);
controller-driven `commands.run` (works, but the sandbox stops being a pool
member and the differentiator evaporates). The second stays as fallback R2.

### D2 — The worker sleeps through the pause. Do not restart it on wake. *(revised)*

**Superseded.** The earlier version of this decision was built on the broken
`ps` probe in §1.1 and said the opposite.

Measured, 5/5: the `flashnode work` process **survives the pause and resumes
polling**. Lease claims went 13 → 19 across a hibernation — the worker did not
merely exist afterwards, it went back to work. Combined with the frozen TTL
clock, the guest is genuinely suspended and restored, not rebuilt.

So the wake path is: `connect(timeout=…)` → verify marker → re-mint OSS URLs →
**the worker is already there**. No re-launch, no re-registration, no recovery
of node identity. A whole path leaves the design.

**What we may now claim, because it is measured:** the prepared environment
*and the running worker* survive the wait. **What we still may not claim:**
that `keep_memory=False` behaves differently (n=1, and it did not), or anything
about light-versus-deep tiering — there is no separate light-hibernation API in
the SDK, only `pause(keep_memory: bool = True)`.

**The product argument is unchanged and now cleaner.** FC suspends a machine
and returns it whole; FlashRuntime survives machines that never come back. The
demo shows both: a sandbox that sleeps through the wait and wakes still
working, and a training worker that is killed outright and whose task completes
anyway on another host. Two different guarantees at two different layers, which
is exactly the point.

### D3 — Exactly one machine in a dedicated pool.

The sandbox machine belongs to a pool containing only itself. No public task can
claim it; no evaluation task can escape to another host. Pool membership is
already the seventh placement gate and already fails closed.

### D4 — One-session machine credential, revoked on every terminal path.

Reuse `enrolment.py` / `db.insert_machine` / `set_machine_token` — do **not**
invent a second credential type. Written into the sandbox at
`FLASHNODE_CREDENTIALS` with mode `0600`, removed from disk once the agent has
registered, revoked in `finally` alongside `kill()`.

The job child receives FlashNode's existing environment allowlist. It never sees
the machine token, the Alibaba key, or any coordinator credential.

### D5 — The sandbox gets **presigned OSS URLs**, never credentials.

`ArtifactStore` exposes only `put_file` / `get_file`; there is no `sign_url`
(verified). Options considered:

| Option | Verdict |
|---|---|
| Alibaba access key inside the sandbox | **Never.** User ML code runs there |
| Proxy every byte through our API | Works, but forfeits D6's independence |
| **Presigned URLs minted by the cloud API** | **Chosen.** No credential in the sandbox; scoped to one object; expires on its own; smallest change |
| STS scoped to `jobs/<id>/` | Correct and more general; keep for post-competition |

Add `sign_get` / `sign_put` on the cloud side over `oss2.Bucket.sign_url`. Do
not widen the public `ArtifactStore` protocol for this.

**Credentials must be re-minted on wake.** A URL signed before a multi-hour
hibernation is expired when the sandbox returns. Requirement D-7 names exactly
this (*"valid credentials after hibernation"*). The wake path signs fresh URLs
as its first action after the health check.

### D6 — The external event is a model artifact appearing in OSS.

Not a timer, not a poll of our own database. A provider-neutral object store is
a better event source than our control plane's opinion, and it is checkable by a
judge. It also means the wake→evaluate path does not require our API to be
alive — which turns the API-restart rehearsal into a real property.

### D7 — Session state is an append-only ledger with compare-and-set transitions.

Every transition records an **observed** state, never an intended one. After any
call that may have succeeded server-side but failed in transit, reconcile by
inspecting, then append what was observed. The API must be restartable mid-run
and resume from `external_sandbox_id`.

### D8 — Deep hibernation for the training wait. Light hibernation only if proven.

Deep is measured and works. Light hibernation requires the **Pro** tier; treat
it as a P2 bonus for gaps between evaluation shards, and only after a
measurement proves the account has it. Do not put it on the critical path.

### D9 — Region `ap-southeast-1`, fixed.

Not negotiable — it is the only region where the feature exists. The ~170 ms RTT
from California adds to the 946 ms wake and is acceptable; report the number
honestly rather than hiding it.

### D10 — Sandbox timeout must exceed the whole demo, and pausing does not stop the clock.

The E2B `Sandbox.create(timeoutMs=...)` bound and the FC session TTL both keep
counting while hibernated (Alibaba doc 3028695: TTL "仍从原始创建时间累计").
A sandbox that expires mid-hibernation is gone with its environment.

**Set the timeout to at least 3× expected training duration, and assert the
remaining budget before pausing.** This needs an explicit test (§6).

### D11 — Everything is killed in `finally`, and a reconciler sweeps orphans.

A forgotten sandbox bills by the second against a voucher that expires
2026-08-15. Cleanup is not best-effort.

---

## 3. Component design

### 3.1 Module map

```
flashml-cloud/apps/api/flashml_cloud_api/
├── settings.py              MODIFY  fc_sandbox_* + oss_* fields, all-or-nothing
├── alibaba_sandbox.py       NEW     SandboxGateway protocol + E2B impl + fake
├── alibaba_oss.py           NEW     bucket client + sign_get/sign_put
├── sandbox_sessions.py      NEW     ledger repository + state machine
├── sandbox_orchestrator.py  NEW     the reducer that drives one session
├── db.py                    MODIFY  session/event queries only
├── app.py                   MODIFY  4 routes + 1 public share route
└── migrations/0014_sandbox_sessions.sql   NEW

apps/web/
├── lib/sandbox-session.ts             NEW  types + event normalisation
├── components/jobs/SandboxLifecycle.tsx NEW  the one evidence view
├── app/(console)/jobs/[jobId]/page.tsx MODIFY  mount it
└── app/share/[token]/page.tsx          NEW  PUBLIC, no auth — G-1

flashml/e2e/competition/          NEW  train.py, evaluate.py, flashml.*.yaml
flashml-cloud/scripts/competition/
├── alibaba_fc_sandbox_smoke.py   EXISTS
├── measure_lifecycle.py          NEW  5× create/pause/resume → p50/p95
└── run_demo.py                   NEW  unattended end-to-end
```

### 3.2 `SandboxGateway`

```python
class SandboxGateway(Protocol):
    async def create(self, *, template: str, timeout_ms: int,
                     metadata: dict[str, str]) -> SandboxObservation: ...
    async def connect(self, sandbox_id: str) -> SandboxObservation: ...
    async def run(self, sandbox_id: str, argv: list[str], *,
                  timeout_s: int, background: bool = False) -> CommandEvidence: ...
    async def write_file(self, sandbox_id: str, path: str,
                         data: bytes, *, mode: int = 0o600) -> None: ...
    async def pause(self, sandbox_id: str) -> SandboxObservation: ...
    async def inspect(self, sandbox_id: str) -> SandboxObservation: ...
    async def kill(self, sandbox_id: str) -> SandboxObservation: ...
```

`SandboxObservation` carries `sandbox_id`, `state`, `observed_at`,
`latency_ms` — small typed values, never raw SDK dicts (which can carry
secrets). The synchronous E2B SDK is wrapped in `asyncio.to_thread` with
explicit timeouts. A `FakeSandboxGateway` implements the same protocol for
tests, including the allowlist 403, a transport failure whose call actually
succeeded, and an idempotent double-kill.

`Settings`: `fc_sandbox_enabled`, `fc_sandbox_api_key`, `fc_sandbox_api_url`,
`fc_sandbox_domain`, `fc_sandbox_region`, `fc_sandbox_template`,
`fc_sandbox_pool_id`, `fc_sandbox_timeout_ms`, plus `oss_bucket`,
`oss_endpoint`, `oss_access_key`, `oss_secret`. All-or-nothing when enabled;
redacted in every `repr`, log line, and exception.

### 3.3 Data model — migration `0014`

```sql
create table public.sandbox_sessions (
  id              uuid primary key default gen_random_uuid(),
  owner_id        uuid not null references auth.users(id),
  pool_id         uuid not null references public.pools(id),
  machine_id      uuid references public.machines(id),
  training_job_id text not null,
  evaluation_job_id text,
  provider        text not null default 'alibaba-fc-sandbox',
  region          text not null,
  template        text not null,
  external_sandbox_id text unique,
  state           text not null check (state in (
                    'REQUESTED','ACTIVE','PREPARED','HIBERNATED',
                    'RESUMING','EVALUATING','SUCCEEDED','FAILED','TERMINATED')),
  marker_sha256   text,
  share_token     text unique,          -- G-1 public view
  created_at      timestamptz not null default now(),
  updated_at      timestamptz not null default now(),
  terminated_at   timestamptz,
  error_code      text,
  error_message   text                  -- sanitized only
);

create table public.sandbox_events (
  id          bigserial primary key,
  session_id  uuid not null references public.sandbox_sessions(id) on delete cascade,
  sequence    bigint not null,
  type        text not null,
  source      text not null,            -- 'controller' | 'fc' | 'runtime'
  observed_at timestamptz not null default now(),
  latency_ms  double precision,
  data        jsonb not null default '{}'::jsonb,
  unique (session_id, sequence)
);
```

No token column, ever. RLS mirrors the existing owner-scoping; a non-owner gets
404, not 403.

### 3.4 Lifecycle

```
REQUESTED
  └─ create(timeout_ms) ────────────────► ACTIVE          [observed]
       └─ write marker + credential
          launch flashnode (background)
          await registration ───────────► PREPARED        [observed]
             └─ assert remaining TTL > budget   (D10)
                pause() ─────────────────► HIBERNATED     [observed, ~2.7s]
                   │
                   │  training runs elsewhere; a worker is killed;
                   │  the lease expires; another node restores
                   │
                   └─ model object appears in OSS  (D6)
                      queue eval task FIRST, then
                      connect(sandbox_id) ──────► RESUMING → ACTIVE  [~0.95s]
                         └─ verify marker hash
                            re-sign OSS URLs      (D5)
                            RE-LAUNCH flashnode   (D2)
                            worker re-registers ─► EVALUATING
                               └─ accepted commit ► SUCCEEDED
                                     └─ kill + revoke ► TERMINATED
```

**Ordering on the wake path — corrected 2026-08-11.** The first draft said
"queue the evaluation task *before* resuming, so wake latency reads as
time-to-productive." That is a race, and the orchestrator agent caught it:
because the worker **survives hibernation and resumes polling immediately**
(§1.1), it can claim a pre-queued task in the ~1–2 s before `artifacts.json`
has been written, and find no model to evaluate.

Corrected order:

```
connect(timeout=…)          → measure WAKE latency (sandbox observed active)
verify_worker               → marker hash + pid + still claiming
re-mint presigned URLs, write artifacts.json   → assert it exists
submit the evaluation task  → measure TIME-TO-FIRST-CLAIM separately
```

This is strictly better evidence, not merely safer: it yields **two honest
numbers** — wake latency, and time-to-productive measured from submit to claim
— where the original ordering blended them into one. The presign still happens
after the wake, so nothing about credential expiry changes.

### 3.5 Routes

```
POST /v1alpha1/jobs/{job_id}/sandbox-evaluation   -> 201 {session_id, share_url}
GET  /v1alpha1/sandbox-sessions/{id}
GET  /v1alpha1/sandbox-sessions/{id}/events
POST /v1alpha1/sandbox-sessions/{id}/cleanup
GET  /share/{share_token}                          PUBLIC, no auth   [G-1]
```

The browser may not choose template, region, pool, command, or external sandbox
id. The share route is read-only, exposes no ids beyond suffixes, and is the
auto-DQ insurance.

### 3.6 OSS layout

```
oss://<bucket>/jobs/<job_id>/ckpt/step-<n>.json
oss://<bucket>/jobs/<job_id>/model.pt
oss://<bucket>/jobs/<job_id>/metrics.json
oss://<bucket>/sessions/<session_id>/eval/metrics.json
```

Parts first, manifest last, SHA-256 verified. **Keep the model in OSS, not on
sandbox disk** — deep-hibernation disk bills as `memory × 2 + disk` with no free
allowance, so a lean 15 GiB sandbox hibernates at 23 GiB instead of 38, ~39.5%
cheaper. Design rule: **environment stays, data streams.**

---

## 4. What the judge sees

1. One job page. Two resource groups: an owned training pool, and one Alibaba
   evaluation sandbox.
2. Training submitted. Sandbox created and prepared in parallel; marker and
   template digest shown.
3. Sandbox **deep-hibernated** — observed state, pause latency, and a running
   "active compute avoided" timer.
4. A training worker is killed. Lease expires, second node restores from the
   last checkpoint, continues. *Said out loud: this is cross-node recovery, not
   cross-provider migration.*
5. Final model committed to OSS. That object is the event.
6. Same `sandboxId` woken — **946 ms**, marker verified, worker relaunched,
   evaluation claimed and accepted.
7. Cost panel: measured wait × published rates, both baselines (§ dossier 3.1),
   and the OSS disk-shrink result.
8. Cleanup: sandbox destroyed, credential revoked, zero live sandboxes.

---

## 5. Implementation phases

### Phase 1 — today (Aug 11 PM) · unblocks everything

- [ ] Create OSS bucket in `ap-southeast-1`, private, no public read.
- [ ] `measure_lifecycle.py`: 5× create/pause/resume → p50/p95 for C-6.5. Also
      probes light hibernation, and probes whether a long `timeout_ms` survives
      a pause (D10).
- [ ] `settings.py` fields + `alibaba_sandbox.py` gateway + fake + tests.
- [ ] `alibaba_oss.py` with `sign_get`/`sign_put` + tests.

### Phase 2 — Aug 12 · the loop

- [ ] Migration `0014` + `sandbox_sessions.py` repository, CAS transitions, RLS
      tests.
- [ ] Ephemeral machine provisioning reusing `enrolment.py` (D4) + revoke path.
- [ ] `sandbox_orchestrator.py` — the reducer, restartable from
      `external_sandbox_id`.
- [ ] Demo workload: deterministic 60 s CPU training writing
      `out/ckpt/step-*.json` + `model.pt`; evaluator consuming an
      `artifact://` model. Three clean kill/restore runs on two owned nodes.

### Phase 3 — Aug 13 · visible, then freeze

- [ ] `SandboxLifecycle.tsx` on the job page + `/share/[token]` public route.
- [ ] Three consecutive unattended `run_demo.py` runs.
- [ ] One API-restart-during-hibernation rehearsal.
- [ ] Cost panel from measured durations.
- [ ] **Freeze at EOD.**

### Phase 4 — Aug 14 · evidence

- [ ] Record the 3-minute video; keep an uncut technical run.
- [ ] Evidence bundle: versions, commit, template digest, timings, hashes.
- [ ] Sheet row complete: track **Software for Agents**, live URL, video, deck,
      repo, Alibaba Use cell, real spend figure.
- [ ] **Submit.** Aug 15 is buffer, not workspace.

---

## 6. Tests

Written before the code they cover, per house practice.

| Area | Test |
|---|---|
| Gateway | every call against the fake; timeout; retryable transport failure; terminal 403; idempotent double-kill; **no key in any repr/exception/log** |
| Ledger | CAS rejects a second controller moving HIBERNATED→RESUMING; duplicate trigger yields one evaluation job; non-owner gets 404 |
| Credential | token returned once; job child cannot read it; after cleanup every agent call is 401 |
| Pool | evaluation cannot claim on a non-FC node; a public job cannot claim the FC node |
| **TTL (D10)** | a sandbox created with a short timeout and paused past it is observed dead, not silently assumed alive |
| **Wake (D2)** | after resume, marker matches **and** the worker process is absent until relaunched — asserts the honest claim |
| OSS | presigned GET works, expired URL fails, wrong prefix fails, re-mint on wake succeeds |
| Restart | API killed mid-hibernation resumes from `external_sandbox_id` and completes once |
| Cleanup | controller exception injected at each state still terminates and revokes |

Plus the existing suites stay green: runtime, node, cloud API, web.

---

## 7. Cut order

If Phase 3 is at risk, cut in this order and say so in the writeup:

1. Light hibernation (D8)
2. Elasticity probe (C-6.1) — quote the 150-concurrent account cap instead
3. E2B side-by-side (C-6.3) — needs an E2B account we may not have
4. Isolation probe (C-6.2)
5. Live training-recovery — fall back to recorded uncut evidence
6. OSS — coordinator artifacts still work; the loop survives

**Never cut:** the hibernate→wake→evaluate loop, cleanup, the public share
route, or the honesty of any claim.

---

## 8. Deliberately not built

Qwen / Model Studio (no LLM in the execution path — adding one is the model
wrapper the rubric penalises) · SLS / CloudMonitor (P2; our ledger is the P1
evidence) · ECS as a third tier (ready, but after the main two) · RAM/STS
(presigned URLs suffice) · ACR (built-in template; and ACR Economy cannot serve
FC images — a competitor was quoted $1,400 to escape that) · PAI-DLC ·
ECI · ACK · EventBridge · MNS · AMD/ROCm.

State the omissions on the slide. In a field where one team lists thirteen
services, explaining what you did not use reads as judgement.

---

## 9. Risks

| # | Risk | Mitigation | Fallback |
|---|---|---|---|
| R1 | ~~pause not enabled~~ | **RESOLVED** in `ap-southeast-1` | — |
| R2 | ~~Sandbox expires mid-hibernation~~ | **LARGELY RESOLVED 2026-08-11.** A 45-minute hibernation survived with `end_at` byte-identical across 10 polls — the E2B sandbox TTL genuinely stops while paused, and the wake was 1212 ms, no slower than a short one. **Still true and still required:** always pass `timeout` explicitly on `connect()`, since a bare connect re-arms to exactly 300 s. **Residual:** Alibaba doc 3028695 describes an FC *Session* TTL accruing from creation; that is a different object and has not been separately observed. 45 min is evidence, not a proof of unboundedness — do not claim multi-hour without measuring one | If a very long wait is ever needed, measure at that duration first |
| R3 | ~~process continuity~~ | **CONFIRMED GONE.** Relaunch on wake is the design | — |
| R4 | Voucher expires Aug 15, Demo Day Aug 22 | Fund the balance; +1 the Discord extension request | Recorded demo; share page degrades gracefully |
| R5 | Singapore latency visible on stage | Report the measured number | Pre-recorded wake segment in the video |
| R6 | 2.5 days is not enough | §7 cut order, daily gate | Ship the loop; drop every multiplier |
| R7 | Trusted runner reads as weak isolation | FC is the outer boundary; child env allowlist; dedicated pool; one-session token | Say plainly what the boundary is |

---

## 10. Open questions to close in Phase 1

1. Does the account have **Pro** tier (light hibernation), or Std only? — probe.
2. What `timeout_ms` survives a long pause? — probe (D10).
3. Does `flashnode work` need any patch to run inside the sandbox (no Docker,
   non-root, outbound-only)? — run it before Phase 2 depends on it.
4. Does the coupon actually draw down against FC and OSS usage? — check
   **Billing → Coupons → View Deduction Details** after Phase 1's measurements.
