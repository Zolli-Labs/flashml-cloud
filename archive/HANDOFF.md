# HANDOFF — read me second (after HANDBOOK.md)

> Written 2026-07-19 by the agent that built the local milestone, at
> session end. `HANDBOOK.md` tells you what the system *is*; this file
> tells you what only the builder knew — sharp edges, risks, judgment
> calls, and exactly where to start. Written for my successor: trust the
> tests over any prose, including mine.

## 0. State of the world in one paragraph

The complete Mode A loop works locally and is proven by e2e tests across
real process/network boundaries: plan → submit → machines pull leases →
validated idempotent commits → kill-a-machine recovery → distributed
K-means → **cross-machine training resume from relayed checkpoints** —
plus a durable lease store (coordinator restarts are a non-event), the
strategy planner, and a built-in dashboard. Suites at handoff:
**flashruntime 109, flashnode 28, e2e 3 — all green.** All work is
committed on branch **`local-milestone-2026-07`** in each of the three
repos (main untouched; nothing pushed — no remotes were configured).

## 1. Immediate actions for the next session (in order)

1. `git -C flashruntime log --oneline -3` etc. — confirm the
   `local-milestone-2026-07` branches exist; decide with the user whether
   to merge to main (recommended: yes, fast-forward or merge commit — the
   work is tested) and whether to add remotes/push.
2. Run the suites to confirm nothing rotted:
   `cd flashruntime && .venv/bin/pytest -q` · `cd flashnode && .venv/bin/pytest -q`
   · `make e2e` (needs `make e2e-setup` once per machine).
3. Open `SPRINT_PLAN.md` — Day 1 is the metrics engine, and it starts
   with a *paper* task (R3: exact metric definitions) before any code.
4. Log everything in `PROGRESS.md` per its protocol. Evidence or it
   didn't happen.

## 2. Top risks, ranked

1. **Alibaba credentials** gate Sprint Days 4–7. No ACK/ACR/OSS/RAM keys
   have ever existed in this workspace (`.env.alibaba.example` lists what's
   needed). Everything cloud is *configured but never deployed* — treat
   every `infra/alibaba/` file as untested until the first real deploy.
   Swap rule if blocked is in SPRINT_PLAN.
2. **The open artifact PUT** (HANDBOOK §6): any network peer can upload
   artifacts to the coordinator. Fine on localhost; **must** be closed
   (STS-scoped uploads, R2) before any internet-exposed deployment. Do not
   put the current coordinator on a public IP for longer than a demo.
3. **Checkpoint manifests are in-memory** (R1): a coordinator restart
   orphans perfectly good checkpoint *files*. The SqliteLeaseStore
   pattern (protocol + save() + rehydrating cache) is the template; this
   is a half-day job and scheduled for Sprint Day 3.
4. **Host disk pressure** (recorded in project memory): the colima VM
   previously corrupted its data disk when the Mac's disk filled. Keep
   ≥5 GB free before building images; `make poc-local-down` reclaims; the
   Docker test image `flashnode-test-task:local` can be `docker rmi`'d.
5. **Single-writer assumption**: LeaseManager/SqliteLeaseStore are safe
   only because FastAPI runs them on one event loop. If anyone adds
   threads, a second worker process (`uvicorn --workers 2`!), or moves
   endpoints to `def` (threadpool), the state machine needs locking.
   **Never run the coordinator with >1 uvicorn worker** until Postgres +
   row-level concurrency lands (R4).

## 3. Hard-won gotchas (each cost real debugging time)

- **colima/Docker Desktop only share `$HOME`** on macOS: any bind mount
  from `/var/folders/...` (system tmp) appears EMPTY inside containers.
  That's why `FLASHNODE_WORKDIR` exists and why docker tests use
  `~/.cache/flashnode-tests/`. Symptom: "file not found" for files that
  visibly exist on the host.
- **`kind load` is broken on Docker 29** (project memory): use the local
  registry pattern (`registry.sh`, localhost:5001), never `kind load`.
