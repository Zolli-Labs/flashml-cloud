# Full-pipeline test findings — 2026-08-13

**Method:** the owner drove the real product end to end on dev — sign-up,
workspace, three host types (Apple-Silicon laptop, RunPod pod, Alibaba FC
sandbox), repo authoring, submission, execution — while this session diagnosed
every failure at the source. Everything below was **observed live**, not
inferred. Fixes were verified at the API level with no UI in the loop.

The one-sentence summary: **three hosts joined the fleet and, for a while,
zero of them could complete a task — for three unrelated reasons — and every
one of them enrolled successfully and was allowed to claim work.** Capability
is what the fleet claims; completion is what it delivers. The gap between
those two is where every finding below lives.

---

## 1. Fixed tonight

### 1.1 `uv venv` built task environments against whatever Python led PATH
**flashnode** · `executor/environments.py:388` · **fixed on
`fix/trusted-tier-execution` (`46df740`), 586 tests passed, pushed**

Bare `uv venv` discovers an interpreter from PATH. On a stock Ubuntu 20.04 pod
that is Python 3.8, and the resolve then fails with *"numpy==1.26.4 depends on
Python>=3.9"* — an error naming the **package** while the cause is the
**interpreter**. Three consecutive live failures, each requeueing the task.
The venv is now created with `--python sys.executable` — the same interpreter
the stdlib fallback two lines below always used. Regression test locks the
failure shape.

### 1.2 The demo workload hardcoded `/work` and died on every trusted host
**Zolli-Labs/zolli-demo** · `jobs/train.py` · **fixed on all three branches,
verified on the GitHub remote per branch**

`/work` is a naming convention, not a guaranteed mount. The trusted tier
(Colab, pods — hosts that cannot nest Docker) has no container; the agent
delivers the real workdir via `FLASHML_WORK_DIR`, and the argv rewrite only
covers `/work`-prefixed argv **tokens** — argparse defaults never pass through
argv. The script wrote checkpoints and `metrics.json` to the host's root
filesystem as root, exited 0, and failed the attempt with *"produced no
metrics.json"*. **Five consecutive times on a Python-3.11 pod.**

Worth stating plainly: the workload was written faithfully from
`writing-flashml-yaml.md`, and the guide never mentions `FLASHML_WORK_DIR`
(finding 3.1). The docs manufactured this bug.

### 1.3 A record corrected
Earlier tonight this session claimed machine capabilities are "snapshotted at
enrolment and never refreshed." **Wrong.** The agent re-registers on every
`flashnode work` start; the flags come from the **runner tier chosen on the
command line** (`agent/cli.py:443-447`). The Mac reported `modules-only`
because `--runner docker` registers no argv capability at all — see 2.1.

### 1.4 A swept variable must exist on every branch's script
**zolli-demo** · fixed, verified per-branch at the SHA level

The `sweep` branch swept `--hidden` while its `train.py` lacked the flag
(it lived only on `quick`): argparse **exit 2 on every machine
simultaneously**, with the visible error led by a Docker platform warning
that buried the argparse line. The job's code snapshot is pinned at submit,
so the fix required cancel + resubmit. Two lessons that outlive the demo:
error surfacing leads with container noise and hides the workload's own
last line (a flashnode papercut worth fixing), and branch-per-job repos need
their shared argument surface on **every** branch.

## 2. Open — flashnode (public repo)

### 2.1 The recommended runner registers a laptop as unable to run jobs
`agent/cli.py:443-447`: `argv_capable=(runner=="argv")`,
`unsandboxed_argv_capable=(runner=="trusted")` — **`--runner docker` sets
neither**, so a Docker-capable laptop registers as *Modules only*, is never
offered command jobs, and prints *"waiting — no work queued"* while six tasks
sit queued. `flashnode login`'s own post-enrol hint prints `--runner docker`
(`cli.py:138`; `doctor.py:505` same). Every user following the printed
instruction lands here; it cost the owner an hour tonight.

Also note `--runner argv` sets `module_capable=False` — the tiers are mutually
exclusive in registration, which is either deliberate policy or the same bug
from the other side. Needs a decision, then one-line fixes plus the spec §2.2
tier-aware hints. **Blocked tonight:** `cli.py` carries a peer session's
uncommitted `--ephemeral` work.

### 2.2 The trusted-tier contract (approved 2026-08-09) is partly unexecuted
§3 (workdir delivery) **shipped in 0.4.0** — the ROADMAP note saying otherwise
is stale. Still open from that spec: §2 (tier-aware health checks — a trusted
host that fails three times self-quarantines with a report naming Docker) and
§4/§5 (dependency-environment failure handling and cooldown).

