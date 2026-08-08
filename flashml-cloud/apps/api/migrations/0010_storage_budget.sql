-- 0010_storage_budget.sql
--
-- Per-account artifact storage accounting and its ceiling.
--
-- WHY THIS EXISTS. Nothing in this system limited how much one account
-- could store. Artifacts land on a 5 GB disk attached to the prod
-- coordinator and every workspace shares it, so one person submitting a
-- hundred shards that each write 200 MB fills it. A full disk does not
-- degrade that person's job — it takes the coordinator down, and with it
-- every other workspace's running work. This is the only exposure in the
-- system whose blast radius is "everyone".
--
-- TWO COLUMNS, TWO DIFFERENT JOBS.
--
-- `jobs.artifact_bytes` is a MEASUREMENT, recorded when a job reaches a
-- terminal state and its footprint stops changing. Summing it per owner is
-- what "how much am I using" means. It is deliberately not computed live
-- from the coordinator: that would be one HTTP call per job to answer a
-- question asked on every submit.
--
-- `profiles.storage_limit_bytes` is a POLICY, and it is NULLABLE on
-- purpose. NULL means "use whatever this deployment's default is", so the
-- operator can move every un-overridden account at once by changing one
-- environment variable instead of running an UPDATE across the table. A
-- stored 0 is a real value meaning "this account may store nothing" — an
-- admin freezing someone — and the reading code must not confuse it with
-- NULL. See flashml_cloud_api/storage.py, where that distinction is the
-- thing three separate tests pin.
--
-- BACKFILL: none needed, and that is worth stating rather than leaving to
-- inference. `artifact_bytes` defaults to 0, which is honest for every
-- pre-existing job: nobody measured them, and claiming a number we did not
-- measure would make the first quota refusal unexplainable. The practical
-- effect is that historical usage reads as zero and accrues from here.
--
-- HOW THIS IS APPLIED: by the migration runner (flashml_cloud_api/migrate.py),
-- in filename order, in one transaction per file.

alter table public.jobs
    add column if not exists artifact_bytes bigint not null default 0;

comment on column public.jobs.artifact_bytes is
    'Total bytes of artifacts this job produced, summed from the '
    'coordinator''s artifact listing and recorded once the job is '
    'terminal. 0 means not measured (every job predating migration 0010), '
    'never "measured and empty".';

alter table public.profiles
    add column if not exists storage_limit_bytes bigint;

comment on column public.profiles.storage_limit_bytes is
    'Per-account artifact budget in bytes. NULL means "use the deployment '
    'default" so an operator can move every un-overridden account with one '
    'environment variable. A stored 0 is a real value meaning this account '
    'may store nothing — do not read it as NULL.';

-- NO NEW INDEX, deliberately. The usage query is
-- `sum(artifact_bytes) where owner_id = ?`, and 0001 already indexes
-- `jobs (owner_id)`; a partial index on top of it would buy almost
-- nothing and cost this migration a dependency on a column it does not
-- otherwise touch. That is not hypothetical: the first draft carried
-- `create index ... on public.jobs (owner_id)` and
-- test_migrate.py::test_baseline_through_then_apply_runs_only_the_remainder
-- failed on it, because the baseline flow stands `jobs` up as
-- `(id text primary key)` and nothing more. Depending on fewer objects is
-- exactly what that test rewards.
