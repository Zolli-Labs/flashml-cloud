# Foundation — versioned releases and a diskless control plane

**Date:** 2026-08-01
**Scope:** flashruntime, flashnode, flashml-cloud (all three)
**Status:** design approved 2026-08-01. Not yet implemented.
**Position:** S1 of a five-spec program (§2). Prerequisite for S2 (submit CLI)
and S4 (host desktop app); both need to install a *known version* of the
runtime, which today does not exist.

---

## 1. The problem

Two consumers run two different bit-streams of the same `flashruntime` source,
a third channel does not exist at all, and nothing pins or checks any of them:

| Consumer | Installs from | Resolves to |
|---|---|---|
| Render API | `pip install -e ../../../flashruntime` (`render.yaml`) | monorepo HEAD, unversioned |
| Volunteer laptop | `git+https://github.com/Zolli-Labs/flashruntime` (`EnrolInstructions.tsx`) | public mirror `main` |
| `pip install flashruntime` | PyPI | **nothing — the name 404s** |

> **Correction, verified 2026-08-01.** `render.yaml` states that "the published
> wheel predates the federated-averaging driver this API runs in-process." That
> is wrong: `https://pypi.org/pypi/flashruntime/json` returns 404, as does
> `flashnode`. Neither package has ever been published.
> `EnrolInstructions.tsx` is the accurate source. This *simplifies* the
> migration — there is no stale release to supersede and both distribution
> names are free to claim — and the incorrect comment in `render.yaml` is
> removed as part of Plan A.
>
> Separately: the name `flashml` on PyPI is **taken** by an unrelated,
> abandoned AutoML package (`flashML` 0.1.3, last uploaded 2021-12-10). The
> future submit CLI (S2) needs a different distribution name; this spec does
> not depend on it, but S2 must not assume `flashml` is available.

Keeping them in agreement is a human step: `git subtree split`, ancestor check,
push, remembered on every change, forever. It has already been forgotten once —
`flashnode/executor/images.py` existed in the monorepo and not in the public
mirror, so a public install still demanded an image allowlist the monorepo had
removed. The subtree ancestor check cannot catch that class of bug: it proves a
commit is reachable, not that the two halves still agree.

Separately, the coordinator cannot be deployed without an outage. It has a 5 GB
Render disk, and Render's documentation is explicit on both consequences:

> "You can't scale a service to multiple instances if it has a disk attached."
> "Adding a disk to a service prevents zero-downtime deploys. This is because
> when you redeploy your service, Render stops the existing instance before
> bringing up the new instance."