### 2.3 `--ephemeral` is documented but not shipped
The RunPod guide says it is *required* on rented pods; flashnode 0.4.0 rejects
it (`unrecognized arguments` — hit live tonight). The implementation exists,
uncommitted, in the shared public-repo checkout (`cli.py`, `identity/*`,
tests). Whoever owns it: there is a tester waiting.

### 2.4 The FC-sandbox trusted worker stalls silently and holds its lease
**ROOT-CAUSED** by the same-day six-agent audit
(`2026-08-13-bug-audit-leases-fc-fragmented-devices.md` §0.1), and the
flashnode half is **fixed on `fix/trusted-tier-execution` (`c133c5f`), 588
tests passed, pushed**. Two layers:

- **Worker layer (fixed):** the sandbox was not hung — it was inside a
  dependency build it never logged, against an index it never chose. pip
  inherited FC's `/etc/pip.conf` (the lagging Aliyun mirror) because
  `_build` passed no index and no `--no-input`, and pulled toward the 1800 s
  ceiling in total silence; the per-job cooldown key meant each poisoned job
  paid a fresh build. Now: `PIP_INDEX_URL` pinned to real PyPI (env, so
  requirements-file `--index-url` overrides still win), `--no-input`, and
  log bracketing with dependency count and elapsed time. **And when a build
  did finish, the trusted tier capped every task at 600 s** —
  `TrustedArgvRunner()` built bare, `FLASHNODE_TASK_TIMEOUT_S` silently
  ignored (audit C1). Now honored, same default (3600) as the docker tier.
- **Protocol layer (open, coordinator):** heartbeat renewal is independent
  of progress, renewal is unbounded, and no force-expire/revoke primitive
  exists (audit C11) — so a stalled worker is unbreakable without revoking
  the machine. Fix shapes are designed **as one set** in the audit's §5;
  do not patch piecemeal.

## 3. Open — docs (they manufactured real failures tonight)

1. **`writing-flashml-yaml.md` teaches the `/work` contract without
   `FLASHML_WORK_DIR`.** A workload written from the guide breaks on every
   trusted host. This is how 1.2 happened.
2. **RunPod guide** documents unshipped `--ephemeral` (2.3); says login always
   suggests `--runner docker` (0.4.0 suggests `trusted` when Docker is absent
   — stale); names no minimum host anything (moot once 1.1 ships).
3. **`scripts/dev.sh`** missing-venv hint says `uv pip install -e
   ../../../flashruntime` — a relative-path install of a directory deleted
   2026-08-01, the exact pattern `CLAUDE.md` forbids. The owner pasted it
   tonight; it cannot work.

## 4. Open — scheduler / platform

1. **No progress watchdog on leases.** A machine renewing without ever
   committing a checkpoint looks identical to one training (2.4). N renewals
   with zero progress artifacts should surface, then requeue.
2. **No image-architecture gate.** Curated images are `linux/amd64` only; an
   arm64 Mac runs them under QEMU at ~1% native (measured: 11 s/epoch vs
   0.1 s). Nothing errors, nothing warns — the platform string is right there
   on the machine row. Fix is two-sided: multi-arch image builds (public repo
   CI) and a placement-time warning.
3. **Attempt cooldown:** the 3.8 pod claimed the same task three times in 30
   seconds, failing identically each time. Spec §5 covers this; unexecuted.
4. **Console:** `FleetPill` pluralisation ("1 machines online"). Trivial;
   listed so it is not lost.

## 5. What is proven at the API level, no UI

| Step | Status | Evidence |
|---|---|---|
| Auth via CLI credential | **proven** | `GET /v1alpha1/me` → 200 |
| Submit from repo (parse, preflight, dataset admission) | **proven** | `POST /v1alpha1/jobs/from-repo` → 201, job `36c647236e3a` |
| flashnode unit suite with fixes | **proven** | 586 passed, 12 deselected |
| Sweep fan-out, claim → run → checkpoint → **accept**, 6 tasks | **proven** | job `f8d6baf207a7` → `SUCCEEDED`; 6× `TASK_COMMIT_ACCEPTED` (12:41:56–12:50:01Z); 54 artifacts incl. all six `task-N/metrics.json` |
| Trusted tier end-to-end (shipped 0.4.0 agent + fixed workload) | **proven** | the pod committed five of the six tasks ~40 s apart; the workdir delivery was already in the 0.4.0 wheel — only the workload (1.2) was wrong. The fix-branch install is needed only for stock Python-3.8 pods (1.1) |
| Cancel via API | **proven** | `POST /jobs/36c647236e3a/cancel` → 200 (the poisoned first submit — see 1.4) |
| `e2e/` suite `LOCAL=1` against the fixed checkout | **not run** | the gate before releasing flashnode 0.4.1 |

