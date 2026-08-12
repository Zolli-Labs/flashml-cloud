-- 0018_marketplace.sql
--
-- The Zolli marketplace's persistent core: a double-entry credit ledger, an
-- order book per capability class, and the priced entitlements that pair the
-- two. Six tables in `public`.
--
-- WHY THIS EXISTS. Until now the only record of who did what was
-- `public.contributions` — accepted work, counted, never spent. That table is
-- forbidden a spendable balance in its own module's words ("It must never
-- grow a `balance`, a `remaining`, or a `credits`"), and the rule stands: a
-- measurement that becomes a promise is a promise this product has not
-- designed and cannot honour. So credits live HERE, in a ledger that
-- REFERENCES accepted work and never writes it. Nothing in this migration
-- adds a column to `contributions`, and nothing in `marketplace.py` writes
-- one; `test_marketplace.py::test_no_code_path_writes_contributions` is what
-- keeps that true after everybody who read this has moved on.
--
-- THE THREE THINGS A READER MUST TAKE FROM THIS FILE.
--
-- 1. **A MATCH IS A PRICED ENTITLEMENT, NOT AN ASSIGNMENT.**
--    `flashruntime/scheduler/__init__.py:628`: "FlashML is a PULL system.
--    Nodes claim; the coordinator never picks a node for a task." There is no
--    push path in the runtime, so a row in `public.matches` cannot cause a
--    host to run anything. It makes a task ELIGIBLE for a host that would
--    otherwise not be eligible, at an agreed price, and the host still has to
--    ask. That is why `state` starts at 'granted' and why 'granted' is a
--    state of its own rather than a flag on 'claimed': a buyer reading a
--    match as a reservation is the single most likely misunderstanding this
--    schema can cause, and the data has to contradict it. A matched host that
--    never claims produces no work and no charge, and the terminal state for
--    that is 'expired' — an ordinary outcome, not an error.
--
-- 2. **ESCROW IS HELD ON `claimed`, NEVER ON `granted`.** Holding at grant
--    would let a host who never shows up lock a buyer's balance indefinitely,
--    and since a grant costs the host nothing, that is a denial of service
--    with no attacker required. The hold is keyed on the LEASE, not on the
--    match, because a match may cover many tasks and each one is claimed
--    separately: holding a match's whole estimate on its first claim
--    over-collateralises everything that has not been asked for yet.
--
-- 3. **SETTLEMENT IS ON ACCEPTED WORK, NOT ELAPSED TIME.** Hard rule 4. A
--    buyer is never charged for an attempt that produced no accepted output;
--    a host whose machine dies mid-task earns nothing and the task requeues.
--    This is only expressible because `attempts.outcome` (migration 0015)
--    finally distinguishes a resolved attempt from an in-flight one — before
--    it, "did this attempt produce anything?" had no answer in Postgres and
--    every billing rule would have had to guess.
--
-- INTEGERS, EVERYWHERE, WITH NO EXCEPTIONS. Every money-shaped column here is
-- `bigint` millicredits: 1 Zolli credit (1 ZC = one hour of the reference
-- class `gpu-24gb`, design §2.5.1) is 1000 of them. There is no `numeric`,
-- no `double precision`, and no `real` in this file, and adding one is how a
-- ledger silently stops balancing — a half-millicredit rounding error is
-- invisible per row and unfixable in aggregate, because by the time the sum
-- disagrees there is no way to tell which row is wrong. `contributions.
-- duration_s` is `numeric` and stays that way: it measures seconds, not money.
--
-- ZERO IS A LEGAL PRICE (M13). A host may ask 0 and is then labelled
-- *donated* rather than priced. There is deliberately NO positive-price
-- constraint anywhere below, and the columns that could carry a price are
-- nullable exactly where "there is no price" and "the price is zero" are
-- different facts — see `price_observations`, where confusing the two would
-- draw a chart line at the floor for a class nobody is selling in.
--
-- RLS, AND WHAT "OWNER-SCOPED" MEANS HERE. Row Level Security is enabled on
-- all six tables and DELIBERATELY has no policies at all, exactly as in 0001
-- and 0014. In Postgres, RLS with zero policies denies every role except the
-- table owner and BYPASSRLS (the `service_role` key this API uses), which is
-- the whole design: the database is API-only and the owner filter is folded
-- into the query itself, never applied afterwards in Python. Do not add a
-- policy naming `anon` or `authenticated` here;
-- `test_schema.py::test_no_migration_creates_any_policy_at_all` forbids any
-- policy at all, and that test is the doctrine.
--
-- EVERY FOREIGN KEY TO `profiles` CASCADES, and that includes the ledger.
-- 0014 states the reason and it is not about convenience: `profiles.id`
-- references `auth.users(id) on delete cascade`, so a `restrict` anywhere
-- down this chain would make SUPABASE'S OWN user-delete fail on our table —
-- an outage in somebody else's product caused by ours. The honest consequence
-- is written down rather than hidden: deleting an account deletes its
-- accounts and its entries with it, so the conservation invariant
-- (`sum(delta_zc) over every account == total granted`) is a statement about
-- LIVE accounts. It is not a statement about all credits ever minted, and no
-- reader should take it as one.
--
-- HOW THIS IS APPLIED: by the migration runner,
-- `python -m flashml_cloud_api.migrate`, which records it in
-- public.schema_migrations. There are TWO databases, dev (auto-migrated on
-- merge to `develop`) and production (gated behind a manual workflow).
-- Project refs deliberately not named here: they belong in the gitignored env
-- files and in Render's `sync: false` prompts, nowhere in git. The runner is
-- what keeps the two honest, so do not apply this by hand to either.
--
-- Idempotent: safe to re-run (`create table if not exists`, `create index if
-- not exists`, re-runnable `alter table ... enable row level security` and
-- `comment on ...`).
--
-- Do not edit this file after it has been applied anywhere: the runner
-- checksums it, and an edit reads as drift and blocks every later migration.

