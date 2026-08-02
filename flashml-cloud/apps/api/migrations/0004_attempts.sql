-- 0004_attempts.sql
--
-- The attempt ledger: the API's durable mapping from a lease to the work it
-- covers.
--
-- WHY THIS EXISTS. `contributions` is keyed on (machine_id, job_id,
-- task_id), but the hop where the API learns work was ACCEPTED --
-- POST /v1alpha1/attempts/{lease_id}/complete -- carries neither job_id nor
-- task_id. The request body is {output_sha256}; the response body is
-- {accepted: bool}. The only place those ids appear is the Lease returned by
-- the CLAIM one hop earlier. So the API must remember the claim to be able
-- to credit the completion.
--
-- Until this landed, `db.record_contributions` had exactly one caller --
-- inside fedavg.on_round -- so only FEDERATED rounds paid anybody. Sweeps and
-- command jobs, which is what donated laptops are actually good at, credited
-- nobody at all.
--
-- APPLIED TO SUPABASE by hand, like 0003, against project
-- yualksqjjvlfscbbsygq ONLY. There is no migration runner in this service.
--
-- Both writes against this table are best-effort in the API. It is an
-- accounting record, never a precondition for scheduling work.

create table if not exists public.attempts (
    -- The coordinator's lease id. Primary key rather than a surrogate: a
    -- lease is claimed exactly once, so this is the natural key, and it
    -- makes a duplicated claim forward a no-op instead of a second row.
    lease_id    text primary key,
    -- The machine the CLAIMING token resolved to. A machine_id, not a
    -- node_id: the API had already resolved it, and a foreign key beats a
    -- string that would have to be re-resolved at credit time.
    machine_id  uuid not null references public.machines(id) on delete cascade,
    job_id      text not null,
    task_id     text not null,
    claimed_at  timestamptz not null default now(),
    -- Set when the credit is written, by the same UPDATE that reads the row.
    -- NOT the credit itself -- `contributions` remains the ledger. This makes
    -- a completion processed twice VISIBLY a repeat (the second UPDATE
    -- matches no row) rather than something silently absorbed downstream by
    -- the unique index from 0003.
    accepted_at timestamptz
);

comment on table public.attempts is
    'Durable lease -> (job, task) mapping, written when a machine claims a '
    'lease and consumed when the coordinator reports that attempt ACCEPTED. '
    'Exists because the complete hop carries no job/task id. Not yet a full '
    'attempt history: failed and expired attempts leave no mark (see the '
    'design spec, section 4.2).';

alter table public.attempts enable row level security;

-- Credit lookup is by primary key, so it needs no index. This one serves the
-- "what has this machine worked on" query and the cascade delete.
create index if not exists attempts_machine_id_idx on public.attempts (machine_id);
