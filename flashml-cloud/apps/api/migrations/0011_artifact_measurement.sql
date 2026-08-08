-- 0011_artifact_measurement.sql
--
-- When a job's artifact footprint was measured — the marker that lets the
-- measurement actually happen.
--
-- WHY THIS EXISTS. 0010 added `jobs.artifact_bytes` and described it as
-- "recorded when a job reaches a terminal state". Nothing ever recorded it.
-- The rule (storage.py), the measurement (db.storage_usage_for_owner), the
-- submit gate and GET /me/storage all shipped, and with no writer every
-- account read 0 bytes used for ever: the budget could not refuse anything
-- no matter what was on the shared 5 GB disk. This column is what closes
-- that, and it does so by answering the one question the recording hook has
-- to ask on every poll.
--
-- WHAT IT IS FOR, PRECISELY. A job page polls every two seconds and keeps
-- polling after the job has finished. Measuring a footprint costs an HTTP
-- call to the coordinator's artifact listing, and the answer stops changing
-- the moment the job is terminal — so the hook must be able to tell "I have
-- already measured this" cheaply, from a row it is holding anyway, with no
-- extra query and no second network call.
--
-- `artifact_bytes` ALONE CANNOT BE THAT MARKER, and 0010's own column
-- comment says why without drawing the conclusion: 0 means "not measured",
-- "never measured and empty". A job that genuinely wrote nothing would
-- therefore be re-listed on every poll for the rest of its life, which is
-- exactly the per-poll HTTP call the design set out to avoid. Worse, the
-- failure path needs the same distinction from the other side: an artifact
-- listing that times out must NOT mark the job measured, or one unlucky
-- second makes that job free for ever. A separate timestamp makes both
-- cases unambiguous — set it only when a listing actually answered.
--
-- A TIMESTAMP, NOT A BOOLEAN. Same cost, and it answers "when" as well as
-- "whether". A footprint measured before a later round wrote more (the
-- federated case, where the parent job's terminal status is written by the
-- in-API driver) is a thing an operator will eventually want to see the age
-- of; a boolean would have to be re-migrated to say it.
--
-- BACKFILL: none, deliberately. Every pre-existing job has a null here,
-- which is the truth: nobody measured them, and 0010's `artifact_bytes`
-- default of 0 already reads as "not measured" for exactly the same reason.
-- The practical effect is that historical usage stays 0 and accrues from
-- here — and that a job already terminal when this ships is measured the
-- next time somebody opens its page, which is correct rather than merely
-- convenient.
--
-- NO NEW INDEX, deliberately, for the same reason 0010 added none. This
-- column is only ever read for a single job whose row has already been
-- fetched by primary key on the route that reads it; it is never a search
-- predicate. Depending on fewer objects is also what keeps this migration
-- applyable under the baseline flow, where
-- test_migrate.py::test_baseline_through_then_apply_runs_only_the_remainder
-- stands `jobs` up as `(id text primary key)` and nothing more — the trap
-- 0010's first draft fell into.
--
-- HOW THIS IS APPLIED: by the migration runner (flashml_cloud_api/migrate.py),
-- in filename order, in one transaction per file.

alter table public.jobs
    add column if not exists artifact_bytes_recorded_at timestamptz;

comment on column public.jobs.artifact_bytes_recorded_at is
    'When this job''s artifact footprint was measured from the '
    'coordinator''s listing. NULL means never measured, which is what '
    'distinguishes a job that wrote nothing (0 bytes, timestamp set) from '
    'one nobody has looked at yet (0 bytes, timestamp null) — and what '
    'stops a failed listing from being recorded as an answer. Set only by '
    'db.record_job_artifact_bytes, in the same statement as the bytes.';
