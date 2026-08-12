# Checkpointing is always on — decision, evidence, and the upstream backlog

**Date:** 2026-08-11
**Status:** decided and implemented (cloud-only)
**Supersedes:** nothing. This is the first time checkpointing has been reachable
from the product at all.

---

## 1. The bug this closes

`flashnode/executor/loop.py:466` gates **both** the incremental checkpoint relay
**and** `inputs["resume"]` staging on a single test:

```python
if payload.get("checkpoint") is not None:
```

That key was absent from `ALLOWED_KEYS` in `flashml_yaml.py`, `compile.py` never
emitted it, and **no module in the cloud API set it anywhere**. The only two
callers in the whole workspace were hand-built JobSpecs:
`e2e/test_training_resume.py:43` and `e2e/competition/run_local_recovery.sh:471`.

> **Every job submitted through the console shipped no checkpoints while it ran
> and could not resume after machine death. It restarted from step 0.**

Fault tolerance is FlashML's headline claim. `components/landing/RecoveryDemo.tsx`
sells it on the marketing page and `components/auth/AuthShell.tsx` puts
"Leases · checkpoints · deterministic recovery" on the sign-in screen.

### Why the test suite never caught it

`e2e/test_training_resume.py` is green, and stays green, because it **hand-builds
its JobSpec** and so bypasses the authoring surface entirely. It proves the
*runtime* resumes. It proves nothing about whether a *user's* job resumes. Any
future test of this behaviour must go through `compile_to_jobspec`, not around it.

## 2. The decision

**Always on.** `compile_to_jobspec` and `compile_federated_round` unconditionally
emit `parameters["checkpoint"] = {}`. There is **no `checkpoint:` YAML key**.

Three facts forced this shape:

1. **The fix is cloud-only.** `flashruntime/recipes/command.py:199-200` already
   forwards the key verbatim into `task.payload` — verified in the *installed*
   pin at `apps/api/.venv/.../flashruntime/recipes/command.py:199`, not merely in
   the source tree. No public-repo change, no PyPI release, no four-site version
   bump. The runtime and the agent have been ready the whole time; only the
   compiler was silent.

2. **The value is never read.** The relay's directory (`workdir/out/ckpt`,
   `loop.py:476`) and glob (`step-*.json`, `loop.py:130`) are hardcoded. A
   `checkpoint: {dir, glob}` key would be accurate today and a lie the moment
   anyone changed it. `{}` is the shape this repo's own e2e already sends and the
   one the SDK path emits (`flashruntime/workloads/command.py:146-147`).
   Deliberately **not** `plan_v1alpha1.CheckpointPolicy` — flashnode honours none
   of its three fields, and the relay polls at a fixed 0.3s (`loop.py:117`).

3. **The cloud has exactly one bit.** Because the gate is a single presence test
   on a value nobody reads, "relay on, resume staging off" **cannot be expressed
   from the cloud**. Splitting them requires changing flashnode. So the audit's
   preferred middle option was never actually cheaper than its blocking one —
   both are upstream. The real choice was binary, and the feature was 100% dead.

Not emitted from `build_evaluation_jobspec` (`app.py`): one short task on one
pinned sandbox machine, no lease to lose.

## 3. Accepted risks

### R1 — a coordinator outage at task start is still fatal (residual)

`checkpoint_latest` raises a bare `RuntimeError` on any non-404, non-200
(`flashnode/executor/client.py:194-202`). That call now runs at the start of
**every** task. The raise is caught neither as `TaskExecutionError` nor as
`LeaseLost`, so it lands in the outer handler which does **not** call `fail()` —
the lease is held to expiry rather than requeued — and increments
`consecutive_failures`, pushing a healthy volunteer toward quarantine.

