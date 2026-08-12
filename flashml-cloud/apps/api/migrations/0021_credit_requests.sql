-- 0021_credit_requests.sql
-- Testing-credit requests are reviewable history, not a mutable allowance.
-- A person may have one pending request at a time, while every decided row
-- remains available for both the requester and the admin queue.

-- Existing profiles are deliberately ineligible. PostgreSQL materializes the
-- false default for rows already present when this column is added; only after
-- that boundary is established does the default change for profiles created
-- after this migration. This is what makes the 10 ZC grant prospective even
-- for an older account that never opened the wallet under the previous policy.
alter table public.profiles
    add column if not exists starter_grant_eligible boolean not null default false;

alter table public.profiles
    alter column starter_grant_eligible set default true;

comment on column public.profiles.starter_grant_eligible is
    'False for profiles that existed when testing-credit policy launched; true '
    'by default only for profiles created afterwards. Guards the prospective '
    '10 ZC starter grant without rewriting historical balances.';

comment on column public.credit_accounts.balance_zc is
    'Millicredits, integer only, never negative (enforced). 1 ZC = 1000 of '
    'these and has a fixed testing value of 1 USD. Credits remain closed-loop: '
    'they cannot be bought, sold, withdrawn, or redeemed for cash.';

comment on column public.credit_entries.reason is
    'What moved the credits. escrow_hold: spendable -> escrow on CLAIM. '
    'spent_accepted_work / earned_accepted_work: settlement for accepted work. '
    'escrow_release and escrow_refund return held credits. grant: one-time '
    'starter mint for an eligible new profile. adjustment: reviewed testing '
    'credit approval or a compensating ledger entry.';

create table if not exists public.credit_requests (
    id            uuid primary key default gen_random_uuid(),
    user_id       uuid not null
                  references public.profiles(id) on delete cascade,
    requested_zc  bigint not null check (requested_zc > 0),
    approved_zc   bigint check (approved_zc > 0),
    purpose       text not null
                  check (purpose = btrim(purpose))
                  check (char_length(btrim(purpose)) >= 1)
                  check (char_length(btrim(purpose)) <= 2000),
    status        text not null default 'pending'
                  check (status in ('pending', 'approved', 'declined')),
    requested_at  timestamptz not null default now(),
    decided_at    timestamptz,
    decided_by    uuid
                  references public.profiles(id) on delete set null,

    check (
        (status = 'pending'
         and approved_zc is null
         and decided_at is null
         and decided_by is null)
        or
        (status = 'approved'
         and approved_zc is not null
         and decided_at is not null)
        or
        (status = 'declined'
         and approved_zc is null
         and decided_at is not null)
    )
);

comment on table public.credit_requests is
    'Append-preserving requests for testing credits. One request per user may '
    'be pending; approval mints one adjustment ledger entry keyed by request id.';

comment on column public.credit_requests.requested_zc is
    'Requested millicredits. Positive bigint; the reviewer may approve a '
    'different positive amount without changing the original request.';

alter table public.credit_requests enable row level security;

create index if not exists credit_requests_user_history_idx
    on public.credit_requests (user_id, requested_at desc);

create index if not exists credit_requests_admin_queue_idx
    on public.credit_requests (status, requested_at);

create index if not exists credit_requests_decided_by_idx
    on public.credit_requests (decided_by);

create unique index if not exists credit_requests_one_pending_per_user_idx
    on public.credit_requests (user_id)
    where status = 'pending';