-- ---------------------------------------------------------------------------
-- credit_accounts: TWO rows per person, and the second one is the whole
-- reason double entry works here.
--
-- The design sketch (§5) keyed this table by `user_id`. It cannot be, and the
-- reason is arithmetic rather than taste: escrow moves credits OUT of a
-- spendable balance without paying anybody yet, so the hold needs a
-- counterparty account or the invariant "every debit has a matching credit"
-- is unenforceable — there would be a debit with nothing on the other side of
-- it, and the ledger would only balance if you agreed in advance not to look
-- at escrow. One row per (owner, kind) makes the hold an ordinary transfer
-- between two accounts, which is what it actually is: the money has left the
-- balance and has not been earned by anyone.
--
--   spendable — what a person may bid with. This is the number the console
--               calls "balance".
--   escrow    — what is currently held against claims in flight. Not
--               spendable, not earned, and NOT the host's yet.
--
-- `balance_zc >= 0` is a CHECK rather than a convention because an overdraft
-- must be impossible even given a bug in `marketplace.py`, the same
-- discipline `machines.status` (0001) and `sandbox_sessions.state` (0014)
-- apply to their own invalid states. It is also what makes "insufficient
-- credits" a database answer rather than a race between a balance read and a
-- balance write.
-- ---------------------------------------------------------------------------
create table if not exists public.credit_accounts (
    id         uuid primary key default gen_random_uuid(),

    -- `public.profiles`, not `auth.users`, for the reason 0014 sets out at
    -- length: every other table in this schema references profiles, and a
    -- foreign key pointing INTO Supabase's schema would make their user
    -- delete fail on our table.
    owner_id   uuid not null references public.profiles(id) on delete cascade,

    kind       text not null check (kind in ('spendable', 'escrow')),

    -- Millicredits. Never negative, never a float.
    balance_zc bigint not null default 0 check (balance_zc >= 0),

    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),

    -- One spendable and one escrow account per person, and never a second of
    -- either. Two spendable accounts for one owner would make "the balance"
    -- ambiguous, and every function that reads it would pick whichever the
    -- planner happened to return first.
    unique (owner_id, kind)
);

