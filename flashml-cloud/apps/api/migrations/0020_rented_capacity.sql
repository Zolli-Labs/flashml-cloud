-- 0020_rented_capacity.sql
--
-- One row per machine this control plane rented. The row is opened BEFORE
-- the venue is asked for anything, so a crash between "we decided to spend
-- money" and "the venue answered" leaves evidence rather than an orphan
-- that bills forever.
--
-- REQUESTED -> ACTIVE -> RELEASED, or -> FAILED from anywhere.
-- `provider_handle` is null only in REQUESTED; the reconciler's job is to
-- make that window short.

create table if not exists public.rented_capacity (
    id                uuid primary key default gen_random_uuid(),
    venue_id          text        not null,
    state             text        not null default 'REQUESTED',
    owner_id          uuid        not null,
    pool_id           uuid        not null,
    job_id            text        not null,
    provider_handle   text,
    machine_id        uuid,
    gpu_count         integer     not null default 1,
    usd_per_hour      numeric(10, 4),
    created_at        timestamptz not null default now(),
    acquired_at       timestamptz,
    released_at       timestamptz,
    failure_code      text,
    failure_detail    text,
    constraint rented_capacity_state_check
        check (state in ('REQUESTED', 'ACTIVE', 'RELEASED', 'FAILED'))
);

-- The reconciler's query: everything still costing money.
create index if not exists rented_capacity_unreleased_idx
    on public.rented_capacity (state)
 where state in ('REQUESTED', 'ACTIVE');

create index if not exists rented_capacity_owner_idx
    on public.rented_capacity (owner_id, created_at desc);
