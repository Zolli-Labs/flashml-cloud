-- 0026_correlation_id.sql
--
-- ONE ID SPANNING session_id -> sandbox_id -> job_id -> task_id -> lease_id.
--
-- WHY THIS EXISTS. Every one of those five ids already exists in this schema
-- and nothing joins them, so answering "what happened to this piece of work?"
-- means five queries and a human holding the thread between them. That is the
-- gap recorded as G-B in
-- `docs/superpowers/specs/2026-08-12-observability-and-verification-gaps.md`,
-- and D-1/D-2/D-3 there are the design this migration implements.
--
-- THREE TABLES, BECAUSE THREE TABLES ANCHOR THE FIVE IDS. `public.jobs` is
-- `job_id`. `public.sandbox_sessions` is `session_id` (its own `id`) AND
-- `sandbox_id` (`external_sandbox_id`). `public.attempts` is `task_id` AND
-- `lease_id` (its primary key). Nothing else needs a column: `verifications`,
-- `contributions` and `job_rounds` are all reachable by the (job_id, task_id)
-- they already carry. And NO JOIN TABLE — a chain that needs a join table to
-- be read is a chain nobody reads.
--
-- `public.rented_capacity` is DELIBERATELY NOT HERE even though D-1 names an
-- acquisition as one of the three edges. Its only writer is
-- `capacity/acquire.py:_open_row`, which is claimed by another session
-- tonight; a column no code can write is dead schema that reads, to the next
-- person, like a chain that exists and is empty. It is one `alter table` and
-- one column in one INSERT when that file frees up.
--
-- NULLABLE, WITH NO DEFAULT, AND NOT BACKFILLED. All three of those are the
-- same rule (D-2), stated three ways, and it is the rule that matters most:
--
--   **Absent is null. A row without a correlation id says so.**
--
-- A DEFAULT of `gen_random_uuid()` would be the database minting one on every
-- write nobody supplied one for — which is precisely the prohibited
-- behaviour, expressed in DDL, and it is the version of it nobody would ever
-- notice because every row would look threaded. An UPDATE backfilling the
-- existing rows would be worse still and is unrecoverable: it would relate
-- every job this deployment has ever run to every other, and no later
-- migration can tell an invented thread from a real one afterwards.
--
-- Pre-existing rows stay null for ever. That is correct, and it is the same
-- discipline `public.verifications` applies to `unknown`: a "could not tell"
-- that arrives as an answer is worse than no answer, and a tolerant writer is
-- exactly how that happens.
--
-- `uuid`, NOT `text`. The column is the key every log line and every future
-- lookup is threaded on, so it must not be able to hold a value a submitter
-- authored. `text` would accept a hostname (`machines.name` IS the `hostname`
-- its host supplied at enrolment, `enrolment.py:164`), a task's stderr, or a
-- path the submitted code chose; `uuid` refuses all three in the database,
-- underneath any bug in `db.py`. The application refuses them too
-- (`observability.correlation_id_or_none`), and both checks are wanted: this
-- one is the one that cannot be forgotten.
--
-- MINTED BY THE APPLICATION, ONCE, AT THE EDGE. `observability.new_correlation_id`
-- is the only definition of a mint in the package, and it is called from the
-- outermost thing a user initiated — a job submission, a sandbox session, an
-- acquisition. Everything below inherits: `record_attempt` copies the id from
-- the job the attempt is an attempt OF, in the same INSERT, so a downstream
-- row can only ever join a thread that already exists.
--
-- IT IS NOT A REQUEST ID, and that is the requirement rather than a nuance. A
-- request id dies with the response. This has to outlive the request because
-- the work it names does: a sandbox session hibernates, the instance is
-- destroyed, and the process that would have carried an in-memory id is gone
-- long before the evaluation runs. A column is what survives that, which is
-- why this is a migration and not a middleware.
--
-- NO INDEX, YET, AND THAT IS A DECISION. The read this serves is "show me
-- everything on thread X", which is an operator query against three tables
-- that have never been queried that way — and there are no non-null values in
-- production the day this applies. Indexing all three now buys nothing and
-- costs a write on every job, session and lease claim in the system. Add
-- partial indexes (`where correlation_id is not null`, as 0025 does for
-- `share_token`) when there is a reader.
--
-- Row Level Security on all three tables is unchanged and still has no
-- policies, exactly as 0001, 0004 and 0014 left them — which in Postgres
-- denies every role except the table owner and BYPASSRLS. A column grants no
-- access, and `test_no_migration_creates_any_policy_at_all` is the doctrine.
--
-- HOW THIS IS APPLIED: by the migration runner,
-- `python -m flashml_cloud_api.migrate`, in filename order, one transaction
-- per file. There are TWO databases, dev (auto-migrated on merge to
-- `develop`) and production (gated behind a manual workflow). Do not apply it
-- by hand to either.
--
-- Idempotent: `add column if not exists` throughout, safe to re-run.
--
-- Do not edit this file after it has been applied anywhere: the runner
-- checksums it, and an edit reads as drift and blocks every later migration.

alter table public.jobs
    add column if not exists correlation_id uuid;

alter table public.sandbox_sessions
    add column if not exists correlation_id uuid;

alter table public.attempts
    add column if not exists correlation_id uuid;

comment on column public.jobs.correlation_id is
    'The thread this submission started, or NULL. Minted ONCE at the edge by '
    'observability.new_correlation_id and never by a SQL default: a default '
    'would manufacture a thread for every row nobody supplied one for. NULL '
    'means "no correlation id", which is a fact and never a gap to be filled '
    'in later — pre-migration rows stay NULL for ever and that is correct.';

comment on column public.sandbox_sessions.correlation_id is
    'The thread this session belongs to, or NULL. Inherited from the training '
    'job when that job carries one; minted by sandbox_orchestrator.start_session '
    'only when nothing upstream does, because a sandbox session is itself one '
    'of the three edges a user initiates. This is the column D-4 is really '
    'about: deep hibernation destroys the instance, so the id that names the '
    'work either lives here or does not survive the pause.';

comment on column public.attempts.correlation_id is
    'The thread this lease belongs to, or NULL. INHERITED, never minted: '
    'record_attempt copies it from public.jobs for the job this attempt is an '
    'attempt of, or through job_rounds.coordinator_job_id for a federated '
    'round whose coordinator job id is not a row in public.jobs. A miss is '
    'NULL — attempts.job_id has no foreign key precisely because it may name '
    'a job this database has never heard of.';