We make it worse by construction: `forward` converts "coordinator did not answer"
into a **502 answer** (`app.py`, `forward`'s except clause). flashnode's transport
retry only fires when *nothing answered*; an `HTTPError` is returned to the caller
immediately as a decision. So our gateway shape defeats the retry the agent
already has.

**Mitigated cloud-side, not closed.** `GET .../checkpoints/latest` is now the one
retried agent route, with its own ladder `AGENT_RETRY_DELAYS = (1.0, 2.0, 4.0)`.
It is deliberately *shorter* than the browser ladder: flashnode's socket timeout
is 15s and a ladder outlasting it cannot help, because the agent has already
given up. Pinned by
`test_the_agent_retry_ladder_fits_inside_flashnode_s_socket_timeout`.

**The tempting wrong fix is answering 404 on a gateway error.** 404 means "no
checkpoint exists", the agent believes it, and a resumable task silently restarts
from step 0 — destroying exactly the work this feature protects. Do not do this.

### R2 — a failed task now leaves `ckpt/*` at its result keys

The relay uploads during the run, so a task that dies has checkpoint files at
result keys even though it never committed. This contradicts the rule stated at
`loop.py:266-277` (a failed task uploads only logs, so results and orphans stay
distinguishable). **Accepted deliberately**: a dead task keeping its checkpoints
is the entire point of the feature, and they are namespaced under `ckpt/` so they
remain distinguishable. The console groups and labels them rather than showing
them as job output. They are not mirrored to OSS — `artifact_mirror.py:43-47`
copies only `COMPLETED` task prefixes.

### R3 — the convention is load-bearing and invisible

Fault tolerance now depends entirely on the workload writing
`out/ckpt/step-<N>.json` and reading `/work/inputs/resume.json`. Mitigated by two
new preflight WARNINGs (`no-checkpoint`, `checkpoint-path`) and a documented
section in the authoring guide, which mentioned checkpointing zero times before.

## 4. Upstream backlog (public repo — needs a release + four-site pin bump)

Not affordable before the 2026-08-15 submission. In rough priority order:

1. **Make the `checkpoint_latest` failure non-fatal** (`loop.py:466-469`). Log and
   proceed without resume; still start the relay. This is R1's real fix and the
   single highest-value item here.
2. **Split the gate** so the relay and resume staging are independently
   controllable, then let the cloud choose. Today the cloud has one bit.
3. **`CHECKPOINT_MANIFEST_COMMITTED` never reaches a job's event feed.**
   `service/checkpoints.py:25` scopes the catalog as `"<job_id>::<task_id>"` and
   `catalog.py:120` emits under that as its `job_id`, so the event cannot match a
   query for `<job_id>`. The console works around this by polling
   `checkpoints/latest`; the event feed itself is still wrong.
4. **A non-numeric `step-*.json` re-fails every 0.3s for the whole task.**
   `int(path.stem.split("-")[1])` (`loop.py:134`) raises on `step-final.json`,
   the name is never added to `_shipped` (`loop.py:146`), and the warning loops.
   Harmless when nobody opted in; fleet-wide now.
5. **`CheckpointCatalog._manifests` / `_registered` are unbounded** dicts with no
   eviction (`catalog.py:51-53`). Previously bounded by "nobody opts in".
6. **`flashruntime.torch.checkpoint()` cannot be relayed.** Its root defaults to
   `_output_dir()/"ckpt"` (`torch/__init__.py:146-152`) — the exact directory the
   relay watches — but it writes `step-{step:06d}/` **directories** containing
   `model.pt` (`:236,:243`), while the relay globs `step-*.json` **files**. A user
   reaching for the runtime's own PyTorch helper lands in the right directory in a
   shape the relay cannot see, and hears nothing. Its `FLASHML_CKPT_DIR` comment
   assumes a persistent disk the volunteer model does not provide. The real fix is
   teaching the relay multi-part checkpoints so it can ship the
   `step-<N>/manifest.json` + parts layout that `checkpoint/local.py:90` and
   `torch/__init__.py:236` already produce — collapsing two incompatible
   checkpoint conventions into one.
7. **`GET .../checkpoints/latest` has no authorization on the coordinator**
   (`service/checkpoints.py:73-78`, versus `authorize_task_write` on `/parts` and
   `/commit`) and returns artifact keys. Consistent with the already-recorded
   "artifact READS unauthorized" audit item rather than a new hole — but it now
   sits on the critical path of every job, so it belongs in that ledger.

**Only after (6)** should a `checkpoint:` key be reconsidered, and it should carry
a layout name, not a dir and a glob.

## 5. What we are deliberately not doing

- No `checkpoint:` YAML key of any shape. It is refused with an explanation
  rather than as an unknown-key typo, which turns the error into the docs.
- No public-repo change, no PyPI release, no four-site pin bump before
  2026-08-15. `recipes/command.py:199` means none is needed.
- No change to the relay's directory or glob. One fixed convention is what makes
  the preflight check possible at all.
- Neither preflight finding is an ERROR. A short job with nothing to checkpoint
  is legitimate, and preflight refuses only contracts the author opted into
  (`preflight.py:28-31`, `:694-711`).