- **Repo directories shadow installed packages**: launching
  `python -m flashnode...` with cwd = workspace root resolves `flashnode/`
  (the repo dir) as a namespace package and fails weirdly. Always set a
  neutral cwd for subprocesses (e2e's `run_demo.py` shows the pattern).
- **Stale editable installs**: flashnode/.venv once had a flashruntime
  editable that predated `flashml_workloads` packaging — imports failed
  only in that venv. If a module "doesn't exist" in one venv but does in
  another: `VIRTUAL_ENV=<venv> uv pip install -e ../flashruntime`.
- **`threading.Thread` has an internal `_stop`** — naming an attribute
  `self._stop` on a Thread subclass breaks `join()` with
  "'Event' object is not callable".
- **pytest conftest hooks are not directory-scoped**:
  `pytest_collection_modifyitems` in `tests/integration/conftest.py`
  receives ALL items — filter by path (already done; don't regress it).
- **Env scrubbing vs PYTHONPATH**: task subprocesses get a 6-var
  whitelist. Tests that inject fake task modules must use
  `monkeypatch.setenv("PYTHONPATH", ...)` — `syspath_prepend` doesn't
  reach subprocesses.
- **uvicorn/pipes buffer stdout** — demo scripts need `python -u`.
- The **`test_dead_node_task_requeues_via_http`** service test exercises
  the polite-fail path despite its name; the true expiry path is covered
  in `test_leases.py` and the e2e (time injection vs real clocks).

## 4. Judgment calls I made (and why) — revisit consciously, not accidentally

- **Node registry stays volatile** while leases are durable: agents
  re-register within seconds (built + tested), so persisting the registry
  bought nothing. Revisit only when nodes carry server-side state that
  can't be re-announced (trust scores).
- **Commit-time validation re-hashes the artifact on the coordinator** —
  O(size) per commit. Fine for MB-scale outputs; when outputs grow, store
  the sha at upload time and compare (the upload path already computes it).
- **Checkpoint scope key is `job::task`** at the service layer, keeping
  the catalog API untouched. If tasks ever share checkpoints (Mode B
  groups), rethink the scoping, not the catalog.
- **The relay commits single-part manifests** — the catalog fully
  supports multi-part (tested); the *relay* just never needed it. LoRA
  (R5) will: Trainer checkpoints are directories of several files.
- **Planner constants are deliberately pessimistic** and labeled
  `[assumption]` in `planner/catalog.py`. Don't "fix" an estimate by
  tweaking a constant without a measurement; the design says profiling
  (`basis: profiled`) replaces them.
- **FIFO claim, no scheduler** — deliberate (R9). Resist adding placement
  logic before payloads carry resource requirements.
- **`rebuild/` never existed**: the user redirected the learn-by-rebuild
  plan into direct implementation. PLAN_2WEEKS' Part II reads as history,
  not instruction; PROGRESS.md is the truth.

## 5. Small known debts (fine to batch-fix on a slow day)

- FastAPI `@app.on_event` deprecation warnings (77 in the suite) → migrate
  to lifespan context; same for starlette TestClient httpx warning.
- `LeaseManager._find_lease` is O(all tasks) per heartbeat — index by
  lease_id when fleets grow.
- Dashboard polls every 2 s (SSE is Sprint Day 13); K-means jobs briefly
  show "no leased tasks" for ray-backend jobs (cosmetic).
- `flashml_workloads/sharded_kmeans.py` (Ray/Mode B version) and
  `kmeans_shard.py` (lease version) duplicate the partial-sums math —
  unify when touching either.
- No artifact GC; no checkpoint retention pruning (`catalog` has
  quarantine but nothing ages out old steps).
- e2e suite prints coordinator JSON logs into pytest output — filter with
  `grep -vE '"level"'` or set `--log-level error` in the fixture someday.

## 6. Notes per upcoming sprint item

- **Metrics (Day 1–2)**: all events carry coordinator-side timestamps —
  compute MTTD/MTTR purely from the ledger, no new instrumentation. The
  kill moment for MTTD is LEASE_CLAIMED's deadline vs LEASE_EXPIRED time;
  document the definition before coding (R3). Beware: LEASE_RENEWED events
  are high-volume — aggregate, don't list.
- **Second machine (Day 3)**: expect macOS firewall prompt on first
  0.0.0.0 bind; `make local-coordinator JOIN_CODE=...` exists; the
  runbook is e2e/README.md §"A real second machine".
- **ECS (Day 4)**: the compose file does not exist yet — write it from
  the env-var table in HANDBOOK §5 (coordinator + optional cloud
  API/web). Remember `FLASHML_ENABLE_KUBERAY=0`.
- **LoRA (Days 8–10)**: decide multipart relay first (see §4); watch
  checkpoint sizes vs the artifact cap (`FLASHML_MAX_ARTIFACT_MB`
  default 256 — an fp16 0.5B adapter checkpoint fits; full model states
  won't).
- **Postgres (Day 11)**: keep BOTH stores; config-switch, don't replace.
  The restart-survival test (`tests/test_leases_sqlite.py`) is the
  conformance suite — run it against the new store.

## 6b. Designed interfaces for all future work (added post-handoff, same session)

By user request, every not-yet-built part now carries its **complete
designed interface** — full ABCs with typed inputs/outputs, semantics,
error behavior, and per-method notes — plus contract tests
(`tests/test_interfaces.py` in both repos) proving importability, ABC
enforcement, and a conforming dummy. Implement *against* these; don't
redesign them casually (changing a contract = red test + note first).

| Interface | Where | Contract in one line |
|---|---|---|
| `StrategyCompiler` → `LaunchSpec` | `flashruntime/strategies/` | pure, deterministic plan→config; env preflight belongs to launchers; registry via `register_compiler`/`compiler_for` |
| `Launcher`/`LaunchHandle`/`LaunchState` | `flashruntime/launchers/` | start + watch, never retry (recovery is the coordinator's); `healthy()` is the only env preflight |
| `WorkloadRecipe` | `flashruntime/recipes/` | expand/validate_params/validate_output/reduce/checkpointable — bundles today's three hand-wired touchpoints; migrate existing expansions here without wire changes |
| `PlacementPolicy` + `FifoPlacement` | `flashruntime/scheduler/` | eligible/score/choose template; Fifo = today's exact behavior (wiring it is a pure refactor); R9 |
| `ManifestStore` + in-memory ref | `flashruntime/checkpoint/store.py` | the R1 persistence seam; migration steps written in the module docstring — copy SqliteLeaseStore's tested upsert |
| `Profiler`/`ProfileResult`/`ProfileCache` | `flashruntime/profiling/` | four isolation invariants; results born `basis="profiled"`; skip policy lives in the planner |
| `ResourceProvider`/`Offer`/`AcquiredCapacity` | `flashruntime/providers/` | offers/acquire/release; idempotent release is the cost-safety anchor; providers quoted-not-trusted |
| `flash.run(plan)` | `flashruntime/__init__.py` | raises NotImplementedError; the intended 3-step pipeline is in its docstring |
| `AdmissionProbe`/`run_admission` | `flashnode/benchmark/` | budgeted, failure-isolated raw measurements; standard probe names listed |
| `TelemetryCollector`/`TelemetrySample` | `flashnode/telemetry/` | machine-only observation; needs additive NodeHeartbeat.telemetry protocol field (noted) |
| `HostPolicy`/`load_host_policy` | `flashnode/config/` | **concrete**: conservative defaults, fail-closed loading, owner can only narrow allowlists; wiring points listed |

## 7. Where everything lives (one-screen recap)

`HANDBOOK.md` (architecture bible) · `PROGRESS.md` (status + log + logging
protocol) · `SPRINT_PLAN.md` (next 2 weeks) · `FLASHRUNTIME_EVALUATION.md`
(deep design rationale) · `PLAN_2WEEKS.md` (Alibaba runbook detail) ·
`e2e/` (proof suite + LAN runbook) · `archive/` (POC record) · per-repo
`AGENTS.md` (current state + missing lists) · project memory
(`~/.claude/.../memory/`) has the colima/kind gotchas and points here.

Good luck. The system is smaller than it looks: one pattern (pull a
lease, heartbeat, commit-validated-once), applied everywhere. When in
doubt, write the failing test first and let it tell you the truth.