comment on table public.credit_accounts is
    'Two accounts per person: `spendable` (what they may bid with) and '
    '`escrow` (held against claims in flight, earned by nobody yet). Two '
    'rather than one because escrow is a transfer, and a transfer needs a '
    'counterparty — with a single account the hold would be a debit with '
    'nothing on the other side and the ledger could not be checked. '
    'balance_zc is millicredits and is maintained in the same transaction as '
    'the entries that move it; public.credit_entries is the truth and '
    'marketplace.verify_ledger is what proves the two agree.';

comment on column public.credit_accounts.balance_zc is
    'Millicredits, integer only, never negative (enforced). 1 ZC = 1000 of '
    'these = one hour of the reference class gpu-24gb. Credits are not money '
    'and have no exit: they cannot be bought, sold, withdrawn or converted, '
    'which is what bounds the exposure of settling on unverified work (M14).';

alter table public.credit_accounts enable row level security;

create index if not exists credit_accounts_owner_idx
    on public.credit_accounts (owner_id);

-- ---------------------------------------------------------------------------
-- credit_entries: APPEND ONLY. Nothing updates or deletes a row here, ever.
--
-- One row per leg. An ordinary movement writes TWO of them with the same
-- (ref_type, ref_id) and deltas that sum to zero; a `grant` writes ONE,
-- because a grant mints credits that did not exist and is the single
-- documented exception to conservation.
--
-- THE UNIQUE INDEX IS THE IDEMPOTENCY, and it is an EXPRESSION index rather
-- than the plain `unique (reason, ref_type, ref_id, account_id)` the design
-- sketch wrote down. That plain constraint has the exact hole migration 0003
-- had to fix on `contributions`: `ref_type` and `ref_id` are NULLABLE, and in
-- a unique index NULLs never collide — Postgres treats two nulls as distinct,
-- so an entry with no ref could be written an unbounded number of times for
-- one account and the index would permit every one of them. That is not a
-- hypothetical here: `grant` is precisely the reason with nothing natural to
-- reference, and it is also the one reason that must be impossible to write
-- twice. Folding null to the empty string gives those rows a single shared
-- key, which is what makes "250 ZC, ONE TIME per account" (M10) a property of
-- the TABLE rather than of the caller's discipline.
--
-- With it in place every writer inserts unconditionally with `on conflict do
-- nothing` and is correct under retry — a settled attempt reported twice by a
-- retried callback pays once, and a hold placed twice for one lease holds
-- once. `flashml-cloud/CLAUDE.md` hard rule 4: idempotent commits, no double
-- counting.
--
-- WHAT `ref_type` / `ref_id` NAME, and why there is no foreign key on them.
-- The same call `attempts.job_id`, `contributions.job_id` and
-- `verifications.job_id` all make: an id here may name a row in this schema
-- or an object that only the coordinator knows about, and a foreign key would
-- refuse the second kind at exactly the moment there is nothing to be done
-- about it. In practice:
--
--   ('attempt', lease_id) — every hold, settlement, release and refund. The
--                           LEASE is the money unit because it is the unit
--                           the runtime already guarantees exactly once.
--   ('match',   match_id) — reserved for movements that belong to a whole
--                           match rather than one claim of it.
--   ('account', account_id) — the one-time grant.
--
-- A consequence worth stating: the amount still held against a lease is
-- `sum(delta_zc)` over that lease's entries on the buyer's escrow account. It
-- is READ OUT OF THE LEDGER, not tracked in a second column somewhere, so it
-- cannot drift from the ledger. Two independent counts of the same thing
-- eventually disagree, and the one that is wrong is never the one you are
-- looking at.
-- ---------------------------------------------------------------------------
create table if not exists public.credit_entries (
    id         bigserial primary key,

    account_id uuid not null
               references public.credit_accounts(id) on delete cascade,

    -- Signed millicredits. Negative is a debit, positive a credit.
    delta_zc   bigint not null,

    -- Constrained in the database so a bug in `marketplace.py` cannot invent
    -- a seventh reason that every later reader then has to guess the meaning
    -- of. `adjustment` is in the list and NOTHING WRITES IT — reserved for a
    -- manual correction that does not exist yet, the same way `abandoned` is
    -- reserved in `attempts.outcome` (0015). The value is in the vocabulary
    -- so the day a correction is needed it does not need a constraint
    -- migration; nothing manufactures one in the meantime.
    reason     text not null check (reason in (
                   'grant',
                   'escrow_hold',
                   'escrow_release',
                   'escrow_refund',
                   'earned_accepted_work',
                   'spent_accepted_work',
                   'adjustment')),

    ref_type   text,
    ref_id     text,

    created_at timestamptz not null default now()
);

