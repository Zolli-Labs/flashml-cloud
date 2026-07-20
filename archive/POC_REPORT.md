# FlashML POC — Final Report

Date: 2026-07-17/18. Companion to `POC_PLAN.md` (audit + plan).

## 1. Architecture actually implemented

```
User → FlashML Cloud web (Next.js :3000, npm run dev)
     → FlashML Cloud API (FastAPI :8000, node registry SQLite, job proxy)
     → FlashRuntime API (FastAPI :8100, SQLite event ledger)
     → KubeRayExecutionBackend (JobSpec → RayJob CR, status/event mapping)
     → KubeRay operator 1.6.2 → ephemeral Ray 2.46.0 cluster (head num-cpus=0
       + 3 workers, anti-affinity spread, nodeSelector flashml.dev/compute)
     → Kind cluster flashml-poc (1 control-plane + 3 workers = simulated devices)
     → FlashNode DaemonSet: 1 agent per worker → registers + heartbeats to Cloud
Artifacts: Ray driver → MinIO (ArtifactStore) → ArtifactRecords → Cloud UI
Images:    local kind-registry localhost:5001 (ACR overlay for cloud)
```

One shared protocol: `flashruntime.protocol.v1alpha1` (JobSpec, JobState,
Event, ArtifactRecord, NodeRegistration/Heartbeat) imported by flashnode and
flashml-cloud. No cross-repo code copying; no Ray code in the Cloud.

## 2. Files changed

**flashruntime** (public): `protocol/v1alpha1.py` (+`protocol/__init__`),
`backends/{__init__,base,kuberay}.py`, `artifacts/{__init__,store}.py`,
`service/{__init__,ledger,app,cli}.py`, `flashml_workloads/{__init__,
sharded_kmeans}.py`, `docker/Dockerfile.{kmeans,service}`,
`examples/job-kmeans.yaml`, `docs/adr/000{1-flashnode-on-ack-edge,
2-pai-dlc-backend}.md`, tests ×4 new files, `pyproject.toml`, `.dockerignore`,
`AGENTS.md`.

**flashnode** (public): `identity/store.py`, `inventory/capabilities.py`,
`agent/{kube,daemon}.py`, `agent/cli.py` (agent subcommand), `Dockerfile`,
tests ×3 files, `pyproject.toml` (+flashruntime dep), `AGENTS.md`.

**flashml-cloud** (private): `apps/api/` (new FastAPI app + store + tests +
Dockerfile), `apps/web/lib/poc-api.ts`, pages `nodes/ jobs/ jobs/[jobId]/
submit/ integration/`, Navbar; `infra/{base,local,alibaba}` (9 manifests +
kustomizations + ACK templates + SLS config + sandbox docs),
`scripts/{local,alibaba}/*.sh`, `AGENTS.md`, `.gitignore`.

**workspace root**: `Makefile` (poc targets), `POC_PLAN.md`, this report,
`.env.alibaba.example`, `.dockerignore`.

## 3–5. Commands

- Local startup: `make poc-local-up` (kind cluster → registry → KubeRay →
  images → kustomize apply → waits for 3 online FlashNodes).
- Submission: `make poc-local-submit` (or UI `/submit`, or
  `flashruntime submit flashruntime/examples/job-kmeans.yaml`).
- Failure injection: `make poc-local-fail-worker` (deletes one *running*
  Ray worker pod). Stronger variant documented: `docker stop <kind-worker>`.
- Also: `poc-local-status/logs/forward/down`, `poc-reset`.

## 6. Observed worker-loss and retry evidence (run 46a9a0c259d2)

All signals measured, none fabricated (sources in parentheses):

```
04:03:55 JOB_ACCEPTED          (flashruntime.service)
04:03:56 RAYJOB_CREATED        flashml-46a9a0c259d2
         3 worker pods Running on worker/worker2/worker3  ← anti-affinity
04:05:00 RAY_WORKER_LOST       pod ...-worker-c5tj8 terminating (kubernetes.pod)
04:05:02 RAY_WORKER_REPLACED   pod ...-worker-p84d4 created    (kubernetes.pod)
04:05:06 RAY_WORKER_LOST       Ray node left cluster           (ray.nodes)
04:05:28 RAY_WORKER_REPLACED   Ray node joined, iteration 7    (ray.nodes)
04:05:50 TASK_ATTEMPT_RETRIED  task 9c2353f5... attempt 2      (ray.task-state API)
04:05:55 JOB_SUCCEEDED + 5× ARTIFACT_COMMITTED (minio)
```

execution-summary.json: 12/12 iterations, 432 tasks, 1 task retried,
1 worker lost, 4 pods contributed (3 original + 1 replacement),
total 73.1 s. First run (ffba53b1fec0) showed the same pattern.

