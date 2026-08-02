# CI/CD and environment separation

**Date:** 2026-08-02
**Status:** design approved (four decisions taken by the owner, §0)
**Supersedes:** nothing. This repo has never had CI.

---

## 0. Decisions taken

| # | Question | Decision |
|---|---|---|
| 1 | How separate should dev be? | **Gate prod first, then add dev.** Phase 1 = CI + deploy gate ($0). Phase 2 = dev environment (+~$7/mo). |
| 2 | Dev database | **A new `flashml-dev` Supabase project**, schema built from `migrations/*.sql` alone. Not the legacy `ZolliAI-Dev`. |
| 3 | Migrations | **Runner with a `schema_migrations` table.** Auto-apply to dev; prod application is a manual approval step. |
| 4 | CI scope | **All four:** apps/api pytest, e2e, apps/web build + tests, secret scanning. |

This spec covers **Phase 1 in full** and designs Phase 2 well enough to
schedule it. Phase 2 is not implemented here.

## 1. What is actually wrong today

Not "prod and dev are the same". Three distinct things:

### 1.1 Untested code deploys itself to production

All three services (verified 2026-08-02 via the Render API):

```
flashml-coordinator  srv-d9mts2u417fc73c7l5g0  branch main  autoDeploy yes  trigger commit
flashml-api          srv-d9mtspu417fc73c7mip0  branch main  autoDeploy yes  trigger commit
flashml-web          srv-d9mtt9e417fc73c7ni30  branch main  autoDeploy yes  trigger commit
```

`.github/workflows/` **does not exist in this repository.** Every commit that
reaches `main` deploys to production, and nothing runs the 419 API tests, the
61 e2e tests, or the 36 web tests first. The only CI in the org belongs to the
public `flashml` repo.

This is the acute problem and it costs nothing to fix.

### 1.2 There is one environment

One Supabase project (`flashml-poc` / `yualksqjjvlfscbbsygq`) behind one set
of services. Testing a schema change means testing it on production data.

### 1.3 Migrations are applied by hand

There is no runner. `0003` and `0004` carry hand-written header comments
saying "APPLIED TO SUPABASE by hand … against project yualksqjjvlfscbbsygq
ONLY". Nothing records what has been applied, so nothing can tell you whether
a database is up to date — you have to remember.

With one database that is survivable. With two it drifts within a week, and
the failure is silent: the app works against whichever database happens to
have the column.

## 2. Phase 1 — CI and the deploy gate

### 2.1 Control inversion: CI deploys, Render does not

Render's `autoDeployTrigger: checksPass` would gate deploys on GitHub checks.
It is **rejected** for two reasons, neither about its behaviour:

1. It is a dashboard/API setting, not part of the blueprint schema, so it
   cannot be expressed in `render.yaml` and reviewed in a diff. The two
   `zolli-ai-*` services already use it — set by hand, invisible in any repo.
2. The Render MCP surface available here has no update-service call, so it
   cannot be set or verified programmatically from this session.

Instead:

- `autoDeploy: false` on all three services in `render.yaml` (plainly part of
  the blueprint schema, reviewable, version-controlled).
- A GitHub Actions job triggers the deploy via the Render API
  (`POST /v1/services/{id}/deploys`) after the test jobs pass.

One mechanism, in version control, and it gives the prod-migration approval a
natural place to live (§2.4).

**Consequence to accept:** deploys now depend on GitHub Actions being up. A
manual deploy from the Render dashboard remains available and is the documented
break-glass path.

### 2.2 Jobs

| Job | Runs | Notes |
|---|---|---|
| `api` | `pytest -q` in `apps/api` | 419 expected |
| `web` | `npm ci`, `npm run build`, `npm test` | 36 expected |
| `e2e` | `make e2e-setup && make e2e` | 61 expected |
| `secrets` | gitleaks over the diff and history | no script exists in this repo yet |

All four run on `push` and `pull_request`. Deploy runs only on `push` to
`main`, and only if all four pass.

**`api` needs Postgres binaries, not a Postgres service.** `tests/conftest.py`
starts its *own* throwaway server with `initdb`/`pg_ctl` and requires
`initdb, pg_ctl, pg_isready, psql` on `PATH`. A `services: postgres:` container
would not satisfy it — the binaries must be on the runner. GitHub's
`ubuntu-latest` ships PostgreSQL but does not put its `bin` on `PATH`, so the
job prepends `/usr/lib/postgresql/<v>/bin`, discovered rather than hardcoded.

**`web` build needs `NEXT_PUBLIC_CLOUD_API`.** `next.config.ts` deliberately
fails a production build when it is missing — that guard exists because an
empty value was once baked into a bundle and surfaced as "Failed to fetch".
CI sets it to the real prod URL for the `main` build. This is not a secret;
it is already a literal in `render.yaml`.

**`e2e` is the slow one.** `make e2e-setup` installs the pinned artifacts from
PyPI *plus* torch, numpy, scikit-learn, pandas, scipy. That is a large
download, so the uv cache is keyed on the pins in the `Makefile`.

