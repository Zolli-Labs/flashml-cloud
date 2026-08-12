-- 0015_attempt_outcomes.sql
--
-- Give an attempt a TERMINAL OUTCOME. Three columns on public.attempts, no
-- new table.
--
-- WHY THIS EXISTS. 0004 created `attempts` with `claimed_at` and
-- `accepted_at` and conceded the gap in its own table comment: "failed and
-- expired attempts leave no mark". The consequence is not a missing nicety,
-- it is a wrong number on the one page whose whole job is to prove that
-- work survives machines dying:
--
--   * A FAILED attempt and an IN-FLIGHT attempt were the same row —
--     `accepted_at is null` — so `tasks_accepted / tasks_attempted` had a
--     denominator that grew with every claim and shrank for nothing. It
--     drifted downward forever and could not recover, which makes it a
--     measure of how much work has been handed out, not of reliability.
--   * `POST /v1alpha1/attempts/{lease_id}/fail` was a pure proxy. The one
--     moment this API is TOLD an attempt failed, it wrote nothing down.
--   * Lease expiry is decided by the coordinator's sweeper, which never
--     informs this API at all, so a machine that simply stopped left an
--     attempt row open for ever.
--   * `metrics.py` therefore returned null for `lost_task_seconds` and
--     `mttr_seconds` — correctly, and it says at length why inventing them
--     would be worse. Those two are what this migration makes computable.
--   * `contributions` holds only ACCEPTED work, so every duration read out
--     of it is biased fast: the runs that timed out or were abandoned are
--     invisible there by construction. A resolved attempt has a duration
--     whether or not it produced anything.
--
-- WHAT `outcome` MEANS. It is the attempt's terminal state, written once:
--
--   accepted  — the coordinator answered `{"accepted": true}` and the credit
--               was taken (`db.claim_attempt_credit`, same statement).
--   failed    — the agent reported the attempt failed and the coordinator
--               accepted that report (`db.record_attempt_failure`, written
--               only AFTER the coordinator call succeeds).
--   expired   — the lease's last known deadline passed with no terminal
--               write (`db.reconcile_expired_attempts`). INFERRED, not
--               observed; see the deadline column below.
--   abandoned — reserved, and deliberately unwritten today. The coordinator
--               distinguishes an abandoned lease (a node that vanished and
--               later came back to claim again) from an expired one, and
--               that distinction is not visible from this side. The value is
--               in the vocabulary so the day it becomes visible it does not
--               need a constraint migration; nothing invents it in the
--               meantime.
--
-- NULL IS ALSO AN ANSWER, and it is the one thing every reader must handle.
-- A null `outcome` means "this attempt has no terminal state on record" —
-- either it is still in flight, or it is one of the rows that existed before
-- this migration and genuinely cannot be classified. Both are UNRESOLVED,
-- and every denominator in `db.metrics_counts_for_owner` excludes them
-- rather than counting them as failures. Counting an unresolved row as a
-- failure is exactly the survivorship bias inverted, and it would make the
-- goodput of a busy account fall while it worked.
--
-- BACKFILL: one direction only, and it is not a guess. A row with
-- `accepted_at` set was accepted — that is what the column has always meant
-- and `claim_attempt_credit` is its only writer — so it becomes
-- `outcome='accepted'` with `resolved_at = accepted_at`, the instant already
-- recorded. A row WITHOUT `accepted_at` is genuinely unknown: it may have
-- failed, expired, or still be running, and nothing written down can tell
-- the three apart. Those rows are left null. Bucketing them would
-- manufacture exactly the number this whole change exists to stop being
-- manufactured.
--
-- `lease_deadline` IS WHAT MAKES EXPIRY DETECTABLE AT ALL. It is the
-- coordinator's own deadline for the lease, copied from the `Lease` body the
-- claim proxy already parses and refreshed from the renewed `Lease` the
-- attempt-heartbeat proxy already forwards. Past that instant the
-- coordinator refuses the heartbeat (410) and rejects the commit, so the
-- attempt is dead by the coordinator's own rule and no legitimate terminal
-- write can follow it. That is what lets a reconciler resolve the row
-- without being told, and why `resolved_at` for an expired attempt is the
-- DEADLINE and not the moment the reconciler noticed: the second is an
-- artefact of when somebody opened a page, the first is when the work
-- actually stopped counting.
--
-- Null `lease_deadline` is honest too — an attempt claimed before this
-- migration, or one whose claim response could not be parsed, has no
-- deadline on record and is therefore never reconciled. It stays
-- unresolved and stays out of every denominator.
--
-- Idempotent: safe to re-run (`add column if not exists`, `drop constraint
-- if exists` + `add constraint`, `create index if not exists`, and a
-- backfill guarded on `outcome is null`).
--
-- Row Level Security on `public.attempts` is unchanged and still has no
-- policies, exactly as 0004 left it. Columns and indexes grant no access, so
-- nothing here reopens direct database access to `anon` or `authenticated`.
--
-- HOW THIS IS APPLIED: by the migration runner,
-- `python -m flashml_cloud_api.migrate`, which records it in
-- public.schema_migrations. There are TWO databases, dev (auto-migrated on
-- merge to `develop`) and production (gated behind a manual workflow).
-- Project refs deliberately not named here: they belong in the gitignored
-- env files and in Render's `sync: false` prompts, nowhere in git. The runner
-- is what keeps the two honest, so do not apply this by hand to either.
--
-- Do not edit this file after it has been applied anywhere: the runner
-- checksums it, and an edit reads as drift and blocks every later migration.

