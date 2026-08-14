-- 0034_job_coordinator.sql
--
-- WHICH CONTROL PLANE SERVES THIS JOB. One nullable text column on
-- public.jobs, plus the CHECK that keeps it to the two venues the API can
-- actually address.
--
-- ---------------------------------------------------------------------------
-- WHY THIS EXISTS.
--
-- Until now this deployment had exactly one coordinator: the Render
-- `type: pserv` private service, reached over Render's internal network.
-- There are now two. The second is the same coordinator image running as an
-- Alibaba Function Compute Web Function, and the whole reason it exists is to
-- be MEASURED against the first — invocation count, cold starts, duty cycle.
--
-- They are two DEPLOYMENTS WITH TWO DATABASES, not two routes to one. The
-- coordinator that accepted a job is the only one that holds its tasks, its
-- leases, its event ledger and its artifacts. So "which coordinator" is a
-- property OF THE JOB, decided once at submission, and every later call about
-- that job — status, events, tasks, cancel, artifacts, checkpoints, the
-- attempt commit an agent makes — has to reach the same one. That fact has to
-- outlive the request that made it, which is why this is a column and not a
-- header, a setting, or anything the caller re-asserts per call.
--
-- WHAT GOES WRONG IF A JOB'S VENUE IS NOT PINNED. A job that switched venue
-- mid-flight would not error. Its leases live inside whichever coordinator
-- issued them, so the new one has never heard of the job: the agent's commit
-- lands nowhere, the lease ages out in the original coordinator's sweeper, and
-- the task is silently requeued. The run gets slower and more expensive and
-- nothing anywhere says why. Pinning the job — never the request — is the
-- entire point of this column.
--
-- ---------------------------------------------------------------------------
-- NULLABLE, WITH NO DEFAULT, AND NOT BACKFILLED.
--
-- **NULL means 'render'**, and the API resolves it on the way out so no
-- response ever carries a null `coordinator`. Three consequences, all wanted:
--
--   1. Every row that exists today stays valid, untouched, and keeps meaning
--      exactly what it meant — every one of them really did run on Render,
--      because there was nowhere else to run.
--   2. This migration is safe to apply to both Supabase projects in either
--      order relative to the API deploy. Applied first, the current API never
--      writes the column and every row reads back as 'render', which is what
--      it already does. Applied after, the new API's INSERT would name a
--      column that does not exist — so the DATABASE MOVES FIRST, the ordinary
--      rule for an additive column.
--   3. A row with an explicit 'render' and a row with NULL are the same job to
--      every reader. The API writes the explicit value on submits that named a
--      venue; nothing depends on being able to tell the two apart, and nothing
--      should start.
--
-- NO DEFAULT of 'render', deliberately. A default would be the database making
-- the venue decision for every writer that forgot to, which is precisely the
-- thing that should be visible in code. The fold from NULL to 'render' lives
-- in one function in the API and is testable there.
--
-- NO BACKFILL. `update public.jobs set coordinator = 'render'` would be
-- correct today and is still not worth doing: it rewrites every row in the
-- table to record a fact the read path already knows, and the day a third
-- venue arrives it is the migration that makes 'no answer' indistinguishable
-- from 'answered render'.
--
-- ---------------------------------------------------------------------------
-- `text` + CHECK, NOT AN ENUM.
--
-- A Postgres enum needs `alter type ... add value` to grow, which cannot run
-- inside a transaction block — and this repo's migration runner
-- (`flashml_cloud_api.migrate`) applies every file inside one. A third venue
-- would therefore need a deploy step this project does not have. A CHECK is
-- dropped and re-added in the same transaction, which is what the statements
-- below do, and it is the same shape 0015 uses for `attempts.outcome`.
--
-- The CHECK is not decoration. `CoordinatorClient._resolve` refuses an unknown
-- venue rather than falling back, and the submit routes refuse one at the edge
-- with a 400 — but a job row carrying a venue string nothing can resolve is a
-- job that can never be read, cancelled or committed to again, and the failure
-- would surface far from the write that caused it. The database is the check
-- that cannot be forgotten by a future writer.
--
-- `coordinator is null or coordinator in (...)`: the null branch is explicit
-- because a bare `in` would already admit NULL (it evaluates to NULL, which a
-- CHECK treats as passing), and relying on that is how someone later "tightens"
-- the constraint into one that rejects every pre-migration row.
--
-- ---------------------------------------------------------------------------
-- NO INDEX ON THIS COLUMN, AND THAT IS A DECISION.
--
-- One read does scan on it: `db.active_job_venues`, which answers "which
-- venues currently have a non-terminal job" so the lease-claim route knows
-- which coordinators are worth polling. That query is
-- `where coordinator is not null and finished_at is null and status not in
-- (terminal...)` — a `select distinct` over what is, on any deployment this
-- exists on, a small table filtered to the handful of jobs actually running.
-- The claim route reads it once per claim; measure before indexing. When there
-- is a reason, the right index is partial and leads with the selective half
-- (`(coordinator) where finished_at is null`), the same shape 0025 uses for
-- `share_token`.
--
-- ---------------------------------------------------------------------------
-- WHY THIS MATTERS FOR CLAIMS. A lease claim is worker-initiated and carries
-- no job id, so nothing in its payload says which coordinator to ask. It is
-- resolved from this column instead: only venues with an active job are polled.
-- FC's whole value is a duty-cycle number, and an idle coordinator being
-- polled by the fleet every few seconds would inflate its invocation count
-- until the measurement said nothing. "No FC job, no FC traffic" is a property
-- of this column being accurate.
--
-- Row Level Security on public.jobs is unchanged — enabled since 0001, still no
-- policies, which in Postgres denies every role except the table owner and
-- BYPASSRLS. A column grants nobody anything, and
-- `test_no_migration_creates_any_policy_at_all` is the doctrine.
--
-- HOW THIS IS APPLIED: by the migration runner,
-- `python -m flashml_cloud_api.migrate`, which records it in
-- public.schema_migrations. There are TWO databases, dev (auto-migrated by the
-- `migrate-dev` job in .github/workflows/ci.yml on every push to `develop`)
-- and production (the `migrate-prod` job, gated on every test job and on the
-- manual deploy workflow). Do not apply it by hand to either — the runner is
-- what keeps the two honest.
--
-- Idempotent: `add column if not exists`, `drop constraint if exists` before
-- the add, re-runnable `comment on`.
--
-- Do not edit this file after it has been applied anywhere: the runner
-- checksums it, and an edit reads as drift and blocks every later migration.

alter table public.jobs
    add column if not exists coordinator text;

alter table public.jobs
    drop constraint if exists jobs_coordinator_check;
alter table public.jobs
    add constraint jobs_coordinator_check
        check (coordinator is null or coordinator in ('render', 'fc'));

comment on column public.jobs.coordinator is
    'Which control plane serves this job: ''render'' (the Render pserv '
    'coordinator) or ''fc'' (the Alibaba Function Compute one). NULL means '
    '''render'' — every row predating the second venue really did run there, '
    'and the API folds NULL to ''render'' on the way out so no response ever '
    'carries a null. Decided ONCE at submission and never re-decided: the two '
    'venues are separate deployments with separate databases, so the '
    'coordinator that accepted a job is the only one holding its tasks, '
    'leases, ledger and artifacts. A job that switched venue mid-flight would '
    'not error — its leases would simply age out in the coordinator that '
    'issued them and its tasks would silently requeue. Also the input to '
    'db.active_job_venues, which is what stops the fleet polling an idle FC '
    'coordinator and inflating the invocation count its duty-cycle '
    'measurement depends on.';