### 2.3 The migration runner

New: `flashml_cloud_api/migrate.py`, plus a `public.schema_migrations` table.

```sql
create table if not exists public.schema_migrations (
    version     text primary key,   -- '0004_attempts'
    checksum    text not null,      -- sha256 of the file as applied
    applied_at  timestamptz not null default now()
);
```

Behaviour:

- Applies pending `migrations/*.sql` in sorted order, **each in its own
  transaction**, recording version and checksum.
- **Checksum guard.** If a file whose version is already recorded no longer
  hashes to what was recorded, the runner refuses to do anything and names the
  file. An edited-after-apply migration means the databases have silently
  diverged, and continuing would deepen it.
- `--dry-run` lists what would be applied and exits non-zero if anything is
  pending. This is what makes "is prod up to date?" answerable.
- Idempotent: running it against an up-to-date database is a no-op.

**The existing four migrations must be back-filled, not re-applied.** They are
already in `flashml-poc`, and `0001` would fail against live tables. The runner
therefore takes `--baseline`, which records versions as applied without
executing them.

**Baselining is per-prefix, not all-or-nothing** (`--baseline-through
VERSION`), because prod is *behind*: it has 0001-0003 and not `0004_attempts`.
A bare `--baseline` would record 0004 as applied without creating
`public.attempts` — the ledger would credit nobody, forever, while `--dry-run`
reported the database up to date. Recording a migration you did not run is
indistinguishable, afterwards, from having run it. The prod sequence is
therefore: baseline through 0003, verify 0004 is still pending, then apply.
A one-time human operation (§5).

**`tests/conftest.py` switches to the runner.** It currently globs and applies
the SQL directly. Routing it through `migrate.py` means all 427+ tests exercise
the runner on every run — the alternative is a deploy-critical component whose
only test is the one place it is used least.

### 2.4 The prod gate

Prod migration and prod deploy run in a GitHub Environment named `production`
with a required reviewer. The pipeline stops and waits; the owner approves in
the GitHub UI; then `migrate.py` runs against prod, and only then does the
deploy fire. This is decision 3 ("gated for prod") implemented with a native
mechanism rather than a convention.

### 2.5 Secrets required

Repository secrets (set by the owner, §5):

| Secret | Used by |
|---|---|
| `RENDER_API_KEY` | deploy jobs |
| `PROD_DATABASE_URL` | prod migration job |

Service IDs are **not** secrets and are written literally in the workflow — a
service id is useless without the key, and having them visible makes the
workflow readable.

`DATABASE_URL` is deliberately **not** needed by the test jobs: the API suite
builds its own throwaway Postgres, and the e2e suite is cloud-free.

## 3. Phase 2 — the dev environment (designed, not built)

- A second Render blueprint or a `develop`-branch service group:
  `flashml-dev-coordinator` (`pserv`, **starter — forced**, since the free tier
  has neither private services nor disks), `flashml-dev-api` (free; sleeping is
  acceptable for dev), `flashml-dev-web` (free).
- A new `flashml-dev` Supabase project, schema created by the runner from
  `migrations/*.sql` with **no baseline** — which is what proves the migrations
  reconstruct the schema from nothing. Nothing verifies that today.
- `develop` merges auto-migrate and auto-deploy dev. `main` keeps the gate.

Estimated ~$7/mo. Not started here.

## 4. What this does not do

1. **No rollback automation.** A bad deploy is rolled back from the Render
   dashboard, and a bad migration is fixed by writing a new one forward. The
   runner has no `down` step, deliberately: reversible-by-script migrations
   are a much larger commitment and are usually wrong under load.
2. **No preview environments per PR.**
3. **Phase 1 does not separate prod and dev.** It gates prod. The owner chose
   this order explicitly; saying otherwise afterwards would misreport it.
4. **The e2e job does not test the deployed services.** It runs the loop
   locally against pinned artifacts, as `make e2e` does today. Post-deploy
   smoke testing is not in scope.

## 5. Human gates

🔒 Create `RENDER_API_KEY` and `PROD_DATABASE_URL` as repository secrets.
🔒 Create the `production` GitHub Environment with yourself as a required reviewer.
🔒 Run the one-time `--baseline` against `flashml-poc` so the four existing
migrations are recorded as applied rather than re-run.
🔒 `0004_attempts.sql` is still **not applied to prod** — after baselining, it
is the first migration the new pipeline will apply.

## 6. Definition of done

1. `.github/workflows/ci.yml` exists and runs four jobs on push and PR.
2. All four jobs pass on the current tree (419 / 61 / 36 / clean).
3. `render.yaml` sets `autoDeploy: false` on all three services.
4. A deploy job triggers Render only after the four jobs pass on `main`.
5. `migrate.py` applies pending migrations, refuses on checksum mismatch, and
   supports `--dry-run` and `--baseline`.
6. `tests/conftest.py` builds its schema through `migrate.py`.
7. The prod migration and deploy sit behind the `production` environment gate.
8. Test counts are unchanged: 419 / 61 / 36.