comment on table public.credit_entries is
    'APPEND ONLY double-entry ledger. One row per leg; an ordinary movement '
    'writes two whose deltas sum to zero, and `grant` writes one because it '
    'mints. Nothing updates or deletes a row here — a correction is a new '
    'compensating entry, which is why `adjustment` exists as a reason. '
    'Idempotent by the expression unique index below, not by convention.';

comment on column public.credit_entries.reason is
    'What moved the credits. escrow_hold: spendable -> escrow on CLAIM (never '
    'on grant). spent_accepted_work / earned_accepted_work: the pair that '
    'pays a host out of a buyer''s escrow for work the coordinator ACCEPTED. '
    'escrow_release: the unspent remainder of a hold going back to the buyer '
    'after settlement. escrow_refund: the whole hold going back because the '
    'attempt produced nothing or the job was cancelled. grant: the one-time '
    '250 ZC mint. adjustment: reserved, unwritten.';

comment on column public.credit_entries.ref_id is
    'The object this leg is about, as text and with no foreign key — the same '
    'call attempts.job_id and contributions.job_id make, because an id here '
    'may name a coordinator-side object this schema has no row for. Paired '
    'with ref_type: (''attempt'', lease_id) for money that follows one claim, '
    '(''match'', match_id) for money that belongs to a whole entitlement, '
    '(''account'', account_id) for the grant.';

alter table public.credit_entries enable row level security;

-- Idempotency, and the one-time grant, in one index. See the header above for
-- why `coalesce` is load-bearing rather than defensive.
create unique index if not exists credit_entries_idempotency_idx
    on public.credit_entries (
        reason, coalesce(ref_type, ''), coalesce(ref_id, ''), account_id
    );

-- One account's statement, newest last. `id` rather than `created_at` as the
-- tiebreak column: two legs of one movement are written by the same statement
-- and routinely share a timestamp to the microsecond, and a statement that
-- renders a transfer's credit above its debit reads as two unrelated events.
create index if not exists credit_entries_account_idx
    on public.credit_entries (account_id, id);

-- "What has this lease cost, and what is still held against it" — the read
-- behind every settlement and every refund. The idempotency index above
-- cannot serve it: its leading column is `reason`, so a lookup by ref would
-- scan.
create index if not exists credit_entries_ref_idx
    on public.credit_entries (ref_type, ref_id);

