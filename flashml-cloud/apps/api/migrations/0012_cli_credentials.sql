-- 0012_cli_credentials.sql
--
-- A credential a developer's own program can present.
--
-- WHY THIS EXISTS. Until now the API knew exactly two kinds of caller: a
-- Supabase JWT (a browser) and an `fmk_` machine token (an enrolled worker).
-- Every job-author route is tagged `browser`, so the only way to submit a job
-- was to be a person clicking in a console. A CLI — and the MCP server built
-- on it — has nothing to present. This table is that third kind.
--
-- IT IS NOT A MACHINE. `public.machines` rows are workers: they have a
-- node_id, they claim leases, they are placed on. A cli_credential does none
-- of that. It acts as its owner, with exactly their access — an un-admitted
-- account's CLI token gets the same 403 their browser does — and it is
-- revocable on its own without disturbing their browser session.
--
-- ONE CODE TABLE, NOT TWO. Both flows mint a short user_code a human types at
-- /activate, and that page cannot tell which flow a code belongs to unless
-- user_code is unique across both. Postgres will not enforce uniqueness across
-- two tables, so `kind` discriminates within one. The default keeps every
-- existing row and every existing insert meaning exactly what it meant before.
--
-- RELAXING node_id IS GUARDED. A CLI code has no node, so the NOT NULL had to
-- go; the check constraint below puts it straight back for kind='machine', so
-- the volunteer enrolment path cannot be weakened by this migration.

create table if not exists public.cli_credentials (
    id            uuid primary key default gen_random_uuid(),
    owner_id      uuid not null references public.profiles(id) on delete cascade,
    label         text,
    token_hash    text not null unique,
    token_prefix  text not null,
    status        text not null default 'active'
                  check (status in ('active', 'revoked')),
    last_used_at  timestamptz,
    created_at    timestamptz not null default now(),
    revoked_at    timestamptz
);

comment on table public.cli_credentials is
    'A developer CLI credential. token_hash is a sha256 hex digest of an '
    '`fmu_` token; the raw token is returned to the CLI exactly once at '
    'redemption and never stored. Presenting it authenticates AS THE OWNER '
    '— it grants no access the owner does not already have. status is '
    'constrained at the database level so an invalid state cannot be '
    'written even by a bug in the API.';

comment on column public.cli_credentials.last_used_at is
    'Written at most once per minute per credential. A timestamp update on '
    'every authenticated request would put a write in front of every read.';

alter table public.cli_credentials enable row level security;

create index if not exists cli_credentials_owner_idx
    on public.cli_credentials (owner_id);

alter table public.device_codes
    add column if not exists kind text not null default 'machine';

alter table public.device_codes
    drop constraint if exists device_codes_kind_check;
alter table public.device_codes
    add constraint device_codes_kind_check check (kind in ('machine', 'cli'));

alter table public.device_codes
    add column if not exists credential_id uuid
        references public.cli_credentials(id) on delete set null;

alter table public.device_codes alter column node_id drop not null;

alter table public.device_codes
    drop constraint if exists device_codes_machine_needs_node;
alter table public.device_codes
    add constraint device_codes_machine_needs_node
        check (kind <> 'machine' or node_id is not null);

comment on column public.device_codes.kind is
    'Which flow this code belongs to. A machine code redeems for an fmk_ '
    'token bound to a machines row; a cli code redeems for an fmu_ token '
    'bound to a cli_credentials row. One table so user_code is unique '
    'across both — /activate has only the typed code to go on.';
