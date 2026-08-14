-- 0033_one_live_match_per_bid_listing.sql
--
-- A BID MAY HOLD AT MOST ONE LIVE ENTITLEMENT ON ANY ONE LISTING.
-- One partial unique index on public.matches (bid_id, listing_id), over every
-- state except `expired`.
--
-- ---------------------------------------------------------------------------
-- WHY THIS EXISTS.
--
-- `routing.refill_open_bids` re-matches a bid when new supply appears, and the
-- one shape of over-entitlement the market does NOT tolerate is a second
-- entitlement for the same bid on the same listing. 0018's header argues at
-- length that over-entitling across DIFFERENT machines is a feature — "a match
-- says these machines MAY pull this job's work", `max_concurrent_tasks` is a
-- concurrency hint rather than a decrementing quota, and a host that never
-- shows up costs nobody anything — but none of that argument reaches a bid
-- holding two rows against one listing. That is the same machine promised the
-- same work twice, and the remainder arithmetic that decides whether the bid
-- is `partial` or `filled` counts both.
--
-- In code it is prevented by `_plan_one_class`'s `exclude_listings`, which
-- `refill_open_bids` fills from the bid's own non-expired matches. That is a
-- READ, and a read cannot see a write another connection has not committed
-- yet: two refills racing on one bid — the ordinary case, since the hook fires
-- from `create_market_listing` and two hosts may list into a class in the same
-- second — both saw an empty held-set and both granted. The real fix is the
-- row lock `refill_open_bids` now takes before it reads anything
-- (`marketplace.lock_bid`); this index is the BACKSTOP, and it is here for the
-- reason 0018's header gives for spelling the capability ladder out in a
-- CHECK: an invariant the engine cannot act on when it is violated should be
-- refused by the database, not discovered later in a number that does not add
-- up. A future third caller that re-matches a live bid inherits the guarantee
-- without having to rediscover the argument.
--
-- ---------------------------------------------------------------------------
-- WHY `state <> 'expired'` AND NOT A BARE UNIQUE INDEX.
--
-- `expired` is the ordinary end of an entitlement nobody claimed (0018: "no
-- work, no charge, not an error"), and its tasks are unspent demand again —
-- `routing._live_tasks_assigned` and `marketplace.grant_matches` both compute
-- the remainder over exactly this predicate, and `refill_open_bids` builds its
-- held-listing set with it too. So a bid whose match on a listing expired
-- SHOULD be able to match that listing again when it reappears in the book: it
-- holds nothing there any more. A bare unique index would forbid that, which
-- is not a stricter version of the rule — it is a different, wrong rule that
-- permanently burns a listing for a bid because one host went offline once.
--
-- The predicate is the same one those three call sites use, deliberately: the
-- constraint and the arithmetic must agree about what "still holds this
-- listing" means, or one of them is enforcing something the other does not
-- believe. `settled` and `refunded` are inside the index because they are
-- inside that predicate — a settled match still counts against the bid's
-- remainder, so re-matching its listing would be the same double count.
--
-- NO LEGITIMATE DUPLICATE EXISTS TODAY. `public.matches` has exactly one
-- writer, `marketplace.grant_matches`, which inserts one row per
-- `MatchPlan.fills` entry; `marketplace.match_bid` produces at most one `Fill`
-- per ask and `marketplace.open_asks` yields one ask per listing, so a single
-- plan cannot contain the same listing twice. Two bids of the same job, and
-- two different buyers' bids, may of course both match one listing — that is
-- the concurrency-hint behaviour 0018 argues for, and this index does not
-- touch it: the key leads with `bid_id`.
--
-- ---------------------------------------------------------------------------
-- IT ALSO SERVES A READ. `routing._live_tasks_assigned`,
-- `routing._unproven_tasks_spent` and `grant_matches`' own recount all filter
-- `bid_id = ... and state <> 'expired'`, which today uses `matches_bid_idx`
-- (0018) and re-checks the state per row. This index is narrower on both axes,
-- so it is not the pure write-side cost a uniqueness backstop usually is.
--
-- `create unique index` WITHOUT `concurrently`: the migration runner applies
-- each file inside one transaction (`flashml_cloud_api.migrate`) and
-- CONCURRENTLY cannot run in one. It takes a SHARE lock — reads continue,
-- writes to `public.matches` wait — for as long as the build takes, which on a
-- table this size (one row per entitlement ever granted, in a market with a
-- handful of live listings) is milliseconds. If that ever stops being true the
-- answer is a separate, transaction-free deploy step, not a weaker constraint.
--
-- Row Level Security on `public.matches` is unchanged (0018: enabled, no
-- policies). An index grants nobody anything.
--
-- Idempotent: safe to re-run (`if not exists`, re-runnable `comment on`).
--
-- HOW THIS IS APPLIED: by the migration runner,
-- `python -m flashml_cloud_api.migrate`, which records it in
-- public.schema_migrations. There are TWO databases, dev (auto-migrated by the
-- `migrate-dev` job in .github/workflows/ci.yml on every push to `develop`)
-- and production (the `migrate-prod` job, gated on every test job and on the
-- manual deploy workflow). Do not apply it by hand to either — the runner is
-- what keeps the two honest.
--
-- ORDERING WITH THE API is free in both directions, unlike 0032's. The index
-- forbids only what the API never writes: applied first, the current API's
-- behaviour is byte-identical because it has never produced a duplicate on a
-- single connection; applied after, the API that takes the row lock is simply
-- unprotected against a concurrent duplicate for the length of the gap, which
-- is the state it is in today. Neither order breaks anything.
--
-- IF THIS MIGRATION FAILS TO APPLY it has found a duplicate that already
-- exists, which means the race described above really happened on that
-- database. Do not weaken the index. Find the rows —
--
--   select bid_id, listing_id, count(*)
--     from public.matches where state <> 'expired'
--    group by bid_id, listing_id having count(*) > 1;
--
-- — and decide per pair which entitlement is real; the surplus rows are
-- capacity promised to work that does not exist, and `expired` is the state
-- that says so without charging anybody.
--
-- Do not edit this file after it has been applied anywhere: the runner
-- checksums it, and an edit reads as drift and blocks every later migration.

create unique index if not exists matches_one_live_per_bid_listing_idx
    on public.matches (bid_id, listing_id)
 where state <> 'expired';

comment on index public.matches_one_live_per_bid_listing_idx is
    'A bid may hold at most one NON-EXPIRED match on any one listing. The '
    'backstop for routing.refill_open_bids: it re-matches a live bid when new '
    'supply appears and excludes the listings that bid already holds, but '
    'that exclusion is a read, so two concurrent refills could each see an '
    'empty held-set and both grant — one machine promised the same work '
    'twice, counted twice in the bid''s remainder. The row lock in '
    'marketplace.lock_bid is the fix; this is the constraint that makes it '
    'checkable. `expired` is excluded because an expired match holds nothing '
    '(0018: no work, no charge, not an error) and its listing must be '
    'matchable again — the same `state <> ''expired''` predicate the '
    'remainder arithmetic uses. It does NOT stop two different bids matching '
    'one listing: that is the concurrency-hint behaviour 0018 argues for.';