-- ---------------------------------------------------------------------------
-- listings: a host's standing offer. One machine, one price, one class.
--
-- `capability_class` is DERIVED from the capabilities the machine already
-- reported at registration (`machines.capabilities`, 0001) and is never user
-- entered — `marketplace.capability_class` is the only thing that computes
-- it and `marketplace.create_listing` takes no class argument at all. A host
-- who could type their own class would sell a 3070 as `gpu-80gb-hopper` for
-- twenty times the price, and the buyer would find out when the task OOMs.
-- The column is constrained to the ladder in design §2.5.2 so a bug cannot
-- write a class the book has never heard of either.
--
-- `ask_zc_per_hour >= 0` — and zero is legal (M13). A volunteer asking
-- nothing is a real supply tier, labelled *donated* rather than priced, and
-- pricing them at a floor would misrepresent them. There is no minimum here
-- on purpose.
--
-- WORKSPACE DEMAND TAKES PRIORITY (M12). A workspace member may list the same
-- machine on the open market while still serving their team free; the open
-- listing is what the machine does when its team is idle. That priority is
-- NOT expressed in this table — it is a gate applied before the book, in
-- `marketplace.match_bid`, because it is a question about what the workspace
-- wants right now and not a property of the offer. A column here would be
-- stale the moment somebody queued a job.
-- ---------------------------------------------------------------------------
create table if not exists public.listings (
    id                  uuid primary key default gen_random_uuid(),

    machine_id          uuid not null
                        references public.machines(id) on delete cascade,

    -- The machine's owner, copied at creation. Denormalised deliberately: it
    -- is who gets PAID, and it must not change under a settled match because
    -- somebody transferred a machine later. Every owner-scoped read folds
    -- this into the query rather than joining through machines.
    owner_id            uuid not null
                        references public.profiles(id) on delete cascade,

    capability_class    text not null check (capability_class in (
                            'cpu-small', 'cpu-large',
                            'gpu-8gb', 'gpu-16gb', 'gpu-24gb',
                            'gpu-48gb', 'gpu-80gb', 'gpu-80gb-hopper')),

    -- Millicredits per machine-hour. Zero is legal and means donated.
    ask_zc_per_hour     bigint not null check (ask_zc_per_hour >= 0),

    max_concurrent_tasks integer not null default 1
                        check (max_concurrent_tasks > 0),

    available_from      timestamptz,
    available_until     timestamptz,

    state               text not null default 'open'
                        check (state in ('open', 'paused', 'withdrawn')),

    created_at          timestamptz not null default now(),
    updated_at          timestamptz not null default now()
);

comment on table public.listings is
    'A host''s standing offer for one machine at one price in one capability '
    'class. The class is DERIVED from machines.capabilities and never typed '
    'by the host. A zero ask is legal and labelled donated (M13). paused and '
    'withdrawn are both "not in the book"; they differ in intent, and '
    'withdrawn is terminal so that a machine sold once and taken back cannot '
    'be quietly returned to the book by a stale client.';

comment on column public.listings.ask_zc_per_hour is
    'Millicredits per machine-hour, integer only. What the HOST is paid — '
    'matching executes at the ask, never at the bid and never at a midpoint. '
    'There is no fee in v1 and no mechanism to take one (M11).';

alter table public.listings enable row level security;

-- A machine may hold at most ONE open listing. Two open listings for one
-- machine means two prices for the same hour and no rule for which one a
-- match executes at; the second one is not more supply, it is an ambiguity.
-- Partial so that the whole history of paused and withdrawn listings does not
-- have to be unique, and does not have to be in this index at all.
create unique index if not exists listings_one_open_per_machine_idx
    on public.listings (machine_id)
 where state = 'open';

-- The book read: the open asks in one class, cheapest first. PARTIAL for the
-- same reason `sandbox_sessions_unfinished_idx` is — the open set is small
-- and bounded by what is actually for sale, and the closed set is the whole
-- history and must not be scanned to find it.
create index if not exists listings_open_book_idx
    on public.listings (capability_class, ask_zc_per_hour)
 where state = 'open';

-- Owner-scoped listing, newest first, matching machines_owner_id_idx and
-- jobs_owner_id_idx. Every owner-scoped read in this schema has one.
create index if not exists listings_owner_idx
    on public.listings (owner_id, created_at desc);