So every deploy drops every in-flight lease. Compounding it: the service runs
one uvicorn worker because `LeaseManager` and `SqliteLeaseStore` are safe only
on a single event loop (HANDOFF risk #5), and checkpoint manifests live in a
Python dict, so a restart orphans checkpoint *files* that are perfectly good
(HANDOFF risk #3).

**These are one problem.** Both are the absence of a durable, versioned
boundary between components — in the first case across repos, in the second
between a process and its state.

### 1.1 Correction: the second app factory is not dead code

An earlier draft of this spec, and Plan A Task 2, described
`apps/api/app.py`'s second `FastAPI` factory (`_create_legacy_app`,
`version="0.1.0"`) as dead legacy code to delete. **That is wrong.**
`create_app()` (`app.py:1402`) deliberately falls back to it:

```python
if os.environ.get("SUPABASE_URL") and os.environ.get("COORDINATOR_URL"):
    ...
    return create_cloud_app(settings, connect=connect)
return _create_legacy_app()
```

and `tests/test_agent_proxy.py:825` pins that behaviour by name.

What the investigation *did* surface is more serious than dead code, and it
belongs to S3 rather than here: **a production deploy that loses one
environment variable silently downgrades to the legacy app, which serves an
open, unauthenticated node registry.** The docstring argues the all-or-nothing
choice ("a half-authenticated API is the worst of both"), and that reasoning is
sound — but the fallback direction is not. On a deployed control plane the
correct response to incomplete configuration is to refuse to boot, exactly as
`Settings.from_env` already does, not to serve an open door. `render.yaml`
declares `SUPABASE_URL` with `sync: false`, so it is prompted at blueprint
creation and one careless edit away from absent.

S3 should decide whether `_create_legacy_app` survives at all, and if it does,
gate it behind an explicit opt-in such as `FLASHML_ALLOW_LEGACY_APP=1` rather
than behind the *absence* of configuration.

### 1.2 What this spec is not

Not a directory reshuffle. The repository layout changes as a *consequence* of
fixing versioning, not as the goal. Not the security close-out (S3), the submit
CLI (S2), or the desktop app (S4).

---

## 2. Program context

| # | Spec | Removes |
|---|---|---|
| **S1** | **This document** | Divergent runtime copies; deploys that drop leases |
| S2 | Submit CLI — `flashml submit .` | Public-GitHub-only submission |
| S3 | Security close-out | Blockers to exposing anything past the API |
| S4 | Host desktop app | The 3-command venv install funnel |
| S5 | Network economics — trust, credits, abuse | — |

The product this serves: **an open volunteer compute network**, with a
consumer-grade host app (S4) and a developer-grade submit CLI (S2). That
decision is what makes versioning load-bearing — you do not control when a
volunteer upgrades, so version skew is a permanent operating condition rather
than a transitional one.

---

## 3. Part A — repository topology and releases

### 3.1 Target

```
github.com/Zolli-Labs/flashml            PUBLIC   (new)
├── flashruntime/          → PyPI: flashruntime
├── flashnode/             → PyPI: flashnode
└── examples/federated/    (absorbs Zolli-Labs/flashml-example-federated)

github.com/Zolli-Labs/flashml-cloud      PRIVATE  (existing, minus two subtrees)
├── apps/api/     depends on a PINNED flashruntime
├── apps/web/
├── e2e/          installs the same pinned versions
└── render.yaml
```

One direction of dependency. No subtrees, no mirrors, no manual sync step.

### 3.2 Migration, preserving history

The existing public repos *are* the split histories, so the new repo is
assembled from them rather than re-split:

1. Create empty `Zolli-Labs/flashml`.
2. `git subtree add --prefix=flashruntime <public flashruntime> main`
3. `git subtree add --prefix=flashnode <public flashnode> main`
4. `git subtree add --prefix=examples/federated <flashml-example-federated> main`
5. Remove `flashruntime/` and `flashnode/` from `flashml-cloud`; replace with
   pinned dependencies (§3.3).

**The old public repos are archived read-only, never deleted.** Today's
enrolment command points at those URLs; archived GitHub repos still clone, so
every machine currently enrolled keeps working through the transition. Deleting
them breaks hosts that are earning right now. Each gets a README commit
pointing at the new home before archiving.

`flashml-example-federated` moving into the monorepo is safe because the API
fetches job repos by URL at submit time — but any doc or test citing the old
URL must be updated in the same change, and the old repo is likewise archived,
not deleted.

### 3.3 The pinning rule

Every consumer names an exact version:

- `apps/api/pyproject.toml`: `flashruntime==0.3.0`
- `flashnode/pyproject.toml`: `flashruntime>=0.3,<0.4`
- `e2e`: installs the same pinned versions, not the working tree
- `render.yaml`: drops `pip install -e ../../../flashruntime` entirely; the
  build becomes a plain `pip install -e .`

**Pre-release escape hatch.** When the API needs an unreleased runtime feature —
exactly today's `build_round` situation — it pins a git SHA against the now-public
repo, which needs no credentials:

```
flashruntime @ git+https://github.com/Zolli-Labs/flashml@<sha>#subdirectory=flashruntime
```

**SHA pins are permitted on `main` and forbidden in a release.** A release check
fails if any dependency resolves to a SHA. That gate is what converts "we'll
publish the wheel later" into "we publish the wheel now" — the discipline whose
absence produced the stale PyPI wheel.

### 3.4 Release pipeline

Tag `flashruntime-v0.3.0` on the public repo → CI builds the wheel, publishes to
PyPI via trusted publishing (no long-lived token), creates a GitHub release.
Same for `flashnode-vX.Y.Z`. Versions are independent; `flashnode` declares a
compatible range on `flashruntime`, never `*`.

### 3.5 Protocol version handshake

Pins fix what *we* deploy. They cannot fix what a volunteer is running, because
we do not control when a volunteer upgrades. The failure mode is already
documented in `flashnode/pyproject.toml`: an agent on `flashruntime` 0.1.0
registers without `module_capable`, the module gate fails open and tolerates it,
but the `argv_capable` gate fails closed — and the volunteer sees a task that
never arrives, with no explanation.

So: the agent sends `protocol_version` at registration, and the coordinator
answers with an explicit outcome — accepted, accepted-with-warning, or refused
with an upgrade instruction. Skew becomes a managed, observable condition
instead of a silent capability denial. Cheap now; load-bearing once S4 puts the
agent on machines we cannot reach.

### 3.6 Drift detection

`e2e` installs the **pinned** runtime and the **pinned** node — not the working
tree — and runs the full loop. This is the check that replaces the subtree
ancestor check, and unlike it, it catches semantic divergence rather than only a
missing file. It is the test that would have caught `images.py`.

This extends D12 (cross-repo seams need a test importing both sides) from
import-time to runtime, and from one process to the deployed artifact.

### 3.7 Preserving the local dev loop

`CLAUDE.md` documents how fragile the cross-component editable-install loop
already is; absolute paths recorded in console scripts mean a moved checkout
looks like a broken venv. Two repos makes this worse unless handled explicitly:

`scripts/dev.sh` gains `--local-runtime`, which editable-installs from a sibling
`../flashml/flashruntime` checkout when present. **The pinned version is the
default.** Fast inner loop preserved; it cannot leak into a deploy, because
`render.yaml` no longer has a relative path to leak through.

---

## 4. Part B — the diskless control plane

### 4.1 What is on the disk

Four things depend on coordinator-local state. All four must move, or the disk
stays and §1's deploy outage stays with it.

| State | Today | Where it lives |
|---|---|---|
| Leases | `SqliteLeaseStore` | `/data/leases.db` (`service/app.py:139`) |
| Jobs + events | `Ledger`, "POC-scale persistence: one file, synchronous sqlite3 behind a lock" | `FLASHML_LEDGER_PATH`, `/data/ledger.db` in `render.yaml` (code default `/data/flashruntime.db`, `service/app.py:63`) |
| Artifacts | raw directory, not behind `ArtifactStore` | `/data/artifacts` (`service/app.py:71`) |
| Checkpoint manifests | `CheckpointCatalog._manifests`, a Python dict | process memory — lost on restart |

### 4.2 Concurrency: optimistic, not row-locking

`LeaseManager.claim()` (`leases/manager.py:86`) is a read-modify-write in
Python: `next_pending()` → mutate the record → `save()`. Two workers calling it
concurrently both receive the same record.

A row-locking claim (`SELECT … FOR UPDATE SKIP LOCKED`) is the usual fix and is
**wrong here**. The scheduler path in the same method loads *all* pending
records and calls `policy.choose(pending_specs, node)` — placement logic in
Python that cannot be expressed as one SQL statement, and that seam is exactly
what M2's heterogeneity-aware placement is built on. Row locking would make FIFO
claims safe and silently leave the policy path racy.

**Mechanism: optimistic concurrency.** Each `TaskRecord` carries a version.
`LeaseStore.save(record, expected_version=...)` raises `ConflictError` when the
row has moved underneath the caller. `claim()` retries in a bounded loop; on
exhaustion it returns `None`, which callers already handle as "nothing
claimable."

Three reasons this is the right shape:

- it covers the FIFO path and the policy path with one mechanism;
- it keeps the existing `LeaseStore` protocol shape rather than adding an atomic
  `claim()` the policy path could not use;
- **SQLite can implement it too** — so the OSS single-machine path gets identical
  semantics and shares the concurrency tests, instead of being a second, less
  tested code path.

`sweep()` mutates expired leases and takes the same treatment.

### 4.3 Work items

1. **Version + `expected_version` on `LeaseStore`**, implemented in
   `InMemoryLeaseStore` and `SqliteLeaseStore` first, with the retry loop in
   `LeaseManager.claim()` and `sweep()`. No Postgres yet — this step is
   verifiable on its own.
2. **`PostgresLeaseStore`** behind the same protocol. SQLite implementations
   stay: `flashruntime` is public and must remain runnable on a laptop with no
   Postgres, and e2e runs on SQLite.
3. **Make `CheckpointCatalog` use the `ManifestStore` protocol that already
   exists.** *(Corrected 2026-08-01: an earlier draft of this spec claimed no
   protocol existed. `flashruntime/checkpoint/store.py` defines a
   `ManifestStore` Protocol — `add`/`save`/`get`/`all` — with an
   `InMemoryManifestStore` reference implementation. The catalog simply
   ignores it and keeps its own `self._manifests` dict.)* So the work is
   dependency injection plus a Postgres implementation, not an extraction.
   Retires HANDOFF risk #3.

   The catalog's other piece of state, `_registered` — parts announced but not
   yet committed — stays in memory deliberately. Losing it on restart costs an
   agent one re-registration, and persisting pre-commit scratch would add write
   volume for no durability the system needs.
4. **Ledger behind a store protocol**, same pattern, SQLite and Postgres
   implementations.
5. **Wire the service to `ArtifactStore`, and move agents onto scoped URLs
   (§4.4).** The Protocol and `S3CompatibleArtifactStore` already exist and are
   tested against MinIO (`tests/integration/test_minio_artifact_store.py`); the
   service simply bypasses them. Add a `LocalArtifactStore` implementation for
   the OSS and e2e paths, replace the raw `FLASHML_LOCAL_ARTIFACTS_DIR` writes,
   and add lease-scoped presigned URL minting so agents transfer bytes
   directly.
6. **Drop the disk; lift `--workers 1`; enable rolling deploys.** A config
   change, safe only once 1–5 land.

Items 1–5 are independently shippable and independently verifiable. Item 6 is
the payoff.

### 4.4 The artifact path changes shape, not just its backend

Today an agent uploads an artifact by `PUT`ing it **through the API**, which
proxies to the coordinator, which writes it to a local directory. Moving to
object storage is an opportunity to fix two things at once rather than lifting
the same shape onto a new backend.

**Agents read and write object storage directly, using short-lived scoped
URLs minted by the coordinator.** The control plane hands out a credential
that is valid for one object key, one verb, and a few minutes; the bytes never
traverse the API. Three consequences, in descending order of importance:

1. **It closes HANDOFF risk #2 as a side effect.** The open, unauthenticated
   artifact `PUT` on the coordinator stops existing — there is no endpoint to
   leave open, because uploads no longer arrive at the coordinator at all. What
   replaces it is a URL scoped to the caller's live lease. That risk is
   currently *contained* by D2 rather than fixed; this fixes it.
2. Multi-gigabyte checkpoints stop streaming through a `starter`-plan web
   service that also has to answer lease claims.
3. The API stops needing to hold artifact bytes in memory or on its own
   ephemeral disk.

The cost is that the coordinator must be able to mint scoped credentials for
whichever backend is chosen, which is a real constraint on §8.2's choice — S3
and R2 both do this with presigned URLs; verify Supabase Storage's signed-upload
support before selecting it.

This does **not** pull S3's remaining work forward. The unscoped operator token
is untouched and still belongs to S3.

### 4.5 One Postgres instance, two schemas

The API and the coordinator share a Postgres **instance** and never share a
**schema**:

| Schema | Owner | Holds |
|---|---|---|
| `public` | API | `profiles`, `machines`, `device_codes`, `jobs`, `job_rounds`, `contributions` |
| `coordinator` | Coordinator | leases, ledger + event stream, checkpoint manifests |

The boundary is not cosmetic. `flashruntime` is public code that must run on a
laptop with nothing but a `DATABASE_URL`; it must never learn what a `profile`
or a Supabase project is. Sharing an instance is a cost decision and can be
reversed later by handing the coordinator a different DSN — sharing a schema
would be a design decision, and reversing it would mean untangling product
concepts from runtime state.

The coordinator's role owns only its own schema, so a compromised coordinator
cannot read accounts.

### 4.6 Which Postgres

The coordinator is public code and must never know what Supabase is: it takes a
`DATABASE_URL` and nothing else. Point it at the same Supabase instance the API
already uses, with its own schema and its own role — cheapest option, and the
two datasets stay logically separated.

Inherit the connection guidance `render.yaml` already documents the hard way:
**session pooler on :5432, not the transaction pooler on :6543.** psycopg uses
prepared statements by default and transaction mode breaks them intermittently —
the worst possible failure mode for a lease store.

### 4.7 What this does not change

D2 stands: the coordinator remains a private service and the API remains the
only public door. Nothing here touches the security model — the unscoped
operator token and the unauthenticated coordinator-side artifact `PUT` (HANDOFF
risk #2) are untouched by this spec and belong to S3. Both are contained today
*because* the coordinator is a `pserv` with no public URL; S3's first task is to
verify that containment claim rather than assume it.

That containment is what makes §4.3 item 6 safe: lifting `--workers 1` and
removing the disk changes how the coordinator scales, not who can reach it. The
service stays `pserv`.

---

## 5. Testing

**Part A**

- A test asserting no dependency in `flashml-cloud` resolves to a git SHA when
  building a release (§3.3).
- `e2e` installs pinned published versions rather than the working tree (§3.6),
  and runs the existing full loop.
- A protocol-handshake test: an agent declaring an old `protocol_version` gets
  an explicit refusal with upgrade instructions, not a silent capability denial.

**Part B**

- **The load-bearing new test:** N concurrent claimers against one store,
  asserting no task is ever double-leased. Parameterized over
  `InMemoryLeaseStore`, `SqliteLeaseStore`, and `PostgresLeaseStore` so all three
  are held to the same contract. Today this property holds by accident — one
  event loop — and nothing tests it.
- The same concurrency test for `sweep()` racing `claim()`.
- Restart tests: coordinator restart preserves leases, jobs, events, and
  **checkpoint manifests** (the last is new; it currently fails).
- Existing flashruntime, flashnode, and e2e suites stay green throughout. Record
  counts per suite in `PROGRESS.md` per its logging protocol.

---

## 6. Definition of done

1. `Zolli-Labs/flashml` is public and holds `flashruntime`, `flashnode`, and
   `examples/federated` with history intact.
2. The old public repos and `flashml-example-federated` are archived read-only
   with a pointer README; no history was rewritten and no repo was deleted.
3. `flashml-cloud` contains no `flashruntime/` or `flashnode/` directory, and
   `render.yaml` contains no relative-path install.
4. `flashruntime` and `flashnode` are published to PyPI from a tag, and
   `pip install flashnode` works on a clean machine.
5. Every consumer pins an exact version or a SHA; a release with a SHA pin fails.
6. An agent with an incompatible `protocol_version` receives an explicit refusal.
7. `e2e` runs against pinned published artifacts and is green.
8. The coordinator holds no state on local disk; the Render disk is removed.
9. The coordinator runs more than one worker, and a deploy completes without
   dropping an in-flight lease — demonstrated, not asserted.
10. The concurrency test passes against all three lease-store implementations.

---

## 7. Risks

1. **The e2e suite currently installs from the working tree.** Switching it to
   pinned artifacts means a runtime change is no longer covered by e2e until it
   is released. Mitigation: e2e accepts a SHA pin on `main` (§3.3), so the gap is
   one CI run, not one release cycle.
2. **Removing the disk is one-way on Render.** Confirm artifacts have fully
   migrated before deleting, or committed artifacts are lost. Sequence: migrate,
   verify reads against object storage in production, *then* remove.
3. **Optimistic retry can starve** under heavy contention with many nodes
   claiming from a small pending set. Bounded retry returns `None`, which is
   correct-but-idle. Worth measuring against a realistic node count before
   assuming the bound is generous enough.
4. **The Supabase session pooler is a single point of failure** shared with the
   API. If the coordinator's connection pattern is materially different from the
   API's, a dedicated instance may be warranted — decide with load data, not now.
5. **Two repos worsens the venv fragility** already documented in `CLAUDE.md`.
   §3.7 mitigates but does not eliminate it; the reinstall runbook needs updating
   in the same change.

---

## 8. Open questions

1. Does `SqliteLeaseStore` implement `expected_version` cleanly, or does the
   optimistic-concurrency contract leak Postgres-shaped assumptions into the OSS
   path? Decide with code in hand at §4.3 item 1 — if it leaks, the honest
   outcome is a thin shared interface with two stores, not a leaky abstraction.
2. Object storage: Supabase Storage (one fewer vendor) or Cloudflare R2 (no
   egress fees, which matters once volunteers pull checkpoints)? Not load-bearing
   for the design — `ArtifactStore` already abstracts it — so defer to
   implementation.
3. Should `flashml-cloud` remain a monorepo of `apps/api` + `apps/web` + `e2e`,
   or does the web console eventually want its own deploy cadence? Out of scope
   here; revisit if console deploys start blocking on API changes.
4. Package name for the future submit CLI (`flashml` on PyPI?) — S2's decision,
   but worth reserving the name on PyPI during §3.4 rather than discovering it
   taken later.