## 6. The release gate

The dev environment installs `flashnode` from PyPI, so RunPod/Colab testers
get 1.1 only via a **flashnode 0.4.1 release** (which should also carry
`--ephemeral` once its owner lands it, and the 2.1 decision if made). Until
then, pods can install the fixed agent directly from the branch:

```
pip install "git+https://github.com/Zolli-Labs/flashml.git@fix/trusted-tier-execution#subdirectory=flashnode"
```

Before tagging: run the `e2e/` suite `LOCAL=1`, and remember the release
gotchas already on record — a red release run still publishes, and
docs-deploy always fails on tags.

## 6. The 14:00Z dev outage — connection exhaustion (root-caused live)

**What the operator saw:** the console erroring "internal error — see server
logs" / "Failed to fetch" on every job page; Render mailing instance-failure
notices every ~3 minutes; the pod logging Render's entire 502 HTML page as an
error string; a healthy task's results discarded on lease loss.

**The chain:** `db.connect()` opens a NEW psycopg connection per request →
Supabase SESSION pooler hard-caps at 15 clients → three workers (heartbeats +
checkpoint relay) + the console (ten endpoints × 5 s, each doubled by a CORS
preflight) + observer scripts exceeded 15 → `EMAXCONNSESSION`, requests hung
→ `/healthz` (deliberately DB-touching) blew Render's 5 s budget → instance
restart loop → 502 storm → checkpoint uploads failed (`IncompleteRead`/502),
lease expired mid-run, enforcing mode correctly 403'd the late uploads →
`lease lost during run — discarding result` → the re-claim burned an attempt
on a 502 code-artifact download. **Audit findings C5/C6/C12/C14/4.1 observed
live in one incident.** Not Render rate limiting; Render was correctly
restarting an instance that could not reach its database.

**Amplifier (flashnode, new finding):** the checkpoint relay retries every
unshipped file ~once per second with no backoff — a thundering herd that
re-flattens the API the moment it recovers. Fix: exponential backoff with
jitter in the relay. Goes into 0.4.1 with C1/C2.

**Mitigation (shipped):** `prepare_threshold=None` in `db.connect()` (safe
on both ports), then DATABASE_URL moved to the TRANSACTION pooler `:6543`
(dev; verified from outside: 25 concurrent connections vs. the 15 cap).
render.yaml's old "avoid :6543" comment is reversed with the reasoning, at
both DATABASE_URL sites. **Prod (`flashml-api`) still runs :5432 — flip it
(same one-digit edit) before any fleet touches prod, unless the pool below
lands first.**

**Design follow-ups (P0 before real users):**
1. Bounded connection pool at the `create_app(connect=...)` seam — one
   place, injected already; gives backpressure instead of theatrical death.
2. Checkpoint-relay backoff (above).
3. Console self-DDoS: ten polled endpoints × 5 s × CORS preflights. Roadmap
   already owns the real answer (SSE, Stage 6); until then a shared slower
   poll timer.

### 6.1 Post-fix re-test (job 292eb5c67fc9) — and the FC python-3.13 wheel gap

After the pooler fix: 30/30 concurrent requests where 15 was the death
line, sustained; all three venues claimed tasks simultaneously. 5/6 tasks
COMPLETED (pod + Mac). The job still ended FAILED because of a new finding:

**The FC sandbox image ships Python 3.13.13, and the curated sklearn
manifest pins numpy==1.26.4, whose wheels stop at cp312.** Every env build
on FC falls into a source build and dies (visible now thanks to the C2 log
bracketing — the fix that surfaces this class loudly). The scheduler then
handed task-005 back to the same broken node four times with no cooldown or
node exclusion (audit C15, observed live) until TASK_EXHAUSTED — one
incompatible machine burned a healthy task's whole budget while two healthy
machines sat eligible. FC workers killed and the machine left in the pool
idle; remaining leases requeued and completed within a minute.

Fixes filed: (a) curated manifests need pins with wheels for every
interpreter the fleet actually runs, or the agent must refuse to claim work
whose env cannot resolve on its interpreter (cheap preflight: check wheel
availability before claiming); (b) C15's cooldown/exclusion, already in the
audit's §5 set.