-- ---------------------------------------------------------------------------
-- bids: a job's willingness to pay.
--
-- `job_id` is text with no foreign key, matching `attempts.job_id`,
-- `contributions.job_id`, `verifications.job_id` and
-- `sandbox_sessions.training_job_id`: a job id here may name a row in
-- `public.jobs` or a coordinator-side job, and a foreign key would refuse the
-- second kind at exactly the moment there is nothing to be done about it.
--
-- `state` carries 'partial' as a first-class value because a partial fill is
-- the EXPECTED outcome, not a degraded one (design §4 rule 5): a 40-trial
-- sweep matching eight machines at four different prices is the thing this
-- market is for. A bid that filled some of its tasks and is still looking is
-- neither 'open' nor 'filled', and collapsing it into either would make the
-- console lie about one of them.
-- ---------------------------------------------------------------------------
create table if not exists public.bids (
    id               uuid primary key default gen_random_uuid(),

    job_id           text not null,

    owner_id         uuid not null
                     references public.profiles(id) on delete cascade,

    capability_class text not null check (capability_class in (
                         'cpu-small', 'cpu-large',
                         'gpu-8gb', 'gpu-16gb', 'gpu-24gb',
                         'gpu-48gb', 'gpu-80gb', 'gpu-80gb-hopper')),

    -- The most the buyer will pay per machine-hour, in millicredits. Zero is
    -- legal here too, and means "donated capacity only" — a real request, and
    -- the only bid a person with an empty balance can still make.
    max_zc_per_hour  bigint not null check (max_zc_per_hour >= 0),

    tasks_wanted     integer not null check (tasks_wanted > 0),

    -- Seconds of machine time one task is expected to need. The estimate the
    -- escrow hold is computed from, so it lives with the bid rather than
    -- being passed around: a hold sized from a number nobody wrote down is a
    -- charge nobody can explain afterwards.
    --
    -- STRICTLY POSITIVE, and with no default, which is the one place this
    -- schema refuses a zero that the price columns cheerfully allow. A hold
    -- of nothing is not a cheap hold: settlement is CAPPED at what was held
    -- (a buyer cannot be charged past what they committed to when the claim
    -- was made), so a zero estimate would make every task under that bid free
    -- however long it actually ran. A default would let a caller reach that
    -- state by saying nothing at all, which is exactly how it would happen.
    est_task_seconds integer not null check (est_task_seconds > 0),

    deadline         timestamptz,

    state            text not null default 'open'
                     check (state in ('open', 'partial', 'filled',
                                      'cancelled', 'expired')),

    created_at       timestamptz not null default now(),
    updated_at       timestamptz not null default now()
);

comment on table public.bids is
    'A job''s willingness to pay, per capability class. `partial` is a '
    'first-class state because a partial fill across several machines at '
    'different prices is the expected outcome of matching, not a failure of '
    'it. est_task_seconds is what the escrow hold is sized from; it is an '
    'ESTIMATE and the settlement is on measured accepted work, so the '
    'remainder is released rather than kept.';

alter table public.bids enable row level security;

-- The book read: live demand in one class, highest bid first. Partial for the
-- same reason the ask index is; 'partial' is live demand and belongs in it.
create index if not exists bids_open_book_idx
    on public.bids (capability_class, max_zc_per_hour desc)
 where state in ('open', 'partial');

create index if not exists bids_owner_idx
    on public.bids (owner_id, created_at desc);

create index if not exists bids_job_idx
    on public.bids (job_id);

