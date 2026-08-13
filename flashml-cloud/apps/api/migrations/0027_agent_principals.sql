-- 0027_agent_principals.sql
--
-- AG-6: AN AGENT ACTS AS ITSELF, NOT AS THE HUMAN WHO STARTED IT.
--
-- Until now an agent loop (`agent_loop.py`, AG-2) or a wallet-gated submit
-- (`agent_wallet.py`, AG-3) runs under whatever credential the calling
-- process already held — in practice the human's own Supabase JWT, borrowed
-- for the duration of the run. Borrowing is total: an agent holding it can
-- read every pool the human can read, submit into every pool the human can
-- submit into, and spend against the human's full balance, because the
-- token carries no distinction between "the human, at a keyboard" and "a
-- loop the human started an hour ago and is not watching." AG-3's allowance
-- is a spend ceiling layered ON TOP of that borrowed JWT; it narrows what an
-- agent may DO, not who it is claimed to BE. This migration is the other
-- half: an identity of its own, scoped and revocable independently of the
-- human's sign-in.
--
-- THE MACHINE-TOKEN MACHINERY IS THE TEMPLATE, DELIBERATELY REUSED RATHER
-- THAN REINVENTED. `auth.new_machine_token` / `auth.hash_machine_token` mint
-- and hash an agent token exactly as they do a machine token: `fmk_` prefix,
-- 32 bytes of `secrets.token_urlsafe` entropy, sha256 hex digest stored, raw
-- value returned to the caller exactly once and never persisted. There is no
-- reason a bearer secret an agent presents on every call should be weaker
-- than one a volunteer's laptop presents on every call. `sandbox_identity.py`
-- already trusted this same machinery with a self-authorising,
-- no-human-in-the-loop mint once before this one (an FC sandbox worker); this
-- is the second time, not the first.
--
-- THREE SCOPES, AND EACH IS A DIFFERENT BLAST RADIUS.
--
--   'read'   — see what the owner can see. No write of any kind.
--   'submit' — submit work into ONE named pool (`pool_id`), and only that
--              pool. Not "every pool the owner belongs to": the row names
--              the single pool this token may act into, so a leaked token's
--              damage is contained to one team's queue rather than the
--              owner's entire footprint. Requires `pool_id`.
--   'spend'  — authorise spend up to `allowance_zc` millicredits, AG-3's own
--              ceiling, now carried on the credential itself rather than
--              threaded through by whichever caller happens to build the
--              approver. Requires `allowance_zc > 0`; `0` is the value that
--              MEANS "may not spend at all" and is also the column's
--              default, so a principal is never one forgotten argument away
--              from an unbounded wallet.
--
-- A principal may hold any subset, including neither write scope — a
-- read-only monitoring agent never needs `submit` or `spend` — and the
-- ceiling is structural rather than a convention a caller has to remember:
-- `submit` without a named pool, or `spend` without a positive allowance, is
-- not a row this table can hold at all (see the two CHECK constraints below).
--
-- REVOCATION IS TOTAL, THE SAME DISCIPLINE `revoke_sandbox_machine` STATES
-- FOR A MACHINE ROW — AND STRICTER. Two things must stop being true,
-- together, in the same statement: the token must stop authenticating
-- (`status = 'revoked'`, which `authenticate_agent_token` refuses on sight),
-- AND the credential material itself is destroyed (`token_hash` /
-- `token_prefix` set to null) rather than merely marked dead. The closing
-- CHECK constraint below makes that coupling a property of the ROW, not of
-- application discipline: `token_hash` is non-null if and only if
-- `status = 'active'`. A future bug that flips `status` without clearing the
-- hash, or clears the hash without flipping `status`, cannot be written —
-- not "will be caught by a test", cannot be written. This is stricter than
-- `public.machines`, which only flips `status` and leaves `token_hash` in
-- place (`authenticate_machine`'s own `status = 'active'` filter is the sole
-- guard there); a credential with three independently-gated blast radii gets
-- the belt as well as the braces.
--
-- OWNER-SCOPED, END TO END, THE SAME PERVASIVE DISCIPLINE AS EVERY OTHER
-- OWNED ROW IN THIS SCHEMA (`machines`, `cli_credentials`, `pools`): every
-- read and write the API layer performs against this table folds `owner_id`
-- into the query itself, never as a filter applied afterwards. A stranger
-- who guesses a principal id gets exactly the same nothing a stranger who
-- guesses a machine id gets.
--
-- `pool_id` REFERENCES `public.pools(id)` WITH NO `ON DELETE` CLAUSE
-- (defaults to `NO ACTION`), deliberately. Nothing in this codebase deletes a
-- pool today, so this is not yet a live decision — but the safe default is
-- the one that fails loud: a future pool-delete path that tries to remove a
-- pool a live `submit` principal still names would be refused by the
-- database rather than silently orphaning (`SET NULL`) a row the CHECK below
-- would then also reject, or silently destroying (`CASCADE`) a credential
-- that a `read`/`spend`-only principal might still be relying on.
--
-- NO NEW KIND OF DATABASE ACCESS. RLS is enabled with zero policies, exactly
-- as every other table in this schema — see 0001's header for what that
-- means: every role but the table owner and BYPASSRLS is denied outright.
--
-- HOW THIS IS APPLIED: by the migration runner,
-- `python -m flashml_cloud_api.migrate`, which records it in
-- public.schema_migrations. There are TWO databases, dev (auto-migrated on
-- merge to `develop`) and production (gated behind a manual workflow).
--
-- Idempotent: `create table if not exists`, `create index if not exists`,
-- re-runnable `comment on`.
--
-- Do not edit this file after it has been applied anywhere: the runner
-- checksums it, and an edit reads as drift and blocks every later migration.

