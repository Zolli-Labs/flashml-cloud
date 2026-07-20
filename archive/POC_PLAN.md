# FlashML POC — Audit and Implementation Plan

Date: 2026-07-17. Scope: local-first Kubernetes/KubeRay POC with an Alibaba
ACK deployment profile, per the workspace CLAUDE.md brief.

## 1. Audit findings

### Workspace
- Root `Makefile` has `setup`, `test`, `sync-docs`, `check-docs`. POC targets
  (`poc-local-*`, `poc-ack-*`) will be added here — the root Makefile is the
  established cross-repo task runner.
- No Dockerfiles, no Kubernetes manifests, no Compose files, and no CI
  workflows exist anywhere in the three repos.
- Host machine: macOS (Apple M4, 10 cores, 16 GB RAM, ~16 GB free disk).
  **No container runtime or K8s tooling was installed** (no Docker Desktop,
  colima, kind, kubectl, helm). Being installed via Homebrew: colima + docker
  CLI (container runtime), kind, kubectl, helm. Disk is the tightest
  constraint — use slim Ray images and prune aggressively.

### flashruntime (Python 3.10+, setuptools, uv; branch `main`, clean)
- Working prototype carried from the original FlashML hackathon:
  - `adapters/base.py` — `Provider`/`WorkerPool`/`Task`/`TaskResult`/`Storage`
    abstractions with a local thread-pool provider (`adapters/local/`).
  - `algorithms/kmeans.py` — distributed Lloyd's K-Means as broadcast→map→
    reduce over shards, deterministic seeding, storage-keyed shards. **Highly
    reusable**: the shard/partial-sums/reduce math ports almost directly to
    Ray tasks.
  - `engine/` — synchronous training loop + `Job`/`JobEvent` handle.
  - `storage/local.py` — file-based Storage.
- `protocol/`, `leases/`, `checkpoint/`, `recovery/`, `scheduler/` are
  docstring-only scaffolds. The versioned public protocol lands in
  `protocol/`.
- Tests: 8 passing (`test_local_provider.py`, `test_documentation.py`).
- No FastAPI/CLI service yet; pyproject has only numpy (+sklearn extra).

### flashnode (Python 3.10+, setuptools; branch `main`, clean)
- Docstring-only scaffold (identity, inventory, executor, telemetry,
  artifacts, benchmark packages; stub CLI). Nothing to preserve except the
  package layout and the security contract in AGENTS.md/README.
- The old "executor runs workload containers" concept is explicitly **not**
  used in the Kubernetes profile — KubeRay owns workload pods; FlashNode is a
  DaemonSet reporter/heartbeater. The executor package stays as a documented
  non-K8s future path.

### flashml-cloud (Next.js 16 / React 19 / Tailwind 4; branch `main`, clean)
- `apps/web` is a seeded prototype dashboard with a dark design system,
  K-Means visualizations, and a `lib/api.ts` client pointed at a **legacy
  FastAPI coordinator that does not exist in this repo** (port 8000).
- `apps/api`, `services/*` are empty placeholders (FastAPI/Postgres/Redis
  planned). For the POC we add a small FastAPI app at `apps/api` (SQLite,
  no Postgres/Redis — POC-scale, documented as such) and rebuild the web
  pages around nodes/jobs/timeline while keeping the design system.

### Conflicts / risks identified
- Port 8000 is already the assumed API port in `lib/api.ts` — keep it for the
  flashml-cloud API; FlashRuntime API takes 8100; MinIO 9000/9001 (port-
  forwarded); web 3000.
- Duplicated models risk: web `lib/api.ts` defines its own job types — will
  be regenerated from the flashruntime v1alpha1 protocol shapes.
- Dependency rule: flashnode and flashml-cloud import
  `flashruntime.protocol` only (editable installs locally, same package in
  images).
- Prototype `Provider` abstraction is **not** the new backend interface; the
  new `ExecutionBackend` protocol (per brief §3.1) lives alongside it. The
  prototype engine keeps working for the pure-local example; nothing is
  deleted.

## 2. Implementation plan (ordered, gated)

**Gate 0 — tooling + smallest RayJob (before any refactor):**
colima up → kind cluster (1 control-plane + 3 workers, labeled
`flashml.dev/pool=local`) → Helm-install pinned KubeRay operator → run a
minimal upstream RayJob sample to `Succeeded`. Pins: KubeRay operator 1.4.x,
Ray 2.46.x (exact pins recorded in `infra/local/versions.env` once proven).

**Phase 1 — flashruntime core:**
- `protocol/v1alpha1.py`: pydantic `JobSpec` (apiVersion flashml.dev/v1alpha1)
  per brief §14, `Event` types (§7 list), `ArtifactRecord`, `NodeInfo`,
  heartbeat messages. Version field on every wire model.
- `backends/base.py`: `ExecutionBackend` protocol exactly as briefed.
- `backends/kuberay.py`: JobSpec→RayJob CR translation (pinned versions,
  flashml.dev labels, node selectors, retries), status watch → FlashRuntime
  states, pod/K8s event normalization → event ledger.
- `artifacts/`: `ArtifactStore` protocol; `S3CompatibleArtifactStore`
  (MinIO, boto3-style via `aioboto3` or minio client) and OSS config mode.
- `service/`: small FastAPI app (submit/status/events/logs/cancel/artifacts)
  + CLI. SQLite event ledger.
- Local-profile rejection of `isolation.tier: sandboxed` with the exact
  briefed message.

**Phase 2 — workload:** `examples/ray_kmeans/` sharded K-Means on Ray Core
(36 shards, 12 iterations, `max_retries` on tasks, per-task node identity
capture, attempt metadata, final artifacts centroids/metrics/execution-
summary/node-contributions/recovery-events). One image `flashml/kmeans`.

**Phase 3 — flashnode agent:** identity (stable ID file), K8s downward-API
node discovery, capability report (psutil + allocatable from cloud-provided
metadata), outbound heartbeat loop to flashml-cloud, graceful termination.
DaemonSet manifest, non-root, no privileged, narrow RBAC (read own Node).

**Phase 4 — flashml-cloud:** FastAPI `apps/api` (node registry, job CRUD
proxying FlashRuntime, event timeline, artifact links, Alibaba status
panel data), SQLite. Web pages: Nodes, Submit (K-Means template), Job
detail (timeline), Alibaba integration panel.

**Phase 5 — local infra:** `infra/base/` + `infra/local/` manifests (MinIO,
FlashRuntime, cloud API+web, FlashNode DaemonSet, RBAC, NetworkPolicy),
kind config, image build/load scripts, root-Makefile `poc-local-*` targets.

**Phase 6 — E2E + failure injection:** scripted demo (`make poc-fail-worker`
deletes one Ray worker pod mid-run), verify real retry evidence from Ray/K8s
signals, artifacts durable in MinIO, timeline in Cloud UI. Run all repo test
suites.

**Phase 7 — Alibaba profile (config + docs; deploy only with credentials):**
`infra/alibaba/{ack,acr,oss,sls,sandbox}`, `.env.alibaba.example`,
`scripts/alibaba/acr-login.sh` + `acr-build-push.sh`, OSS artifact-store
wiring, SLS collection config, sandbox node-pool overlay (RuntimeClass via
deployment config, fail-closed), `poc-ack-*` Makefile targets, ADRs:
ACK Edge onboarding, PAI-DLC backend boundary.

**Phase 8 — final report** per brief §21, separating local-verified from
Alibaba-configured-only.

## 3. Explicit non-goals honored
No custom scheduler, no Ray replacement, no marketplace/billing, no fake
sandbox locally, no `latest` tags, no credentials in git.