alter table public.attempts
    add column if not exists resolved_at timestamptz;

alter table public.attempts
    add column if not exists outcome text;

alter table public.attempts
    drop constraint if exists attempts_outcome_check;
alter table public.attempts
    add constraint attempts_outcome_check
        check (outcome in ('accepted', 'failed', 'expired', 'abandoned'));

alter table public.attempts
    add column if not exists lease_deadline timestamptz;

comment on column public.attempts.resolved_at is
    'When this attempt reached its terminal state. For accepted and failed '
    'that is when the API observed it; for expired it is the lease deadline '
    'itself — the instant the work stopped counting — never the moment the '
    'reconciler happened to run. NULL means unresolved: in flight, or one '
    'of the pre-0015 rows nothing can classify.';

comment on column public.attempts.outcome is
    'Terminal state of the attempt: accepted / failed / expired / abandoned, '
    'or NULL for unresolved. Written once and never overwritten, except that '
    'an OBSERVED outcome (accepted, failed) may correct an INFERRED expired — '
    'the coordinator answering is stronger evidence than a deadline having '
    'passed. Unresolved rows are excluded from every metrics denominator, '
    'never counted as failures.';

comment on column public.attempts.lease_deadline is
    'The coordinator''s deadline for this lease, from the claim response and '
    'refreshed by each proxied attempt heartbeat. Past it the coordinator '
    'refuses heartbeats and rejects commits, so it is the one instant this '
    'API can use to resolve an attempt nobody ever reported on. NULL means '
    'no deadline was ever recorded, and such a row is never reconciled.';

comment on table public.attempts is
    'Durable lease -> (job, task) mapping with a terminal outcome, written '
    'when a machine claims a lease and resolved when the attempt is '
    'accepted, reported failed, or found past its lease deadline. Exists '
    'because the complete hop carries no job/task id. Since 0015 this IS an '
    'attempt history: an unresolved row means in-flight or pre-0015, never '
    '"failed", and every metric that divides by attempts divides by the '
    'resolved ones.';

-- ---------------------------------------------------------------------------
-- Backfill. Only the direction the ledger already knows.
-- ---------------------------------------------------------------------------
update public.attempts
   set outcome = 'accepted',
       resolved_at = accepted_at
 where accepted_at is not null
   and outcome is null;

-- ---------------------------------------------------------------------------
-- Indexes.
--
-- `attempts_job_task_idx` serves both of the metrics queries this migration
-- makes possible and one that already existed. `metrics_counts_for_owner`
-- joins `attempts` to the account's coordinator jobs on `job_id` — a column
-- that until now carried no index at all, so a page covering a month of work
-- scanned the whole table — and the recovery-interval query pairs a resolved
-- failure with the replacement attempt on the same `(job_id, task_id)`.
-- One index, leading column shared, rather than two.
--
-- `attempts_unresolved_deadline_idx` is PARTIAL, on the reconciler's exact
-- predicate. The set of unresolved attempts is small and bounded by what is
-- actually in flight; the resolved ones are the whole history and must not
-- be in this index. That is what makes the reconciler an indexed no-op on
-- every run after the first, which matters because it runs on a page a
-- console polls.
-- ---------------------------------------------------------------------------
create index if not exists attempts_job_task_idx
    on public.attempts (job_id, task_id);

create index if not exists attempts_unresolved_deadline_idx
    on public.attempts (lease_deadline)
 where resolved_at is null;
