# Trusted-tier execution contract — tier-owned gating, workdir delivery, dependency environments

**Date:** 2026-08-09
**Status:** approved design (brainstormed with the owner).
**Repos touched:** `flashml` (agent + protocol), `flashml-cloud` (compiler).
**Origin:** the owner's first real RunPod host, enrolled 2026-08-09 against a
`cloudflared` tunnel to the local control plane. It never completed a single
task. Three separate defects fired in sequence, each one masking the next.

The session is the argument for this document, so it is recorded here rather
than summarised:

1. The pod claimed `task-001` and failed it three times in ~1s each with
   `ModuleNotFoundError: No module named 'torch'`.
2. The host then shut *itself* down — `stopping: this host can no longer run
   tasks`, `failed_checks: ["docker CLI on PATH", …]` — on a tier that
   cannot have Docker by definition, and that the startup gate deliberately
   exempts.
3. With torch installed, attempts got as far as 9s and failed with `task
   produced no metrics.json — nothing to commit`. The workload had written
   its output to a literal `/work/out` on the pod's own filesystem, which
   succeeded silently because the agent ran as root.

Every one of those is a *host-side* failure the volunteer was expected to
diagnose. None of them was reported in terms that pointed at the real cause;
the second actively lied. The same three jobs completed on the owner's
laptop, because a laptop runs `--runner argv`, which is containerised, and
the container makes all three problems disappear.

---

## 1. Decisions made during brainstorming

1. **One package, not a trusted-only build.** Rejected outright. Two copies
   of the agent drift, and the drift is invisible until a volunteer's
   install demands something the other half already removed — that is the
   `flashnode/executor/images.py` bug the 2026-08-01 topology rule exists to
   prevent. The tier is a runtime property, not a distribution.
2. **The runner owns its tier contract.** The tier is chosen by a CLI flag
   and then re-derived, separately, in four places. Three derive it wrong.
   Replace the re-derivation with a protocol the runners implement.
3. **Dependencies are declared, not discovered.** An optional
   `dependencies:` in `flashml.yaml`, resolved at submit time, carried in
   the task payload. Rejected: an image→packages lookup table (lies for
   custom images) and reading `requirements.txt` on the host (deps unknown
   until after the lease is claimed).
4. **Cached environments keyed by dependency hash.** Rejected: a fresh venv
   per task (191 MB of torch re-downloaded per shard, three times for one
   round of the federated example) and installing into the host interpreter
   (writes a submitter's version choices permanently into a volunteer's
   system Python, and two jobs in one pool silently break each other).
5. **Satisfaction is checked after the claim, not before it.** `ClaimRequest`
   is `node_id` plus an optional `job_id` (`flashruntime/service/modea.py:560`);
   a node learns the payload only by claiming it. A true pre-claim gate would
   need an eighth placement predicate fed by registration — and a node that
   has cached nothing would then never be selected, so nothing would ever
   bootstrap. One honest attempt, attributably failed, plus a per-job
   cooldown, is the better trade.

---

## 2. The runner owns its tier contract

### 2.1 What is wrong now

`--runner trusted` is a first-class concept in exactly one of the four places
that need it:

| Site | Tier-aware? |
|---|---|
| Startup gate, `flashnode/agent/cli.py:247-262` | **yes** — skips the docker checks deliberately |
| `_blocking_problems()`, `cli.py:326-342` | no — runs the docker checks unconditionally |
| `TrustedArgvRunner` child env, `executor/trusted_runner.py:63` | no — passes `task_env()` and nothing else |
| Post-login hint, `cli.py:138` and `doctor.py:505` | no — both hardcode `flashnode work --runner docker` |

The startup gate carries an explicit comment explaining the exemption:
*"Deliberately OUTSIDE the ("docker", "argv") branch above: this tier has no
container and no docker CLI dependency, so the docker doctor gate has nothing
to check."* (`cli.py:274-280`). `_blocking_problems` carries an equally
explicit comment claiming it cannot drift from that gate — *"Filtered HERE,
with the same set the startup gate reads, so the two cannot drift"* — and it
is wrong. The two sets agree on the **status** axis and disagree on the
**tier** axis, which is the axis that did not exist when
`2026-08-02-host-status-and-self-quarantine-design.md` was written. That
spec never mentions runners or tiers; the trusted tier arrived afterwards
and the quarantine path was never revisited.

