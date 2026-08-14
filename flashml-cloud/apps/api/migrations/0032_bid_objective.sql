-- 0032_bid_objective.sql
--
-- WHAT THE BUYER ASKED THE BOOK TO BE RANKED BY, STORED ON THE BID.
-- One text column on public.bids, defaulted, checked against the three
-- objectives the engine has.
--
-- ---------------------------------------------------------------------------
-- WHY THIS EXISTS.
--
-- A bid records the class, the cap, the task count and the estimate — every
-- input to matching EXCEPT the order the book was walked in. That gap has two
-- consequences, and until now both were absorbed by saying so out loud:
--
--   * `GET /v1alpha1/jobs/{id}/routing` re-runs `routing.plan_pool_routing`
--     against the live book to explain a job's routing as it stands now. With
--     no stored objective it had to pick one, and it picked `cheapest` — the
--     engine's own fallback — and its docstring said "storing the objective on
--     the bid is a migration and is deliberately not done for v1". So a job
--     submitted `fastest` was re-explained in an order it was never matched
--     in. The label was honest; the answer was still the wrong question.
--   * Nothing could ever RE-MATCH a bid. `routing.refill_open_bids` (the
--     increment this column exists for) walks the open bids in a class when a
--     listing appears and grants what the new supply clears. A refill that
--     guessed `cheapest` for a `fastest` job would quietly buy that buyer a
--     different machine than their submission did, at the same cap, with
--     nothing in the response to say the objective had changed underneath
--     them.
--
-- Both are the same missing fact, so it is stored once, here, rather than
-- re-derived from the job's config at either call site. Re-deriving is not a
-- cheaper version of this column, it is a WRONG one: a job's `flashml.yaml`
-- lives in a git repo that may have been edited since submission, and the
-- SPILLED classes of a multi-class walk were never derived from that config
-- in the first place (`routing.plan_pool_routing` asks each class for the
-- remainder the previous ones could not fill). The bid is the only row that
-- knows what actually happened.
--
-- ---------------------------------------------------------------------------
-- WHY THE DEFAULT IS 'cheapest' AND NOT 'balanced'.
--
-- `flashml.yaml` defaults `price.objective` to `balanced` (owner-approved,
-- 2026-08-13) and every job submitted since carries one, so `balanced` looks
-- like the natural default for this column. It is the wrong one, because a
-- column default is not a product default — it is what the rows that are
-- ALREADY IN THIS TABLE will be backfilled with, and this DDL backfills every
-- one of them.
--
-- `marketplace.DEFAULT_RANK_OBJECTIVE` is `cheapest`: it is what
-- `match_bid`/`rank_asks` do when a caller names nothing, and it is what every
-- bid written before this migration was ACTUALLY matched under. Defaulting to
-- `balanced` would stamp a claim on historical rows that is false about all of
-- them — and the surface that reads this column re-explains a book with it, so
-- the false claim would be published as the order those matches were made in.
--
-- The two defaults are allowed to differ and this is the reason. New bids get
-- their objective from the caller (`marketplace.create_bid`'s `objective`
-- argument, threaded from `routing.route_submitted_job` out of
-- `price.objective`); this default only ever describes rows nobody named one
-- for.
--
-- ---------------------------------------------------------------------------
-- NOT NULL, and the CHECK, follow `bids.capability_class` one column up.
--
-- The register is closed and small, exactly like the capability ladder, and
-- the ladder is spelled out in a CHECK rather than a lookup table for the
-- reason 0018's header gives: a value nothing in the engine can act on should
-- be refused by the database, not discovered when a response builder raises a
-- KeyError. `marketplace.OBJECTIVES` is the code-side copy and
-- `marketplace._checked_objective` refuses the same three; a caller therefore
-- fails at the Python boundary with a message naming the objectives, and this
-- constraint is the backstop for anything that reaches the table another way.
--
-- If a fourth objective is ever added, this constraint is one of the sites
-- that has to move with `marketplace.OBJECTIVES` — `drop constraint if
-- exists` + `add constraint` in a later migration, the idiom 0015 established
-- and 0029/0031 used for `machines_geo_source_check`.
--
-- ---------------------------------------------------------------------------
-- NO INDEX. Nothing filters or orders by this column: it is READ off a bid row
-- the caller already has (`bids_for_job`, `open_bids`) and passed to the
-- ranking engine. An index would be maintained on every bid write for a query
-- nobody makes.
--
-- Row Level Security on `public.bids` is unchanged (0018: enabled, no
-- policies). `anon` and `authenticated` reach this column no differently than
-- they reach the rest of the row, which is to say not at all.
--
-- Idempotent: safe to re-run (`add column if not exists`, `drop constraint if
-- exists` + `add constraint`, re-runnable `comment on`).
--
-- HOW THIS IS APPLIED: by the migration runner,
-- `python -m flashml_cloud_api.migrate`, which records it in
-- public.schema_migrations. There are TWO databases, dev (auto-migrated by the
-- `migrate-dev` job in .github/workflows/ci.yml on every push to `develop`)
-- and production (the `migrate-prod` job, gated on every test job and on the
-- manual deploy workflow). Do not apply it by hand to either — the runner is
-- what keeps the two honest.
--
-- ORDERING WITH THE API. This migration goes FIRST, and the degradation is not
-- symmetrical:
--
--   * API before migration: `marketplace.create_bid` names a column that does
--     not exist, so EVERY priced submission's routing block fails — fail-open
--     in the submit handler, so the job still lands, but no bid is ever
--     written and the market goes quiet with only a warning line to say so.
--     `_BID_SELECT` would also name it, breaking every bid read. This order is
--     wrong and nothing catches it for you.
--   * Migration before API: the column exists, every existing row reads
--     `cheapest`, and nothing writes or reads it until the API that knows
--     about it deploys. The book behaves byte-identically to today. This is
--     the correct order, and it is the order CI produces — `migrate-dev` and
--     `migrate-prod` both run before their deploy step.
--
-- Do not edit this file after it has been applied anywhere: the runner
-- checksums it, and an edit reads as drift and blocks every later migration.

-- ---------------------------------------------------------------------------
-- The column. Defaulted and backfilled in one statement — `public.bids` is a
-- small table (one row per capability class per priced submission) and a
-- non-volatile default is metadata-only on Postgres 11+, so this does not
-- rewrite the table on either database.
-- ---------------------------------------------------------------------------
alter table public.bids
    add column if not exists objective text not null default 'cheapest';

alter table public.bids
    drop constraint if exists bids_objective_check;
alter table public.bids
    add constraint bids_objective_check
        check (objective in ('cheapest', 'balanced', 'fastest'));

comment on column public.bids.objective is
    'Which of marketplace.OBJECTIVES this bid asked the book to be ranked by '
    '— what the submitter wrote as `price.objective` in flashml.yaml, carried '
    'here by routing.route_submitted_job. It changes the ORDER the book is '
    'consumed in and nothing else: the cap in max_zc_per_hour is still '
    'compared against the effective price and a fill still executes at the '
    'host''s own ask. STORED RATHER THAN RE-DERIVED because the two readers '
    'cannot honestly re-derive it — GET /jobs/{id}/routing re-explains against '
    'the live book long after the repo''s flashml.yaml may have been edited, '
    'and routing.refill_open_bids re-matches a bid whose spilled classes were '
    'never derived from that config at all. The default is `cheapest` because '
    'that is marketplace.DEFAULT_RANK_OBJECTIVE and therefore what every row '
    'written before migration 0032 was actually matched under; it is '
    'deliberately NOT flashml.yaml''s own `balanced` default, which would '
    'stamp a false claim on every historical row. See 0032''s header.';
