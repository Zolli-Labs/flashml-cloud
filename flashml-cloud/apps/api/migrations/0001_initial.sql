-- 0001_initial.sql
-- FlashML Cloud: initial schema for real accounts (Plan 3 of 7, Task 1).
--
-- Five tables in `public`: profiles, machines, device_codes, jobs,
-- contributions. Row Level Security is enabled on every one of them and
-- DELIBERATELY has no policies at all. In Postgres, enabling RLS on a table
-- with zero policies denies all access to every role except the table
-- owner and roles with BYPASSRLS (the Supabase `service_role` key that this
-- API uses). That is the point: the database is API-only. A browser
-- holding a valid Supabase JWT (as `anon` or `authenticated`) must not be
-- able to read or write Postgres directly — every read and write goes
-- through this API, which authenticates the caller and filters on
-- `owner_id` itself. Do not add any policy granting either of those two
-- roles access here; that would silently reopen direct access and defeat
-- the whole design.
--
-- Idempotent: safe to re-run. Uses `create table if not exists`,
-- `create index if not exists`, and re-runnable `alter table ... enable
-- row level security` / `comment on table` statements.
--
-- Apply with the Supabase `apply_migration` MCP tool, name `initial_schema`,
-- against project `yualksqjjvlfscbbsygq` ONLY. Never apply to any other
-- Supabase project.

create extension if not exists pgcrypto;

-- ---------------------------------------------------------------------------
-- profiles: one row per signed-in user, mirroring auth.users. Created (or
-- upserted) by the API the first time it sees a new Supabase JWT `sub`.
-- ---------------------------------------------------------------------------
create table if not exists public.profiles (
    id            uuid primary key references auth.users(id) on delete cascade,
    display_name  text,
    github_login  text,
    is_host       boolean not null default false,
    is_developer  boolean not null default false,
    created_at    timestamptz not null default now()
);

comment on table public.profiles is
    'One row per signed-in user (mirrors auth.users). is_host = has enrolled '
    'at least one machine; is_developer = has submitted at least one job.';

alter table public.profiles enable row level security;

-- ---------------------------------------------------------------------------
-- machines: a host's enrolled machine. Identified to the coordinator by
-- node_id (unique — a node_id already bound to another machine must be
-- refused at enrolment, or that machine could impersonate one already
-- enrolled). Never stores the raw machine token, only its hash: the API
-- hashes the token before writing (see hash_machine_token, sha256 hex), so
-- the database never sees plaintext and a DB leak cannot be replayed as a
-- live credential.
-- ---------------------------------------------------------------------------
create table if not exists public.machines (
    id            uuid primary key default gen_random_uuid(),
    owner_id      uuid not null references public.profiles(id) on delete cascade,
    node_id       text not null unique,
    name          text,
    platform      text,
    capabilities  jsonb not null default '{}'::jsonb,
    token_hash    text,
    token_prefix  text,
    status        text not null default 'pending'
                  check (status in ('pending', 'active', 'revoked')),
    last_seen_at  timestamptz,
    created_at    timestamptz not null default now(),
    revoked_at    timestamptz
);

comment on table public.machines is
    'A host''s enrolled machine. token_hash is a sha256 hex digest of the '
    'machine token; the raw token is returned to the host exactly once at '
    'redemption time and never stored. status is constrained at the '
    'database level so an invalid state cannot be written even by a bug '
    'in the API.';

alter table public.machines enable row level security;

create index if not exists machines_owner_id_idx on public.machines (owner_id);
create index if not exists machines_last_seen_at_idx on public.machines (last_seen_at);

-- ---------------------------------------------------------------------------
-- device_codes: the device-code enrolment flow (start / approve / redeem).
-- user_code is the short, human-typable code a person enters in the
-- browser to approve a machine that requested enrolment.
-- ---------------------------------------------------------------------------
create table if not exists public.device_codes (
    device_code  text primary key,
    user_code    text not null unique,
    node_id      text not null,
    hostname     text,
    platform     text,
    machine_id   uuid references public.machines(id) on delete set null,
    approved_by  uuid references public.profiles(id) on delete set null,
    expires_at   timestamptz not null,
    consumed_at  timestamptz
);

comment on table public.device_codes is
    'Device-code enrolment flow: a machine starts a code, a signed-in user '
    'approves it via user_code, and the machine redeems device_code for a '
    'token exactly once. machine_id/approved_by are null until approval; '
    'consumed_at is null until redemption.';

alter table public.device_codes enable row level security;

create index if not exists device_codes_node_id_idx on public.device_codes (node_id);
create index if not exists device_codes_expires_at_idx on public.device_codes (expires_at);

-- ---------------------------------------------------------------------------
-- jobs: a job submitted by a developer, owned by exactly one profile.
-- ---------------------------------------------------------------------------
create table if not exists public.jobs (
    id           text primary key,
    owner_id     uuid not null references public.profiles(id) on delete cascade,
    name         text,
    source       jsonb,
    spec         jsonb,
    status       text not null default 'pending',
    created_at   timestamptz not null default now(),
    finished_at  timestamptz
);

comment on table public.jobs is
    'A job submitted by a developer. owner_id is set from the verified '
    'JWT sub, never from the request body. GET /jobs returns only the '
    'caller''s own jobs; fetching another user''s job by id is 404.';

alter table public.jobs enable row level security;

create index if not exists jobs_owner_id_idx on public.jobs (owner_id);
create index if not exists jobs_status_idx on public.jobs (status);

-- ---------------------------------------------------------------------------
-- contributions: a machine's accepted (not merely attempted) piece of work
-- on a job, for credit/metrics accounting.
-- ---------------------------------------------------------------------------
create table if not exists public.contributions (
    id           uuid primary key default gen_random_uuid(),
    machine_id   uuid not null references public.machines(id) on delete cascade,
    job_id       text not null,
    task_id      text,
    accepted_at  timestamptz not null default now(),
    duration_s   numeric
);

comment on table public.contributions is
    'A machine''s accepted contribution to a job (distinct from merely '
    'attempted work — see workspace hard rule on attempted vs accepted). '
    'Used for host credit and metrics accounting.';

alter table public.contributions enable row level security;

create index if not exists contributions_machine_id_idx on public.contributions (machine_id);
create index if not exists contributions_job_id_idx on public.contributions (job_id);