The consequence, observed twice in one session: three failures of *any*
cause quarantine the host permanently, and the report names Docker.

### 2.2 The change

```python
class Runner(Protocol):
    def health_checks(self) -> list[CheckResult]: ...
    def task_env(self, workdir: Path) -> dict[str, str]: ...
    def run(self, payload: dict, workdir: Path, inputs: dict[str, Path]) -> Path: ...
```

- The startup gate calls `runner.health_checks()`.
- `_blocking_problems()` closes over **the same bound method** on the same
  runner instance, not over a parallel call to `run_checks()`.
- `DockerRunner` and `ArgvDockerRunner` return the docker check set.
  `TrustedArgvRunner` and `SubprocessRunner` return a set with no docker
  member: interpreter present, workdir writable, disk headroom.

Sharing the bound method is the point. The two call sites cannot disagree
about the tier because there is only one tier-dependent expression, and the
loop no longer imports the doctor at all — which also closes the
loop → doctor → executor → loop cycle the current comment worries about.

Both post-login hints — `cli.py:138` after enrolment and `doctor.py:505`
after a clean check — read the same tier and print the command that will
actually work on that host. Today they hardcode `--runner docker`, which is
the first thing a pod operator is told and the first thing that is wrong.

---

## 3. Workdir delivery

### 3.1 What is wrong now

`TrustedArgvRunner` rewrites `/work`-prefixed **argv tokens** onto the real
workdir, and says why: *"this runner has no container, so `/work` is a
naming convention to honour, not a mount to make"*
(`executor/trusted_runner.py:48-52`).

`examples/federated/train.py` never receives its output path through argv.
It resolves it itself:

```python
WORK = Path(os.environ.get("FLASHML_WORK_DIR", "/work"))   # line 68
OUT_DIR = WORK / "out"
```

So the rewrite never reaches it. On a pod, running as root,
`OUT_DIR.mkdir(parents=True, exist_ok=True)` genuinely creates `/work/out`
on the host filesystem and the run exits 0 with its outputs written to the
wrong place. The runner checks `<workdir>/out/metrics.json`, finds nothing,
and fails the attempt.

The workload already ships the escape hatch — `FLASHML_WORK_DIR` exists
precisely so the example can *"run the whole federated loop on a laptop
without root"* — and the agent has no way to deliver it.
`_TASK_ENV_WHITELIST` is `("PATH", "HOME", "PYTHONPATH", "LANG", "LC_ALL",
"TMPDIR")` (`executor/runner.py:40`), so exporting the variable in the host
shell is filtered out before the child sees it.

### 3.2 The change

`TrustedArgvRunner.task_env()` and `SubprocessRunner.task_env()` return
`task_env() | {"FLASHML_WORK_DIR": str(workdir)}`.

`_TASK_ENV_WHITELIST` is **not** extended, and the reason is documented at
its definition: the whitelist governs what the *host's* environment may leak
into workload code, while `FLASHML_WORK_DIR` is the agent's own contract
with the workload, set explicitly rather than inherited. Conflating the two
is what hid this bug, so the distinction is written down where the next
reader will be standing.

Containerised tiers set nothing and keep bind-mounting `/work`. The
variable's default is `/work`, so their behaviour is byte-identical.

---

## 4. Dependency environments

### 4.1 Declaration

> **SUPERSEDED by `2026-08-09-dependency-provisioning-design.md`.** This
> section has the submitter declare the whole dependency list. That is wrong
> twice over: `torch==2.13.0` is a different artifact on a CPU host than on a
> CUDA host, and the curated images already encode the correct set for each —
> so restating it in `flashml.yaml` creates two sources of truth for one
> environment. The replacement makes the image's own manifest the base and
> `dependencies:` the extras on top. §4.2, §4.3 and §5 below survive; read
> them with that amendment in mind.

`flashml.yaml` gains an optional `dependencies:` — a list of pip requirement
strings, validated the same way `args` is (a list of strings, never a shell
string). Resolution happens at **submit time**, in the compiler, in this
order:

1. an explicit `dependencies:` list, verbatim;
2. otherwise a `requirements.txt` at the repository root, read and split;
3. otherwise empty.

The resolved list — never the file path, never the fallback rule — goes into
the task payload. Resolving in the compiler rather than on the host is what
makes the value the same for every node that runs the job, and what lets the
failure message name a specific requirement.