-- ---------------------------------------------------------------------------
-- matches: the priced entitlement, and the audit trail. Append-only in
-- substance — a row is written once and only its state and timestamps ever
-- move.
--
-- READ THE HEADER'S POINT 1 BEFORE THIS TABLE. A row here does not schedule
-- anything. It says: this machine may pull up to `tasks_assigned` tasks of
-- this bid's job, and if it does, they are priced at `agreed_zc_per_hour`.
-- The host still claims. `state` is therefore about what has HAPPENED, never
-- about what is intended:
--
--   granted  — the entitlement exists. NO MONEY HAS MOVED. Nobody is
--              committed to anything and the buyer's balance is untouched.
--   claimed  — a host actually claimed a lease under it. Escrow is held from
--              this point, per lease.
--   settled  — the work under it is done and paid for what was accepted.
--   refunded — a claim produced nothing accepted, or the job was cancelled;
--              every millicredit held went back to the buyer.
--   expired  — granted and never claimed, or the bid's deadline passed
--              first. NO WORK, NO CHARGE, AND NOT AN ERROR. This is the
--              ordinary outcome of matching a host that turns out to be busy
--              or offline, and it must be visible as itself.
--
-- The legal TRANSITIONS between these are policy and live in
-- `marketplace.py` as pure functions, for the reason 0014 gives about
-- `sandbox_sessions.state`: a state machine expressed in a trigger cannot be
-- read, argued with, or tested without a database.
--
-- THERE IS NO `escrow_entry_id` COLUMN, and the design sketch's §8 was wrong
-- to have one. A hold is a PAIR of entries (the debit and the credit), and a
-- match with four tasks accrues a pair per claim plus a settlement pair plus
-- a release per lease — so a single bigint here could name at most one leg of
-- one of them, and whichever it named would read as "the" escrow entry. The
-- link runs the other way: every entry names what it is about in
-- (ref_type, ref_id), and a match's money is `('attempt', lease_id)` over its
-- leases. One-to-many, pointing the direction the many actually is.
--
-- THERE ARE NO MONEY TOTALS HERE EITHER — no `escrow_zc`, no `settled_zc`.
-- They would be a second count of something the ledger already knows, and
-- two independent counts of the same thing eventually disagree; the headline
-- number that does not add up to the list beneath it reads as a bug in
-- whichever half is actually right. `marketplace.held_for_lease` and
-- `marketplace.match_settlement` read the ledger.
-- ---------------------------------------------------------------------------
create table if not exists public.matches (
    id                  uuid primary key default gen_random_uuid(),

    bid_id              uuid not null
                        references public.bids(id) on delete cascade,
    listing_id          uuid not null
                        references public.listings(id) on delete cascade,
    machine_id          uuid not null
                        references public.machines(id) on delete cascade,

    -- Both parties, copied at match time. Denormalised for the same reason
    -- `listings.owner_id` is: these are who pays and who is paid, and a
    -- settlement that resolved them by joining through a listing would pay
    -- somebody different if the machine changed hands mid-job.
    buyer_id            uuid not null
                        references public.profiles(id) on delete cascade,
    host_id             uuid not null
                        references public.profiles(id) on delete cascade,

    capability_class    text not null check (capability_class in (
                            'cpu-small', 'cpu-large',
                            'gpu-8gb', 'gpu-16gb', 'gpu-24gb',
                            'gpu-48gb', 'gpu-80gb', 'gpu-80gb-hopper')),

    -- THE HOST'S ASK, not the bid and not a midpoint (design §4 rule 4). The
    -- host gets the number they posted; the buyer never pays more than they
    -- bid. No hidden spread, and nothing for Zolli to take (M11).
    agreed_zc_per_hour  bigint not null check (agreed_zc_per_hour >= 0),

    tasks_assigned      integer not null check (tasks_assigned > 0),

    -- Copied from the bid so the hold this entitlement produces can be sized
    -- from what was agreed, not from a bid somebody edited afterwards.
    -- Strictly positive for the reason `bids.est_task_seconds` is: a hold of
    -- nothing plus a charge capped at the hold is free compute.
    est_task_seconds    integer not null check (est_task_seconds > 0),

    -- TRUE when this match was made with a host that had no acceptance rate
    -- yet (fewer than metrics.MIN_EVIDENCE resolved attempts in this class).
    -- Recorded rather than derived because the question a reader asks later
    -- is "was this host unproven AT THE TIME", and by then it will not be.
    -- It is also what makes the unproven SHARE CAP auditable: the cap is a
    -- bound on blast radius, and a bound nobody can check afterwards is a
    -- promise rather than a control.
    unproven_host       boolean not null default false,

    state               text not null default 'granted'
                        check (state in ('granted', 'claimed', 'settled',
                                         'refunded', 'expired')),

    granted_at          timestamptz not null default now(),
    claimed_at          timestamptz,
    closed_at           timestamptz
);

comment on table public.matches is
    'A PRICED ENTITLEMENT, not an assignment. FlashML is a pull system '
    '(flashruntime/scheduler/__init__.py:628) — a row here cannot make a host '
    'run anything; it makes a task eligible for that host at an agreed price '
    'and the host still claims. `granted` means no money has moved and none '
    'may: escrow is held on `claimed`, so a host that never shows up cannot '
    'lock a buyer''s balance. `expired` is the ordinary end of a match nobody '
    'claimed: no work, no charge, not an error.';

comment on column public.matches.agreed_zc_per_hour is
    'The HOST''S ASK in millicredits per machine-hour. Matching ranks on '
    'effective price (ask / acceptance rate) and executes at the ask, so this '
    'is always a number the host posted and never one the engine computed.';

