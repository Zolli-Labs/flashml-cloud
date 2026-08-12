-- 0016_artifacts_mirrored.sql
--
-- When a job's accepted artifacts were copied to OSS — the marker that lets
-- the mirror run once instead of on every poll.
--
-- WHY THIS EXISTS. `artifact_mirror.py` shipped complete, tested, and with
-- no callers at all. Wiring it means running it at the one moment a job is
-- first observed terminal (`GET /v1alpha1/jobs/{id}`), which is a route a
-- console polls every two seconds for as long as a tab stays open. Without a
-- marker read off the row that route has already fetched, every one of those
-- polls would re-enter the mirror: a task listing, an artifact listing and a
-- manifest HEAD against OSS, forever, for a job whose bytes stopped changing
-- when it finished. This column is what makes "copy it once" a property of
-- the schema rather than of somebody's discipline.
--
-- DO NOT REUSE `artifact_bytes_recorded_at` (0011). The two markers look
-- interchangeable — both mean "this terminal job has been dealt with" — and
-- they are not, because they are STAMPED INDEPENDENTLY and one of them is
-- stamped by a path that never mirrors anything:
--
--   * The footprint measurement is one coordinator listing and it either
--     answers or it does not. The mirror is N object copies to a third party
--     that can fail halfway through, and `MirrorError` is deliberately its
--     only exit — the caller's whole contract is to log and leave the job
--     unmarked so the next observation retries. A failed mirror hiding
--     behind 0011's stamp would never retry: the footprint call that ran
--     beside it succeeded, so the row would read "done" and that job's model
--     would sit on a disk that a redeploy erases, permanently, with nothing
--     anywhere recording that the copy was owed.
--   * `DELETE /v1alpha1/jobs/{id}/artifacts` stamps 0011 on its way out
--     (`record_job_artifact_bytes(db, job_id, 0)`), because zeroing the
--     usage without the marker would let the next poll re-measure the job it
--     just emptied. Nothing was mirrored there — the OSS copy is being
--     deleted, not written — so a shared marker would record a mirror that
--     never happened for every job anyone ever deleted.
--
-- Separate markers also keep the two facts independently true: a job may be
-- measured and unmirrored (every job that predates this migration, and every
-- deployment with no OSS configured) or mirrored and unmeasured (a listing
-- that failed while the copies succeeded). One column cannot say that.
--
-- WHAT SETS IT, AND WHEN. Only after `mirror_job` reports the artifacts are
-- in OSS — `MIRRORED` (copied now) or `ALREADY_MIRRORED` (a current manifest
-- was already there). Deliberately NOT set for `NOT_CONFIGURED`: a
-- deployment with no OSS has not mirrored anything, and stamping there would
-- mean that the day OSS is configured, every job that had ever finished
-- before it would be permanently excluded from the mirror. That path costs
-- nothing to re-enter — `mirror_job` returns on a property check, before any
-- network call — so leaving it unstamped is free as well as honest.
--
-- A TIMESTAMP, NOT A BOOLEAN, for 0011's reason: same cost, and it answers
-- "when" as well as "whether". The manifest in OSS carries its own
-- `mirrored_at`, and an operator reconciling the two wants both sides to be
-- able to say it.
--
-- BACKFILL: none, deliberately. Every existing job reads NULL, which is the
-- truth — nothing has ever mirrored anything, since this is the migration
-- that gives the mirror its first caller. The practical effect is that a job
-- already terminal when this ships is mirrored the next time somebody opens
-- its page, which is correct rather than merely convenient. Nothing sweeps
-- the backlog: a job nobody ever looks at again is never mirrored, and that
-- is a known gap rather than a hidden one (there is no scheduler in this
-- process to hang such a sweep off, and inventing one behind a page poll is
-- how N replicas end up copying the same job N times).
--
-- NO NEW INDEX, deliberately, for the reason 0010 and 0011 both give. This
-- column is only ever read for a single job whose row the route has already
-- fetched by primary key; it is never a search predicate. Depending on fewer
-- objects is also what keeps this migration applyable under the baseline
-- flow, where
-- test_migrate.py::test_baseline_through_then_apply_runs_only_the_remainder
-- stands `jobs` up as `(id text primary key)` and nothing more.
--
-- Row Level Security on `public.jobs` is unchanged and still has no
-- policies, exactly as 0001 left it. A column grants no access.
--
-- Idempotent: `add column if not exists`, safe to re-run.
--
-- HOW THIS IS APPLIED: by the migration runner,
-- `python -m flashml_cloud_api.migrate`, in filename order, in one
-- transaction per file. There are TWO databases, dev (auto-migrated on merge
-- to `develop`) and production (gated behind a manual workflow); the runner
-- is what keeps them honest, so do not apply this by hand to either.
--
-- Do not edit this file after it has been applied anywhere: the runner
-- checksums it, and an edit reads as drift and blocks every later migration.

alter table public.jobs
    add column if not exists artifacts_mirrored_at timestamptz;

comment on column public.jobs.artifacts_mirrored_at is
    'When this job''s accepted artifacts were confirmed present in OSS '
    '(flashml_cloud_api.artifact_mirror). NULL means never mirrored — no OSS '
    'configured, never observed terminal, or a mirror that failed and is '
    'owed a retry. Deliberately separate from artifact_bytes_recorded_at: '
    'that one is stamped by the footprint measurement and by artifact '
    'deletion, neither of which copies a byte, so sharing it would record '
    'mirrors that never happened and suppress the retry of ones that '
    'failed. Set only after mirror_job reports the objects are in OSS.';
