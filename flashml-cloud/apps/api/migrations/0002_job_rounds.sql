-- 0002_job_rounds.sql
-- FlashML Cloud: per-round history for federated-averaging jobs.
--
-- One table in `public`: job_rounds. Row Level Security is enabled and
-- DELIBERATELY has no policies at all — exactly as in 0001_initial.sql. In
-- Postgres, enabling RLS on a table with zero policies denies all access to
-- every role except the table owner and roles with BYPASSRLS (the Supabase
-- `service_role` key this API uses). The database is API-only: a browser
-- holding a valid Supabase JWT (as `anon` or `authenticated`) must not be
-- able to read Postgres directly. Do not add any policy naming either of
-- those roles here; that would silently reopen direct database access and
-- defeat the whole design. Round history is exposed only through
-- `GET /v1alpha1/jobs/{id}/rounds`, which filters on `owner_id` itself.
--
-- Idempotent: safe to re-run.
--
-- Apply with the Supabase `apply_migration` MCP tool, name `job_rounds`,
-- against project `yualksqjjvlfscbbsygq` ONLY. Never apply to any other
-- Supabase project.

-- ---------------------------------------------------------------------------
-- job_rounds: one row per COMPLETED federated-averaging round.
--
-- Written by the driver's `on_round` callback, which fires only after a
-- round has met quorum, been reduced, and had its aggregated weights
-- committed — so a row here means the round is durable, not merely started.
-- That is what makes this table the resume log as well as the UI's history:
-- `(round, coordinator_job_id)` pairs feed straight into the driver's
-- `resume_state`, which probes each round's weights artifact newest-first.
--
-- `job_id` is the parent federated job in `public.jobs` (one row, one run).
-- `coordinator_job_id` is the *round's* job on the coordinator — a
-- federated run submits one coordinator job per round, so without this the
-- rounds could not be traced back to the tasks that produced them, and the
-- resume path would have nothing to probe.
-- ---------------------------------------------------------------------------
create table if not exists public.job_rounds (
    id                  uuid primary key default gen_random_uuid(),
    job_id              text not null references public.jobs(id) on delete cascade,
    round               integer not null check (round >= 0),
    participants        integer not null check (participants >= 0),
    mean_loss           double precision,
    -- Which node ids contributed to this round's average. jsonb (an array of
    -- strings) rather than text[]: it is read straight back out as JSON by
    -- the API, and node ids come from the coordinator's task view, which is
    -- a wire format that may grow richer per-contributor detail later.
    contributors        jsonb not null default '[]'::jsonb,
    coordinator_job_id  text,
    recorded_at         timestamptz not null default now(),
    -- A round is aggregated exactly once, and `on_round` is the only writer.
    -- Without this, a driver restarted onto a run whose rounds were already
    -- recorded would append a second history for the same rounds and the UI
    -- would show a loss curve that doubles back on itself.
    unique (job_id, round)
);

comment on table public.job_rounds is
    'One row per completed federated-averaging round, written by the '
    'in-API driver''s on_round callback after the round met quorum and its '
    'aggregated weights were committed. job_id is the parent federated job; '
    'coordinator_job_id is that round''s job on the coordinator. Read only '
    'via GET /v1alpha1/jobs/{id}/rounds, owner-scoped.';

alter table public.job_rounds enable row level security;

create index if not exists job_rounds_job_id_round_idx
    on public.job_rounds (job_id, round);