comment on column public.matches.unproven_host is
    'The host had NO acceptance rate in this class when the match was made — '
    'fewer than 5 resolved attempts, where metrics.acceptance_rates returns '
    'None and nothing may substitute a number for it. Such hosts rank on ask '
    'alone and are limited by a cap on the SHARE of a job''s tasks they may '
    'take. Recorded at match time because it is a fact about the decision, '
    'and the host''s history will have moved by the time anybody audits it.';

alter table public.matches enable row level security;

-- The hot read, and the reason this table is on the critical path at all:
-- "which entitlements does this machine hold right now?" — the question the
-- pool gate asks every time a host pulls. PARTIAL on the live states, because
-- the settled and expired history is unbounded and none of it can entitle
-- anybody to anything.
create index if not exists matches_live_for_machine_idx
    on public.matches (machine_id)
 where state in ('granted', 'claimed');

create index if not exists matches_bid_idx on public.matches (bid_id);
create index if not exists matches_listing_idx on public.matches (listing_id);
create index if not exists matches_buyer_idx on public.matches (buyer_id, granted_at desc);
create index if not exists matches_host_idx on public.matches (host_id, granted_at desc);

-- ---------------------------------------------------------------------------
-- price_observations: written on every book change, never derived afterwards.
--
-- A price series assembled retroactively from matches is a story; one
-- recorded as it happened is evidence. The difference matters here more than
-- it usually would, because M5 says prices do not drift on a timer — they
-- move when somebody posts a better ask or a bid clears — and the only way to
-- SHOW that is a series whose every point names the event that caused it.
-- That is what `cause` is for, and it is why this table has a column the
-- design sketch did not: without it the chart asserts the claim and cannot
-- support it.
--
-- EVERY PRICE COLUMN IS NULLABLE, AND NULL IS NOT ZERO. A zero ask is legal
-- and means donated (M13), so "the best ask is 0" and "there are no asks" are
-- different facts about the market and must be different values in the
-- column. Defaulting the empty book to 0 would draw a line along the floor
-- for a class nobody is selling in — the single most misleading thing this
-- chart could do, because it is indistinguishable from a class full of
-- volunteers. `open_listings` and `open_bids` are NOT NULL because zero is
-- the honest answer there: none is a count, not an absence.
-- ---------------------------------------------------------------------------
create table if not exists public.price_observations (
    id               bigserial primary key,

    capability_class text not null check (capability_class in (
                         'cpu-small', 'cpu-large',
                         'gpu-8gb', 'gpu-16gb', 'gpu-24gb',
                         'gpu-48gb', 'gpu-80gb', 'gpu-80gb-hopper')),

    observed_at      timestamptz not null default now(),

    -- Millicredits per machine-hour. NULL means "no ask / no bid / no match
    -- in this class", which is not the same as zero. See the header.
    best_ask_zc      bigint,
    best_bid_zc      bigint,
    last_match_zc    bigint,

    open_listings    integer not null check (open_listings >= 0),
    open_bids        integer not null check (open_bids >= 0),

    -- What moved the book. Constrained so the series cannot grow a point
    -- nobody can attribute — a price observation with no cause is exactly the
    -- synthetic demand curve M5 forbids, arriving by the back door.
    cause            text not null check (cause in ('listing', 'bid', 'match'))
);

comment on table public.price_observations is
    'The book, sampled at every change and never reconstructed afterwards. '
    'One row per event that could move a price, carrying what the book looked '
    'like immediately after it and WHY it was written. Prices do not drift on '
    'a timer (M5); every point here has a cause, and a series with no causes '
    'is a synthetic demand curve however honestly it was drawn.';

comment on column public.price_observations.best_ask_zc is
    'Cheapest open ask in this class, in millicredits per machine-hour, or '
    'NULL when there are none. NULL is NOT zero: a zero ask is legal and '
    'means donated, so an empty book recorded as 0 is indistinguishable from '
    'a book full of volunteers.';

alter table public.price_observations enable row level security;

-- The chart's read: one class, most recent first.
create index if not exists price_observations_class_idx
    on public.price_observations (capability_class, observed_at desc);