create table if not exists public.agent_principals (
    id            uuid primary key default gen_random_uuid(),
    owner_id      uuid not null references public.profiles(id) on delete cascade,
    label         text not null
                  check (label = btrim(label))
                  check (char_length(label) >= 1)
                  check (char_length(label) <= 200),
    token_hash    text unique,
    token_prefix  text,
    scopes        text[] not null
                  check (scopes <@ array['read', 'submit', 'spend']::text[])
                  check (cardinality(scopes) > 0),
    pool_id       uuid references public.pools(id),
    allowance_zc  bigint not null default 0 check (allowance_zc >= 0),
    status        text not null default 'active'
                  check (status in ('active', 'revoked')),
    created_at    timestamptz not null default now(),
    revoked_at    timestamptz,

    -- 'submit' without a named pool would be "submit anywhere the owner
    -- can" — the exact unscoped blast radius this table exists to remove.
    check (not (scopes @> array['submit']::text[]) or pool_id is not null),
    -- 'spend' with a zero-or-negative ceiling is a solvency gate against a
    -- bound that can never be satisfied — dead weight that LOOKS like an
    -- authorised spender rather than actually being one. Refused at the
    -- schema, not only at the call site.
    check (not (scopes @> array['spend']::text[]) or allowance_zc > 0),
    -- The coupling `revoke_agent_principal` exists to guarantee, restated as
    -- an invariant the table itself enforces: active means a live credential
    -- and no revocation timestamp; revoked means no credential material left
    -- to steal, and a timestamp saying when it stopped.
    check (
        (status = 'active'
         and token_hash is not null and token_prefix is not null
         and revoked_at is null)
        or
        (status = 'revoked'
         and token_hash is null and token_prefix is null
         and revoked_at is not null)
    )
);

comment on table public.agent_principals is
    'A scoped, revocable credential an AGENT presents, minted independently '
    'of the owning human''s Supabase sign-in (AG-6). token_hash is a sha256 '
    'hex digest of an fmk_ token, minted the same way as a machine token '
    '(auth.new_machine_token/hash_machine_token); the raw value is returned '
    'to the caller exactly once, at creation, and never stored. scopes is a '
    'non-empty subset of read/submit/spend, each independently gated: '
    '''submit'' names the one pool_id it may act into, ''spend'' names the '
    'allowance_zc ceiling it may authorise against. Revoking clears '
    'token_hash and token_prefix as well as flipping status — the closing '
    'CHECK constraint makes that coupling a property of the row, not of '
    'caller discipline.';

comment on column public.agent_principals.scopes is
    'Non-empty subset of {read, submit, spend}, checked against exactly '
    'those three values at the database level so a bug in agent_identity.py '
    'cannot write a fourth. read: see what the owner can see, and nothing '
    'else. submit: submit work into pool_id, and only that pool — never '
    '"every pool the owner belongs to". spend: authorise spend up to '
    'allowance_zc millicredits, the same ceiling AG-3 already gates a '
    'human''s own submit with, now carried on the credential itself.';

comment on column public.agent_principals.pool_id is
    'Required when scopes contains ''submit'' (enforced below). The one pool '
    'this token may submit work into — not a default, a restriction: a '
    'token scoped to one pool cannot be replayed against another, even one '
    'the same owner administers.';

comment on column public.agent_principals.allowance_zc is
    'Millicredits this principal may authorise, total, while scopes contains '
    '''spend''. 0 means "may not spend at all", which is also the default '
    'and the value every non-spending principal keeps forever — never a '
    'hidden unbounded permission waiting on a caller to forget a check.';

comment on column public.agent_principals.token_hash is
    'sha256 hex digest of the raw fmk_ token, or NULL once revoked. Never '
    'the raw token itself: a leak of this column must not be replayable as '
    'a live credential, the same argument public.machines.token_hash '
    'makes. Unlike public.machines, this column is also actively cleared on '
    'revoke rather than left in place behind a status flag alone.';

alter table public.agent_principals enable row level security;

create index if not exists agent_principals_owner_id_idx
    on public.agent_principals (owner_id);