## 7. Artifact locations

MinIO bucket `flashml-artifacts`, prefix `jobs/<job-id>/`:
`centroids.json, metrics.json, execution-summary.json,
node-contributions.json, recovery-events.json` — public URIs
`artifact://jobs/<job-id>/...`; etags + sha256 in ArtifactRecords; visible
in the Cloud job page and MinIO console (`make poc-local-forward`, :9001).

## 8. Tests executed (all passing)

- `flashruntime: .venv/bin/pytest` → **36 passed** (JobSpec validation incl.
  unsupported backend + latest-tag rejection, RayJob manifest generation,
  status mapping, event serialization, artifact metadata, K-Means shard
  determinism + reduce equivalence, sandbox fail-closed, docs links).
- `flashnode: .venv/bin/pytest` → **9 passed** (stable identity, capability
  mapping incl. K8s allocatable preference + label allow-list, environment
  classification, registration retry/backoff against an HTTP stub,
  heartbeat + graceful termination, failure reported-not-raised).
- `flashml-cloud/apps/api: .venv/bin/pytest` → **7 passed** (registration,
  heartbeat mismatch/404, offline timeout, terminating status, FlashRuntime
  unreachable → 502, integration panel defaults).
- Web: `next build` succeeds with all POC pages.
- End-to-end (executed twice, above): submit → 3 nodes → kill worker →
  KubeRay replaces pod → Ray retries task → SUCCEEDED → durable artifacts →
  Cloud timeline.

## 9. Alibaba services actually deployed

**None** — no Alibaba credentials were available in this environment. No
claim of ACK verification is made.

## 10. Alibaba resources configured but not deployed

- ACK overlay (`infra/alibaba/ack/*.tpl.yaml` + `render.sh`): render and
  `kubectl kustomize` build verified locally with test values (ACR image
  refs, OSS env, sandbox selectors correctly produced).
- ACR: `scripts/alibaba/acr-{login,build-push}.sh` (env-driven, immutable
  tags `poc-v1-<sha>`, no secret output).
- OSS: native `OSSArtifactStore` (oss2, STS-capable) behind the same
  `ArtifactStore` protocol as MinIO; selected by `FLASHML_ARTIFACT_BACKEND=oss`.
- SLS: `AliyunLogConfig` for flashml-namespace stdout JSON with `job_id`
  correlation; requires ACK Logtail add-on (documented).
- Managed Prometheus: documented (ARMS add-on); display wired in the panel.
- Sandboxed Containers: fail-closed tier translation implemented and tested;
  secure-pool prerequisites + demo pod in `infra/alibaba/sandbox/`.
- ADRs: ACK Edge onboarding (0001), PAI-DLC backend boundary (0002).

## 11. Security limitations (POC honesty)

- Single-tenant, no authentication on any API; CORS `*` on the cloud API.
- MinIO dev credentials are committed for the local profile only; K8s
  Secrets (not RRSA/STS) hold artifact creds in-cluster.
- FlashNode init container runs as root once to chown its hostPath state dir.
- No NetworkPolicies applied yet; Ray dashboard not exposed, but intra-
  namespace traffic is unrestricted.
- Workload trust: first-party images only; no arbitrary code submission
  path; no protection claimed against a malicious host owner.
- Runtime ledger/node registry are SQLite on emptyDir — restart loses
  history (violates the eventual "durable state" rule; acceptable POC scope,
  Postgres is the documented target).

## 12. Proof outputs

Captured in-session (see conversation): 3 FlashNodes online via
`/v1alpha1/nodes` (fn-2af4…/fn-7292…/fn-7de3…, one per kind worker, arm64,
6 allocatable cores); RayJob created; worker pods on 3 distinct nodes; pod
deletion; K8s + Ray worker-lost/replaced events; ray.task-state retry;
SUCCEEDED; MinIO object listing with etags.

## 13. Next improvements (max 3)

1. Durable state: move runtime ledger + node registry to Postgres with the
   append-only event schema flashml-cloud already mandates.
2. Live event streaming (watch API / SSE) instead of 2-s polling, and ingest
   workload recovery events during the run rather than at completion.
3. Deploy the ACK profile against a real cluster (credentials pending) and
   run the documented smoke test, including the sandbox-pool demo pod.

## Operational note

Host disk is tight (~3 GB free with the colima VM at 30 GB sparse cap).
`make poc-local-down` deletes the kind cluster + registry and reclaims most
of it. The VM previously corrupted its data disk when host disk ran out —
keep ≥5 GB free before rebuilding images (see memory notes).