`dependencies` is a field a FlashNode must understand, so under hard rule 2
it is added to `flashruntime.protocol.v1alpha1` first and consumed here
second. It is optional with an empty-list default, for the same
fleet-compatibility reason `CompleteRequest.evidence` is optional: every
agent deployed today predates it.

### 4.2 Materialisation

`EnvironmentCache`, at `$FLASHNODE_STATE_DIR/envs/<sha256>`, where the key is
the sha256 of the normalised dependency list — sorted, stripped, lowercased
— so that two jobs declaring the same requirements in a different order
share one environment.

Built with `uv venv` + `uv pip install` when `uv` is on PATH, falling back to
`python -m venv` + `pip install`. Evicted least-recently-used against
`FLASHNODE_ENV_BUDGET_GB` (default 8). A build that would leave less than
1 GB of free disk is refused before it starts rather than part-way through:
the RunPod CPU pod that motivated this document has a 5 GB container disk,
and a torch install is most of a gigabyte.

Nothing is ever written to the host's own interpreter.

### 4.3 Invocation

The compiler emits `python <entrypoint> *args`
(`flashml_yaml.py:355-357`). When a task has a non-empty dependency list,
the runner substitutes `<env>/bin/python` for `argv[0]` — **only when
`argv[0]` is a bare interpreter name** (`python`, `python3`,
`python3.<n>`). An absolute path in `argv[0]` is a submitter being
deliberate and is left untouched.

That rule matters beyond this feature. `python` as a bare token is resolved
against the host's `PATH`, and on the pod that resolved to `/usr/bin/python`
while `pip` was `/usr/bin/python3.13` — two interpreters, one of which had
torch. Any host can have that split. Binding the interpreter to the
environment removes the ambiguity for every job that declares dependencies.

An empty dependency list builds no environment and changes nothing.

---

## 5. Failure handling

The environment is built after the claim and before execution. On failure:

```
TaskExecutionError("host cannot satisfy dependencies: torch==2.13.0: <pip stderr tail>")
```

Attributable at the point of failure — the same rule the existing
`"task produced no metrics.json — nothing to commit"` error follows, and for
the same stated reason: a failure named here beats a mysterious rejection
three hops later.

`ExecutorLoop` gains a per-job cooldown. A host that has failed to build a
job's environment stops claiming *that job's* tasks for a bounded interval.
Other jobs are unaffected, and other nodes still receive the task
immediately — which is what happened by accident in the origin session, where
the owner's laptop absorbed all three shards the pod could not run and the
job succeeded.

The cooldown is per job, not per host. A host is not broken because one
submitter asked for something it cannot install, and the existing
self-quarantine path is the wrong hammer — as §2 establishes, it was firing
for the wrong reason already.

---

## 6. Testing

The two bug reproductions come first, and they are the acceptance criteria
for §2 and §3:

- a trusted-tier host with no Docker on PATH survives a three-failure streak
  and does not self-quarantine;
- a workload that writes to `$FLASHML_WORK_DIR/out` has its `metrics.json`
  committed by `TrustedArgvRunner`.

Then:

- compiler resolution, all three paths: explicit list wins over a present
  `requirements.txt`; the file is read when the list is absent; neither
  yields an empty list;
- cache identity — same list in a different order is the same path and does
  not rebuild; a different list is a different path;
- eviction under `FLASHNODE_ENV_BUDGET_GB`, and refusal when free disk would
  drop below the reserve;
- `argv[0]` substitution in both directions: bare `python` is replaced, an
  absolute path is not;
- a dependency build failure produces the attributable error and the job
  cooldown, while a second job on the same host is still claimed.

---

## 7. Out of scope

- **A RunPod template image** with `flashnode`, an unambiguous `python` and
  common wheels preinstalled. It is the right on-ramp and it is a
  distribution problem, not a runtime contract.
- **The console's hardcoded `localhost:8000`.** `cloudApiBase()` falls back
  to it when `NEXT_PUBLIC_CLOUD_API` is missing from the Next.js process env
  (`apps/web/lib/cloud-api.ts:59`), and `scripts/dev.sh` never exports it —
  so the Colab and RunPod connect panels print an address that is correct
  for a second laptop and unusable for every remote machine those tabs
  exist to serve. Separate repo surface, separate fix.
- **GPU wheel selection.** Dependencies are installed as declared; choosing
  `cu121` over `cpu` stays the submitter's decision in v1.
