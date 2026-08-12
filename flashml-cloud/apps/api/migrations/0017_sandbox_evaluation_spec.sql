-- 0017_sandbox_evaluation_spec.sql
--
-- What the sandbox was opened to evaluate, on the session row instead of in
-- an event payload.
--
-- WHY THIS EXISTS. A session is opened while training is still running and
-- the evaluation is submitted much later, on the far side of a hibernation
-- that is explicitly designed to outlive the process that started it. So the
-- specification has to be DURABLE, and 0014 gave it nowhere to live: the
-- controller stored it as a `sandbox_events` payload and read it back on the
-- wake path.
--
-- That works until somebody writes a spec with an ordinary field name.
-- `sandbox_sessions.redact_data` replaces the value under any key matching
-- `signature|secret|password|token|credential|authorization|access_key|
-- api_key|private_key` — as a SUBSTRING, deliberately, and its own docstring
-- says so: "a field called `tokens_processed` is redacted along with a field
-- called `token`". Applied to an error message that is exactly right.
-- Applied to a job specification it is silent data loss: a spec containing
-- `api_key_name`, `token_budget` or `secret_share_count` comes back as
-- `"[redacted]"`, the evaluation is submitted with a mangled spec, and
-- nothing anywhere says so. It passes every test written with a plain spec
-- and fails on the first real one, hours into a run, on the one code path
-- that only executes after a hibernation.
--
-- THE REDACTION IS NOT WEAKENED BY THIS. Not one pattern changes and not one
-- event stops being scrubbed; `error_message`, `error_code` and every string
-- inside every event's `data` go through it exactly as before. This
-- migration removes a *use* of that machinery which never should have been
-- one — a specification is an input somebody wrote down, not an SDK
-- exception echoing the request that caused it, and the two want opposite
-- treatment.
--
-- WHAT MAY GO IN IT: a job specification, and 0014's rule still holds
-- unchanged — NO TOKEN, KEY OR SIGNED URL, EVER. This is the same kind of
-- value as `jobs.spec` (0001), which is also jsonb and is also not a
-- credential store: it says what to run and against what, and the things
-- that grant access to any of it live elsewhere by construction. The sandbox
-- authenticates as an ordinary machine (hashed `fmk_` token in
-- `public.machines`) and reaches OSS through presigned URLs minted per
-- object at the moment of use (`alibaba_oss`, spec D5). A column whose leak
-- grants somebody something is precisely what the GitHub App design removes,
-- and adding one here would put it back. The column comment below says this
-- where the next person will actually read it.
--
-- NOT `jobs.spec`, AND NOT A FOREIGN KEY TO ONE. The evaluation is a
-- DIFFERENT job from the training run the session names, and at the moment
-- this row is created it does not exist yet — the coordinator mints its id
-- only when the task is finally submitted, on the wake path. There is
-- nothing to point at, which is the whole reason the value has to be carried
-- rather than referenced.
--
-- NULLABLE, NO DEFAULT, for 0016's reason. NULL is the truth for every
-- session that predates this migration, and it stays a legitimate value: a
-- session may be opened with nothing to say beyond "evaluate this job", and
-- `'{}'::jsonb` as a default would assert an empty specification about rows
-- that never had one.
--
-- WRITTEN ONCE, AT CREATE. `create_session` sets it and `transition` does
-- not touch it — the same discipline as `share_token`, and for a sharper
-- reason: `transition`'s column arguments are fill-in-only precisely so this
-- ledger cannot un-learn a fact, and a specification that could change
-- half-way through a session would make the evaluation that actually ran
-- unexplainable from the row that describes it.
--
-- NOT IN THE PUBLIC SHARE VIEW. `SESSION_SHARE_COLUMNS` is what the
-- no-auth evidence page may read, and this column is deliberately absent
-- from it. A spec names datasets, repositories, metrics and thresholds that
-- belong to the person who wrote it; the evidence page exists to prove a
-- sandbox hibernated and woke, and it can do that without publishing what
-- somebody was measuring.
--
-- NO NEW INDEX, deliberately, for the reason 0010, 0011 and 0016 all give.
-- It is only ever read for a single session whose row the caller has already
-- fetched by primary key, and it is never a search predicate.
--
-- Row Level Security on `public.sandbox_sessions` is unchanged and still has
-- no policies, exactly as 0014 left it — which in Postgres denies every role
-- except the table owner and BYPASSRLS, and the owner filter stays folded
-- into the queries themselves. A column grants no access.
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

alter table public.sandbox_sessions
    add column if not exists evaluation_spec jsonb;

comment on column public.sandbox_sessions.evaluation_spec is
    'What this sandbox was opened to evaluate: a JOB SPECIFICATION, written '
    'once when the session is created and read on the wake path, when the '
    'process that wrote it is routinely no longer running. NOT A PLACE FOR A '
    'CREDENTIAL — 0014''s rule is unchanged, and no token, key or signed URL '
    'may be stored here or anywhere else on this table. It exists because '
    'the alternative was an event payload, and every string in an event is '
    'scrubbed by a key-name matcher that would silently replace a spec field '
    'called api_key_name or token_budget with [redacted]. That scrubbing is '
    'unchanged and still applies to every event; this column simply is not '
    'one. Deliberately outside SESSION_SHARE_COLUMNS: the unauthenticated '
    'evidence page proves a hibernation, it does not publish what somebody '
    'was measuring.';
